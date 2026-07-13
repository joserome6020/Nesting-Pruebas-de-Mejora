#include "packer_svgnest_ultra.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <numeric>
#include <random>
#include <vector>

#include "clipper2/clipper.h"
#include "clipper2/clipper.minkowski.h"

namespace arga {
namespace {

using namespace Clipper2Lib;

constexpr double kPi = 3.14159265358979323846;
constexpr double kPartInPartMaxAreaMm2 = 800'000.0;

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
    std::vector<Bounds> fijas_bounds;
};

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

std::vector<std::vector<Point2D>> buffer_rings(const std::vector<std::vector<Point2D>>& rings, double delta) {
    return from_paths_d(buffer_paths(to_paths_d(rings), delta));
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
    return out;
}

void append_nfp_candidates(
    std::vector<std::pair<double, double>>& anclajes,
    const PathsD& stationary_buff,
    const PathD& orbiting_norm) {
    if (stationary_buff.empty() || orbiting_norm.empty()) {
        return;
    }
    const PathD inv_orb = invert_path(orbiting_norm);
    for (const auto& stat_path : stationary_buff) {
        if (stat_path.size() < 3) {
            continue;
        }
        const PathsD nfp_paths = MinkowskiSum(inv_orb, stat_path, true, 3);
        for (const auto& nfp : nfp_paths) {
            for (const auto& pt : nfp) {
                anclajes.emplace_back(pt.x, pt.y);
            }
        }
    }
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
    const auto angles = build_rotation_angles(rotation_step_deg);

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

LimitContext make_hole_limit(const std::vector<Point2D>& hole_ring, double kerf_radio) {
  LimitContext ctx;
  if (hole_ring.size() < 3) {
    return ctx;
  }
  auto paths = to_paths_d({hole_ring});
  if (kerf_radio > 0.0) {
    paths = InflatePaths(paths, -kerf_radio, JoinType::Miter, EndType::Polygon);
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

double nfp_score(double px, double py) {
    return (py * 1'000'000.0) + px + std::sqrt((px * px) + (py * py)) * 0.01;
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
    double rotation_step_deg) {
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

    for (const auto& var : variaciones) {
        std::vector<std::pair<double, double>> anclajes;
        if (hole_limit) {
            anclajes.emplace_back(hole_limit->bounds.minx, hole_limit->bounds.miny);
        } else {
            anclajes.emplace_back(margin_px, margin_px);
        }
        for (const auto& b : state.fijas_bounds) {
            anclajes.emplace_back(b.maxx + 1.0, b.miny);
            anclajes.emplace_back(b.minx, b.maxy + 1.0);
        }
        for (size_t idx = 0; idx < state.fijas_buff_paths.size(); ++idx) {
            append_nfp_candidates(anclajes, state.fijas_buff_paths[idx], var.outer_norm);
        }

        for (const auto& anclaje : anclajes) {
            double px = anclaje.first - var.b_minx;
            double py = anclaje.second - var.b_miny;

            if (px + var.b_minx < margin_px - 0.1 || py + var.b_miny < margin_px - 0.1
                || px + var.b_maxx > w_placa - margin_px + 0.1
                || py + var.b_maxy > h_placa - margin_px + 0.1) {
                continue;
            }
            if (comprobar_colision(px, py, var, place_limit, state.fijas_bounds, state.fijas_buff_paths)) {
                continue;
            }

            compact_slide_position(px, py, var, margin_px, place_limit, state.fijas_bounds, state.fijas_buff_paths);

            const double score = nfp_score(px, py);
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
    state.fijas_bounds.push_back(bounds_of_rings(cand_buff_final));

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
    double rotation_step_deg) {
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
                        rotation_step_deg)) {
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

std::pair<PlacementState, std::vector<PieceIn>> pack_with_order(
    const std::vector<PieceIn>& piezas,
    const std::vector<size_t>& order,
    double w_placa,
    double h_placa,
    double kerf_custom,
    double margin_custom,
    const std::optional<std::vector<std::vector<Point2D>>>& limite_rings,
    double rotation_step_deg,
    bool part_in_part) {
    PlacementState state;
    std::vector<PieceIn> restos;

    const double kerf_radio = (kerf_custom * 25.4) / 2.0;
    const double margin_px = margin_custom > 0.0 ? (margin_custom * 25.4) : 0.0;
    const LimitContext sheet_limit = make_limit_context(limite_rings, margin_px);

    for (const size_t idx : order) {
        if (idx >= piezas.size()) {
            continue;
        }
        if (!colocar_pieza_nfp(
                piezas[idx],
                state,
                w_placa,
                h_placa,
                kerf_radio,
                margin_px,
                sheet_limit,
                nullptr,
                rotation_step_deg)) {
            restos.push_back(piezas[idx]);
        }
    }

    if (part_in_part && !restos.empty()) {
        try_part_in_part(
            state,
            restos,
            w_placa,
            h_placa,
            kerf_radio,
            margin_px,
            sheet_limit,
            rotation_step_deg);
    }

    const double denom = w_placa * h_placa;
    state.hoja.eficiencia = denom > 0.0 ? (state.hoja.area_usada / denom) * 100.0 : 0.0;
    return {state, restos};
}

double fitness_score(const SheetOut& hoja, const std::vector<PieceIn>& restos, size_t total_pieces) {
    const double placed = static_cast<double>(hoja.piezas.size());
    const double rest = static_cast<double>(restos.size());
    return (placed * 1e12) + hoja.area_usada - (rest * 1e8) + (hoja.eficiencia * 1e4)
           - (static_cast<double>(total_pieces) - placed) * 1e10;
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
    std::uint32_t ga_seed) {
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
    const double rot_step = std::max(5.0, std::min(rotation_step_deg, 90.0));

    std::mt19937 rng(ga_seed != 0 ? ga_seed : static_cast<std::uint32_t>(std::random_device{}()));
    std::uniform_real_distribution<double> prob(0.0, 1.0);
    const double mutation_rate = 0.15;

    std::vector<Individual> pop(static_cast<size_t>(population));
    for (auto& ind : pop) {
        ind.order = random_permutation(n, rng);
    }

    SheetOut mejor_hoja;
    std::vector<PieceIn> mejor_restos = piezas;
    std::vector<size_t> mejor_order;

    auto evaluate = [&](Individual& ind) {
        auto [state, restos] = pack_with_order(
            piezas,
            ind.order,
            w_placa,
            h_placa,
            kerf_override,
            margin_override,
            limite_rings,
            rot_step,
            part_in_part);
        ind.fitness = fitness_score(state.hoja, restos, n);
        if (es_mejor_pack(state.hoja, restos, mejor_hoja, mejor_restos)) {
            mejor_hoja = std::move(state.hoja);
            mejor_restos = std::move(restos);
            mejor_order = ind.order;
        }
    };

    for (auto& ind : pop) {
        evaluate(ind);
    }

    for (int gen = 1; gen < generations; ++gen) {
        std::sort(pop.begin(), pop.end(), [](const Individual& a, const Individual& b) {
            return a.fitness > b.fitness;
        });

        std::vector<Individual> next_gen;
        next_gen.reserve(pop.size());
        next_gen.push_back(pop.front());

        while (static_cast<int>(next_gen.size()) < population) {
            const Individual& p1 = pop[static_cast<size_t>(rng() % (population / 2 + 1))];
            const Individual& p2 = pop[static_cast<size_t>(rng() % (population / 2 + 1))];
            Individual child;
            if (prob(rng) < 0.7) {
                order_crossover(p1.order, p2.order, child.order, rng);
            } else {
                child.order = p1.order;
            }
            mutate_swap(child.order, rng, mutation_rate);
            evaluate(child);
            next_gen.push_back(std::move(child));
        }
        pop = std::move(next_gen);
    }

    if (!mejor_order.empty()) {
        auto [state, restos] = pack_with_order(
            piezas,
            mejor_order,
            w_placa,
            h_placa,
            kerf_override,
            margin_override,
            limite_rings,
            rot_step,
            part_in_part);
        mejor_hoja = std::move(state.hoja);
        mejor_restos = std::move(restos);
    }

    const double denom = w_placa * h_placa;
    mejor_hoja.eficiencia = denom > 0.0 ? (mejor_hoja.area_usada / denom) * 100.0 : 0.0;
    out.hoja = std::move(mejor_hoja);
    out.restos = std::move(mejor_restos);
    return out;
}

}  // namespace arga
