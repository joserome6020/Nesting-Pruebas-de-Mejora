#include "packer_lab.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <memory>
#include <unordered_map>

#include "clipper2/clipper.h"

#if defined(ARGA_LAB_PILOT)
#include "cuda/lab_grid_filter.hpp"
#endif

namespace arga {
namespace {

using namespace Clipper2Lib;

constexpr double kScale = 1000.0;
constexpr double kPi = 3.14159265358979323846;
#if defined(ARGA_LAB_PILOT)
constexpr std::size_t kCudaMinOffsets = 2048;
#endif

struct Bounds {
    double minx = 0.0;
    double miny = 0.0;
    double maxx = 0.0;
    double maxy = 0.0;
};

struct RasterGrid {
    double resolution;
    int w, h;
    int stride; // number of uint64_t words per row
    std::vector<uint64_t> bits;

    RasterGrid() : resolution(4.0), w(0), h(0), stride(0) {}
    RasterGrid(double w_mm, double h_mm, double res_mm) {
        resolution = res_mm;
        w = static_cast<int>(std::ceil(w_mm / resolution)) + 1;
        h = static_cast<int>(std::ceil(h_mm / resolution)) + 1;
        stride = (w + 63) / 64;
        bits.assign(h * stride, 0);
    }

    void set(int x, int y) {
        if (x < 0 || x >= w || y < 0 || y >= h) return;
        bits[y * stride + (x / 64)] |= (1ULL << (x % 64));
    }

