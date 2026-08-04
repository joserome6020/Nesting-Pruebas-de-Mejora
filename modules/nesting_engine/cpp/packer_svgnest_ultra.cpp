#include "packer_svgnest_ultra.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <functional>
#include <limits>
#include <mutex>
#include <numeric>
#include <optional>
#include <random>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include "clipper2/clipper.h"
#include "clipper2/clipper.minkowski.h"
#include "cuda/nest_accel_raster.hpp"

namespace arga {
namespace {

using namespace Clipper2Lib;

constexpr double kPi = 3.14159265358979323846;
constexpr double kPartInPartMaxAreaMm2 = 800'000.0;
constexpr double kVoidMinAreaMm2 = 25.0 * 25.0;
constexpr double kAreaEstructuralUmbralMm2 = 200.0 * 645.16;
constexpr int kMaxGuardCavidad = 80;
// NFP/Minkowski: barrenos densos (miles de verts) saturan Clipper. Export usa anillo exacto.
constexpr size_t kNfpMaxHoleVerts = 48;
constexpr size_t kNfpMaxOuterVerts = 64;
constexpr double kNfpSimplifyEpsMm = 0.35;  // ~0.014"

struct Bounds {
    double minx = 0.0;
    double miny = 0.0;
    double maxx = 0.0;
    double maxy = 0.0;
};

struct Variation {
    std::vector<std::vector<Point2D>> poly;
    std::vector<std::vector<Point2D>> poly_buff;
    std::vector<std::vector<Point2D>> marks;
    PathD outer_norm;
    double b_minx = 0.0;
    double b_miny = 0.0;
    double b_maxx = 0.0;
    double b_maxy = 0.0;
};

struct LimitContext {
    bool active = false;
    Bounds bounds{};
    PathsD eval_paths;
};

struct PlacementState {
    SheetOut hoja;
    std::vector<PathsD> fijas_buff_paths;
    std::vector<PathsD> fijas_solid_paths;
    std::vector<Bounds> fijas_bounds;
    std::vector<char> fijas_es_anfitriona;
};

struct CavidadAbierta {
    size_t host_idx = 0;
    std::vector<std::vector<Point2D>> rings;
};

bool pieza_cabe_en_hueco_aabb(const PieceIn& p, const Bounds& hb, double tol);

PathD to_path_d(const std::vector<Point2D>& ring) {
    PathD out;
    for (const auto& p : ring) {
        out.emplace_back(p.x, p.y);
    }
    return out;
}

PathsD to_paths_d(const std::vector<std::vector<Point2D>>& rings) {
    PathsD out;
    for (const auto& ring : rings) {
        if (ring.size() >= 3) {
            out.push_back(to_path_d(ring));
        }
    }
    return out;
}

std::vector<std::vector<Point2D>> from_paths_d(const PathsD& paths) {
    std::vector<std::vector<Point2D>> out;
    for (const auto& path : paths) {
        std::vector<Point2D> ring;
        for (const auto& p : path) {
            ring.push_back({p.x, p.y});
        }
        if (ring.size() >= 3) {
            out.push_back(std::move(ring));
        }
    }
    return out;
}

Bounds bounds_of_paths(const PathsD& paths) {
    Bounds b;
    bool first = true;
    for (const auto& path : paths) {
        for (const auto& p : path) {
            if (first) {
                b.minx = b.maxx = p.x;
                b.miny = b.maxy = p.y;
                first = false;
            } else {
                b.minx = std::min(b.minx, p.x);
                b.maxx = std::max(b.maxx, p.x);
                b.miny = std::min(b.miny, p.y);
                b.maxy = std::max(b.maxy, p.y);
            }
        }
    }
    return b;
}

Bounds bounds_of_rings(const std::vector<std::vector<Point2D>>& rings) {
    return bounds_of_paths(to_paths_d(rings));
}

double polygon_area_ring(const std::vector<Point2D>& ring) {
    if (ring.size() < 3) {
        return 0.0;
    }
    double area = 0.0;
    const size_t n = ring.size();
    for (size_t i = 0; i < n; ++i) {
        const auto& a = ring[i];
        const auto& b = ring[(i + 1) % n];
        area += (a.x * b.y) - (b.x * a.y);
    }
    return std::abs(area) * 0.5;
}

double total_area(const std::vector<std::vector<Point2D>>& rings) {
    if (rings.empty()) {
        return 0.0;
    }
    double area = polygon_area_ring(rings.front());
    for (size_t i = 1; i < rings.size(); ++i) {
        area -= polygon_area_ring(rings[i]);
    }
    return std::max(0.0, area);
}

double piece_area(const PieceIn& p) {
    return p.area > 0.0 ? p.area : total_area(p.rings);
}

Point2D polygon_centroid(const std::vector<Point2D>& ring) {
    if (ring.size() < 3) {
        return {0.0, 0.0};
    }
    double cx = 0.0;
    double cy = 0.0;
    double a = 0.0;
    const size_t n = ring.size();
    for (size_t i = 0; i < n; ++i) {
        const auto& p0 = ring[i];
        const auto& p1 = ring[(i + 1) % n];
        const double cross = (p0.x * p1.y) - (p1.x * p0.y);
        a += cross;
        cx += (p0.x + p1.x) * cross;
        cy += (p0.y + p1.y) * cross;
    }
    a *= 0.5;
    if (std::abs(a) < 1e-12) {
        const Bounds b = bounds_of_rings({ring});
        return {(b.minx + b.maxx) * 0.5, (b.miny + b.maxy) * 0.5};
    }
    cx /= (6.0 * a);
    cy /= (6.0 * a);
    return {cx, cy};
}

void translate_paths(PathsD& paths, double dx, double dy) {
    for (auto& path : paths) {
        for (auto& p : path) {
            p.x += dx;
            p.y += dy;
        }
    }
}

void translate_rings(std::vector<std::vector<Point2D>>& rings, double dx, double dy) {
    for (auto& ring : rings) {
        for (auto& p : ring) {
            p.x += dx;
            p.y += dy;
        }
    }
}

void rotate_rings(std::vector<std::vector<Point2D>>& rings, double cx, double cy, double angle_deg) {
    const double rad = angle_deg * kPi / 180.0;
    const double cos_a = std::cos(rad);
    const double sin_a = std::sin(rad);
    for (auto& ring : rings) {
        for (auto& p : ring) {
            const double dx = p.x - cx;
            const double dy = p.y - cy;
            p.x = cx + (dx * cos_a) - (dy * sin_a);
            p.y = cy + (dx * sin_a) + (dy * cos_a);
        }
    }
}

/**
 * Buffer de kerf preservando canales abiertos (perfil C / VFM).
 * No usar pick-largest sobre el offset: colapsa el canal cóncavo.
 */
PathsD buffer_paths(const PathsD& subject, double delta) {
    if (subject.empty()) {
        return {};
    }
    PathsD cleaned = SimplifyPaths(subject, 0.1, true);
    if (cleaned.empty()) {
        cleaned = subject;
    }

    const double abs_delta = std::abs(delta);
    const JoinType jt = JoinType::Round;
    if (abs_delta < 1e-12) {
        return cleaned;
    }

    if (cleaned.size() == 1) {
        PathsD solution = InflatePaths(cleaned, delta, jt, EndType::Polygon);
        PathsD u = Union(solution, FillRule::NonZero);
        return u.empty() ? solution : u;
    }

    PathsD outer = InflatePaths({cleaned[0]}, abs_delta, jt, EndType::Polygon);
    PathsD outer_u = Union(outer, FillRule::NonZero);
    if (outer_u.empty()) {
        outer_u = outer;
    }
    if (outer_u.empty()) {
        return {};
    }

    PathsD holes_shrunk;
    for (size_t i = 1; i < cleaned.size(); ++i) {
        PathsD hi = InflatePaths({cleaned[i]}, -abs_delta, jt, EndType::Polygon);
        if (hi.empty()) {
            continue;
        }
        holes_shrunk.insert(holes_shrunk.end(), hi.begin(), hi.end());
    }
    if (holes_shrunk.empty()) {
        return outer_u;
    }

    PathsD solid = Difference(outer_u, holes_shrunk, FillRule::NonZero);
    return solid.empty() ? outer_u : solid;
}

std::vector<std::vector<Point2D>> buffer_rings(const std::vector<std::vector<Point2D>>& rings, double delta) {
    return from_paths_d(buffer_paths(to_paths_d(rings), delta));
}

/** Metal real = exterior − huecos (necesario para AABB−metal en VFM). */
PathsD materialize_metal(const std::vector<std::vector<Point2D>>& rings) {
    if (rings.empty()) {
        return {};
    }
    PathsD outer = to_paths_d({rings[0]});
    if (outer.empty()) {
        return {};
    }
    if (rings.size() == 1) {
        return outer;
    }
    PathsD holes;
    for (size_t i = 1; i < rings.size(); ++i) {
        PathsD h = to_paths_d({rings[i]});
        holes.insert(holes.end(), h.begin(), h.end());
    }
    if (holes.empty()) {
        return outer;
    }
    PathsD solid = Difference(outer, holes, FillRule::NonZero);
    if (solid.empty()) {
        solid = Difference(outer, holes, FillRule::EvenOdd);
    }
    return solid.empty() ? outer : solid;
}

bool pieza_es_anfitriona(const PieceIn& p) {
    if (p.rings.size() >= 2) {
        return true;
    }
    const double area_mat = piece_area(p);
    if (area_mat >= kAreaEstructuralUmbralMm2) {
        return true;
    }
    const Bounds bb = bounds_of_rings(p.rings);
    const double bbox_area = (bb.maxx - bb.minx) * (bb.maxy - bb.miny);
    return bbox_area > kVoidMinAreaMm2 * 4.0 && bbox_area > 0.0 && area_mat / bbox_area < 0.85;
}

bool paths_intersect(const PathsD& a, const PathsD& b) {
    if (a.empty() || b.empty()) {
        return false;
    }
    const PathsD inter = Intersect(a, b, FillRule::NonZero);
    return !inter.empty() && std::abs(Area(inter)) > 1e-6;
}

bool path_contained_in(const PathsD& subject, const PathsD& container) {
    if (subject.empty() || container.empty()) {
        return false;
    }
    const PathsD diff = Difference(subject, container, FillRule::NonZero);
    return diff.empty() || std::abs(Area(diff)) < 1e-4;
}

PathsD translate_copy(const PathsD& src, double dx, double dy) {
    PathsD out = src;
    translate_paths(out, dx, dy);
    return out;
}

std::vector<std::vector<Point2D>> translate_rings_copy(
    const std::vector<std::vector<Point2D>>& rings,
    double dx,
    double dy) {
    auto out = rings;
    translate_rings(out, dx, dy);
    return out;
}

PathD invert_path(const PathD& path) {
    PathD out;
    for (const auto& p : path) {
        out.emplace_back(-p.x, -p.y);
    }
    return out;
}

PathD simplify_path_rdp(const PathD& in, double eps, size_t max_pts) {
    if (in.size() <= 3 || (in.size() <= max_pts && eps <= 0.0)) {
        return in;
    }
    // Douglas-Peucker iterativo (evita stack profundo en barrenos densos).
    const size_t n = in.size();
    std::vector<char> keep(n, 0);
    keep[0] = 1;
    keep[n - 1] = 1;
    std::vector<std::pair<size_t, size_t>> stack;
    stack.emplace_back(0, n - 1);
    const double eps2 = eps * eps;
    while (!stack.empty()) {
        const auto [i0, i1] = stack.back();
        stack.pop_back();
        double best_d2 = -1.0;
        size_t best_i = i0;
        const double ax = in[i0].x;
        const double ay = in[i0].y;
        const double bx = in[i1].x;
        const double by = in[i1].y;
        const double abx = bx - ax;
        const double aby = by - ay;
        const double ab2 = abx * abx + aby * aby;
        for (size_t i = i0 + 1; i < i1; ++i) {
            double d2 = 0.0;
            if (ab2 < 1e-18) {
                const double dx = in[i].x - ax;
                const double dy = in[i].y - ay;
                d2 = dx * dx + dy * dy;
            } else {
                const double t = ((in[i].x - ax) * abx + (in[i].y - ay) * aby) / ab2;
                const double ux = ax + t * abx;
                const double uy = ay + t * aby;
                const double dx = in[i].x - ux;
                const double dy = in[i].y - uy;
                d2 = dx * dx + dy * dy;
            }
            if (d2 > best_d2) {
                best_d2 = d2;
                best_i = i;
            }
        }
        if (best_d2 > eps2 && best_i > i0 && best_i < i1) {
            keep[best_i] = 1;
            stack.emplace_back(i0, best_i);
            stack.emplace_back(best_i, i1);
        }
    }
    PathD out;
    out.reserve(std::min(n, max_pts + 2));
    for (size_t i = 0; i < n; ++i) {
        if (keep[i]) {
            out.push_back(in[i]);
        }
    }
    if (out.size() < 3) {
        return in;
    }
    // Si aún demasiados puntos, muestreo uniforme preservando extremos.
    if (out.size() > max_pts) {
        PathD sampled;
        sampled.reserve(max_pts);
        const size_t last = out.size() - 1;
        for (size_t k = 0; k < max_pts; ++k) {
            const size_t idx = (k * last) / (max_pts - 1);
            sampled.push_back(out[idx]);
        }
        return sampled;
    }
    return out;
}

PathD normalize_outer_at_origin(const std::vector<std::vector<Point2D>>& rings) {
    if (rings.empty() || rings.front().size() < 3) {
        return {};
    }
    const Bounds bb = bounds_of_rings(rings);
    PathD out = to_path_d(rings.front());
    for (auto& p : out) {
        p.x -= bb.minx;
        p.y -= bb.miny;
    }
    if (out.size() > kNfpMaxOuterVerts) {
        out = simplify_path_rdp(out, kNfpSimplifyEpsMm, kNfpMaxOuterVerts);
    }
    return out;
}

/** Cache NFP relativo estilo Deepnest/SVGNest: clave (A@origen, B@origen). Thread-safe. */
struct NfpPairCache {
    std::unordered_map<std::uint64_t, PathsD> relative;
    mutable std::mutex mu;

