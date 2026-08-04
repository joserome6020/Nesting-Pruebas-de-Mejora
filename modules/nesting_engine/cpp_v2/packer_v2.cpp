#include "packer_v2.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <limits>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <unordered_map>
#include <utility>

#include "cuda/raster_filter.hpp"
#include "clipper2/clipper.h"
#include "clipper2/clipper.minkowski.h"

namespace arga_v2 {
namespace {

using namespace Clipper2Lib;

constexpr double kPi = 3.14159265358979323846;
// La validación real mostró que 10 mm pierde candidatos válidos cerca de los
// bordes. 5 mm conserva la exactitud Clipper2/NFP y eleva la resolución BLF.
constexpr double kGridStepMm = 5.0;
constexpr double kOverlapEps = 1e-6;
constexpr double kCacheCoordinateScale = 1000.0;  // 0.001 mm
constexpr char kNfpCacheAlgorithmVersion[] = "nfp_outer_minkowski_v1";

struct Bounds {
    double minx = 0.0;
    double miny = 0.0;
    double maxx = 0.0;
    double maxy = 0.0;
};

PathD to_path_d(const std::vector<Point2D>& ring) {
    PathD out;
    out.reserve(ring.size());
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
        ring.reserve(path.size());
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

int64_t quantize_cache_value(double value) {
    return static_cast<int64_t>(std::llround(value * kCacheCoordinateScale));
}

double normalize_angle_deg(double angle_deg) {
    double angle = std::fmod(angle_deg, 360.0);
    if (angle < 0.0) {
        angle += 360.0;
    }
    return angle;
}

std::string canonical_ring_signature(const std::vector<Point2D>& ring) {
    std::vector<std::pair<int64_t, int64_t>> points;
    points.reserve(ring.size());
    for (const auto& point : ring) {
        points.emplace_back(quantize_cache_value(point.x), quantize_cache_value(point.y));
    }
    if (points.size() > 1 && points.front() == points.back()) {
        points.pop_back();
    }
    if (points.size() < 3) {
        return {};
    }

    auto encode = [&points](size_t start, bool reverse) {
        std::ostringstream out;
        const size_t n = points.size();
        for (size_t i = 0; i < n; ++i) {
            const size_t index = reverse ? (start + n - i) % n : (start + i) % n;
            out << points[index].first << ',' << points[index].second << ';';
        }
        return out.str();
    };

    std::string best;
    for (size_t start = 0; start < points.size(); ++start) {
        for (const bool reverse : {false, true}) {
            const std::string candidate = encode(start, reverse);
            if (best.empty() || candidate < best) {
                best = candidate;
            }
        }
    }
    return best;
}

struct NormalizedGeometry {
    std::vector<std::vector<Point2D>> rings;
    Point2D origin{};
    std::string signature;
};

NormalizedGeometry normalize_geometry_for_cache(
    const std::vector<std::vector<Point2D>>& source) {
    NormalizedGeometry normalized;
    if (source.empty() || source.front().size() < 3) {
        return normalized;
    }

    const Bounds bounds = bounds_of_rings(source);
    normalized.origin = {bounds.minx, bounds.miny};
    normalized.rings = source;
    for (auto& ring : normalized.rings) {
        for (auto& point : ring) {
            point.x -= normalized.origin.x;
            point.y -= normalized.origin.y;
        }
    }

    std::vector<std::string> hole_signatures;
    for (size_t i = 1; i < normalized.rings.size(); ++i) {
        const std::string signature = canonical_ring_signature(normalized.rings[i]);
        if (!signature.empty()) {
            hole_signatures.push_back(signature);
        }
    }
    std::sort(hole_signatures.begin(), hole_signatures.end());

    normalized.signature = "outer=" + canonical_ring_signature(normalized.rings.front());
    normalized.signature += "|holes=";
    for (const auto& hole : hole_signatures) {
        normalized.signature += '[';
        normalized.signature += hole;
        normalized.signature += ']';
    }
    return normalized;
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

double piece_area(const PieceIn& p) {
    if (p.area > 0.0) {
        return p.area;
    }
    double sum = 0.0;
    for (const auto& ring : p.rings) {
        sum += polygon_area_ring(ring);
    }
    return sum;
}

Point2D polygon_centroid(const std::vector<Point2D>& ring) {
    if (ring.empty()) {
        return {};
    }
    double cx = 0.0;
    double cy = 0.0;
    for (const auto& p : ring) {
        cx += p.x;
        cy += p.y;
    }
    const double n = static_cast<double>(ring.size());
    return {cx / n, cy / n};
}

void translate_rings(std::vector<std::vector<Point2D>>& rings, double dx, double dy) {
    for (auto& ring : rings) {
        for (auto& p : ring) {
            p.x += dx;
            p.y += dy;
        }
    }
}

void translate_paths(PathsD& paths, double dx, double dy) {
    for (auto& path : paths) {
        for (auto& p : path) {
            p.x += dx;
            p.y += dy;
        }
    }
}

std::vector<std::vector<Point2D>> translate_rings_copy(
    const std::vector<std::vector<Point2D>>& rings,
    double dx,
    double dy) {
    auto out = rings;
    translate_rings(out, dx, dy);
    return out;
}

PathsD translate_copy(const PathsD& src, double dx, double dy) {
    PathsD out = src;
    translate_paths(out, dx, dy);
    return out;
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

PathsD buffer_paths(const PathsD& subject, double delta) {
    if (subject.empty()) {
        return {};
    }
    PathsD cleaned = SimplifyPaths(subject, 0.1, true);
    if (cleaned.empty()) {
        cleaned = subject;
    }
    PathsD solution = InflatePaths(cleaned, delta, JoinType::Miter, EndType::Polygon);
    if (solution.empty()) {
        return {};
    }
    if (solution.size() > 1) {
        double best = -1.0;
        size_t best_i = 0;
        for (size_t i = 0; i < solution.size(); ++i) {
            const double a = std::abs(Area(solution[i]));
            if (a > best) {
                best = a;
                best_i = i;
            }
        }
        return {solution[best_i]};
    }
    return solution;
}

bool paths_intersect(const PathsD& a, const PathsD& b) {
    if (a.empty() || b.empty()) {
        return false;
    }
    const PathsD inter = Intersect(a, b, FillRule::NonZero);
    return !inter.empty() && std::abs(Area(inter)) > kOverlapEps;
}

bool path_contained_in(const PathsD& subject, const PathsD& container) {
    if (subject.empty() || container.empty()) {
        return false;
    }
    const PathsD diff = Difference(subject, container, FillRule::NonZero);
    return diff.empty() || std::abs(Area(diff)) < 1e-4;
}

PathD invert_path(const PathD& path) {
    PathD out;
    out.reserve(path.size());
    for (const auto& p : path) {
        out.emplace_back(-p.x, -p.y);
    }
    return out;
}

std::vector<std::vector<Point2D>> compute_nfp_outer_uncached(
    const std::vector<std::vector<Point2D>>& rings_a,
    const std::vector<std::vector<Point2D>>& rings_b) {
    if (rings_a.empty() || rings_b.empty() || rings_a.front().size() < 3
        || rings_b.front().size() < 3) {
        return {};
    }
    const PathD path_a = to_path_d(rings_a.front());
    const PathD path_b = to_path_d(rings_b.front());
    return from_paths_d(MinkowskiSum(invert_path(path_b), path_a, true, 3));
}

uint64_t fnv1a_hash(const std::string& value) {
    uint64_t hash = 1469598103934665603ULL;
    for (const unsigned char character : value) {
        hash ^= character;
        hash *= 1099511628211ULL;
    }
    return hash;
}

struct NfpCacheKey {
    uint64_t algorithm_hash = 0;
    uint64_t geometry_a_hash = 0;
    uint64_t geometry_b_hash = 0;
    int64_t angle_a = 0;
    int64_t angle_b = 0;
    int64_t kerf = 0;

    bool operator==(const NfpCacheKey& other) const {
        return algorithm_hash == other.algorithm_hash && geometry_a_hash == other.geometry_a_hash
            && geometry_b_hash == other.geometry_b_hash && angle_a == other.angle_a
            && angle_b == other.angle_b && kerf == other.kerf;
    }
};

struct NfpCacheKeyHash {
    std::size_t operator()(const NfpCacheKey& key) const {
        std::size_t seed = static_cast<std::size_t>(key.algorithm_hash);
        const auto combine = [&seed](uint64_t value) {
            seed ^= static_cast<std::size_t>(value) + 0x9e3779b9U + (seed << 6U) + (seed >> 2U);
        };
        combine(key.geometry_a_hash);
        combine(key.geometry_b_hash);
        combine(static_cast<uint64_t>(key.angle_a));
        combine(static_cast<uint64_t>(key.angle_b));
        combine(static_cast<uint64_t>(key.kerf));
        return seed;
    }
};

struct NfpCacheEntry {
    // Los signatures completos validan una posible colisión de hash.
    std::string geometry_a_signature;
    std::string geometry_b_signature;
    std::vector<std::vector<Point2D>> normalized_nfp;
};

struct NfpCacheStore {
    std::unordered_map<NfpCacheKey, std::vector<NfpCacheEntry>, NfpCacheKeyHash> entries;
    std::size_t entry_count = 0;
    std::size_t hits = 0;
    std::size_t misses = 0;
    std::size_t evictions = 0;
    std::size_t capacity = 4096;
    std::mutex mutex;
};

NfpCacheStore& nfp_cache_store() {
    static NfpCacheStore store;
    return store;
}

NfpCacheKey nfp_cache_key(
    const NormalizedGeometry& a,
    const NormalizedGeometry& b,
    double angle_a_deg,
    double angle_b_deg,
    double kerf_mm) {
    return {
        fnv1a_hash(kNfpCacheAlgorithmVersion),
        fnv1a_hash(a.signature),
        fnv1a_hash(b.signature),
        quantize_cache_value(normalize_angle_deg(angle_a_deg)),
        quantize_cache_value(normalize_angle_deg(angle_b_deg)),
        quantize_cache_value(kerf_mm),
    };
}

std::vector<std::vector<Point2D>> compute_nfp_normalized_cached(
    const NormalizedGeometry& normalized_a,
    const NormalizedGeometry& normalized_b,
    double angle_a_deg,
    double angle_b_deg,
    double kerf_mm) {
    const NfpCacheKey key =
        nfp_cache_key(normalized_a, normalized_b, angle_a_deg, angle_b_deg, kerf_mm);
    auto& store = nfp_cache_store();

    {
        std::lock_guard<std::mutex> lock(store.mutex);
        const auto bucket = store.entries.find(key);
        if (bucket != store.entries.end()) {
            for (const auto& entry : bucket->second) {
                if (entry.geometry_a_signature == normalized_a.signature
                    && entry.geometry_b_signature == normalized_b.signature) {
                    ++store.hits;
                    return entry.normalized_nfp;
                }
            }
        }
        ++store.misses;
    }

    NfpCacheEntry computed{
        normalized_a.signature,
        normalized_b.signature,
        compute_nfp_outer_uncached(normalized_a.rings, normalized_b.rings),
    };

    std::lock_guard<std::mutex> lock(store.mutex);
    const auto bucket = store.entries.find(key);
    if (bucket != store.entries.end()) {
        for (const auto& entry : bucket->second) {
            if (entry.geometry_a_signature == normalized_a.signature
                && entry.geometry_b_signature == normalized_b.signature) {
                // Otra hebra completó la misma clave durante el cálculo.
                return entry.normalized_nfp;
            }
        }
    }
    if (store.entry_count >= store.capacity) {
        store.evictions += store.entry_count;
        store.entries.clear();
        store.entry_count = 0;
    }
    auto& inserted = store.entries[key];
    inserted.push_back(std::move(computed));
    ++store.entry_count;
    return inserted.back().normalized_nfp;
}

bool point_on_segment(const Point2D& point, const Point2D& a, const Point2D& b) {
    const double dx = b.x - a.x;
    const double dy = b.y - a.y;
    const double cross = (point.x - a.x) * dy - (point.y - a.y) * dx;
    const double scale = std::max(1.0, std::abs(dx) + std::abs(dy));
    if (std::abs(cross) > 1e-7 * scale) {
        return false;
    }
    return point.x >= std::min(a.x, b.x) - 1e-7 && point.x <= std::max(a.x, b.x) + 1e-7
        && point.y >= std::min(a.y, b.y) - 1e-7 && point.y <= std::max(a.y, b.y) + 1e-7;
}

bool point_in_or_on_ring(const Point2D& point, const std::vector<Point2D>& ring) {
    if (ring.size() < 3) {
        return false;
    }
    bool inside = false;
    for (size_t i = 0, j = ring.size() - 1; i < ring.size(); j = i++) {
        const auto& a = ring[j];
        const auto& b = ring[i];
        if (point_on_segment(point, a, b)) {
            return true;
        }
        const bool straddles = (a.y > point.y) != (b.y > point.y);
        if (straddles) {
            const double x_cross = (b.x - a.x) * (point.y - a.y) / (b.y - a.y) + a.x;
            if (point.x < x_cross) {
                inside = !inside;
            }
        }
    }
    return inside;
}

struct Variation {
    std::vector<std::vector<Point2D>> poly;
    std::vector<std::vector<Point2D>> poly_buff;
    std::vector<std::vector<Point2D>> marks;
    PathsD buff_paths;
    NormalizedGeometry nfp_geometry;
    Bounds bb{};
    double w = 0.0;
    double h = 0.0;
    double angle_deg = 0.0;
};

struct LimitContext {
    bool active = false;
    Bounds bounds{};
    PathsD eval_paths;
};

struct PlacementState {
    SheetOut hoja;
    std::vector<PathsD> fijas_buff_paths;
    std::vector<Bounds> fijas_bounds;
    std::vector<NormalizedGeometry> fijas_nfp_geometry;
    std::vector<double> fijas_angles_deg;
    bool cuda_raster_enabled = false;
    int raster_w = 0;
    int raster_h = 0;
    std::vector<std::uint8_t> raster_fixed_inner;
    // Sesión opcional: evita re-subir la máscara fija en cada lote BLF.
    // Solo se crea con ARGA_CPP_V2_CUDA_RASTER=1; no es la ruta diaria.
    std::unique_ptr<cuda::RasterSession> raster_session;
};

struct RasterMask {
    int origin_x = 0;
    int origin_y = 0;
    int width = 0;
    int height = 0;
    std::vector<std::uint8_t> cells;
};

struct CandidateLocation {
    double x = 0.0;
    double y = 0.0;
    double dx = 0.0;
    double dy = 0.0;
    int step_x = 0;
    int step_y = 0;
};

bool cuda_raster_requested() {
    const char* value = std::getenv("ARGA_CPP_V2_CUDA_RASTER");
    if (!value) {
        return false;
    }
    const std::string text(value);
    return text == "1" || text == "true" || text == "TRUE" || text == "on" || text == "ON";
}

RasterMask rasterize_inner_paths(
    const PathsD& paths,
    int grid_w,
    int grid_h,
    double cell_size_mm) {
    RasterMask mask;
    if (paths.empty() || grid_w <= 0 || grid_h <= 0 || cell_size_mm <= 0.0) {
        return mask;
    }
    const Bounds bounds = bounds_of_paths(paths);
    const int first_x = std::max(0, static_cast<int>(std::floor(bounds.minx / cell_size_mm)));
    const int first_y = std::max(0, static_cast<int>(std::floor(bounds.miny / cell_size_mm)));
    const int last_x = std::min(grid_w, static_cast<int>(std::ceil(bounds.maxx / cell_size_mm)));
    const int last_y = std::min(grid_h, static_cast<int>(std::ceil(bounds.maxy / cell_size_mm)));
    if (last_x <= first_x || last_y <= first_y) {
        return mask;
    }
    mask.origin_x = first_x;
    mask.origin_y = first_y;
    mask.width = last_x - first_x;
    mask.height = last_y - first_y;
    mask.cells.assign(
        static_cast<std::size_t>(mask.width) * static_cast<std::size_t>(mask.height),
        0);

    for (int y = first_y; y < last_y; ++y) {
        for (int x = first_x; x < last_x; ++x) {
            const double left = static_cast<double>(x) * cell_size_mm;
            const double bottom = static_cast<double>(y) * cell_size_mm;
            const PathD cell{
                {left, bottom},
                {left + cell_size_mm, bottom},
                {left + cell_size_mm, bottom + cell_size_mm},
                {left, bottom + cell_size_mm},
            };
            if (path_contained_in(PathsD{cell}, paths)) {
                const auto index = static_cast<std::size_t>(y - first_y) * mask.width
                    + static_cast<std::size_t>(x - first_x);
                mask.cells[index] = 1;
            }
        }
    }
    return mask;
}

void add_inner_mask_to_fixed(const RasterMask& mask, PlacementState& state) {
    if (!state.cuda_raster_enabled || mask.cells.empty()) {
        return;
    }
    for (int y = 0; y < mask.height; ++y) {
        const int target_y = mask.origin_y + y;
        if (target_y < 0 || target_y >= state.raster_h) {
            continue;
        }
        for (int x = 0; x < mask.width; ++x) {
            if (mask.cells[static_cast<std::size_t>(y) * mask.width + x] == 0) {
                continue;
            }
            const int target_x = mask.origin_x + x;
            if (target_x >= 0 && target_x < state.raster_w) {
                state.raster_fixed_inner[
                    static_cast<std::size_t>(target_y) * state.raster_w + target_x] = 1;
            }
        }
    }
    if (state.raster_session) {
        state.raster_session->update_fixed(
            state.raster_fixed_inner, state.raster_w, state.raster_h);
    }
}

LimitContext make_limit_context(
    const std::optional<std::vector<std::vector<Point2D>>>& limite_rings,
    double margin_px) {
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
    ctx.eval_paths = std::move(paths);
    ctx.bounds = bounds_of_paths(ctx.eval_paths);
    return ctx;
}

std::vector<Variation> build_variations(
    const std::vector<std::vector<Point2D>>& poly_src,
    const std::vector<std::vector<Point2D>>& marks_src,
    double w_placa,
    double h_placa,
    double margin_px,
    double kerf_radio) {
    static const int rotations[] = {0, 90};
    std::vector<Variation> out;
    if (poly_src.empty() || poly_src.front().size() < 3) {
        return out;
    }

    const Point2D centroid = polygon_centroid(poly_src.front());

    for (const int angulo : rotations) {
        auto poly_rot = poly_src;
        auto marks_rot = marks_src;
        if (angulo != 0) {
            rotate_rings(poly_rot, centroid.x, centroid.y, static_cast<double>(angulo));
            if (!marks_rot.empty()) {
                rotate_rings(marks_rot, centroid.x, centroid.y, static_cast<double>(angulo));
            }
        }

        PathsD buff = buffer_paths(to_paths_d(poly_rot), kerf_radio);
        if (buff.empty()) {
            continue;
        }
        const Bounds bb = bounds_of_paths(buff);
        const double w_p = bb.maxx - bb.minx;
        const double h_p = bb.maxy - bb.miny;
        if (w_p <= 0.0 || h_p <= 0.0) {
            continue;
        }
        if (w_p > w_placa - 2.0 * margin_px + 0.1 || h_p > h_placa - 2.0 * margin_px + 0.1) {
            continue;
        }

        Variation var;
        var.poly = std::move(poly_rot);
        var.poly_buff = from_paths_d(buff);
        var.marks = std::move(marks_rot);
        var.buff_paths = std::move(buff);
        var.nfp_geometry = normalize_geometry_for_cache(var.poly_buff);
        var.bb = bb;
        var.w = w_p;
        var.h = h_p;
        var.angle_deg = static_cast<double>(angulo);
        out.push_back(std::move(var));
    }
    return out;
}

bool nfp_confirms_collision(
    const NormalizedGeometry& fixed,
    double fixed_angle_deg,
    const Variation& candidate,
    double dx,
    double dy,
    double kerf_mm) {
    if (fixed.signature.empty() || candidate.nfp_geometry.signature.empty()) {
        return false;
    }
    const auto normalized_nfp = compute_nfp_normalized_cached(
        fixed,
        candidate.nfp_geometry,
        fixed_angle_deg,
        candidate.angle_deg,
        kerf_mm);
    const Point2D candidate_translation_in_nfp_frame{
        dx - (fixed.origin.x - candidate.nfp_geometry.origin.x),
        dy - (fixed.origin.y - candidate.nfp_geometry.origin.y),
    };
    for (const auto& ring : normalized_nfp) {
        if (point_in_or_on_ring(candidate_translation_in_nfp_frame, ring)) {
            return true;
        }
    }
    return false;
}

bool collides(
    double dx,
    double dy,
    const Variation& var,
    const LimitContext& limit,
    const std::vector<Bounds>& fijas_bounds,
    const std::vector<PathsD>& fijas_buff_paths,
    const std::vector<NormalizedGeometry>& fijas_nfp_geometry,
    const std::vector<double>& fijas_angles_deg,
    double kerf_mm) {
    const double cmx = dx + var.bb.minx;
    const double cmy = dy + var.bb.miny;
    const double cMx = dx + var.bb.maxx;
    const double cMy = dy + var.bb.maxy;

    if (limit.active) {
        if (cmx < limit.bounds.minx || cmy < limit.bounds.miny || cMx > limit.bounds.maxx
            || cMy > limit.bounds.maxy) {
            return true;
        }
        const PathsD moved = translate_copy(var.buff_paths, dx, dy);
        if (!path_contained_in(moved, limit.eval_paths)) {
            return true;
        }
    }

    std::optional<PathsD> moved_buff;
    for (size_t idx = 0; idx < fijas_bounds.size(); ++idx) {
        const auto& f_b = fijas_bounds[idx];
        if (cMx <= f_b.minx + 0.05 || cmx >= f_b.maxx - 0.05 || cMy <= f_b.miny + 0.05
            || cmy >= f_b.maxy - 0.05) {
            continue;
        }
        // El NFP puede confirmar un choque sin booleana Clipper2. Un punto
        // fuera del NFP siempre pasa a la validación exacta siguiente.
        if (idx < fijas_nfp_geometry.size() && idx < fijas_angles_deg.size()
            && nfp_confirms_collision(
                fijas_nfp_geometry[idx],
                fijas_angles_deg[idx],
                var,
                dx,
                dy,
                kerf_mm)) {
            return true;
        }
        if (!moved_buff) {
            moved_buff = translate_copy(var.buff_paths, dx, dy);
        }
        if (paths_intersect(*moved_buff, fijas_buff_paths[idx])) {
            return true;
        }
    }
    return false;
}

bool try_place_piece(
    const PieceIn& piece,
    PlacementState& state,
    double w_placa,
    double h_placa,
    double kerf_radio,
    double margin_px,
    const LimitContext& limit) {
    const auto vars = build_variations(
        piece.rings, piece.marks, w_placa, h_placa, margin_px, kerf_radio);
    if (vars.empty()) {
        return false;
    }

    const Variation* best_var = nullptr;
    double best_dx = 0.0;
    double best_dy = 0.0;
    double best_score = std::numeric_limits<double>::infinity();

    for (const auto& var : vars) {
        const double x_max = w_placa - margin_px - var.w;
        const double y_max = h_placa - margin_px - var.h;
        if (x_max < margin_px - 1e-9 || y_max < margin_px - 1e-9) {
            continue;
        }

        // Ruta CPU estable: conserva exactamente el recorrido BLF previo.
        // La ruta batch se habilita solo con CUDA explícitamente solicitada.
        if (!state.cuda_raster_enabled) {
            bool placed_in_variation = false;
            for (double y = margin_px; y <= y_max + 1e-9 && !placed_in_variation;
                 y += kGridStepMm) {
                for (double x = margin_px; x <= x_max + 1e-9; x += kGridStepMm) {
                    const double dx = x - var.bb.minx;
                    const double dy = y - var.bb.miny;
                    if (dx + var.bb.minx < margin_px - 0.1
                        || dy + var.bb.miny < margin_px - 0.1
                        || dx + var.bb.maxx > w_placa - margin_px + 0.1
                        || dy + var.bb.maxy > h_placa - margin_px + 0.1) {
                        continue;
                    }
                    ++state.hoja.packer_timing.candidate_count;
                    const auto collision_started = std::chrono::steady_clock::now();
                    const bool collision = collides(
                        dx,
                        dy,
                        var,
                        limit,
                        state.fijas_bounds,
                        state.fijas_buff_paths,
                        state.fijas_nfp_geometry,
                        state.fijas_angles_deg,
                        kerf_radio * 2.0);
                    state.hoja.packer_timing.exact_collision_ms +=
                        std::chrono::duration<double, std::milli>(
                            std::chrono::steady_clock::now() - collision_started)
                            .count();
                    if (collision) {
                        continue;
                    }
                    const double score = (y * 1'000'000.0) + x;
                    if (score < best_score) {
                        best_score = score;
                        best_var = &var;
                        best_dx = dx;
                        best_dy = dy;
                    }
                    placed_in_variation = true;
                    break;
                }
            }
            continue;
        }

        std::vector<CandidateLocation> candidates;
        const auto candidate_generation_started = std::chrono::steady_clock::now();
        int step_y = 0;
        for (double y = margin_px; y <= y_max + 1e-9; y += kGridStepMm, ++step_y) {
            int step_x = 0;
            for (double x = margin_px; x <= x_max + 1e-9; x += kGridStepMm, ++step_x) {
                candidates.push_back(
                    {x, y, x - var.bb.minx, y - var.bb.miny, step_x, step_y});
            }
        }
        state.hoja.packer_timing.candidate_generation_ms +=
            std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - candidate_generation_started)
                .count();

        const auto accept_candidate = [&](const CandidateLocation& candidate) {
            const double dx = candidate.dx;
            const double dy = candidate.dy;
            if (dx + var.bb.minx < margin_px - 0.1
                || dy + var.bb.miny < margin_px - 0.1
                || dx + var.bb.maxx > w_placa - margin_px + 0.1
                || dy + var.bb.maxy > h_placa - margin_px + 0.1) {
                return false;
            }
            ++state.hoja.packer_timing.candidate_count;
            const auto collision_started = std::chrono::steady_clock::now();
            const bool collision = collides(
                dx,
                dy,
                var,
                limit,
                state.fijas_bounds,
                state.fijas_buff_paths,
                state.fijas_nfp_geometry,
                state.fijas_angles_deg,
                kerf_radio * 2.0);
            state.hoja.packer_timing.exact_collision_ms +=
                std::chrono::duration<double, std::milli>(
                    std::chrono::steady_clock::now() - collision_started)
                    .count();
            if (collision) {
                return false;
            }
            const double score = (candidate.y * 1'000'000.0) + candidate.x;
            if (score < best_score) {
                best_score = score;
                best_var = &var;
                best_dx = dx;
                best_dy = dy;
            }
            return true;
        };

        bool placed_in_variation = false;
        bool raster_evaluated = false;
        // Procesar en orden BLF y por lotes evita evaluar millones de
        // posiciones que jamás se consultan tras hallar la primera válida.
        if (state.cuda_raster_enabled && state.fijas_buff_paths.size() >= 4
            && candidates.size() >= 1024) {
            const double base_dx = margin_px - var.bb.minx;
            const double base_dy = margin_px - var.bb.miny;
            const auto rasterization_started = std::chrono::steady_clock::now();
            const RasterMask candidate_mask = rasterize_inner_paths(
                translate_copy(var.buff_paths, base_dx, base_dy),
                state.raster_w,
                state.raster_h,
                kGridStepMm);
            state.hoja.packer_timing.rasterization_ms +=
                std::chrono::duration<double, std::milli>(
                    std::chrono::steady_clock::now() - rasterization_started)
                    .count();
            if (!candidate_mask.cells.empty()) {
                constexpr std::size_t kCudaBatchSize = 8192;
                raster_evaluated = true;
                for (std::size_t start = 0;
                     start < candidates.size() && !placed_in_variation;
                     start += kCudaBatchSize) {
                    const std::size_t end = std::min(start + kCudaBatchSize, candidates.size());
                    std::vector<cuda::RasterOffset> offsets;
                    offsets.reserve(end - start);
                    for (std::size_t index = start; index < end; ++index) {
                        const auto& candidate = candidates[index];
                        offsets.push_back(
                            {candidate_mask.origin_x + candidate.step_x,
                             candidate_mask.origin_y + candidate.step_y});
                    }
                    cuda::RasterFilterStats raster_stats;
                    const auto safe_rejected = state.raster_session
                        ? state.raster_session->safe_reject_batch(
                            candidate_mask.cells,
                            candidate_mask.width,
                            candidate_mask.height,
                            offsets,
                            &raster_stats)
                        : cuda::safe_reject_batch(
                            state.raster_fixed_inner,
                            state.raster_w,
                            state.raster_h,
                            candidate_mask.cells,
                            candidate_mask.width,
                            candidate_mask.height,
                            offsets,
                            &raster_stats);
                    auto& metrics = state.hoja.cuda_raster;
                    metrics.candidates_evaluated += raster_stats.candidates_evaluated;
                    metrics.safe_rejected += raster_stats.safe_rejected;
                    metrics.h2d_bytes += raster_stats.h2d_bytes;
                    metrics.d2h_bytes += raster_stats.d2h_bytes;
                    metrics.h2d_ms += raster_stats.h2d_ms;
                    metrics.kernel_ms += raster_stats.kernel_ms;
                    metrics.d2h_ms += raster_stats.d2h_ms;
                    metrics.cuda_used = metrics.cuda_used || raster_stats.cuda_used;

                    for (std::size_t index = start; index < end; ++index) {
                        if (safe_rejected[index - start] == 0
                            && accept_candidate(candidates[index])) {
                            placed_in_variation = true;
                            break;
                        }
                    }
                }
            }
        }

        if (raster_evaluated) {
            continue;
        }
        for (const auto& candidate : candidates) {
            if (accept_candidate(candidate)) {
                break;  // Primera superviviente exacta para esta rotación.
            }
        }
    }

    if (best_var == nullptr) {
        return false;
    }

    const auto placed_poly = translate_rings_copy(best_var->poly, best_dx, best_dy);
    const auto placed_marks = translate_rings_copy(best_var->marks, best_dx, best_dy);
    const auto placed_buff = translate_copy(best_var->buff_paths, best_dx, best_dy);

    state.fijas_buff_paths.push_back(placed_buff);
    state.fijas_bounds.push_back(bounds_of_paths(placed_buff));
    state.fijas_nfp_geometry.push_back(
        normalize_geometry_for_cache(from_paths_d(placed_buff)));
    state.fijas_angles_deg.push_back(best_var->angle_deg);
    const auto rasterization_started = std::chrono::steady_clock::now();
    add_inner_mask_to_fixed(
        rasterize_inner_paths(
            placed_buff,
            state.raster_w,
            state.raster_h,
            kGridStepMm),
        state);
    state.hoja.packer_timing.rasterization_ms +=
        std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - rasterization_started)
            .count();

    PieceOut out;
    out.nombre = piece.nombre;
    out.poligonos = placed_poly;
    out.marcas = placed_marks;
    out.area = piece.area;
    out.calibre = piece.calibre;
    out.material = piece.material;
    state.hoja.piezas.push_back(std::move(out));
    state.hoja.area_usada += piece_area(piece);
    return true;
}

}  // namespace

std::vector<std::vector<Point2D>> echo_rings(
    const std::vector<std::vector<Point2D>>& rings) {
    return rings;
}

bool polygons_overlap(
    const std::vector<std::vector<Point2D>>& rings_a,
    const std::vector<std::vector<Point2D>>& rings_b,
    double epsilon) {
    const PathsD a = to_paths_d(rings_a);
    const PathsD b = to_paths_d(rings_b);
    if (a.empty() || b.empty()) {
        return false;
    }
    const PathsD inter = Intersect(a, b, FillRule::NonZero);
    return !inter.empty() && std::abs(Area(inter)) > epsilon;
}

std::vector<std::vector<Point2D>> compute_nfp_outer(
    const std::vector<std::vector<Point2D>>& rings_a,
    const std::vector<std::vector<Point2D>>& rings_b) {
    return compute_nfp_outer_uncached(rings_a, rings_b);
}

std::vector<std::vector<Point2D>> compute_nfp_outer_cached(
    const std::vector<std::vector<Point2D>>& rings_a,
    const std::vector<std::vector<Point2D>>& rings_b,
    double angle_a_deg,
    double angle_b_deg,
    double kerf_mm) {
    const NormalizedGeometry normalized_a = normalize_geometry_for_cache(rings_a);
    const NormalizedGeometry normalized_b = normalize_geometry_for_cache(rings_b);
    if (normalized_a.signature.empty() || normalized_b.signature.empty()) {
        return {};
    }

    const auto normalized_nfp = compute_nfp_normalized_cached(
        normalized_a, normalized_b, angle_a_deg, angle_b_deg, kerf_mm);
    return translate_rings_copy(
        normalized_nfp,
        normalized_a.origin.x - normalized_b.origin.x,
        normalized_a.origin.y - normalized_b.origin.y);
}

NfpCacheStats nfp_cache_stats() {
    auto& store = nfp_cache_store();
    std::lock_guard<std::mutex> lock(store.mutex);
    return {
        store.hits,
        store.misses,
        store.evictions,
        store.entry_count,
        store.capacity,
    };
}

void reset_nfp_cache() {
    auto& store = nfp_cache_store();
    std::lock_guard<std::mutex> lock(store.mutex);
    store.entries.clear();
    store.entry_count = 0;
    store.hits = 0;
    store.misses = 0;
    store.evictions = 0;
}

void set_nfp_cache_capacity(std::size_t capacity) {
    auto& store = nfp_cache_store();
    std::lock_guard<std::mutex> lock(store.mutex);
    store.capacity = std::max<std::size_t>(1, capacity);
    if (store.entry_count > store.capacity) {
        store.evictions += store.entry_count;
        store.entries.clear();
        store.entry_count = 0;
    }
}

NfpCacheWorkloadResult run_nfp_cache_workload(
    const std::vector<PieceIn>& piezas,
    std::size_t iterations,
    double kerf_mm) {
    NfpCacheWorkloadResult result;
    if (piezas.size() < 2 || iterations == 0) {
        result.cache = nfp_cache_stats();
        return result;
    }

    const auto preparation_started = std::chrono::steady_clock::now();
    std::vector<NormalizedGeometry> geometries;
    geometries.reserve(piezas.size());
    for (const auto& piece : piezas) {
        geometries.push_back(normalize_geometry_for_cache(piece.rings));
    }
    result.preparation_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - preparation_started)
                                .count();