    bool get(int x, int y) const {
        if (x < 0 || x >= w || y < 0 || y >= h) return false;
        return (bits[y * stride + (x / 64)] & (1ULL << (x % 64))) != 0;
    }
};

struct Variation {
    std::vector<std::vector<Point2D>> poly;
    std::vector<std::vector<Point2D>> poly_buff;
    std::vector<std::vector<Point2D>> marks;
    RasterGrid grid;
    RasterGrid grid_buff;
    double w = 0.0;
    double h = 0.0;
    double b_minx = 0.0;
    double b_miny = 0.0;
    double b_maxx = 0.0;
    double b_maxy = 0.0;
    int rot_deg = 0;
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
    out.reserve(rings.size());
    for (const auto& ring : rings) {
        if (ring.size() >= 3) {
            out.push_back(to_path_d(ring));
        }
    }
    return out;
}

std::vector<std::vector<Point2D>> from_paths_d(const PathsD& paths) {
    std::vector<std::vector<Point2D>> out;
    out.reserve(paths.size());
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

double polygon_area(const std::vector<Point2D>& ring) {
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
    double area = polygon_area(rings.front());
    for (size_t i = 1; i < rings.size(); ++i) {
        area -= polygon_area(rings[i]);
    }
    return std::max(0.0, area);
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
        Bounds b = bounds_of_rings({ring});
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

void rotate_paths(PathsD& paths, double cx, double cy, double angle_deg) {
    const double rad = angle_deg * kPi / 180.0;
    const double cos_a = std::cos(rad);
    const double sin_a = std::sin(rad);
    for (auto& path : paths) {
        for (auto& p : path) {
            const double dx = p.x - cx;
            const double dy = p.y - cy;
            p.x = cx + (dx * cos_a) - (dy * sin_a);
            p.y = cy + (dx * sin_a) + (dy * cos_a);
        }
    }
}

void rotate_rings(std::vector<std::vector<Point2D>>& rings, double cx, double cy, double angle_deg) {
    auto paths = to_paths_d(rings);
    rotate_paths(paths, cx, cy, angle_deg);
    rings = from_paths_d(paths);
}

double rectangularidad(const std::vector<std::vector<Point2D>>& rings) {
    try {
        const Bounds b = bounds_of_rings(rings);
        const double w = b.maxx - b.minx;
        const double h = b.maxy - b.miny;
        if (w <= 0.0 || h <= 0.0) {
            return 0.0;
        }
        const double bbox_a = w * h;
        const double a = total_area(rings);
        if (a <= 1e-6) {
            return 0.0;
        }
        return bbox_a / a;
    } catch (...) {
        return 0.0;
    }
}

int clasificar_pieza(const std::vector<std::vector<Point2D>>& rings, double area_val) {
    const double a = std::max(0.0, area_val);
    const double r = rectangularidad(rings);
    if (a >= kAreaEstructuralUmbralMm2) {
        return 0;
    }
    if (r >= 0.57) {
        return 1;
    }
    if (r < 0.52) {
        return 3;
    }
    return 2;
}

std::string categoria_pieza_label(int clase) {
    switch (clase) {
        case 0:
            return "estructural";
        case 1:
            return "rectangular";
        case 3:
            return "compleja";
        default:
            return "mixta";
    }
}

std::tuple<int, double, std::string> sort_key_pool(const PieceIn& p) {
    const double area = p.area > 0.0 ? p.area : total_area(p.rings);
    const int clase = clasificar_pieza(p.rings, area);
    return {clase, -area, p.nombre};
}

PathsD buffer_paths(const PathsD& subject, double delta, JoinType join = JoinType::Miter) {
    if (subject.empty()) {
        return {};
    }
    PathsD cleaned = SimplifyPaths(subject, 0.1, true);
    if (cleaned.empty()) {
        cleaned = subject;
    }
    cleaned = InflatePaths(cleaned, 0.01, JoinType::Miter, EndType::Polygon);
    if (cleaned.empty()) {
        cleaned = subject;
    }
    PathsD solution = InflatePaths(cleaned, delta, join, EndType::Polygon);
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

std::vector<std::vector<Point2D>> buffer_rings(const std::vector<std::vector<Point2D>>& rings, double delta) {
    auto buff = buffer_paths(to_paths_d(rings), delta, JoinType::Miter);
    return from_paths_d(buff);
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


RasterGrid rasterize_paths(const PathsD& paths_in, double w_mm, double h_mm, double res_mm) {
    RasterGrid grid(w_mm, h_mm, res_mm);
    if (paths_in.empty()) return grid;

    Paths64 paths64;
    paths64.reserve(paths_in.size());
    for (const auto& path : paths_in) {
        Path64 p64;
        p64.reserve(path.size());
        for (const auto& pt : path) {
            p64.emplace_back(std::round(pt.x * kScale), std::round(pt.y * kScale));
        }
        paths64.push_back(std::move(p64));
    }

    for (int y = 0; y < grid.h; ++y) {
        for (int x = 0; x < grid.w; ++x) {
            double px = x * res_mm + res_mm * 0.5;
            double py = y * res_mm + res_mm * 0.5;
            Point64 pt(std::round(px * kScale), std::round(py * kScale));
            
            int wind_cnt = 0;
            for (const auto& p64 : paths64) {
                if (PointInPolygon(pt, p64) != PointInPolygonResult::IsOutside) {
                    wind_cnt++;
                }
            }
            if (wind_cnt % 2 != 0) {
                grid.set(x, y);
            }
        }
    }
    return grid;
}

bool grid_collide(const RasterGrid& board, const RasterGrid& piece, int board_x, int board_y) {
    if (board_x < 0 || board_y < 0 || board_x + piece.w > board.w || board_y + piece.h > board.h) {
        return true; 
    }
    int shift = board_x % 64;
    int word_offset = board_x / 64;
    
    for (int y = 0; y < piece.h; ++y) {
        const uint64_t* p_row = &piece.bits[y * piece.stride];
        const uint64_t* b_row = &board.bits[(board_y + y) * board.stride];
        
        uint64_t carry = 0;
        for (int x_w = 0; x_w < piece.stride; ++x_w) {
            uint64_t p_word = p_row[x_w];
            uint64_t aligned_word = (p_word << shift) | carry;
            carry = (shift == 0) ? 0 : (p_word >> (64 - shift));
            
            if (b_row[word_offset + x_w] & aligned_word) {
                return true;
            }
        }
        if (carry > 0 && (word_offset + piece.stride < board.stride)) {
            if (b_row[word_offset + piece.stride] & carry) {
                return true;
            }
        }
    }
    return false;
}

#if defined(ARGA_LAB_PILOT)
std::vector<std::uint8_t> raster_to_dense(const RasterGrid& grid) {
    std::vector<std::uint8_t> dense(
        static_cast<std::size_t>(std::max(0, grid.w)) * static_cast<std::size_t>(std::max(0, grid.h)),
        0);
    if (dense.empty()) {
        return dense;
    }
    for (int y = 0; y < grid.h; ++y) {
        for (int x = 0; x < grid.w; ++x) {
            if (grid.get(x, y)) {
                dense[static_cast<std::size_t>(y) * grid.w + static_cast<std::size_t>(x)] = 1;
            }
        }
    }
    return dense;
}

void accumulate_cuda_stats(LabCudaMetrics& metrics, const lab_cuda::GridFilterStats& stats) {
    metrics.enabled = true;
    metrics.cuda_used = metrics.cuda_used || stats.cuda_used;
    metrics.candidates_evaluated += stats.candidates_evaluated;
    metrics.collisions += stats.collisions;
    metrics.h2d_bytes += stats.h2d_bytes;
    metrics.d2h_bytes += stats.d2h_bytes;
    metrics.h2d_ms += stats.h2d_ms;
    metrics.kernel_ms += stats.kernel_ms;
    metrics.d2h_ms += stats.d2h_ms;
}
#endif

void draw_piece_on_board(RasterGrid& board, const RasterGrid& piece, int board_x, int board_y) {
    if (board_x < 0 || board_y < 0 || board_x + piece.w > board.w || board_y + piece.h > board.h) {
        return; 
    }
    int shift = board_x % 64;
    int word_offset = board_x / 64;
    
    for (int y = 0; y < piece.h; ++y) {
        const uint64_t* p_row = &piece.bits[y * piece.stride];
        uint64_t* b_row = &board.bits[(board_y + y) * board.stride];
        
        uint64_t carry = 0;
        for (int x_w = 0; x_w < piece.stride; ++x_w) {
            uint64_t p_word = p_row[x_w];
            uint64_t aligned_word = (p_word << shift) | carry;
            carry = (shift == 0) ? 0 : (p_word >> (64 - shift));
            
            b_row[word_offset + x_w] |= aligned_word;
        }
        if (carry > 0 && (word_offset + piece.stride < board.stride)) {
            b_row[word_offset + piece.stride] |= carry;
        }
    }
}

std::vector<Variation> build_variaciones(
    const std::vector<std::vector<Point2D>>& poly_src,
    const std::vector<std::vector<Point2D>>& marks_src,
    double w_placa,
    double h_placa,
    double margin_px,
    double kerf_radio) {
    static const int rotations[] = {0, 90, 180, 270};
    std::vector<Variation> variaciones;
    if (poly_src.empty()) {
        return variaciones;
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

        Bounds b = bounds_of_rings(poly_rot);
        const double w_p = b.maxx - b.minx;
        const double h_p = b.maxy - b.miny;
        translate_rings(poly_rot, -b.minx, -b.miny);
        if (!marks_rot.empty()) {
            translate_rings(marks_rot, -b.minx, -b.miny);
        }

        if (w_p > (w_placa - (2.0 * margin_px) + 5.0) || h_p > (h_placa - (2.0 * margin_px) + 5.0)) {
            continue;
        }

        std::vector<std::vector<Point2D>> poly_buff;
        try {
            poly_buff = buffer_rings(poly_rot, kerf_radio);
            if (poly_buff.empty()) {
                poly_buff = buffer_rings({poly_rot.front()}, kerf_radio);
            }
        } catch (...) {
            poly_buff = buffer_rings({poly_rot.front()}, kerf_radio);
        }

        const Bounds bb = bounds_of_rings(poly_buff);
        Variation var;
        var.poly = poly_rot;
        var.poly_buff = poly_buff;
        var.marks = marks_rot;
        var.w = w_p;
        var.h = h_p;
        var.b_minx = bb.minx;
        var.b_miny = bb.miny;
        var.b_maxx = bb.maxx;
        var.b_maxy = bb.maxy;
        var.rot_deg = angulo;
        
        // Rasterize the variation for fast bitwise checking (resolution 4mm)
        // Usar la figura SIN INFLAR para el grid de validación rápida. Así no aplicamos el kerf dos veces
        PathsD exact_shifted = translate_copy(to_paths_d(poly_rot), -bb.minx, -bb.miny);
        var.grid = rasterize_paths(exact_shifted, bb.maxx - bb.minx, bb.maxy - bb.miny, 4.0);

        PathsD buff_shifted = translate_copy(to_paths_d(poly_buff), -bb.minx, -bb.miny);
        var.grid_buff = rasterize_paths(buff_shifted, bb.maxx - bb.minx, bb.maxy - bb.miny, 4.0);
        
        variaciones.push_back(std::move(var));
    }
    return variaciones;
}

struct LimitContext {
    bool active = false;
    Bounds bounds{};
    PathsD eval_paths;
};

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

bool comprobar_colision(
    double pos_x,
    double pos_y,
    const Variation& var,
    const LimitContext& limit,
    const std::vector<Bounds>& fijas_bounds,
    const std::vector<PathsD>& fijas_buff_paths) {
    const double cmx = pos_x + var.b_minx;
    const double cmy = pos_y + var.b_miny;
    const double cMx = pos_x + var.b_maxx;
    const double cMy = pos_y + var.b_maxy;

    if (limit.active) {
        if (cmx < limit.bounds.minx || cmy < limit.bounds.miny || cMx > limit.bounds.maxx || cMy > limit.bounds.maxy) {
            return true;
        }
        const PathsD moved = translate_copy(to_paths_d(var.poly_buff), pos_x, pos_y);
        if (!path_contained_in(moved, limit.eval_paths)) {
            return true;
        }
    }

    std::optional<PathsD> moved_buff;
    for (size_t idx = 0; idx < fijas_bounds.size(); ++idx) {
        const auto& f_b = fijas_bounds[idx];
        if (!(cMx <= f_b.minx + 0.05 || cmx >= f_b.maxx - 0.05 || cMy <= f_b.miny + 0.05 || cmy >= f_b.maxy - 0.05)) {
            if (!moved_buff) {
                moved_buff = translate_copy(to_paths_d(var.poly_buff), pos_x, pos_y);
            }
            if (paths_intersect(*moved_buff, fijas_buff_paths[idx])) {
                return true;
            }
        }
    }
    return false;
}

double bbox_perimeter(const std::vector<std::vector<Point2D>>& rings) {
    const Bounds b = bounds_of_rings(rings);
    return 2.0 * ((b.maxx - b.minx) + (b.maxy - b.miny));
}

void compact_slide_position(
    double& px,
    double& py,
    const Variation& var,
    double margin_px,
    const LimitContext& limit,
    const std::vector<Bounds>& fijas_bounds,
    const std::vector<PathsD>& fijas_buff_paths) {
    auto try_slide = [&](double step_mm) {
        bool moved = true;
        while (moved) {
            moved = false;
            const double test_px = px - step_mm;
            if (test_px + var.b_minx >= margin_px) {
                if (!comprobar_colision(test_px, py, var, limit, fijas_bounds, fijas_buff_paths)) {
                    px = test_px;
                    moved = true;
                }
            }
            const double test_py = py - step_mm;
            if (test_py + var.b_miny >= margin_px) {
                if (!comprobar_colision(px, test_py, var, limit, fijas_bounds, fijas_buff_paths)) {
                    py = test_py;
                    moved = true;
                }
            }
        }
    };
    try_slide(kSlideStepCoarseMm);
    try_slide(kSlideStepFineMm);
}

struct ShapeSignature {
    double area = 0.0;
    double bw = 0.0;
    double bh = 0.0;
};

ShapeSignature shape_signature_of(const std::vector<std::vector<Point2D>>& rings, double area_hint) {
    ShapeSignature sig;
    const Bounds b = bounds_of_rings(rings);
    sig.area = area_hint > 0.0 ? area_hint : total_area(rings);
    sig.bw = b.maxx - b.minx;
    sig.bh = b.maxy - b.miny;
    return sig;
}

bool same_shape_signature(const ShapeSignature& a, const ShapeSignature& b, double tol_mm = 2.0) {
    const double area_tol = std::max(250.0, a.area * 0.002);
    return std::abs(a.area - b.area) <= area_tol
        && std::abs(a.bw - b.bw) <= tol_mm
        && std::abs(a.bh - b.bh) <= tol_mm;
}

int count_identical_on_sheet(const std::vector<PieceOut>& placed, const ShapeSignature& sig) {
    int n = 0;
    for (const auto& p : placed) {
        if (same_shape_signature(shape_signature_of(p.poligonos, p.area), sig)) {
            ++n;
        }
    }
    return n;
}

void append_lab_concavity_anchors(
    const std::vector<Point2D>& ring,
    double px,
    double py,
    std::vector<std::pair<double, double>>& anclajes,
    double w_placa,
    double h_placa,
    double margin_px) {
    const size_t n = ring.size();
    if (n < 4) {
        return;
    }
    // Mantener cada vértice cóncavo conserva el acomodo de piezas complejas.
    // La aceleración del piloto viene de no duplicar ni trasladar dos veces
    // las anclas finales, no de omitir geometría.
    const size_t stride = 1;
    for (size_t i = 0; i < n; i += stride) {
        const auto& p0 = ring[(i + n - 1) % n];
        const auto& p1 = ring[i];
        const auto& p2 = ring[(i + 1) % n];
        const double cross = ((p1.x - p0.x) * (p2.y - p1.y)) - ((p1.y - p0.y) * (p2.x - p1.x));
        if (cross >= -0.5) {
            continue;
        }
        double ax = px + p1.x;
        double ay = py + p1.y;
        const double len1 = std::hypot(p1.x - p0.x, p1.y - p0.y);
        const double len2 = std::hypot(p2.x - p1.x, p2.y - p1.y);
        if (len1 > 1e-6 && len2 > 1e-6) {
            double nx = -((p1.y - p0.y) / len1 + (p2.y - p1.y) / len2);
            double ny = ((p1.x - p0.x) / len1 + (p2.x - p1.x) / len2);
            const double nl = std::hypot(nx, ny);
            if (nl > 1e-6) {
                ax += (nx / nl) * 4.0;
                ay += (ny / nl) * 4.0;
            }
        }
        if (ax < margin_px - 0.5 || ay < margin_px - 0.5 || ax > w_placa - margin_px + 0.5
            || ay > h_placa - margin_px + 0.5) {
            continue;
        }
        anclajes.emplace_back(ax, ay);
    }
}

void append_lab_anchors_from_placed(
    const std::vector<std::vector<Point2D>>& poly_placed,
    double px,
    double py,
    std::vector<std::pair<double, double>>& anclajes,
    double w_placa,
    double h_placa,
    double margin_px) {
    if (poly_placed.empty()) {
        return;
    }
    const Bounds b = bounds_of_rings(poly_placed);
    anclajes.emplace_back(px + b.maxx + 1.0, py + b.miny);
    anclajes.emplace_back(px + b.minx, py + b.maxy + 1.0);
    
    // Novedad: agregar anclajes a lo largo del bounding box para mejor empaquetado de piezas complejas
    const double step = 200.0;
    for (double y = b.miny; y < b.maxy; y += step) {
        anclajes.emplace_back(px + b.maxx + 1.0, py + y);
    }
    for (double x = b.minx; x < b.maxx; x += step) {
        anclajes.emplace_back(px + x, py + b.maxy + 1.0);
    }

    append_lab_concavity_anchors(poly_placed.front(), px, py, anclajes, w_placa, h_placa, margin_px);
}

std::pair<SheetOut, std::vector<PieceIn>> llenar_una_hoja_ultrafast(
    std::vector<PieceIn> pendientes,
    double w_placa,
    double h_placa,
    double kerf_custom,
    double margin_custom,
    const std::string& /*opt_mode*/,
    const std::string& /*corner_mode*/,
    const std::optional<std::vector<std::vector<Point2D>>>& limite_rings,
    std::vector<PlacementStep>* timeline_out,
    LabCudaMetrics* cuda_metrics = nullptr) {
    SheetOut hoja;
    std::vector<PathsD> fijas_buff_paths;
    std::vector<Bounds> fijas_bounds;
    std::vector<PieceIn> pendientes_sig;

    const double kerf_radio = (kerf_custom * 25.4) / 2.0;
    const double margin_px = margin_custom > 0.0 ? (margin_custom * 25.4) : 0.0;
    const LimitContext limit = make_limit_context(limite_rings, margin_px);

    RasterGrid board_grid(w_placa, h_placa, 4.0);
#if defined(ARGA_LAB_PILOT)
    std::unique_ptr<lab_cuda::GridSession> cuda_session;
    if (lab_cuda::requested() && lab_cuda::available()) {
        cuda_session = std::make_unique<lab_cuda::GridSession>(
            raster_to_dense(board_grid), board_grid.w, board_grid.h, true);
        if (!cuda_session->cuda_active()) {
            cuda_session.reset();
        } else if (cuda_metrics) {
            cuda_metrics->enabled = true;
        }
    }
#endif

    for (const auto& p_data : pendientes) {
        const double area_pieza = p_data.area > 0.0 ? p_data.area : total_area(p_data.rings);
        const int clase_pieza = clasificar_pieza(p_data.rings, area_pieza);
        const double rectangularidad_val = rectangularidad(p_data.rings);
        const bool es_estructural_grande = area_pieza >= kAreaEstructuralUmbralMm2;
        const bool es_rectangular = (!es_estructural_grande) && rectangularidad_val >= 0.57;

        PlacementStep step_rec;
        if (timeline_out) {
            step_rec.orden_pool = static_cast<int>(timeline_out->size()) + 1;
            step_rec.nombre = p_data.nombre;
            step_rec.categoria = categoria_pieza_label(clase_pieza);
        }

        const auto sig_actual = shape_signature_of(p_data.rings, area_pieza);
        const int dup_en_hoja = count_identical_on_sheet(hoja.piezas, sig_actual);
        const bool modo_interlock_lab = dup_en_hoja > 0 && es_rectangular;

        const auto variaciones = build_variaciones(
            p_data.rings, p_data.marks, w_placa, h_placa, margin_px, kerf_radio);
        if (timeline_out) {
            step_rec.variaciones_evaluadas = static_cast<int>(variaciones.size());
        }
        if (variaciones.empty()) {
            if (timeline_out) {
                step_rec.colocada = false;
                timeline_out->push_back(std::move(step_rec));
            }
            pendientes_sig.push_back(p_data);
            continue;
        }

        const Variation* mejor_var = nullptr;
        double mejor_px = 0.0;
        double mejor_py = 0.0;
        double mejor_score = std::numeric_limits<double>::infinity();

        const std::string estrategia_usada = es_estructural_grande
            ? "estructural"
            : (modo_interlock_lab
                ? "interlock_repetida"
                : (es_rectangular ? "rectangular" : "compleja"));
        const auto score_candidata = [&](double px, double py) {
            if (es_estructural_grande || !es_rectangular) {
                return (px * px) + (py * py);
            }
            if (modo_interlock_lab) {
                return py + (px * 800.0);
            }
            return (px * 1000000.0) + py + ((py * py) * 0.00001);
        };
        const auto seleccionar_mejor = [&](const Variation& var, double px, double py, double score) {
            if (score >= mejor_score) {
                return;
            }
            mejor_score = score;
            mejor_var = &var;
            mejor_px = px;
            mejor_py = py;
        };

#if defined(ARGA_LAB_PILOT)
        struct PreCandidate {
            const Variation* var = nullptr;
            double px = 0.0;
            double py = 0.0;
            double score = 0.0;
        };
        constexpr size_t kPilotShortlistSize = 8;
        std::vector<PreCandidate> top_candidates;
        top_candidates.reserve(kPilotShortlistSize + 1);

        for (const auto& var : variaciones) {
            int max_bx = board_grid.w - var.grid.w;
            int max_by = board_grid.h - var.grid.h;
            if (max_bx < 0 || max_by < 0) {
                continue;
            }

            std::vector<lab_cuda::GridOffset> offsets;
            offsets.reserve(static_cast<std::size_t>((max_bx / 2) + 1) * ((max_by / 2) + 1));
            for (int by = 0; by <= max_by; by += 2) {
                for (int bx = 0; bx <= max_bx; bx += 2) {
                    offsets.push_back({bx, by});
                }
            }

            const bool use_cuda = cuda_session
                && offsets.size() >= kCudaMinOffsets;
            std::vector<std::uint8_t> collided;
            if (use_cuda) {
                lab_cuda::GridFilterStats batch_stats;
                collided = cuda_session->collide_batch(
                    raster_to_dense(var.grid),
                    var.grid.w,
                    var.grid.h,
                    offsets,
                    &batch_stats);
                if (cuda_metrics) {
                    accumulate_cuda_stats(*cuda_metrics, batch_stats);
                }
            }

            for (std::size_t oi = 0; oi < offsets.size(); ++oi) {
                const int bx = offsets[oi].x;
                const int by = offsets[oi].y;
                const bool hit = use_cuda
                    ? (collided[oi] != 0)
                    : grid_collide(board_grid, var.grid, bx, by);
                if (hit) {
                    continue;
                }
                double px = bx * board_grid.resolution - var.b_minx;
                double py = by * board_grid.resolution - var.b_miny;

                if (px + var.b_minx < margin_px - 0.1 || py + var.b_miny < margin_px - 0.1
                    || px + var.b_maxx > w_placa - margin_px + 0.1
                    || py + var.b_maxy > h_placa - margin_px + 0.1) {
                    continue;
                }

                double sc = score_candidata(px, py);

                if (top_candidates.size() < kPilotShortlistSize || sc < top_candidates.back().score) {
                    top_candidates.push_back({&var, px, py, sc});
                    std::sort(top_candidates.begin(), top_candidates.end(), [](const PreCandidate& a, const PreCandidate& b) {
                        return a.score < b.score;
                    });
                    if (top_candidates.size() > kPilotShortlistSize) {
                        top_candidates.pop_back();
                    }
                }
            }
        }
        
        std::vector<PreCandidate> shortlist;
        for (const auto& cand : top_candidates) {
            if (!comprobar_colision(cand.px, cand.py, *cand.var, limit, fijas_bounds, fijas_buff_paths)) {
                shortlist.push_back(cand);
            }
        }

        for (const auto& candidate : shortlist) {
            double px = candidate.px;
            double py = candidate.py;
            compact_slide_position(
                px, py, *candidate.var, margin_px, limit, fijas_bounds, fijas_buff_paths);
            seleccionar_mejor(*candidate.var, px, py, score_candidata(px, py));
        }
#endif

        if (mejor_var != nullptr) {
            const auto cand_final = translate_rings_copy(mejor_var->poly, mejor_px, mejor_py);
            const auto cand_marks_final = translate_rings_copy(mejor_var->marks, mejor_px, mejor_py);
            const auto cand_buff_final = translate_rings_copy(mejor_var->poly_buff, mejor_px, mejor_py);

            fijas_buff_paths.push_back(to_paths_d(cand_buff_final));
            fijas_bounds.push_back(bounds_of_rings(cand_buff_final));

            int draw_bx = static_cast<int>(std::round((mejor_px + mejor_var->b_minx) / board_grid.resolution));
            int draw_by = static_cast<int>(std::round((mejor_py + mejor_var->b_miny) / board_grid.resolution));
            draw_piece_on_board(board_grid, mejor_var->grid_buff, draw_bx, draw_by);
#if defined(ARGA_LAB_PILOT)
            if (cuda_session) {
                cuda_session->update_board(
                    raster_to_dense(board_grid), board_grid.w, board_grid.h);
            }
#endif

            PieceOut placed;
            placed.nombre = p_data.nombre;
            placed.poligonos = cand_final;
            placed.marcas = cand_marks_final;
            placed.area = p_data.area;
            placed.calibre = p_data.calibre;
            placed.material = p_data.material;
            hoja.piezas.push_back(std::move(placed));
            hoja.area_usada += p_data.area;

            if (timeline_out) {
                const Bounds bb_final = bounds_of_rings(cand_final);
                step_rec.colocada = true;
                step_rec.px = mejor_px;
                step_rec.py = mejor_py;
                step_rec.score = mejor_score;
                step_rec.rotacion_grados = mejor_var->rot_deg;
                step_rec.bbox_w_mm = bb_final.maxx - bb_final.minx;
                step_rec.bbox_h_mm = bb_final.maxy - bb_final.miny;
                step_rec.estrategia = estrategia_usada;
                step_rec.pieza_colocada = hoja.piezas.back();
                timeline_out->push_back(std::move(step_rec));
            }
        } else {
            if (timeline_out) {
                step_rec.colocada = false;
                timeline_out->push_back(std::move(step_rec));
            }
            pendientes_sig.push_back(p_data);
        }
    }

    const double denom = w_placa * h_placa;
    hoja.eficiencia = denom > 0.0 ? (hoja.area_usada / denom) * 100.0 : 0.0;
    return {hoja, pendientes_sig};
}

void ordenar_pool_mc(
    std::vector<PieceIn>& pool_intento,
    int iteracion,
    std::mt19937& use_rng,
    std::uniform_real_distribution<double>& dist) {
    if (iteracion == 0) {
        return;
    }
    if (iteracion % 4 == 1) {
        std::sort(pool_intento.begin(), pool_intento.end(), [](const PieceIn& a, const PieceIn& b) {
            const int ca = std::get<0>(sort_key_pool(a));
            const int cb = std::get<0>(sort_key_pool(b));
            if (ca != cb) {
                return ca < cb;
            }
            const double pa = bbox_perimeter(a.rings);
            const double pb = bbox_perimeter(b.rings);
            if (pa != pb) {
                return pa > pb;
            }
            return a.nombre < b.nombre;
        });
    } else if (iteracion % 4 == 2) {
        std::sort(pool_intento.begin(), pool_intento.end(), [](const PieceIn& a, const PieceIn& b) {
            return a.area > b.area;
        });
    } else if (iteracion % 4 == 3) {
        std::sort(pool_intento.begin(), pool_intento.end(), [](const PieceIn& a, const PieceIn& b) {
            const Bounds ba = bounds_of_rings(a.rings);
            const Bounds bb = bounds_of_rings(b.rings);
            const double wa = ba.maxx - ba.minx;
            const double wb = bb.maxx - bb.minx;
            if (wa != wb) {
                return wa > wb;
            }
            return a.area > b.area;
        });
    } else {
        std::unordered_map<std::string, double> mutaciones;
        for (const auto& p : pool_intento) {
            if (!mutaciones.count(p.nombre)) {
                mutaciones[p.nombre] = dist(use_rng);
            }
        }
        std::sort(pool_intento.begin(), pool_intento.end(), [&](const PieceIn& a, const PieceIn& b) {
            const int ca = std::get<0>(sort_key_pool(a));
            const int cb = std::get<0>(sort_key_pool(b));
            if (ca != cb) {
                return ca < cb;
            }
            const double aa = a.area * mutaciones[a.nombre];
            const double ab = b.area * mutaciones[b.nombre];
            if (aa != ab) {
                return aa > ab;
            }
            return a.nombre < b.nombre;
        });
    }
}

std::string modo_orden_mc(int iteracion) {
    if (iteracion == 0) {
        return "clase+area";
    }
    if (iteracion % 4 == 1) {
        return "clase+perimetro";
    }
    if (iteracion % 4 == 2) {
        return "area";
    }
    if (iteracion % 4 == 3) {
        return "ancho+area";
    }
    return "mutacion_mc";
}

TimelinePackResult empaquetar_una_hoja_timeline_impl(
    const std::vector<PieceIn>& piezas,
    double w_placa,
    double h_placa,
    double kerf_override,
    double margin_override,
    const std::string& opt_override,
    const std::string& corner_override,
    const std::optional<std::vector<std::vector<Point2D>>>& limite_rings,
    int mc_iterations) {
    TimelinePackResult out;
    out.pack.hoja.eficiencia = 0.0;
    out.pack.restos = piezas;

    const double margin_px = margin_override > 0.0 ? (margin_override * 25.4) : 0.0;
    const double w_util = w_placa - (2.0 * margin_px);
    const double h_util = h_placa - (2.0 * margin_px);
    if (w_util <= 0.0 || h_util <= 0.0) {
        return out;
    }

    std::vector<PieceIn> pool_base = piezas;
    // Respetar el sembrado inteligente (IA Heurística) generado por Python

    std::mt19937 local_rng(static_cast<uint32_t>(std::random_device{}()));
    std::uniform_real_distribution<double> dist(0.85, 1.15);

    SheetOut mejor_hoja;
    std::vector<PieceIn> mejor_restos = pool_base;
    std::vector<PlacementStep> mejor_timeline;
    int mejor_iter = 0;

    const int iteraciones = std::max(1, std::min(mc_iterations, 2000));

    auto es_mejor = [](const SheetOut& hoja, const std::vector<PieceIn>& restos,
                       const SheetOut& mejor, const std::vector<PieceIn>& mejor_restos) -> bool {
        const size_t n = hoja.piezas.size();
        const size_t n_best = mejor.piezas.size();
        if (n != n_best) {
            return n > n_best;
        }
        if (hoja.area_usada > mejor.area_usada + 1e-6) {
            return true;
        }
        if (std::abs(hoja.area_usada - mejor.area_usada) <= 1e-6) {
            if (restos.size() < mejor_restos.size()) {
                return true;
            }
            if (restos.size() == mejor_restos.size() && hoja.eficiencia > mejor.eficiencia + 1e-6) {
                return true;
            }
        }
        return false;
    };

    for (int i = 0; i < iteraciones; ++i) {
        std::vector<PieceIn> pool_intento = pool_base;
        ordenar_pool_mc(pool_intento, i, local_rng, dist);

        std::vector<PlacementStep> timeline;
        LabCudaMetrics iter_cuda;
        auto [hoja, restos] = llenar_una_hoja_ultrafast(
            pool_intento,
            w_placa,
            h_placa,
            kerf_override,
            margin_override,
            opt_override,
            corner_override,
            limite_rings,
            &timeline,
            &iter_cuda);

        if (es_mejor(hoja, restos, mejor_hoja, mejor_restos)) {
            mejor_hoja = std::move(hoja);
            mejor_restos = std::move(restos);
            mejor_timeline = std::move(timeline);
            mejor_iter = i;
            out.cuda_screen = iter_cuda;
            out.mc_orden_modo = modo_orden_mc(i);
            out.orden_piezas.clear();
            out.orden_piezas.reserve(pool_intento.size());
            for (const auto& p : pool_intento) {
                out.orden_piezas.push_back(p.nombre);
            }
            if (mejor_restos.empty() && mejor_hoja.eficiencia > 88.0) {
                break;
            }
            if (mejor_hoja.eficiencia > 91.0) {
                break;
            }
        }
    }

    const double denom = w_placa * h_placa;
    mejor_hoja.eficiencia = denom > 0.0 ? (mejor_hoja.area_usada / denom) * 100.0 : 0.0;
    out.pack.hoja = std::move(mejor_hoja);
    out.pack.restos = std::move(mejor_restos);
    out.pasos = std::move(mejor_timeline);
    out.mc_iteracion_ganadora = mejor_iter;
    return out;
}

}  // namespace

PackResult empaquetar_una_hoja_mc(
    const std::vector<PieceIn>& piezas,
    double w_placa,
    double h_placa,
    double kerf_override,
    double margin_override,
    const std::string& opt_override,
    const std::string& corner_override,
    const std::optional<std::vector<std::vector<Point2D>>>& limite_rings,
    std::mt19937* rng,
    int mc_iterations) {
    PackResult out;
    out.hoja.eficiencia = 0.0;
    out.restos = piezas;

    const double margin_px = margin_override > 0.0 ? (margin_override * 25.4) : 0.0;
    const double w_util = w_placa - (2.0 * margin_px);
    const double h_util = h_placa - (2.0 * margin_px);
    if (w_util <= 0.0 || h_util <= 0.0) {
        return out;
    }

    std::vector<PieceIn> pool_base = piezas;
    // Respetar el sembrado inteligente (IA Heurística) generado por Python

    const double kerf_a_usar = kerf_override;
    std::mt19937 local_rng(static_cast<uint32_t>(std::random_device{}()));
    std::mt19937& use_rng = rng ? *rng : local_rng;
    std::uniform_real_distribution<double> dist(0.85, 1.15);

    SheetOut mejor_hoja;
    std::vector<PieceIn> mejor_restos = pool_base;

    const int iteraciones = std::max(1, std::min(mc_iterations, 2000));

    auto es_mejor = [](const SheetOut& hoja, const std::vector<PieceIn>& restos,
                       const SheetOut& mejor, const std::vector<PieceIn>& mejor_restos) -> bool {
        const size_t n = hoja.piezas.size();
        const size_t n_best = mejor.piezas.size();
        if (n != n_best) {
            return n > n_best;
        }
        if (hoja.area_usada > mejor.area_usada + 1e-6) {
            return true;
        }
        if (std::abs(hoja.area_usada - mejor.area_usada) <= 1e-6) {
            if (restos.size() < mejor_restos.size()) {
                return true;
            }
            if (restos.size() == mejor_restos.size() && hoja.eficiencia > mejor.eficiencia + 1e-6) {
                return true;
            }
        }
        return false;
    };

    for (int i = 0; i < iteraciones; ++i) {
        std::vector<PieceIn> pool_intento = pool_base;
        if (i == 0) {
            // Orden base (clase + área)
        } else if (i % 4 == 1) {
            std::sort(pool_intento.begin(), pool_intento.end(), [](const PieceIn& a, const PieceIn& b) {
                const int ca = std::get<0>(sort_key_pool(a));
                const int cb = std::get<0>(sort_key_pool(b));
                if (ca != cb) {
                    return ca < cb;
                }
                const double pa = bbox_perimeter(a.rings);
                const double pb = bbox_perimeter(b.rings);
                if (pa != pb) {
                    return pa > pb;
                }
                return a.nombre < b.nombre;
            });
        } else if (i % 4 == 2) {
            std::sort(pool_intento.begin(), pool_intento.end(), [](const PieceIn& a, const PieceIn& b) {
                return a.area > b.area;
            });
        } else if (i % 4 == 3) {
            std::sort(pool_intento.begin(), pool_intento.end(), [](const PieceIn& a, const PieceIn& b) {
                const Bounds ba = bounds_of_rings(a.rings);
                const Bounds bb = bounds_of_rings(b.rings);
                const double wa = ba.maxx - ba.minx;
                const double wb = bb.maxx - bb.minx;
                if (wa != wb) {
                    return wa > wb;
                }
                return a.area > b.area;
            });
        } else {
            std::unordered_map<std::string, double> mutaciones;
            for (const auto& p : pool_intento) {
                if (!mutaciones.count(p.nombre)) {
                    mutaciones[p.nombre] = dist(use_rng);
                }
            }
            std::sort(pool_intento.begin(), pool_intento.end(), [&](const PieceIn& a, const PieceIn& b) {
                const int ca = std::get<0>(sort_key_pool(a));
                const int cb = std::get<0>(sort_key_pool(b));
                if (ca != cb) {
                    return ca < cb;
                }
                const double aa = a.area * mutaciones[a.nombre];
                const double ab = b.area * mutaciones[b.nombre];
                if (aa != ab) {
                    return aa > ab;
                }
                return a.nombre < b.nombre;
            });
        }

        auto [hoja, restos] = llenar_una_hoja_ultrafast(
            pool_intento,
            w_placa,
            h_placa,
            kerf_a_usar,
            margin_override,
            opt_override,
            corner_override,
            limite_rings,
            nullptr);

        if (es_mejor(hoja, restos, mejor_hoja, mejor_restos)) {
            mejor_hoja = std::move(hoja);
            mejor_restos = std::move(restos);
            if (mejor_restos.empty() && mejor_hoja.eficiencia > 88.0) {
                break;
            }
            if (mejor_hoja.eficiencia > 91.0) {
                break;
            }
        }
    }

    const double denom = w_placa * h_placa;
    mejor_hoja.eficiencia = denom > 0.0 ? (mejor_hoja.area_usada / denom) * 100.0 : 0.0;
    out.hoja = std::move(mejor_hoja);
    out.restos = std::move(mejor_restos);
    return out;
}

TimelinePackResult empaquetar_una_hoja_timeline(
    const std::vector<PieceIn>& piezas,
    double w_placa,
    double h_placa,
    double kerf_override,
    double margin_override,
    const std::string& opt_override,
    const std::string& corner_override,
    const std::optional<std::vector<std::vector<Point2D>>>& limite_rings,
    int mc_iterations) {
    return empaquetar_una_hoja_timeline_impl(
        piezas,
        w_placa,
        h_placa,
        kerf_override,
        margin_override,
        opt_override,
        corner_override,
        limite_rings,
        mc_iterations);
}

}  // namespace arga
