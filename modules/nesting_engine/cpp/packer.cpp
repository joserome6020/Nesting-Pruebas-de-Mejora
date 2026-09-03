#include "packer.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <optional>
#include <unordered_map>

#include "clipper2/clipper.h"
#include "cuda/nest_accel_raster.hpp"

namespace arga {
namespace {

using namespace Clipper2Lib;

constexpr double kScale = 1000.0;
constexpr double kPi = 3.14159265358979323846;

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
    double w = 0.0;
    double h = 0.0;
    double b_minx = 0.0;
    double b_miny = 0.0;
    double b_maxx = 0.0;
    double b_maxy = 0.0;
    double m_minx = 0.0;
    double m_miny = 0.0;
    double m_maxx = 0.0;
    double m_maxy = 0.0;
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

std::vector<Variation> build_variaciones(
    const std::vector<std::vector<Point2D>>& poly_src,
    const std::vector<std::vector<Point2D>>& marks_src,
    double w_placa,
    double h_placa,
    double margin_px,
    double kerf_radio,
    const std::vector<int>& rotations_in = {}) {
    const std::vector<int> rotations_default = {0, 90, 180, 270};
    const std::vector<int>& rotations =
        rotations_in.empty() ? rotations_default : rotations_in;
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

        const Bounds mb = bounds_of_rings(poly_rot);
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
        var.m_minx = mb.minx;
        var.m_miny = mb.miny;
        var.m_maxx = mb.maxx;
        var.m_maxy = mb.maxy;
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
        // Placa→pieza: METAL vs margen (kerf solo entre piezas).
        const double mmx = pos_x + var.m_minx;
        const double mmy = pos_y + var.m_miny;
        const double mMx = pos_x + var.m_maxx;
        const double mMy = pos_y + var.m_maxy;
        if (mmx < limit.bounds.minx || mmy < limit.bounds.miny || mMx > limit.bounds.maxx
            || mMy > limit.bounds.maxy) {
            return true;
        }
        const PathsD moved = translate_copy(to_paths_d(var.poly), pos_x, pos_y);
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
            if (test_px + var.m_minx >= margin_px) {
                if (!comprobar_colision(test_px, py, var, limit, fijas_bounds, fijas_buff_paths)) {
                    px = test_px;
                    moved = true;
                }
            }
            const double test_py = py - step_mm;
            if (test_py + var.m_miny >= margin_px) {
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

std::pair<SheetOut, std::vector<PieceIn>> llenar_una_hoja_ultrafast(
    std::vector<PieceIn> pendientes,
    double w_placa,
    double h_placa,
    double kerf_custom,
    double margin_custom,
    const std::string& /*opt_mode*/,
    const std::string& /*corner_mode*/,
    const std::optional<std::vector<std::vector<Point2D>>>& limite_rings,
    bool seed_bottom_alley) {
    SheetOut hoja;
    std::vector<PathsD> fijas_buff_paths;
    std::vector<Bounds> fijas_bounds;
    std::vector<PieceIn> pendientes_sig;

    const double kerf_radio = (kerf_custom * 25.4) / 2.0;
    const double margin_px = margin_custom > 0.0 ? (margin_custom * 25.4) : 0.0;
    const LimitContext limit = make_limit_context(limite_rings, margin_px);

    std::vector<std::pair<double, double>> anclajes;
    anclajes.emplace_back(margin_px, margin_px);

    for (const auto& p_data : pendientes) {
        const double area_pieza = p_data.area > 0.0 ? p_data.area : total_area(p_data.rings);
        const double rectangularidad_val = rectangularidad(p_data.rings);
        const bool es_estructural_grande = area_pieza >= kAreaEstructuralUmbralMm2;
        const bool es_rectangular = (!es_estructural_grande) && rectangularidad_val >= 0.57;

        const auto variaciones = build_variaciones(
            p_data.rings,
            p_data.marks,
            w_placa,
            h_placa,
            margin_px,
            kerf_radio,
            resolve_piece_rotations_deg(p_data));
        if (variaciones.empty()) {
            pendientes_sig.push_back(p_data);
            continue;
        }

        const Variation* mejor_var = nullptr;
        double mejor_px = 0.0;
        double mejor_py = 0.0;
        double mejor_score = std::numeric_limits<double>::infinity();
        std::vector<std::pair<double, double>> mejor_anchors;

        std::optional<cuda::DenseMask> cuda_board;

        for (const auto& var : variaciones) {
            std::vector<std::pair<double, double>> cand_xy;
            cand_xy.reserve(anclajes.size());
            std::vector<std::pair<double, double>> cand_pxpy;
            cand_pxpy.reserve(anclajes.size());
            for (const auto& anclaje : anclajes) {
                const double ax = anclaje.first;
                const double ay = anclaje.second;
                double px = ax - var.b_minx;
                double py = ay - var.b_miny;
                clamp_placement_to_plate_margin(
                    px,
                    py,
                    var.m_minx,
                    var.m_miny,
                    var.m_maxx,
                    var.m_maxy,
                    margin_px,
                    w_placa,
                    h_placa);
                if (!placement_respects_plate_margin(
                        px,
                        py,
                        var.m_minx,
                        var.m_miny,
                        var.m_maxx,
                        var.m_maxy,
                        margin_px,
                        w_placa,
                        h_placa)) {
                    continue;
                }
                cand_xy.emplace_back(px, py);
                cand_pxpy.emplace_back(px, py);
            }
            std::vector<std::uint8_t> rejected;
            if (cuda::filter_worthwhile(cand_xy.size(), fijas_buff_paths.size())) {
                if (!cuda_board.has_value()) {
                    cuda_board = cuda::rasterize_union_occupancy(
                        fijas_buff_paths, w_placa, h_placa, 8.0);
                }
                rejected = cuda::filter_against_board(
                    *cuda_board, to_paths_d(var.poly_buff), cand_xy, 8.0);
            }

            for (std::size_t ci = 0; ci < cand_pxpy.size(); ++ci) {
                if (!rejected.empty() && rejected[ci] != 0) {
                    continue;
                }
                double px = cand_pxpy[ci].first;
                double py = cand_pxpy[ci].second;

                if (comprobar_colision(px, py, var, limit, fijas_bounds, fijas_buff_paths)) {
                    continue;
                }

                compact_slide_position(px, py, var, margin_px, limit, fijas_bounds, fijas_buff_paths);

                double score = 0.0;
                if (es_estructural_grande) {
                    score = (px * px) + (py * py);
                } else if (es_rectangular) {
                    score = (px * 1000000.0) + py + ((py * py) * 0.00001);
                } else {
                    score = (px * px) + (py * py);
                }

                if (score < mejor_score) {
                    mejor_score = score;
                    mejor_var = &var;
                    mejor_px = px;
                    mejor_py = py;
                    mejor_anchors = {
                        {px + var.b_maxx + 1.0, py + var.b_miny},
                        {px + var.b_minx, py + var.b_maxy + 1.0},
                    };
                }
            }
        }

        if (mejor_var != nullptr) {
            const auto cand_final = translate_rings_copy(mejor_var->poly, mejor_px, mejor_py);
            const auto cand_marks_final = translate_rings_copy(mejor_var->marks, mejor_px, mejor_py);
            const auto cand_buff_final = translate_rings_copy(mejor_var->poly_buff, mejor_px, mejor_py);

            fijas_buff_paths.push_back(to_paths_d(cand_buff_final));
            fijas_bounds.push_back(bounds_of_rings(cand_buff_final));

            anclajes.insert(anclajes.end(), mejor_anchors.begin(), mejor_anchors.end());
            if (seed_bottom_alley) {
                const double alley_x = mejor_px + mejor_var->b_maxx + (kerf_radio * 2.0);
                if (alley_x + 40.0 < w_placa - margin_px) {
                    anclajes.emplace_back(alley_x, margin_px);
                }
            }
            anclajes.erase(
                std::remove_if(
                    anclajes.begin(),
                    anclajes.end(),
                    [&](const std::pair<double, double>& a) {
                        return a.first > w_placa - margin_px || a.second > h_placa - margin_px;
                    }),
                anclajes.end());

            PieceOut placed;
            placed.nombre = p_data.nombre;
            placed.poligonos = cand_final;
            placed.marcas = cand_marks_final;
            placed.area = p_data.area;
            placed.calibre = p_data.calibre;
            placed.material = p_data.material;
            hoja.piezas.push_back(std::move(placed));
            hoja.area_usada += p_data.area;
        } else {
            pendientes_sig.push_back(p_data);
        }
    }

    const double denom = w_placa * h_placa;
    hoja.eficiencia = denom > 0.0 ? (hoja.area_usada / denom) * 100.0 : 0.0;
    return {hoja, pendientes_sig};
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
    int mc_iterations,
    bool preserve_input_order,
    bool seed_bottom_alley) {
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
    if (!preserve_input_order) {
        std::sort(pool_base.begin(), pool_base.end(), [](const PieceIn& a, const PieceIn& b) {
            const auto ka = sort_key_pool(a);
            const auto kb = sort_key_pool(b);
            if (std::get<0>(ka) != std::get<0>(kb)) {
                return std::get<0>(ka) < std::get<0>(kb);
            }
            if (std::get<1>(ka) != std::get<1>(kb)) {
                return std::get<1>(ka) < std::get<1>(kb);
            }
            return std::get<2>(ka) < std::get<2>(kb);
        });
    }

    const double kerf_a_usar = kerf_override;
    std::mt19937 local_rng(static_cast<uint32_t>(std::random_device{}()));
    std::mt19937& use_rng = rng ? *rng : local_rng;
    std::uniform_real_distribution<double> dist(0.85, 1.15);

    SheetOut mejor_hoja;
    std::vector<PieceIn> mejor_restos = pool_base;

    const int iteraciones = std::max(1, std::min(mc_iterations, 50));

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
            seed_bottom_alley);

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

}  // namespace arga