    static std::uint64_t hash_path(const PathD& path) {
        std::uint64_t h = 1469598103934665603ull;
        for (const auto& p : path) {
            const auto xi = static_cast<std::int64_t>(std::llround(p.x * 10.0));
            const auto yi = static_cast<std::int64_t>(std::llround(p.y * 10.0));
            h ^= static_cast<std::uint64_t>(xi) + 0x9e3779b97f4a7c15ull;
            h *= 1099511628211ull;
            h ^= static_cast<std::uint64_t>(yi) + 0x9e3779b97f4a7c15ull;
            h *= 1099511628211ull;
        }
        h ^= static_cast<std::uint64_t>(path.size()) * 0x100000001b3ull;
        return h;
    }

    PathsD get_relative(const PathD& station_at_origin, const PathD& orbiting_norm) {
        const std::uint64_t key =
            hash_path(station_at_origin) ^ (hash_path(orbiting_norm) * 0x9e3779b97f4a7c15ull);
        {
            std::lock_guard<std::mutex> lock(mu);
            const auto it = relative.find(key);
            if (it != relative.end()) {
                return it->second;
            }
        }
        // Cache thread-local: evita que 12 hilos disparen el mismo Minkowski a la vez.
        thread_local std::unordered_map<std::uint64_t, PathsD> tl;
        {
            const auto it = tl.find(key);
            if (it != tl.end()) {
                return it->second;
            }
        }
        PathsD nfp;
        if (station_at_origin.size() >= 3 && orbiting_norm.size() >= 3) {
            PathD station = station_at_origin;
            PathD orbit = orbiting_norm;
            if (station.size() > kNfpMaxOuterVerts) {
                station = simplify_path_rdp(station, kNfpSimplifyEpsMm, kNfpMaxOuterVerts);
            }
            if (orbit.size() > kNfpMaxOuterVerts) {
                orbit = simplify_path_rdp(orbit, kNfpSimplifyEpsMm, kNfpMaxOuterVerts);
            }
            const PathD inv_orb = invert_path(orbit);
            nfp = MinkowskiSum(inv_orb, station, true, 3);
        }
        tl[key] = nfp;
        std::lock_guard<std::mutex> lock(mu);
        auto [ins, inserted] = relative.emplace(key, nfp);
        return inserted ? nfp : ins->second;
    }
};

int resolve_intra_threads() {
    auto parse_pos = [](const char* raw) -> int {
        if (raw == nullptr || raw[0] == '\0') {
            return -1;
        }
        char* end = nullptr;
        const long v = std::strtol(raw, &end, 10);
        if (end == raw || v <= 0) {
            return -1;
        }
        return static_cast<int>(std::min<long>(v, 256));
    };
    const int from_arga = parse_pos(std::getenv("ARGA_NEST_OMP_THREADS"));
    if (from_arga > 0) {
        return from_arga;
    }
    const int from_omp = parse_pos(std::getenv("OMP_NUM_THREADS"));
    if (from_omp > 0) {
        return from_omp;
    }
    const unsigned hc = std::thread::hardware_concurrency();
    if (hc == 0) {
        return 1;
    }
    // Deja 1 núcleo para UI/OS; el manager ajusta más bajo en multi-lote.
    return static_cast<int>(std::max(1u, hc > 1 ? hc - 1 : 1));
}

void parallel_for_index(size_t count, int threads, const std::function<void(size_t)>& fn) {
    if (count == 0) {
        return;
    }
    const int nthreads = std::max(1, std::min(threads, static_cast<int>(count)));
    if (nthreads <= 1) {
        for (size_t i = 0; i < count; ++i) {
            fn(i);
        }
        return;
    }
    std::vector<std::thread> workers;
    workers.reserve(static_cast<size_t>(nthreads));
    for (int t = 0; t < nthreads; ++t) {
        workers.emplace_back([=, &fn]() {
            for (size_t i = static_cast<size_t>(t); i < count; i += static_cast<size_t>(nthreads)) {
                fn(i);
            }
        });
    }
    for (auto& w : workers) {
        w.join();
    }
}

void append_nfp_candidates(
    std::vector<std::pair<double, double>>& anclajes,
    const PathsD& stationary_buff,
    const PathD& orbiting_norm,
    NfpPairCache* cache) {
    if (stationary_buff.empty() || orbiting_norm.empty()) {
        return;
    }
    for (const auto& stat_path : stationary_buff) {
        if (stat_path.size() < 3) {
            continue;
        }
        Bounds bb{};
        bool first = true;
        PathD stat_norm;
        stat_norm.reserve(stat_path.size());
        for (const auto& p : stat_path) {
            if (first) {
                bb.minx = bb.maxx = p.x;
                bb.miny = bb.maxy = p.y;
                first = false;
            } else {
                bb.minx = std::min(bb.minx, p.x);
                bb.maxx = std::max(bb.maxx, p.x);
                bb.miny = std::min(bb.miny, p.y);
                bb.maxy = std::max(bb.maxy, p.y);
            }
        }
        for (const auto& p : stat_path) {
            stat_norm.emplace_back(p.x - bb.minx, p.y - bb.miny);
        }

        PathsD nfp_paths;
        if (cache != nullptr) {
            nfp_paths = cache->get_relative(stat_norm, orbiting_norm);
        } else {
            const PathD inv_orb = invert_path(orbiting_norm);
            nfp_paths = MinkowskiSum(inv_orb, stat_norm, true, 3);
        }
        // Deepnest: vertices del NFP; subsample si es enorme.
        for (const auto& nfp : nfp_paths) {
            const size_t n = nfp.size();
            const size_t stride = n > 80 ? std::max<size_t>(1, n / 64) : 1;
            for (size_t i = 0; i < n; i += stride) {
                anclajes.emplace_back(nfp[i].x + bb.minx, nfp[i].y + bb.miny);
            }
        }
    }
}

void dedupe_anchors(std::vector<std::pair<double, double>>& anclajes) {
    if (anclajes.size() < 2) {
        return;
    }
    std::sort(anclajes.begin(), anclajes.end());
    anclajes.erase(
        std::unique(
            anclajes.begin(),
            anclajes.end(),
            [](const auto& a, const auto& b) {
                return std::abs(a.first - b.first) < 0.25 && std::abs(a.second - b.second) < 0.25;
            }),
        anclajes.end());
}

Bounds bounds_of_path(const PathD& path) {
    Bounds b;
    bool first = true;
    for (const auto& p : path) {
        if (first) {
            b.minx = b.maxx = p.x;
            b.miny = b.maxy = p.y;
            first = false;
        } else {
            b.minx = std::min(b.minx, p.x);
            b.maxx = std::max(b.maxx, p.x);
            b.miny = std::min(b.miny, p.y);
            b.maxy = std::max(b.maxy, p.y);
        }
    }
    return b;
}

void append_path_vertices_as_anchors(
    std::vector<std::pair<double, double>>& anclajes,
    const PathsD& paths,
    size_t max_per_path = 64) {
    for (const auto& path : paths) {
        const size_t n = path.size();
        if (n < 3) {
            continue;
        }
        const size_t stride = n > max_per_path ? std::max<size_t>(1, n / max_per_path) : 1;
        for (size_t i = 0; i < n; i += stride) {
            anclajes.emplace_back(path[i].x, path[i].y);
        }
    }
}

/** IFP rectangular exacto (Deepnest GeometryUtil.noFitPolygonRectangle). */
PathsD compute_rect_inner_nfp(const Bounds& container_bb, const Bounds& orb_bb) {
    const double ow = orb_bb.maxx - orb_bb.minx;
    const double oh = orb_bb.maxy - orb_bb.miny;
    const double cw = container_bb.maxx - container_bb.minx;
    const double ch = container_bb.maxy - container_bb.miny;
    if (ow <= 0.0 || oh <= 0.0 || ow > cw + 1e-6 || oh > ch + 1e-6) {
        return {};
    }
    PathD rect = {
        {container_bb.minx - orb_bb.minx, container_bb.miny - orb_bb.miny},
        {container_bb.maxx - orb_bb.maxx, container_bb.miny - orb_bb.miny},
        {container_bb.maxx - orb_bb.maxx, container_bb.maxy - orb_bb.maxy},
        {container_bb.minx - orb_bb.minx, container_bb.maxy - orb_bb.maxy},
    };
    return PathsD{std::move(rect)};
}

/**
 * Inner-NFP estilo Deepnest getInnerNfp:
 * frame expandido ⊕ (−B) − container ⊕ (−B), conservando componentes cuyo
 * centroide cae dentro del contenedor (referencia = bbox-min de B @ origen).
 */
PathsD compute_inner_nfp(const PathD& container, const PathD& orb_norm) {
    if (container.size() < 3 || orb_norm.size() < 3) {
        return {};
    }
    const Bounds cb = bounds_of_path(container);
    const double bw = std::max(1e-3, cb.maxx - cb.minx);
    const double bh = std::max(1e-3, cb.maxy - cb.miny);
    const PathD frame = {
        {cb.minx - 0.05 * bw, cb.miny - 0.05 * bh},
        {cb.maxx + 0.05 * bw, cb.miny - 0.05 * bh},
        {cb.maxx + 0.05 * bw, cb.maxy + 0.05 * bh},
        {cb.minx - 0.05 * bw, cb.maxy + 0.05 * bh},
    };

    const PathD inv = invert_path(orb_norm);
    PathsD nfp_frame = MinkowskiSum(inv, frame, true, 3);
    PathsD nfp_cont = MinkowskiSum(inv, container, true, 3);
    if (nfp_frame.empty()) {
        return {};
    }

    PathsD raw = nfp_cont.empty() ? nfp_frame : Difference(nfp_frame, nfp_cont, FillRule::NonZero);
    if (raw.empty() && !nfp_cont.empty()) {
        raw = Difference(nfp_frame, nfp_cont, FillRule::EvenOdd);
    }

    PathsD ifp;
    for (const auto& path : raw) {
        if (path.size() < 3 || std::abs(Area(path)) < 1.0) {
            continue;
        }
        double cx = 0.0;
        double cy = 0.0;
        for (const auto& p : path) {
            cx += p.x;
            cy += p.y;
        }
        cx /= static_cast<double>(path.size());
        cy /= static_cast<double>(path.size());
        const auto pip = PointInPolygon(PointD{cx, cy}, container);
        if (pip != PointInPolygonResult::IsOutside) {
            ifp.push_back(path);
        }
    }

    if (ifp.empty()) {
        // Fallback Clipper2: MinkowskiDiff a veces recupera IFP en perfiles cóncavos.
        PathsD md = MinkowskiDiff(orb_norm, container, true, 3);
        for (const auto& path : md) {
            if (path.size() < 3 || std::abs(Area(path)) < 1.0) {
                continue;
            }
            double cx = 0.0;
            double cy = 0.0;
            for (const auto& p : path) {
                cx += p.x;
                cy += p.y;
            }
            cx /= static_cast<double>(path.size());
            cy /= static_cast<double>(path.size());
            if (PointInPolygon(PointD{cx, cy}, container) != PointInPolygonResult::IsOutside) {
                ifp.push_back(path);
            }
        }
    }

    // Siempre añadir IFP AABB (exacto si el orificio/hoja es rectangular).
    PathsD rect_ifp = compute_rect_inner_nfp(cb, bounds_of_path(orb_norm));
    ifp.insert(ifp.end(), rect_ifp.begin(), rect_ifp.end());
    return ifp;
}

PathsD outer_nfp_world(
    const PathD& station_world,
    const PathD& orb_norm,
    NfpPairCache* cache) {
    if (station_world.size() < 3 || orb_norm.size() < 3) {
        return {};
    }
    const Bounds bb = bounds_of_path(station_world);
    PathD stat_norm;
    stat_norm.reserve(station_world.size());
    for (const auto& p : station_world) {
        stat_norm.emplace_back(p.x - bb.minx, p.y - bb.miny);
    }
    PathsD nfp_paths;
    if (cache != nullptr) {
        nfp_paths = cache->get_relative(stat_norm, orb_norm);
    } else {
        nfp_paths = MinkowskiSum(invert_path(orb_norm), stat_norm, true, 3);
    }
    for (auto& path : nfp_paths) {
        for (auto& p : path) {
            p.x += bb.minx;
            p.y += bb.miny;
        }
    }
    return nfp_paths;
}

/** Deepnest placeParts: finalNFP = binInnerNFP − ∪(outer NFP de piezas ya puestas). */
PathsD subtract_placed_outer_nfps(
    const PathsD& bin_ifp,
    const PlacementState& state,
    const PathD& orb_norm,
    NfpPairCache* cache) {
    if (bin_ifp.empty()) {
        return {};
    }
    if (state.fijas_buff_paths.empty()) {
        return bin_ifp;
    }

    PathsD obstacles;
    for (const auto& buff : state.fijas_buff_paths) {
        for (const auto& ring : buff) {
            if (ring.size() < 3) {
                continue;
            }
            PathsD nfp_w = outer_nfp_world(ring, orb_norm, cache);
            obstacles.insert(obstacles.end(), nfp_w.begin(), nfp_w.end());
        }
    }
    if (obstacles.empty()) {
        return bin_ifp;
    }

    PathsD united = Union(obstacles, FillRule::NonZero);
    PathsD final_nfp = Difference(bin_ifp, united, FillRule::NonZero);
    if (final_nfp.empty()) {
        final_nfp = Difference(bin_ifp, united, FillRule::EvenOdd);
    }

    PathsD cleaned;
    for (const auto& path : final_nfp) {
        if (path.size() >= 3 && std::abs(Area(path)) > 1.0) {
            cleaned.push_back(path);
        }
    }
    return cleaned.empty() ? bin_ifp : cleaned;
}

PathsD build_bin_inner_nfp(
    const LimitContext* hole_limit,
    const LimitContext& sheet_limit,
    double w_placa,
    double h_placa,
    double margin_px,
    const PathD& orb_norm) {
    const Bounds orb_bb = bounds_of_path(orb_norm);

    if (hole_limit && hole_limit->active && !hole_limit->eval_paths.empty()) {
        PathsD out;
        for (const auto& hp : hole_limit->eval_paths) {
            PathsD ifp = compute_inner_nfp(hp, orb_norm);
            out.insert(out.end(), ifp.begin(), ifp.end());
        }
        return out;
    }

    if (sheet_limit.active && !sheet_limit.eval_paths.empty()) {
        PathsD out;
        for (const auto& sp : sheet_limit.eval_paths) {
            PathsD ifp = compute_inner_nfp(sp, orb_norm);
            out.insert(out.end(), ifp.begin(), ifp.end());
        }
        if (!out.empty()) {
            return out;
        }
    }

    const Bounds plate{margin_px, margin_px, w_placa - margin_px, h_placa - margin_px};
    return compute_rect_inner_nfp(plate, orb_bb);
}

std::vector<int> build_rotation_angles(double step_deg) {
    std::vector<int> angles;
    const int step = std::max(1, static_cast<int>(std::round(step_deg)));
    for (int a = 0; a < 360; a += step) {
        angles.push_back(a);
    }
    if (angles.empty()) {
        angles.push_back(0);
    }
    return angles;
}

/** NestFab Tilt: ±N° alrededor de ortogonales (grano). Env ARGA_ULTRA_TILT_DEG. */
double resolve_tilt_deg() {
    const char* raw = std::getenv("ARGA_ULTRA_TILT_DEG");
    if (!raw || !*raw) {
        return 0.0;
    }
    try {
        const double v = std::stod(raw);
        if (!std::isfinite(v) || v <= 0.0) {
            return 0.0;
        }
        return std::min(15.0, v);
    } catch (...) {
        return 0.0;
    }
}

std::vector<int> build_rotation_angles_with_tilt(double step_deg, double tilt_deg) {
    auto angles = build_rotation_angles(step_deg);
    if (tilt_deg <= 0.05) {
        return angles;
    }
    const int t = std::max(1, static_cast<int>(std::round(tilt_deg)));
    std::vector<int> out = angles;
    // Tilt NestFab: solo sobre base ortogonal (0/90/180/270).
    for (int base : {0, 90, 180, 270}) {
        for (int s : {-t, t}) {
            int a = (base + s) % 360;
            if (a < 0) {
                a += 360;
            }
            out.push_back(a);
        }
    }
    std::sort(out.begin(), out.end());
    out.erase(std::unique(out.begin(), out.end()), out.end());
    return out;
}

/** ARGA taller: piezas grandes ortogonales (+tilt); chicas permiten Any fino (1°+). */
double effective_rotation_step_for_piece(
    double profile_step_deg,
    const std::vector<std::vector<Point2D>>& poly_src) {
    double step = std::max(1.0, std::min(profile_step_deg, 90.0));
    if (poly_src.empty()) {
        return step;
    }
    const double area = std::abs(polygon_area_ring(poly_src.front()));
    // ≥200 in² → 0/90/180/270 (estructural / anillos grandes)
    if (area >= kAreaEstructuralUmbralMm2) {
        return 90.0;
    }
    // 80–200 in² → pasos de 45° (menos inclinaciones raras)
    constexpr double kMidAreaMm2 = 80.0 * 645.16;
    if (area >= kMidAreaMm2) {
        return std::max(45.0, step);
    }
    // Piezas chicas: Any NestFab-like (hasta 1°)
    return step;
}

std::vector<Variation> build_variaciones_fine(
    const std::vector<std::vector<Point2D>>& poly_src,
    const std::vector<std::vector<Point2D>>& marks_src,
    double w_placa,
    double h_placa,
    double margin_px,
    double kerf_radio,
    double rotation_step_deg) {
    std::vector<Variation> variaciones;
    if (poly_src.empty()) {
        return variaciones;
    }

    const Point2D centroid = polygon_centroid(poly_src.front());
    const double eff_step = effective_rotation_step_for_piece(rotation_step_deg, poly_src);
    const double tilt = resolve_tilt_deg();
    const auto angles = build_rotation_angles_with_tilt(eff_step, tilt);

    for (const int angulo : angles) {
        auto poly_rot = poly_src;
        auto marks_rot = marks_src;
        if (angulo != 0) {
            rotate_rings(poly_rot, centroid.x, centroid.y, static_cast<double>(angulo));
            if (!marks_rot.empty()) {
                rotate_rings(marks_rot, centroid.x, centroid.y, static_cast<double>(angulo));
            }
        }

        auto poly_buff = buffer_rings(poly_rot, kerf_radio);
        if (poly_buff.empty()) {
            continue;
        }

        const Bounds bb = bounds_of_rings(poly_buff);
        const double w_p = bb.maxx - bb.minx;
        const double h_p = bb.maxy - bb.miny;
        if (w_p <= 0.0 || h_p <= 0.0) {
            continue;
        }
        if (w_p > w_placa - 2.0 * margin_px + 0.1 || h_p > h_placa - 2.0 * margin_px + 0.1) {
            continue;
        }

        Variation var;
        var.poly = poly_rot;
        var.poly_buff = poly_buff;
        var.marks = marks_rot;
        var.outer_norm = normalize_outer_at_origin(poly_buff);
        var.b_minx = bb.minx;
        var.b_miny = bb.miny;
        var.b_maxx = bb.maxx;
        var.b_maxy = bb.maxy;
        variaciones.push_back(std::move(var));
    }
    return variaciones;
}

LimitContext make_limit_context(const std::optional<std::vector<std::vector<Point2D>>>& limite_rings, double margin_px) {
    LimitContext ctx;
    if (!limite_rings || limite_rings->empty()) {
        return ctx;
    }
    auto paths = to_paths_d(*limite_rings);
    if (margin_px > 0.0) {
        paths = InflatePaths(paths, -margin_px, JoinType::Miter, EndType::Polygon);
    }
    if (!paths.empty()) {
        paths = InflatePaths(paths, 0.1, JoinType::Miter, EndType::Polygon);
    }
    if (paths.empty()) {
        return ctx;
    }
    ctx.active = true;
    ctx.eval_paths = paths;
    ctx.bounds = bounds_of_paths(paths);
    return ctx;
}

/** Orificio: shrink kerf COMPLETO (2·radio). Contorno NFP simplificado; kerf intacto. */
LimitContext make_hole_limit(const std::vector<Point2D>& hole_ring, double kerf_radio) {
    LimitContext ctx;
    if (hole_ring.size() < 3) {
        return ctx;
    }
    PathD hole = to_path_d(hole_ring);
    if (hole.size() > kNfpMaxHoleVerts) {
        hole = simplify_path_rdp(hole, kNfpSimplifyEpsMm, kNfpMaxHoleVerts);
    }
    PathsD paths{std::move(hole)};
    const double shrink = 2.0 * kerf_radio;
    if (shrink > 1e-9) {
        paths = InflatePaths(paths, -shrink, JoinType::Miter, EndType::Polygon);
    }
    if (paths.empty()) {
        return ctx;
    }
    ctx.active = true;
    ctx.eval_paths = paths;
    ctx.bounds = bounds_of_paths(paths);
    return ctx;
}

/** Canal abierto C/VFM / vacío: kerf completo + pieza exacta. */
LimitContext make_void_limit(const std::vector<std::vector<Point2D>>& rings, double kerf_radio) {
    LimitContext ctx;
    if (rings.empty() || rings[0].size() < 3) {
        return ctx;
    }
    PathsD paths = to_paths_d(rings);
    const double shrink = 2.0 * kerf_radio;
    if (shrink > 1e-9) {
        paths = InflatePaths(paths, -shrink, JoinType::Miter, EndType::Polygon);
    }
    if (paths.empty()) {
        return ctx;
    }
    ctx.active = true;
    ctx.eval_paths = paths;
    ctx.bounds = bounds_of_paths(paths);
    return ctx;
}

bool comprobar_colision(
    double pos_x,
    double pos_y,
    const Variation& var,
    const LimitContext& limit,
    const PlacementState& state,
    double kerf_radio) {
    const double cmx = pos_x + var.b_minx;
    const double cmy = pos_y + var.b_miny;
    const double cMx = pos_x + var.b_maxx;
    const double cMy = pos_y + var.b_maxy;
    const double kerf_full = 2.0 * kerf_radio;

    std::optional<PathsD> moved_exact;
    std::optional<PathsD> moved_buff;

    if (limit.active) {
        moved_exact = translate_copy(to_paths_d(var.poly), pos_x, pos_y);
        if (cmx < limit.bounds.minx - 0.5 || cmy < limit.bounds.miny - 0.5
            || cMx > limit.bounds.maxx + 0.5 || cMy > limit.bounds.maxy + 0.5) {
            return true;
        }
        if (!path_contained_in(*moved_exact, limit.eval_paths)) {
            return true;
        }
    }

    auto ensure_exact = [&]() -> const PathsD& {
        if (!moved_exact) {
            moved_exact = translate_copy(to_paths_d(var.poly), pos_x, pos_y);
        }
        return *moved_exact;
    };
    auto ensure_buff = [&]() -> const PathsD& {
        if (!moved_buff) {
            moved_buff = translate_copy(to_paths_d(var.poly_buff), pos_x, pos_y);
        }
        return *moved_buff;
    };

    for (size_t idx = 0; idx < state.fijas_bounds.size(); ++idx) {
        const auto& f_b = state.fijas_bounds[idx];
        const double pad = std::max(0.05, kerf_full + 1.0);
        if (cMx + pad <= f_b.minx || cmx - pad >= f_b.maxx || cMy + pad <= f_b.miny
            || cmy - pad >= f_b.maxy) {
            continue;
        }

        const bool marcada_host = limit.active && idx < state.fijas_es_anfitriona.size()
            && state.fijas_es_anfitriona[idx] && idx < state.fijas_solid_paths.size()
            && !state.fijas_solid_paths[idx].empty();
        bool es_host_cavity = false;
        if (marcada_host) {
            const double inset = std::max(0.0, kerf_radio) + 0.5;
            es_host_cavity = cmx >= f_b.minx + inset - 0.5 && cMx <= f_b.maxx - inset + 0.5
                && cmy >= f_b.miny + inset - 0.5 && cMy <= f_b.maxy - inset + 0.5;
        }
        if (es_host_cavity) {
            if (paths_intersect(ensure_exact(), state.fijas_solid_paths[idx])) {
                return true;
            }
            continue;
        }

        if (paths_intersect(ensure_buff(), state.fijas_buff_paths[idx])) {
            return true;
        }
    }
    return false;
}

void compact_slide_position(
    double& px,
    double& py,
    const Variation& var,
    double margin_px,
    const LimitContext& limit,
    const PlacementState& state,
    double kerf_radio) {
    auto try_slide = [&](double step_mm) {
        bool moved = true;
        while (moved) {
            moved = false;
            const double test_px = px - step_mm;
            if (test_px + var.b_minx >= margin_px) {
                if (!comprobar_colision(test_px, py, var, limit, state, kerf_radio)) {
                    px = test_px;
                    moved = true;
                }
            }
            const double test_py = py - step_mm;
            if (test_py + var.b_miny >= margin_px) {
                if (!comprobar_colision(px, test_py, var, limit, state, kerf_radio)) {
                    py = test_py;
                    moved = true;
                }
            }
        }
    };
    try_slide(kSlideStepCoarseMm);
    try_slide(kSlideStepFineMm);
}

double nfp_score_deepnest(
    double px,
    double py,
    const Variation& var,
    const PlacementState& state) {
    // Deepnest/SVGNest: minimizar width*2 + height del bbox conjunto (gravedad X).
    double minx = px + var.b_minx;
    double miny = py + var.b_miny;
    double maxx = px + var.b_maxx;
    double maxy = py + var.b_maxy;
    for (const auto& b : state.fijas_bounds) {
        minx = std::min(minx, b.minx);
        miny = std::min(miny, b.miny);
        maxx = std::max(maxx, b.maxx);
        maxy = std::max(maxy, b.maxy);
    }
    const double w = std::max(0.0, maxx - minx);
    const double h = std::max(0.0, maxy - miny);
    return (w * 2.0) + h + (py * 1e-3) + (px * 1e-6);
}

bool colocar_pieza_nfp(
    const PieceIn& p_data,
    PlacementState& state,
    double w_placa,
    double h_placa,
    double kerf_radio,
    double margin_px,
    const LimitContext& sheet_limit,
    const LimitContext* hole_limit,
    double rotation_step_deg,
    NfpPairCache* nfp_cache) {
    const auto variaciones = build_variaciones_fine(
        p_data.rings, p_data.marks, w_placa, h_placa, margin_px, kerf_radio, rotation_step_deg);
    if (variaciones.empty()) {
        return false;
    }

    const LimitContext& place_limit = hole_limit ? *hole_limit : sheet_limit;

    const Variation* mejor_var = nullptr;
    double mejor_px = 0.0;
    double mejor_py = 0.0;
    double mejor_score = std::numeric_limits<double>::infinity();

    std::optional<cuda::DenseMask> cuda_board;

    for (const auto& var : variaciones) {
        // Deepnest placeParts: Inner-NFP del bin/orificio − unión NFP de piezas fijadas.
        PathsD bin_ifp = build_bin_inner_nfp(
            hole_limit, sheet_limit, w_placa, h_placa, margin_px, var.outer_norm);
        PathsD final_nfp =
            subtract_placed_outer_nfps(bin_ifp, state, var.outer_norm, nfp_cache);

        std::vector<std::pair<double, double>> anclajes;
        append_path_vertices_as_anchors(anclajes, final_nfp);
        if (anclajes.empty()) {
            // Fallback heurístico si Clipper no devolvió IFP usable.
            if (hole_limit) {
                const auto& hb = hole_limit->bounds;
                anclajes.emplace_back(hb.minx, hb.miny);
                anclajes.emplace_back(hb.maxx, hb.miny);
                anclajes.emplace_back(hb.minx, hb.maxy);
                anclajes.emplace_back((hb.minx + hb.maxx) * 0.5, hb.miny);
                anclajes.emplace_back(hb.minx, (hb.miny + hb.maxy) * 0.5);
            } else {
                anclajes.emplace_back(margin_px, margin_px);
            }
            for (const auto& b : state.fijas_bounds) {
                anclajes.emplace_back(b.maxx + 1.0, b.miny);
                anclajes.emplace_back(b.minx, b.maxy + 1.0);
                anclajes.emplace_back(b.maxx + 1.0, (b.miny + b.maxy) * 0.5);
                anclajes.emplace_back((b.minx + b.maxx) * 0.5, b.maxy + 1.0);
            }
            for (size_t idx = 0; idx < state.fijas_buff_paths.size(); ++idx) {
                append_nfp_candidates(
                    anclajes, state.fijas_buff_paths[idx], var.outer_norm, nfp_cache);
            }
        }
        dedupe_anchors(anclajes);

        std::vector<std::pair<double, double>> cand_xy;
        cand_xy.reserve(anclajes.size());
        std::vector<std::pair<double, double>> cand_pxpy;
        cand_pxpy.reserve(anclajes.size());
        for (const auto& anclaje : anclajes) {
            double px = anclaje.first - var.b_minx;
            double py = anclaje.second - var.b_miny;

            if (px + var.b_minx < margin_px - 0.1 || py + var.b_miny < margin_px - 0.1
                || px + var.b_maxx > w_placa - margin_px + 0.1
                || py + var.b_maxy > h_placa - margin_px + 0.1) {
                continue;
            }
            cand_xy.emplace_back(px, py);
            cand_pxpy.emplace_back(px, py);
        }
        const auto rejected = [&]() -> std::vector<std::uint8_t> {
            if (!cuda::filter_worthwhile(cand_xy.size(), state.fijas_buff_paths.size())) {
                return {};
            }
            if (!cuda_board.has_value()) {
                cuda_board = cuda::rasterize_union_occupancy(
                    state.fijas_buff_paths, w_placa, h_placa, 8.0);
            }
            return cuda::filter_against_board(
                *cuda_board, to_paths_d(var.poly_buff), cand_xy, 8.0);
        }();

        for (std::size_t ci = 0; ci < cand_pxpy.size(); ++ci) {
            if (!rejected.empty() && rejected[ci] != 0) {
                continue;
            }
            double px = cand_pxpy[ci].first;
            double py = cand_pxpy[ci].second;

            if (comprobar_colision(px, py, var, place_limit, state, kerf_radio)) {
                continue;
            }

            compact_slide_position(px, py, var, margin_px, place_limit, state, kerf_radio);

            const double score = nfp_score_deepnest(px, py, var, state);
            if (score < mejor_score) {
                mejor_score = score;
                mejor_var = &var;
                mejor_px = px;
                mejor_py = py;
            }
        }
    }

    if (mejor_var == nullptr) {
        return false;
    }

    const auto cand_final = translate_rings_copy(mejor_var->poly, mejor_px, mejor_py);
    const auto cand_marks_final = translate_rings_copy(mejor_var->marks, mejor_px, mejor_py);
    const auto cand_buff_final = translate_rings_copy(mejor_var->poly_buff, mejor_px, mejor_py);

    state.fijas_buff_paths.push_back(to_paths_d(cand_buff_final));
    state.fijas_solid_paths.push_back(materialize_metal(cand_final));
    state.fijas_bounds.push_back(bounds_of_rings(cand_buff_final));
    state.fijas_es_anfitriona.push_back(pieza_es_anfitriona(p_data) ? 1 : 0);

    PieceOut placed;
    placed.nombre = p_data.nombre;
    placed.poligonos = cand_final;
    placed.marcas = cand_marks_final;
    placed.area = p_data.area;
    placed.calibre = p_data.calibre;
    placed.material = p_data.material;
    state.hoja.piezas.push_back(std::move(placed));
    state.hoja.area_usada += piece_area(p_data);
    return true;
}

void try_part_in_part(
    PlacementState& state,
    std::vector<PieceIn>& restos,
    double w_placa,
    double h_placa,
    double kerf_radio,
    double margin_px,
    const LimitContext& sheet_limit,
    double rotation_step_deg,
    NfpPairCache* nfp_cache) {
    std::vector<PieceIn> siguientes;
    for (auto& p : restos) {
        if (piece_area(p) > kPartInPartMaxAreaMm2) {
            siguientes.push_back(std::move(p));
            continue;
        }

        bool placed = false;
        for (const auto& host : state.hoja.piezas) {
            if (host.poligonos.size() < 2) {
                continue;
            }
            for (size_t hi = 1; hi < host.poligonos.size(); ++hi) {
                const Bounds hb = bounds_of_rings({host.poligonos[hi]});
                if (!pieza_cabe_en_hueco_aabb(p, hb, /*tol=*/2.0)) {
                    continue;
                }
                const LimitContext hole_limit = make_hole_limit(host.poligonos[hi], kerf_radio);
                if (!hole_limit.active) {
                    continue;
                }
                PieceIn trial = p;
                if (colocar_pieza_nfp(
                        trial,
                        state,
                        w_placa,
                        h_placa,
                        kerf_radio,
                        margin_px,
                        sheet_limit,
                        &hole_limit,
                        rotation_step_deg,
                        nfp_cache)) {
                    placed = true;
                    break;
                }
            }
            if (placed) {
                break;
            }
        }
        if (!placed) {
            siguientes.push_back(std::move(p));
        }
    }
    restos = std::move(siguientes);
}

std::vector<CavidadAbierta> listar_cavidades_abiertas_por_host(const PlacementState& state) {
    std::vector<CavidadAbierta> cavidades;
    const size_t n = std::min(state.hoja.piezas.size(), state.fijas_buff_paths.size());
    for (size_t i = 0; i < n; ++i) {
        const auto& placed = state.hoja.piezas[i];
        if (placed.poligonos.empty()) {
            continue;
        }
        if (i >= state.fijas_es_anfitriona.size() || !state.fijas_es_anfitriona[i]) {
            PieceIn probe;
            probe.nombre = placed.nombre;
            probe.area = placed.area;
            probe.rings = placed.poligonos;
            if (!pieza_es_anfitriona(probe)) {
                continue;
            }
        }

        const Bounds bb = bounds_of_rings(placed.poligonos);
        const double bw = bb.maxx - bb.minx;
        const double bh = bb.maxy - bb.miny;
        const double bbox_area = bw * bh;
        if (bbox_area < kVoidMinAreaMm2 * 4.0) {
            continue;
        }

        PathD aabb;
        aabb.emplace_back(bb.minx, bb.miny);
        aabb.emplace_back(bb.maxx, bb.miny);
        aabb.emplace_back(bb.maxx, bb.maxy);
        aabb.emplace_back(bb.minx, bb.maxy);

        PathsD host_solid = i < state.fijas_solid_paths.size() && !state.fijas_solid_paths[i].empty()
            ? state.fijas_solid_paths[i]
            : materialize_metal(placed.poligonos);
        if (host_solid.empty()) {
            continue;
        }
        PathsD free_in = Difference(PathsD{aabb}, host_solid, FillRule::NonZero);
        if (free_in.empty()) {
            free_in = Difference(PathsD{aabb}, host_solid, FillRule::EvenOdd);
        }
        const double area_mat = piece_area(
            PieceIn{placed.nombre, placed.area, placed.calibre, placed.material, placed.poligonos, placed.marcas});
        const bool open_profile = bbox_area > 0.0 && (area_mat / bbox_area) < 0.85;
        for (const auto& path : free_in) {
            const double a = std::abs(Area(path));
            if (a < 5.0 * 645.16) {
                continue;
            }
            const Bounds pb = bounds_of_paths({path});
            const double pw = pb.maxx - pb.minx;
            const double ph = pb.maxy - pb.miny;
            const bool reject_legacy =
                (a > bbox_area * 0.85) || (pw > bw * 0.92 && ph > bh * 0.92);
            if (reject_legacy) {
                // Perfil abierto VFM/C: aceptar bahías internas (tocan <=2 lados del AABB).
                constexpr double tol = 2.0;
                int sides = 0;
                if (std::abs(pb.minx - bb.minx) <= tol) ++sides;
                if (std::abs(pb.maxx - bb.maxx) <= tol) ++sides;
                if (std::abs(pb.miny - bb.miny) <= tol) ++sides;
                if (std::abs(pb.maxy - bb.maxy) <= tol) ++sides;
                if (!(open_profile && sides <= 2)) {
                    continue;
                }
            }
            auto rings = from_paths_d({path});
            if (!rings.empty()) {
                cavidades.push_back(CavidadAbierta{i, std::move(rings)});
            }
        }
    }
    return cavidades;
}

bool pieza_cabe_en_hueco_aabb(const PieceIn& p, const Bounds& hb, double tol = 1.0) {
    const Bounds pb = bounds_of_rings(p.rings);
    const double pw = pb.maxx - pb.minx;
    const double ph = pb.maxy - pb.miny;
    const double hw = hb.maxx - hb.minx;
    const double hh = hb.maxy - hb.miny;
    return (pw <= hw + tol && ph <= hh + tol) || (ph <= hw + tol && pw <= hh + tol);
}

/** Relleno NestFab+ARGA: canales abiertos C/VFM (AABB − metal) tras NFP/GA. */
void try_open_cavities(
    PlacementState& state,
    std::vector<PieceIn>& restos,
    double w_placa,
    double h_placa,
    double kerf_radio,
    double margin_px,
    const LimitContext& sheet_limit,
    double rotation_step_deg,
    NfpPairCache* nfp_cache) {
    std::vector<PieceIn> grandes;
    std::vector<PieceIn> pequenas;
    for (auto& p : restos) {
        if (pieza_es_anfitriona(p) && piece_area(p) >= kAreaEstructuralUmbralMm2) {
            grandes.push_back(std::move(p));
        } else {
            pequenas.push_back(std::move(p));
        }
    }
    if (pequenas.empty()) {
        restos = std::move(grandes);
        return;
    }
    std::sort(pequenas.begin(), pequenas.end(), [](const PieceIn& a, const PieceIn& b) {
        return piece_area(a) < piece_area(b);
    });

    for (int guard = 0; guard < kMaxGuardCavidad && !pequenas.empty(); ++guard) {
        auto cavs = listar_cavidades_abiertas_por_host(state);
        if (cavs.empty()) {
            break;
        }
        std::sort(cavs.begin(), cavs.end(), [](const CavidadAbierta& a, const CavidadAbierta& b) {
            const Bounds ba = bounds_of_rings(a.rings);
            const Bounds bb = bounds_of_rings(b.rings);
            const double la = std::max(ba.maxx - ba.minx, ba.maxy - ba.miny);
            const double lb = std::max(bb.maxx - bb.minx, bb.maxy - bb.miny);
            return la > lb;
        });

        bool progreso = false;
        for (const auto& cav : cavs) {
            const Bounds hb = bounds_of_rings(cav.rings);
            const LimitContext void_limit = make_void_limit(cav.rings, kerf_radio);
            if (!void_limit.active) {
                continue;
            }
            for (size_t pi = 0; pi < pequenas.size(); ++pi) {
                if (!pieza_cabe_en_hueco_aabb(pequenas[pi], hb, 1.0)) {
                    continue;
                }
                if (colocar_pieza_nfp(
                        pequenas[pi],
                        state,
                        w_placa,
                        h_placa,
                        kerf_radio,
                        margin_px,
                        sheet_limit,
                        &void_limit,
                        rotation_step_deg,
                        nfp_cache)) {
                    pequenas.erase(pequenas.begin() + static_cast<std::ptrdiff_t>(pi));
                    progreso = true;
                    break;
                }
            }
            if (progreso) {
                break;
            }
        }
        if (!progreso) {
            break;
        }
    }

    restos.clear();
    restos.reserve(grandes.size() + pequenas.size());
    for (auto& p : grandes) {
        restos.push_back(std::move(p));
    }
    for (auto& p : pequenas) {
        restos.push_back(std::move(p));
    }
}

std::pair<PlacementState, std::vector<PieceIn>> pack_with_order(
    const std::vector<PieceIn>& piezas,
    const std::vector<size_t>& order,
    double w_placa,
    double h_placa,
    double kerf_custom,
    double margin_custom,
    const std::optional<std::vector<std::vector<Point2D>>>& limite_rings,
    double rotation_step_deg,
    bool part_in_part,
    NfpPairCache& nfp_cache) {
    PlacementState state;
    std::vector<PieceIn> restos;

    const double kerf_radio = (kerf_custom * 25.4) / 2.0;
    const double margin_px = margin_custom > 0.0 ? (margin_custom * 25.4) : 0.0;
    const LimitContext sheet_limit = make_limit_context(limite_rings, margin_px);

    std::vector<size_t> hosts_ord;
    std::vector<size_t> peq_ord;
    hosts_ord.reserve(order.size());
    peq_ord.reserve(order.size());
    for (const size_t idx : order) {
        if (idx >= piezas.size()) {
            continue;
        }
        if (pieza_es_anfitriona(piezas[idx])) {
            hosts_ord.push_back(idx);
        } else {
            peq_ord.push_back(idx);
        }
    }
    // NestFab/Deepnest: grandes/anfitrionas primero; el GA ordena dentro de cada grupo.
    // Luego morfología ARGA rellena canales VFM antes del patio libre.
    for (const size_t idx : hosts_ord) {
        if (!colocar_pieza_nfp(
                piezas[idx],
                state,
                w_placa,
                h_placa,
                kerf_radio,
                margin_px,
                sheet_limit,
                nullptr,
                rotation_step_deg,
                &nfp_cache)) {
            restos.push_back(piezas[idx]);
        }
    }
    for (const size_t idx : peq_ord) {
        restos.push_back(piezas[idx]);
    }

    if (!restos.empty()) {
        if (part_in_part) {
            try_part_in_part(
                state,
                restos,
                w_placa,
                h_placa,
                kerf_radio,
                margin_px,
                sheet_limit,
                rotation_step_deg,
                &nfp_cache);
        }
        try_open_cavities(
            state,
            restos,
            w_placa,
            h_placa,
            kerf_radio,
            margin_px,
            sheet_limit,
            rotation_step_deg,
            &nfp_cache);
    }

    std::vector<PieceIn> patio;
    patio.swap(restos);
    for (auto& p : patio) {
        if (!colocar_pieza_nfp(
                p,
                state,
                w_placa,
                h_placa,
                kerf_radio,
                margin_px,
                sheet_limit,
                nullptr,
                rotation_step_deg,
                &nfp_cache)) {
            restos.push_back(std::move(p));
        }
    }

    const double denom = w_placa * h_placa;
    state.hoja.eficiencia = denom > 0.0 ? (state.hoja.area_usada / denom) * 100.0 : 0.0;
    return {state, restos};
}

double fitness_score(const SheetOut& hoja, const std::vector<PieceIn>& restos, size_t total_pieces) {
    const double placed = static_cast<double>(hoja.piezas.size());
    const double rest = static_cast<double>(restos.size());
    // Deepnest: 1) colocar todo 2) compactar (width*2+height). Menor bbox ⇒ mejor.
    double nest_w = 0.0;
    double nest_h = 0.0;
    if (!hoja.piezas.empty()) {
        Bounds bb{};
        bool first = true;
        for (const auto& p : hoja.piezas) {
            const Bounds pb = bounds_of_rings(p.poligonos);
            if (first) {
                bb = pb;
                first = false;
            } else {
                bb.minx = std::min(bb.minx, pb.minx);
                bb.miny = std::min(bb.miny, pb.miny);
                bb.maxx = std::max(bb.maxx, pb.maxx);
                bb.maxy = std::max(bb.maxy, pb.maxy);
            }
        }
        nest_w = std::max(0.0, bb.maxx - bb.minx);
        nest_h = std::max(0.0, bb.maxy - bb.miny);
    }
    const double compact = (nest_w * 2.0) + nest_h;
    return (placed * 1e12) + hoja.area_usada - (rest * 1e8) + (hoja.eficiencia * 1e4)
           - (static_cast<double>(total_pieces) - placed) * 1e10 - compact;
}

struct Individual {
    std::vector<size_t> order;
    double fitness = -std::numeric_limits<double>::infinity();
};

std::vector<size_t> random_permutation(size_t n, std::mt19937& rng) {
    std::vector<size_t> order(n);
    std::iota(order.begin(), order.end(), 0);
    std::shuffle(order.begin(), order.end(), rng);
    return order;
}

void order_crossover(const std::vector<size_t>& a, const std::vector<size_t>& b, std::vector<size_t>& child, std::mt19937& rng) {
    const size_t n = a.size();
    child.assign(n, 0);
    if (n < 2) {
        child = a;
        return;
    }
    size_t c1 = static_cast<size_t>(rng() % n);
    size_t c2 = static_cast<size_t>(rng() % n);
    if (c1 > c2) {
        std::swap(c1, c2);
    }
    std::vector<bool> used(n, false);
    for (size_t i = c1; i <= c2; ++i) {
        child[i] = a[i];
        used[a[i]] = true;
    }
    size_t pos = (c2 + 1) % n;
    for (size_t k = 0; k < n; ++k) {
        const size_t gene = b[(c2 + 1 + k) % n];
        if (!used[gene]) {
            child[pos] = gene;
            used[gene] = true;
            pos = (pos + 1) % n;
        }
    }
}

void mutate_swap(std::vector<size_t>& order, std::mt19937& rng, double rate) {
    if (order.size() < 2) {
        return;
    }
    std::uniform_real_distribution<double> dist(0.0, 1.0);
    if (dist(rng) > rate) {
        return;
    }
    const size_t i = static_cast<size_t>(rng() % order.size());
    size_t j = static_cast<size_t>(rng() % order.size());
    if (i != j) {
        std::swap(order[i], order[j]);
    }
}

bool es_mejor_pack(
    const SheetOut& hoja,
    const std::vector<PieceIn>& restos,
    const SheetOut& mejor,
    const std::vector<PieceIn>& mejor_restos) {
    const size_t n = hoja.piezas.size();
    const size_t n_best = mejor.piezas.size();
    if (n != n_best) {
        return n > n_best;
    }
    if (hoja.area_usada > mejor.area_usada + 1e-6) {
        return true;
    }
    if (std::abs(hoja.area_usada - mejor.area_usada) <= 1e-6) {
        return restos.size() < mejor_restos.size();
    }
    return false;
}

}  // namespace

PackResult empaquetar_una_hoja_svgnest_ultra(
    const std::vector<PieceIn>& piezas,
    double w_placa,
    double h_placa,
    double kerf_override,
    double margin_override,
    const std::string& /*opt_override*/,
    const std::string& /*corner_override*/,
    const std::optional<std::vector<std::vector<Point2D>>>& limite_rings,
    int ga_population,
    int ga_generations,
    double rotation_step_deg,
    bool part_in_part,
    std::uint32_t ga_seed,
    const std::vector<size_t>* seed_order) {
    PackResult out;
    out.restos = piezas;
    out.hoja.eficiencia = 0.0;

    const double margin_px = margin_override > 0.0 ? (margin_override * 25.4) : 0.0;
    const double w_util = w_placa - (2.0 * margin_px);
    const double h_util = h_placa - (2.0 * margin_px);
    if (w_util <= 0.0 || h_util <= 0.0 || piezas.empty()) {
        return out;
    }

    const size_t n = piezas.size();
    const int population = std::max(4, std::min(ga_population, 60));
    const int generations = std::max(1, std::min(ga_generations, 100));
    // NestFab Any: chicas pueden bajar a 1°; estructurales siguen en effective_rotation_step.
    const double rot_step = std::max(1.0, std::min(rotation_step_deg, 90.0));

    std::mt19937 rng(ga_seed != 0 ? ga_seed : static_cast<std::uint32_t>(std::random_device{}()));
    std::uniform_real_distribution<double> prob(0.0, 1.0);
    // Refinar desde un nest bueno: mutación un poco más alta para escapar mínimo local.
    const double mutation_rate = (seed_order && seed_order->size() == n) ? 0.22 : 0.15;

    auto order_is_valid = [&](const std::vector<size_t>& ord) -> bool {
        if (ord.size() != n) {
            return false;
        }
        std::vector<char> seen(n, 0);
        for (size_t idx : ord) {
            if (idx >= n || seen[idx]) {
                return false;
            }
            seen[idx] = 1;
        }
        return true;
    };

    std::vector<Individual> pop(static_cast<size_t>(population));
    const bool refine_from_best = seed_order && order_is_valid(*seed_order);
    if (refine_from_best) {
        // NestFab-like: élite = orden ganador; resto = mutaciones / mezcla con élite.
        pop.front().order = *seed_order;
        for (size_t i = 1; i < pop.size(); ++i) {
            pop[i].order = *seed_order;
            mutate_swap(pop[i].order, rng, mutation_rate);
            if (prob(rng) < 0.25) {
                auto rnd = random_permutation(n, rng);
                std::vector<size_t> child;
                order_crossover(pop.front().order, rnd, child, rng);
                pop[i].order = std::move(child);
            }
        }
    } else {
        for (auto& ind : pop) {
            ind.order = random_permutation(n, rng);
        }
        // Semilla NestFab/Deepnest: anfitrionas/grandes primero (como SVGNest default).
        {
            std::vector<size_t> seed(n);
            std::iota(seed.begin(), seed.end(), 0);
            std::stable_sort(seed.begin(), seed.end(), [&](size_t a, size_t b) {
                const bool ha = pieza_es_anfitriona(piezas[a]);
                const bool hb = pieza_es_anfitriona(piezas[b]);
                if (ha != hb) {
                    return ha && !hb;
                }
                return piece_area(piezas[a]) > piece_area(piezas[b]);
            });
            pop.front().order = std::move(seed);
        }
    }

    SheetOut mejor_hoja;
    std::vector<PieceIn> mejor_restos = piezas;
    std::vector<size_t> mejor_order;

    // Cache NFP entre evaluaciones del GA (misma idea que Deepnest nfpCache).
    NfpPairCache nfp_cache;
    const int intra_threads = resolve_intra_threads();

    auto evaluate_batch = [&](std::vector<Individual>& batch) {
        if (batch.empty()) {
            return;
        }
        const size_t m = batch.size();
        std::vector<SheetOut> hojas(m);
        std::vector<std::vector<PieceIn>> restos_batch(m);

        parallel_for_index(m, intra_threads, [&](size_t i) {
            auto [state, restos] = pack_with_order(
                piezas,
                batch[i].order,
                w_placa,
                h_placa,
                kerf_override,
                margin_override,
                limite_rings,
                rot_step,
                part_in_part,
                nfp_cache);
            batch[i].fitness = fitness_score(state.hoja, restos, n);
            hojas[i] = std::move(state.hoja);
            restos_batch[i] = std::move(restos);
        });

        // Reduce del mejor en serie (determinista con el mismo seed → mismos hijos).
        for (size_t i = 0; i < m; ++i) {
            if (es_mejor_pack(hojas[i], restos_batch[i], mejor_hoja, mejor_restos)) {
                mejor_hoja = std::move(hojas[i]);
                mejor_restos = std::move(restos_batch[i]);
                mejor_order = batch[i].order;
            }
        }
    };

    evaluate_batch(pop);

    for (int gen = 1; gen < generations; ++gen) {
        std::sort(pop.begin(), pop.end(), [](const Individual& a, const Individual& b) {
            return a.fitness > b.fitness;
        });

        std::vector<Individual> next_gen;
        next_gen.reserve(pop.size());
        next_gen.push_back(pop.front());

        std::vector<Individual> children;
        children.reserve(static_cast<size_t>(std::max(0, population - 1)));
        while (static_cast<int>(children.size()) + 1 < population) {
            const Individual& p1 = pop[static_cast<size_t>(rng() % (population / 2 + 1))];
            const Individual& p2 = pop[static_cast<size_t>(rng() % (population / 2 + 1))];
            Individual child;
            if (prob(rng) < 0.7) {
                order_crossover(p1.order, p2.order, child.order, rng);
            } else {
                child.order = p1.order;
            }
            mutate_swap(child.order, rng, mutation_rate);
            children.push_back(std::move(child));
        }

        evaluate_batch(children);
        for (auto& child : children) {
            next_gen.push_back(std::move(child));
        }
        pop = std::move(next_gen);
    }

    // El mejor ya salió de evaluate_batch; re-pack final duplicaba el costo NFP.
    if (mejor_order.empty() && refine_from_best) {
        mejor_order = *seed_order;
    }

    const double denom = w_placa * h_placa;
    mejor_hoja.eficiencia = denom > 0.0 ? (mejor_hoja.area_usada / denom) * 100.0 : 0.0;
    out.hoja = std::move(mejor_hoja);
    out.restos = std::move(mejor_restos);
    out.orden = std::move(mejor_order);
    return out;
}

}  // namespace arga
