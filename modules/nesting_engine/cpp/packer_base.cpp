#include "packer_base.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <map>
#include <unordered_map>
#include <vector>

#include "clipper2/clipper.h"

namespace arga {
namespace {

using namespace Clipper2Lib;

constexpr double kPi = 3.14159265358979323846;
constexpr double kVoidMinAreaMm2 = 50.0 * 50.0;
constexpr double kSlideStepCoarseMm = 3.0;
constexpr double kSlideStepFineMm = 0.5;

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

        auto poly_buff = buffer_rings(poly_rot, kerf_radio);
        if (poly_buff.empty()) {
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

bool colocar_pieza(
    const PieceIn& p_data,
    PlacementState& state,
    double w_placa,
    double h_placa,
    double kerf_radio,
    double margin_px,
    const LimitContext& limit) {
    const double area_pieza = piece_area(p_data);
    const bool es_estructural_grande = area_pieza >= kAreaEstructuralUmbralMm2;

    const auto variaciones = build_variaciones(
        p_data.rings, p_data.marks, w_placa, h_placa, margin_px, kerf_radio);
    if (variaciones.empty()) {
        return false;
    }

    const Variation* mejor_var = nullptr;
    double mejor_px = 0.0;
    double mejor_py = 0.0;
    double mejor_score = std::numeric_limits<double>::infinity();
    std::vector<std::pair<double, double>> mejor_anchors;

    std::vector<std::pair<double, double>> anclajes;
    anclajes.emplace_back(margin_px, margin_px);
    for (const auto& b : state.fijas_bounds) {
        anclajes.emplace_back(b.maxx + 1.0, b.miny);
        anclajes.emplace_back(b.minx, b.maxy + 1.0);
    }

    for (const auto& var : variaciones) {
        for (const auto& anclaje : anclajes) {
            double px = anclaje.first - var.b_minx;
            double py = anclaje.second - var.b_miny;

            if (px + var.b_minx < margin_px - 0.1 || py + var.b_miny < margin_px - 0.1
                || px + var.b_maxx > w_placa - margin_px + 0.1
                || py + var.b_maxy > h_placa - margin_px + 0.1) {
                continue;
            }
            if (comprobar_colision(px, py, var, limit, state.fijas_bounds, state.fijas_buff_paths)) {
                continue;
            }

            compact_slide_position(px, py, var, margin_px, limit, state.fijas_bounds, state.fijas_buff_paths);

            double score = es_estructural_grande ? (px * px) + (py * py) : (px * 1000000.0) + py;

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
    state.hoja.area_usada += area_pieza;
    return true;
}

/** Paso 1-2 pizarrón: agrupar por nombre e ordenar grupos por mayor área. */
std::vector<PieceIn> orden_pizarron(std::vector<PieceIn> piezas) {
    std::map<std::string, std::vector<PieceIn>> grupos;
    for (auto& p : piezas) {
        grupos[p.nombre].push_back(std::move(p));
    }

    struct GrupoOrden {
        std::string nombre;
        double area_max = 0.0;
        std::vector<PieceIn> piezas;
    };

    std::vector<GrupoOrden> lista;
    lista.reserve(grupos.size());
    for (auto& kv : grupos) {
        GrupoOrden g;
        g.nombre = kv.first;
        g.piezas = std::move(kv.second);
        for (const auto& p : g.piezas) {
            g.area_max = std::max(g.area_max, piece_area(p));
        }
        lista.push_back(std::move(g));
    }

    std::sort(lista.begin(), lista.end(), [](const GrupoOrden& a, const GrupoOrden& b) {
        if (a.area_max != b.area_max) {
            return a.area_max > b.area_max;
        }
        return a.nombre < b.nombre;
    });

    std::vector<PieceIn> ordenadas;
    for (auto& g : lista) {
        for (auto& p : g.piezas) {
            ordenadas.push_back(std::move(p));
        }
    }
    return ordenadas;
}

std::pair<PlacementState, std::vector<PieceIn>> colocar_en_orden(
    const std::vector<PieceIn>& ordenadas,
    double w_placa,
    double h_placa,
    double kerf_custom,
    double margin_custom,
    const std::optional<std::vector<std::vector<Point2D>>>& limite_rings) {
    PlacementState state;
    std::vector<PieceIn> restos;

    const double kerf_radio = (kerf_custom * 25.4) / 2.0;
    const double margin_px = margin_custom > 0.0 ? (margin_custom * 25.4) : 0.0;
    const LimitContext limit = make_limit_context(limite_rings, margin_px);

    for (const auto& p_data : ordenadas) {
        if (!colocar_pieza(p_data, state, w_placa, h_placa, kerf_radio, margin_px, limit)) {
            restos.push_back(p_data);
        }
    }

    return {state, restos};
}

PathD rectangulo_placa(double w_placa, double h_placa, double margin_px) {
    PathD rect;
    rect.emplace_back(margin_px, margin_px);
    rect.emplace_back(w_placa - margin_px, margin_px);
    rect.emplace_back(w_placa - margin_px, h_placa - margin_px);
    rect.emplace_back(margin_px, h_placa - margin_px);
    return rect;
}

/** Paso 3: morfología — regiones libres = placa − ocupado (con kerf). */
std::vector<std::vector<std::vector<Point2D>>> detectar_huecos(
    const PlacementState& state,
    double w_placa,
    double h_placa,
    double margin_px,
    double kerf_radio) {
    PathsD sheet = {rectangulo_placa(w_placa, h_placa, margin_px)};
    PathsD occupied;
    for (const auto& piece_paths : state.fijas_buff_paths) {
        occupied.insert(occupied.end(), piece_paths.begin(), piece_paths.end());
    }

    if (occupied.empty()) {
        return {from_paths_d(sheet)};
    }

    PathsD occ_union = Union(occupied, FillRule::NonZero);
    if (occ_union.empty()) {
        occ_union = occupied;
    }

    PathsD free_paths = Difference(sheet, occ_union, FillRule::NonZero);
    std::vector<std::vector<std::vector<Point2D>>> huecos;

    for (const auto& path : free_paths) {
        const double a = std::abs(Area(path));
        if (a < kVoidMinAreaMm2) {
            continue;
        }
        auto rings = from_paths_d({path});
        if (!rings.empty()) {
            huecos.push_back(std::move(rings));
        }
    }

    std::sort(huecos.begin(), huecos.end(), [](const auto& a, const auto& b) {
        return total_area(a) > total_area(b);
    });
    return huecos;
}

bool pieza_cabe_en_hueco(const PieceIn& p, const Bounds& hueco_bounds, double tol = 2.0) {
    const Bounds pb = bounds_of_rings(p.rings);
    const double w_p = pb.maxx - pb.minx;
    const double h_p = pb.maxy - pb.miny;
    const double min_p = std::min(w_p, h_p);
    const double max_p = std::max(w_p, h_p);
    const double w_h = hueco_bounds.maxx - hueco_bounds.minx;
    const double h_h = hueco_bounds.maxy - hueco_bounds.miny;
    const double min_h = std::min(w_h, h_h);
    const double max_h = std::max(w_h, h_h);
    return min_p <= min_h + tol && max_p <= max_h + tol;
}

/** Paso 4: recorrer piezas pequeñas e intentar llenar cada hueco. */
void rellenar_huecos_con_pequenas(
    PlacementState& state,
    std::vector<PieceIn>& restos,
    double w_placa,
    double h_placa,
    double kerf_custom,
    double margin_custom) {
    const double kerf_radio = (kerf_custom * 25.4) / 2.0;
    const double margin_px = margin_custom > 0.0 ? (margin_custom * 25.4) : 0.0;

    std::vector<PieceIn> grandes;
    std::vector<PieceIn> pequenas;
    for (const auto& p : restos) {
        if (piece_area(p) >= kAreaEstructuralUmbralMm2) {
            grandes.push_back(p);
        } else {
            pequenas.push_back(p);
        }
    }
  if (pequenas.empty()) {
        restos = std::move(grandes);
        return;
    }

    std::sort(pequenas.begin(), pequenas.end(), [](const PieceIn& a, const PieceIn& b) {
        return piece_area(a) > piece_area(b);
    });

    auto huecos = detectar_huecos(state, w_placa, h_placa, margin_px, kerf_radio);
    std::vector<PieceIn> sin_colocar;

    for (const auto& hueco_rings : huecos) {
        const Bounds hb = bounds_of_rings(hueco_rings);
        std::vector<PieceIn> candidatos;
        std::vector<PieceIn> siguientes;

        for (auto& p : pequenas) {
            if (pieza_cabe_en_hueco(p, hb)) {
                candidatos.push_back(std::move(p));
            } else {
                siguientes.push_back(std::move(p));
            }
        }
        pequenas = std::move(siguientes);

        if (candidatos.empty()) {
            continue;
        }

        auto orden_candidatos = orden_pizarron(std::move(candidatos));
        auto [sub_state, sub_restos] = colocar_en_orden(
            orden_candidatos,
            w_placa,
            h_placa,
            kerf_custom,
            margin_custom,
            hueco_rings);

        for (auto& placed : sub_state.hoja.piezas) {
            state.hoja.piezas.push_back(std::move(placed));
            state.hoja.area_usada += 0.0;  // recalc below
        }
        for (auto& fp : sub_state.fijas_buff_paths) {
            state.fijas_buff_paths.push_back(std::move(fp));
        }
        for (auto& fb : sub_state.fijas_bounds) {
            state.fijas_bounds.push_back(std::move(fb));
        }

        for (auto& r : sub_restos) {
            pequenas.push_back(std::move(r));
        }
    }

    for (auto& p : pequenas) {
        sin_colocar.push_back(std::move(p));
    }
    for (auto& p : grandes) {
        sin_colocar.push_back(std::move(p));
    }
    restos = std::move(sin_colocar);
}

void finalizar_eficiencia(SheetOut& hoja, double w_placa, double h_placa) {
    double area_real = 0.0;
    for (const auto& p : hoja.piezas) {
        area_real += piece_area(PieceIn{p.nombre, p.area, p.calibre, p.material, p.poligonos, p.marcas});
    }
    hoja.area_usada = area_real;
    const double denom = w_placa * h_placa;
    hoja.eficiencia = denom > 0.0 ? (hoja.area_usada / denom) * 100.0 : 0.0;
}

}  // namespace

PackResult empaquetar_una_hoja_base(
    const std::vector<PieceIn>& piezas,
    double w_placa,
    double h_placa,
    double kerf_override,
    double margin_override,
    const std::string& /*opt_override*/,
    const std::string& /*corner_override*/,
    const std::optional<std::vector<std::vector<Point2D>>>& limite_rings) {
    PackResult out;
    out.restos = piezas;
    out.hoja.eficiencia = 0.0;

    const double margin_px = margin_override > 0.0 ? (margin_override * 25.4) : 0.0;
    const double w_util = w_placa - (2.0 * margin_px);
    const double h_util = h_placa - (2.0 * margin_px);
    if (w_util <= 0.0 || h_util <= 0.0 || piezas.empty()) {
        return out;
    }

    std::vector<PieceIn> pool = piezas;

    // Fases 1-2: mayor área por grupo de idénticas (nombre).
    auto ordenadas = orden_pizarron(std::move(pool));
    auto [state, restos] = colocar_en_orden(
        ordenadas,
        w_placa,
        h_placa,
        kerf_override,
        margin_override,
        limite_rings);

    // Fases 3-4: morfología de huecos y piezas pequeñas (solo placa madre completa).
    const bool es_placa_completa = !limite_rings || limite_rings->empty();
    if (es_placa_completa && !restos.empty()) {
        rellenar_huecos_con_pequenas(
            state,
            restos,
            w_placa,
            h_placa,
            kerf_override,
            margin_override);
    }

    finalizar_eficiencia(state.hoja, w_placa, h_placa);
    out.hoja = std::move(state.hoja);
    out.restos = std::move(restos);
    return out;
}

}  // namespace arga