    const auto lookup_started = std::chrono::steady_clock::now();
    for (std::size_t iteration = 0; iteration < iterations; ++iteration) {
        for (std::size_t a = 0; a < geometries.size(); ++a) {
            if (geometries[a].signature.empty()) {
                continue;
            }
            for (std::size_t b = 0; b < geometries.size(); ++b) {
                if (a == b || geometries[b].signature.empty()) {
                    continue;
                }
                (void)compute_nfp_normalized_cached(
                    geometries[a], geometries[b], 0.0, 0.0, kerf_mm);
                ++result.calls;
            }
        }
    }
    result.lookup_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - lookup_started)
                           .count();
    result.cache = nfp_cache_stats();
    return result;
}

PackResult empaquetar_una_hoja_poc(
    const std::vector<PieceIn>& piezas,
    double w_placa,
    double h_placa,
    double kerf_override,
    double margin_override,
    const std::string& /*opt_override*/,
    const std::string& /*corner_override*/,
    const std::optional<std::vector<std::vector<Point2D>>>& limite_rings) {
    PackResult result;
    if (w_placa <= 0.0 || h_placa <= 0.0 || piezas.empty()) {
        result.restos = piezas;
        return result;
    }

    std::vector<PieceIn> ordenadas = piezas;
    std::stable_sort(
        ordenadas.begin(),
        ordenadas.end(),
        [](const PieceIn& a, const PieceIn& b) {
            return piece_area(a) > piece_area(b);
        });

    // Misma conversión que producción (kerf/margin en pulgadas → mm).
    const double kerf_radio = (kerf_override * 25.4) / 2.0;
    const double margin_px = margin_override > 0.0 ? (margin_override * 25.4) : 0.0;
    const LimitContext limit = make_limit_context(limite_rings, margin_px);

    PlacementState state;
    if (cuda_raster_requested() && cuda::available()) {
        state.cuda_raster_enabled = true;
        state.hoja.cuda_raster.enabled = true;
        state.raster_w = std::max(1, static_cast<int>(std::ceil(w_placa / kGridStepMm)));
        state.raster_h = std::max(1, static_cast<int>(std::ceil(h_placa / kGridStepMm)));
        state.raster_fixed_inner.assign(
            static_cast<std::size_t>(state.raster_w) * static_cast<std::size_t>(state.raster_h),
            0);
        state.raster_session = std::make_unique<cuda::RasterSession>(
            state.raster_fixed_inner,
            state.raster_w,
            state.raster_h,
            true);
        if (!state.raster_session->cuda_active()) {
            // Fallback al filtro stateless/CPU si la sesión no pudo abrirse.
            state.raster_session.reset();
        }
    }
    for (const auto& p : ordenadas) {
        if (!try_place_piece(p, state, w_placa, h_placa, kerf_radio, margin_px, limit)) {
            result.restos.push_back(p);
        }
    }

    const double denom = w_placa * h_placa;
    state.hoja.eficiencia = denom > 0.0 ? (state.hoja.area_usada / denom) * 100.0 : 0.0;
    result.hoja = std::move(state.hoja);
    return result;
}

}  // namespace arga_v2
