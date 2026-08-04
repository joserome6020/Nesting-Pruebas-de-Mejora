#include "arga_nest/nfp_cache.hpp"
#include "arga_nest/lod.hpp"

#include "clipper2/clipper.h"

#include <cmath>
#include <cstdint>
#include <fstream>
#include <list>
#include <sstream>
#ifdef _WIN32
#  include <filesystem>
#else
#  include <filesystem>
#endif

namespace arga::core {
namespace {

using namespace Clipper2Lib;

constexpr std::size_t kDefaultCap = 4096;
constexpr int kPrec = 3;

struct CacheKey {
    std::string s;
    bool operator==(const CacheKey& o) const { return s == o.s; }
};

struct CacheKeyHash {
    std::size_t operator()(const CacheKey& k) const {
        return std::hash<std::string>{}(k.s);
    }
};

PathD to_path(const std::vector<Point2D>& ring) {
    PathD p;
    for (const auto& q : ring) {
        p.push_back(PointD(q.x, q.y));
    }
    if (p.size() >= 2) {
        auto& a = p.front();
        auto& b = p.back();
        if (std::abs(a.x - b.x) < 1e-9 && std::abs(a.y - b.y) < 1e-9) {
            p.pop_back();
        }
    }
    return p;
}

std::string canon_ring(const std::vector<Point2D>& ring) {
    if (ring.empty()) {
        return "";
    }
    // cuantiza y elige lexicográficamente el mejor arranque
    std::vector<std::pair<long long, long long>> pts;
    pts.reserve(ring.size());
    for (const auto& p : ring) {
        pts.emplace_back(
            static_cast<long long>(std::llround(p.x * 1000.0)),
            static_cast<long long>(std::llround(p.y * 1000.0)));
    }
    if (pts.size() >= 2 && pts.front() == pts.back()) {
        pts.pop_back();
    }
    if (pts.empty()) {
        return "";
    }
    std::size_t best = 0;
    for (std::size_t i = 1; i < pts.size(); ++i) {
        if (pts[i] < pts[best]) {
            best = i;
        }
    }
    std::ostringstream oss;
    for (std::size_t k = 0; k < pts.size(); ++k) {
        const auto& q = pts[(best + k) % pts.size()];
        oss << q.first << ',' << q.second << ';';
    }
    return oss.str();
}

std::string make_key(
    const std::vector<std::vector<Point2D>>& a,
    const std::vector<std::vector<Point2D>>& b,
    double ang_a,
    double ang_b,
    double kerf) {
    std::ostringstream oss;
    oss << "v1|" << canon_ring(a.empty() ? std::vector<Point2D>{} : a[0]) << "|"
        << canon_ring(b.empty() ? std::vector<Point2D>{} : b[0]) << "|"
        << std::llround(ang_a * 100) << "|" << std::llround(ang_b * 100) << "|"
        << std::llround(kerf * 1000);
    return oss.str();
}

std::vector<std::vector<Point2D>> compute_nfp_outer(
    const std::vector<std::vector<Point2D>>& rings_a,
    const std::vector<std::vector<Point2D>>& rings_b) {
    std::vector<std::vector<Point2D>> out;
    if (rings_a.empty() || rings_b.empty()) {
        return out;
    }
    PathD A = to_path(rings_a[0]);
    PathD B = to_path(rings_b[0]);
    if (A.size() < 3 || B.size() < 3) {
        return out;
    }
    PathD invB;
    invB.reserve(B.size());
    for (const auto& p : B) {
        invB.push_back(PointD(-p.x, -p.y));
    }
    const PathsD nfp = MinkowskiSum(invB, A, true, kPrec);
    for (const auto& path : nfp) {
        std::vector<Point2D> ring;
        ring.reserve(path.size() + 1);
        for (const auto& p : path) {
            ring.push_back({p.x, p.y});
        }
        if (!ring.empty()) {
            ring.push_back(ring.front());
        }
        if (ring.size() >= 4) {
            out.push_back(std::move(ring));
        }
    }
    return out;
}

struct Store {
    std::mutex mu;
    std::size_t capacity = kDefaultCap;
    std::size_t hits = 0;
    std::size_t misses = 0;
    std::size_t evictions = 0;
    std::list<CacheKey> lru;
    std::unordered_map<
        CacheKey,
        std::pair<std::list<CacheKey>::iterator, std::vector<std::vector<Point2D>>>,
        CacheKeyHash>
        map;
};

Store& store() {
    static Store s;
    return s;
}

struct L2Store {
    std::mutex mu;
    std::string dir;
    std::size_t hits = 0;
    std::size_t misses = 0;
    std::size_t writes = 0;
};

L2Store& l2() {
    static L2Store s;
    if (s.dir.empty()) {
        namespace fs = std::filesystem;
        fs::path p = fs::temp_directory_path() / "ArgaNestCore" / "nfp_l2";
        std::error_code ec;
        fs::create_directories(p, ec);
        s.dir = p.string();
    }
    return s;
}

std::string l2_path_for_key(const std::string& key) {
    // filename-safe hash
    const std::size_t h = std::hash<std::string>{}(key);
    std::ostringstream oss;
    oss << l2().dir << "/nfp_" << std::hex << h << ".bin";
    return oss.str();
}

bool l2_load(const std::string& key, std::vector<std::vector<Point2D>>& out) {
    const std::string path = l2_path_for_key(key);
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        return false;
    }
    std::uint32_t n_rings = 0;
    in.read(reinterpret_cast<char*>(&n_rings), sizeof(n_rings));
    if (!in || n_rings > 64) {
        return false;
    }
    out.clear();
    out.reserve(n_rings);
    for (std::uint32_t r = 0; r < n_rings; ++r) {
        std::uint32_t npts = 0;
        in.read(reinterpret_cast<char*>(&npts), sizeof(npts));
        if (!in || npts > 100000) {
            return false;
        }
        std::vector<Point2D> ring(npts);
        for (std::uint32_t i = 0; i < npts; ++i) {
            in.read(reinterpret_cast<char*>(&ring[i].x), sizeof(double));
            in.read(reinterpret_cast<char*>(&ring[i].y), sizeof(double));
        }
        out.push_back(std::move(ring));
    }
    return static_cast<bool>(in);
}

void l2_save(const std::string& key, const std::vector<std::vector<Point2D>>& value) {
    const std::string path = l2_path_for_key(key);
    std::ofstream out(path, std::ios::binary);
    if (!out) {
        return;
    }
    std::uint32_t n_rings = static_cast<std::uint32_t>(value.size());
    out.write(reinterpret_cast<const char*>(&n_rings), sizeof(n_rings));
    for (const auto& ring : value) {
        std::uint32_t npts = static_cast<std::uint32_t>(ring.size());
        out.write(reinterpret_cast<const char*>(&npts), sizeof(npts));
        for (const auto& p : ring) {
            out.write(reinterpret_cast<const char*>(&p.x), sizeof(double));
            out.write(reinterpret_cast<const char*>(&p.y), sizeof(double));
        }
    }
}

}  // namespace

std::vector<std::vector<Point2D>> compute_nfp_outer_cached(
    const std::vector<std::vector<Point2D>>& rings_a,
    const std::vector<std::vector<Point2D>>& rings_b,
    double angle_a_deg,
    double angle_b_deg,
    double kerf_mm) {
    // LOD ligero antes de Minkowski (más rápido, suficiente para screening)
    auto a = simplify_rings_dp(rings_a, 0.35);
    auto b = simplify_rings_dp(rings_b, 0.35);
    CacheKey key{make_key(a, b, angle_a_deg, angle_b_deg, kerf_mm)};
    auto& st = store();
    {
        std::lock_guard<std::mutex> lock(st.mu);
        auto it = st.map.find(key);
        if (it != st.map.end()) {
            st.lru.splice(st.lru.begin(), st.lru, it->second.first);
            ++st.hits;
            return it->second.second;
        }
        ++st.misses;
    }

    std::vector<std::vector<Point2D>> value;
    {
        auto& L = l2();
        std::lock_guard<std::mutex> lock(L.mu);
        if (l2_load(key.s, value)) {
            ++L.hits;
        } else {
            ++L.misses;
            value = compute_nfp_outer(a, b);
            l2_save(key.s, value);
            ++L.writes;
        }
    }

    {
        std::lock_guard<std::mutex> lock(st.mu);
        if (st.map.find(key) != st.map.end()) {
            return value;
        }
        st.lru.push_front(key);
        st.map.emplace(key, std::make_pair(st.lru.begin(), value));
        while (st.map.size() > st.capacity) {
            const auto& old = st.lru.back();
            st.map.erase(old);
            st.lru.pop_back();
            ++st.evictions;
        }
    }
    return value;
}

NfpCacheStats nfp_cache_stats() {
    auto& st = store();
    std::lock_guard<std::mutex> lock(st.mu);
    return {st.hits, st.misses, st.evictions, st.map.size(), st.capacity};
}

void reset_nfp_cache() {
    auto& st = store();
    std::lock_guard<std::mutex> lock(st.mu);
    st.map.clear();
    st.lru.clear();
    st.hits = st.misses = st.evictions = 0;
}

void set_nfp_cache_capacity(std::size_t capacity) {
    auto& st = store();
    std::lock_guard<std::mutex> lock(st.mu);
    st.capacity = std::max<std::size_t>(16, capacity);
    while (st.map.size() > st.capacity) {
        const auto& old = st.lru.back();
        st.map.erase(old);
        st.lru.pop_back();
        ++st.evictions;
    }
}

void set_nfp_l2_dir(const std::string& dir) {
    auto& L = l2();
    std::lock_guard<std::mutex> lock(L.mu);
    L.dir = dir;
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
}

std::string nfp_l2_dir() {
    return l2().dir;
}

NfpCacheStats nfp_l2_stats() {
    auto& L = l2();
    std::lock_guard<std::mutex> lock(L.mu);
    return {L.hits, L.misses, L.writes, 0, 0};
}

}  // namespace arga::core