#include "packer_base.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <map>
#include <numeric>
#include <optional>
#include <unordered_map>
#include <vector>

#include "clipper2/clipper.h"
#include "clipper2/clipper.minkowski.h"
#include "cuda/nest_accel_raster.hpp"


namespace arga {
namespace {

using namespace Clipper2Lib;

constexpr double kPi = 3.14159265358979323846;
constexpr double kVoidMinAreaMm2 = 25.0 * 25.0;
// Orificio anidable (part-in-part): ≥ ~80 in². Tornillos/pequeños no cuentan.
constexpr double kHostHoleMinMm2 = 80.0 * 645.16;
// Guest PIP: no meter piezas demasiado grandes en barrenos (estilo Ultra).
constexpr double kPartInPartMaxGuestMm2 = 120.0 * 645.16;
constexpr double kSlideStepCoarseMm = 3.0;
constexpr double kSlideStepFineMm = 0.5;
// Alineado con manager.ARGA_AREA_ESTRUCTURAL_MM2 (= 200 in²).
constexpr double kAreaEstructuralUmbralMm2 = 200.0 * 645.16;
constexpr int kMaxHuecosPorPasada = 32;
constexpr int kMaxPasillos = 80;
// Antes 256/48: morfología Clipper en anillos densos; 32 basta con early-break.
constexpr int kMaxGuardRelleno = 32;

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

struct LimitContext {
    bool active = false;
    Bounds bounds{};
    PathsD eval_paths;
};

struct PlacementState {
    SheetOut hoja;
    std::vector<PathsD> fijas_buff_paths;
    std::vector<PathsD> fijas_solid_paths;  // sin kerf: colisión en relleno de cavidades C/VFM
    std::vector<Bounds> fijas_bounds;
    std::vector<char> fijas_es_anfitriona;  // 1 = estructural anfitrión
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

bool pieza_es_anfitriona_huecos(const PieceIn& p) {
    if (p.rings.size() < 2) {
        return false;
    }
    // Solo barrenos grandes anidables — no sumar mil tornillos para marcar host.
    for (size_t i = 1; i < p.rings.size(); ++i) {
        if (polygon_area_ring(p.rings[i]) >= kHostHoleMinMm2) {
            return true;
        }
    }
    return false;
}

bool pieza_va_en_fase_estructural(const PieceIn& p) {
    // Anfitrionas con cavidades (VFM etc.) van primero aunque el área esté bajo el umbral.
    return piece_area(p) >= kAreaEstructuralUmbralMm2 || pieza_es_anfitriona_huecos(p);
}

PathD invert_path(const PathD& path) {
    PathD out;
    out.reserve(path.size());
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

/** NFP solo para anclas en cavidades/orificios (limit.active). No se usa en patio libre. */
void append_nfp_cavity_anchors(
    std::vector<std::pair<double, double>>& anclajes,
    const PathsD& stationary_buff,
    const PathD& orbiting_norm,
    const Bounds& limit_bounds) {
    if (stationary_buff.empty() || orbiting_norm.size() < 3) {
        return;
    }
    const PathD inv_orb = invert_path(orbiting_norm);
    for (const auto& stat_path : stationary_buff) {
        if (stat_path.size() < 3) {
            continue;
        }
        Bounds sb = bounds_of_paths({stat_path});
        // Solo piezas cercanas al hueco (evita Minkowski global).
        if (sb.maxx < limit_bounds.minx - 5.0 || sb.minx > limit_bounds.maxx + 5.0
            || sb.maxy < limit_bounds.miny - 5.0 || sb.miny > limit_bounds.maxy + 5.0) {
            continue;
        }
        const PathsD nfp_paths = MinkowskiSum(inv_orb, stat_path, true, 3);
        for (const auto& nfp : nfp_paths) {
            const size_t n = nfp.size();
            const size_t stride = n > 64 ? std::max<size_t>(1, n / 48) : 1;
            for (size_t i = 0; i < n; i += stride) {
                anclajes.emplace_back(nfp[i].x, nfp[i].y);
            }
        }
    }
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

PathsD pick_largest_path(const PathsD& solution) {
    if (solution.empty()) {
        return {};
    }
    if (solution.size() == 1) {
        return solution;
    }
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

/**
 * Buffer de kerf preservando canales abiertos (perfil C / VFM).
 * NUNCA usar pick_largest_path sobre el offset: colapsa el canal cóncavo a un
 * sólido cerrado y las piezas pequeñas jamás pueden entrar.
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

    // Contorno exterior crece; cada hueco se encoge (material crece hacia dentro).
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

/** Metal real = exterior − huecos. Nunca guardar anillos crudos: con NonZero el
 *  interior del orificio sigue “ocupado” y paths_intersect rechaza TODA pieza dentro. */
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

bool point_in_paths(double x, double y, const PathsD& container) {
    if (container.empty()) {
        return false;
    }
    PathD pt;
    pt.emplace_back(x, y);
    pt.emplace_back(x + 0.02, y);
    pt.emplace_back(x + 0.02, y + 0.02);
    pt.emplace_back(x, y + 0.02);
    return path_contained_in(PathsD{pt}, container);
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
    // Solo ortogonal (0/90/180/270). 45° infla el AABB y en producción
    // suele empeorar el nest (y el corte) frente a rotaciones cardinales.
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

        auto poly_buff = buffer_rings(poly_rot, kerf_radio);
        if (poly_buff.empty()) {
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

/** Límite de orificio: encoge kerf COMPLETO (2·radio); contención = pieza exacta.
 *  Gap pieza↔pared del orificio = kerf (p. ej. 0.3"). NUNCA reducir kerf para forzar cabida. */
LimitContext make_hole_limit(const std::vector<Point2D>& hole_ring, double kerf_radio) {
    LimitContext ctx;
    if (hole_ring.size() < 3) {
        return ctx;
    }
    PathsD paths = to_paths_d({hole_ring});
    const double shrink = 2.0 * kerf_radio;  // kerf completo
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

/** Límite de vacío abierto (canal C/VFM / pasillo): kerf COMPLETO + pieza exacta.
 *  Si la pieza no cabe con kerf real, NO entra: eso es correcto de fabricación. */
LimitContext make_void_limit(const std::vector<std::vector<Point2D>>& rings, double kerf_radio) {
    LimitContext ctx;
    if (rings.empty() || rings[0].size() < 3) {
        return ctx;
    }
    PathsD paths = to_paths_d(rings);
    const double shrink = 2.0 * kerf_radio;  // kerf completo
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

bool bounds_contiene(const Bounds& outer, const Bounds& inner, double tol = 1.0) {
    return inner.minx >= outer.minx - tol && inner.maxx <= outer.maxx + tol
        && inner.miny >= outer.miny - tol && inner.maxy <= outer.maxy + tol;
}

bool comprobar_colision(
    double pos_x,
    double pos_y,
    const Variation& var,
    const LimitContext& limit,
    const std::vector<Bounds>& fijas_bounds,
    const std::vector<PathsD>& fijas_buff_paths,
    const std::vector<PathsD>& fijas_solid_paths,
    const std::vector<char>& fijas_es_anfitriona,
    double kerf_radio) {
    const double cmx = pos_x + var.b_minx;
    const double cmy = pos_y + var.b_miny;
    const double cMx = pos_x + var.b_maxx;
    const double cMy = pos_y + var.b_maxy;
    const double kerf_full = 2.0 * kerf_radio;

    std::optional<PathsD> moved_exact_cache;
    std::optional<PathsD> moved_buff;
    if (limit.active) {
        moved_exact_cache = translate_copy(to_paths_d(var.poly), pos_x, pos_y);
        if (!path_contained_in(*moved_exact_cache, limit.eval_paths)) {
            return true;
        }
    }

    auto ensure_exact = [&]() -> const PathsD& {
        if (!moved_exact_cache) {
            moved_exact_cache = translate_copy(to_paths_d(var.poly), pos_x, pos_y);
        }
        return *moved_exact_cache;
    };

    for (size_t idx = 0; idx < fijas_bounds.size(); ++idx) {
        const auto& f_b = fijas_bounds[idx];
        const double pad = std::max(0.05, kerf_full + 1.0);
        if (cMx + pad <= f_b.minx || cmx - pad >= f_b.maxx || cMy + pad <= f_b.miny
            || cmy - pad >= f_b.maxy) {
            continue;
        }
        const bool marcada_host =
            limit.active && idx < fijas_es_anfitriona.size() && fijas_es_anfitriona[idx]
            && idx < fijas_solid_paths.size() && !fijas_solid_paths[idx].empty();
        bool es_host_cavity = false;
        if (marcada_host) {
            const auto& hb = fijas_bounds[idx];
            const double inset = std::max(0.0, kerf_radio) + 0.5;
            es_host_cavity =
                cmx >= hb.minx + inset - 0.5 && cMx <= hb.maxx - inset + 0.5
                && cmy >= hb.miny + inset - 0.5 && cMy <= hb.maxy - inset + 0.5;
        }
        if (es_host_cavity) {
            if (paths_intersect(ensure_exact(), fijas_solid_paths[idx])) {
                return true;
            }
            continue;
        }
        // Pieza↔pieza: buff↔buff (gap metal = kerf). Rápido y estable.
        if (!moved_buff) {
            moved_buff = translate_copy(to_paths_d(var.poly_buff), pos_x, pos_y);
        }
        if (idx < fijas_buff_paths.size() && paths_intersect(*moved_buff, fijas_buff_paths[idx])) {
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
    const std::vector<Bounds>& fijas_bounds,
    const std::vector<PathsD>& fijas_buff_paths,
    const std::vector<PathsD>& fijas_solid_paths,
    const std::vector<char>& fijas_es_anfitriona,
    double kerf_radio) {
    // Coarse grande + fine corto: evita miles de comprobar_colision por piece.
    auto try_slide_capped = [&](double step_mm, int max_steps) {
        bool moved = true;
        const double min_x = limit.active ? limit.bounds.minx : margin_px;
        const double min_y = limit.active ? limit.bounds.miny : margin_px;
        int steps = 0;
        while (moved && steps < max_steps) {
            moved = false;
            ++steps;
            const double test_px = px - step_mm;
            if (test_px + var.m_minx >= min_x) {
                if (!comprobar_colision(
                        test_px,
                        py,
                        var,
                        limit,
                        fijas_bounds,
                        fijas_buff_paths,
                        fijas_solid_paths,
                        fijas_es_anfitriona,
                        kerf_radio)) {
                    px = test_px;
                    moved = true;
                }
            }
            const double test_py = py - step_mm;
            if (test_py + var.m_miny >= min_y) {
                if (!comprobar_colision(
                        px,
                        test_py,
                        var,
                        limit,
                        fijas_bounds,
                        fijas_buff_paths,
                        fijas_solid_paths,
                        fijas_es_anfitriona,
                        kerf_radio)) {
                    py = test_py;
                    moved = true;
                }
            }
        }
    };
    try_slide_capped(25.0, 80);   // ~1" por paso
    try_slide_capped(kSlideStepCoarseMm, 40);
    try_slide_capped(kSlideStepFineMm, 20);
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
        p_data.rings,
        p_data.marks,
        w_placa,
        h_placa,
        margin_px,
        kerf_radio,
        resolve_piece_rotations_deg(p_data));
    if (variaciones.empty()) {
        return false;
    }

    const Variation* mejor_var = nullptr;
    double mejor_px = 0.0;
    double mejor_py = 0.0;
    double mejor_score = std::numeric_limits<double>::infinity();
    std::vector<std::pair<double, double>> mejor_anchors;

    std::vector<std::pair<double, double>> anclajes;
    // En huecos/limites interiores el ancla debe ser la esquina del hueco, NO el
    // origen de la placa (si no, nunca coloca nada en cavidades tipo VFM).
    if (limit.active) {
        // Centroide del vacío (esquinas AABB suelen caer en metal en canales C irregulares).
        if (!limit.eval_paths.empty() && !limit.eval_paths[0].empty()) {
            std::vector<Point2D> ring;
            ring.reserve(limit.eval_paths[0].size());
            for (const auto& pt : limit.eval_paths[0]) {
                ring.push_back({pt.x, pt.y});
            }
            const Point2D c = polygon_centroid(ring);
            if (point_in_paths(c.x, c.y, limit.eval_paths)) {
                anclajes.emplace_back(c.x, c.y);
            }
        }
        const double midx = (limit.bounds.minx + limit.bounds.maxx) * 0.5;
        const double midy = (limit.bounds.miny + limit.bounds.maxy) * 0.5;
        const double q1x = limit.bounds.minx + (limit.bounds.maxx - limit.bounds.minx) * 0.25;
        const double q3x = limit.bounds.minx + (limit.bounds.maxx - limit.bounds.minx) * 0.75;
        const double q1y = limit.bounds.miny + (limit.bounds.maxy - limit.bounds.miny) * 0.25;
        const double q3y = limit.bounds.miny + (limit.bounds.maxy - limit.bounds.miny) * 0.75;
        anclajes.emplace_back(limit.bounds.minx, limit.bounds.miny);
        anclajes.emplace_back(limit.bounds.minx, limit.bounds.maxy);
        anclajes.emplace_back(limit.bounds.maxx, limit.bounds.miny);
        anclajes.emplace_back(limit.bounds.maxx, limit.bounds.maxy);
        anclajes.emplace_back(midx, limit.bounds.miny);
        anclajes.emplace_back(limit.bounds.minx, midy);
        anclajes.emplace_back(midx, midy);
        anclajes.emplace_back(q1x, q1y);
        anclajes.emplace_back(q3x, q1y);
        anclajes.emplace_back(q1x, q3y);
        anclajes.emplace_back(q3x, q3y);
        // Malla liviana: grilla 14×10×4 rot×slide hacía minutos por placa con anillos.
        const double bw = limit.bounds.maxx - limit.bounds.minx;
        const double bh = limit.bounds.maxy - limit.bounds.miny;
        const int nx = bw > 800.0 ? 6 : (bw > 200.0 ? 4 : 3);
        const int ny = bh > 400.0 ? 5 : (bh > 100.0 ? 3 : 2);
        for (int iy = 0; iy <= ny; ++iy) {
            for (int ix = 0; ix <= nx; ++ix) {
                const double x = limit.bounds.minx + bw * (static_cast<double>(ix) / static_cast<double>(nx));
                const double y = limit.bounds.miny + bh * (static_cast<double>(iy) / static_cast<double>(ny));
                if (point_in_paths(x, y, limit.eval_paths)) {
                    anclajes.emplace_back(x, y);
                }
            }
        }
        if (bw >= bh * 1.5) {
            const double midy = (limit.bounds.miny + limit.bounds.maxy) * 0.5;
            for (int ix = 0; ix <= nx; ++ix) {
                const double x = limit.bounds.minx + bw * (static_cast<double>(ix) / static_cast<double>(nx));
                if (point_in_paths(x, midy, limit.eval_paths)) {
                    anclajes.emplace_back(x, midy);
                }
            }
        } else if (bh >= bw * 1.5) {
            const double midx = (limit.bounds.minx + limit.bounds.maxx) * 0.5;
            for (int iy = 0; iy <= ny; ++iy) {
                const double y = limit.bounds.miny + bh * (static_cast<double>(iy) / static_cast<double>(ny));
                if (point_in_paths(midx, y, limit.eval_paths)) {
                    anclajes.emplace_back(midx, y);
                }
            }
        }
    } else {
        anclajes.emplace_back(margin_px, margin_px);
        // Esquinas del marco útil (descubrir patio sin malla densa costosa).
        anclajes.emplace_back(w_placa - margin_px, margin_px);
        anclajes.emplace_back(margin_px, h_placa - margin_px);
        anclajes.emplace_back(w_placa - margin_px, h_placa - margin_px);
        anclajes.emplace_back((w_placa) * 0.5, margin_px);
        anclajes.emplace_back(margin_px, (h_placa) * 0.5);
        anclajes.emplace_back((w_placa) * 0.5, (h_placa) * 0.5);
    }
    for (const auto& b : state.fijas_bounds) {
        if (limit.active && !bounds_contiene(limit.bounds, b, 2.0)
            && !bounds_contiene(b, limit.bounds, 2.0)) {
            // Solo anclas cercanas al hueco (piezas ya dentro o el propio marco).
            continue;
        }
        anclajes.emplace_back(b.maxx + 1.0, b.miny);
        anclajes.emplace_back(b.minx, b.maxy + 1.0);
        anclajes.emplace_back(b.minx, b.miny);
        anclajes.emplace_back(b.maxx + 1.0, b.maxy + 1.0);
        // Anclas mid-edge: rellenar bahías cóncavas / flancos (explore concave).
        const double mx = (b.minx + b.maxx) * 0.5;
        const double my = (b.miny + b.maxy) * 0.5;
        anclajes.emplace_back(b.maxx + 1.0, my);
        anclajes.emplace_back(mx, b.maxy + 1.0);
        anclajes.emplace_back(b.minx - 1.0, my);
        anclajes.emplace_back(mx, b.miny - 1.0);
    }

    // NFP selectivo: solo en huecos/cavidades (limit.active), no en patio libre.
    if (limit.active && !variaciones.empty() && !state.fijas_buff_paths.empty()) {
        const PathD orb = normalize_outer_at_origin(variaciones.front().poly);
        if (orb.size() >= 3) {
            for (const auto& buff : state.fijas_buff_paths) {
                append_nfp_cavity_anchors(anclajes, buff, orb, limit.bounds);
            }
        }
    }

    // Máscara CUDA lazy: solo se construye si algún batch de anclas lo justifica.
    std::optional<cuda::DenseMask> cuda_board;

    for (const auto& var : variaciones) {
        std::vector<std::pair<double, double>> cand_xy;
        cand_xy.reserve(anclajes.size());
        std::vector<std::pair<double, double>> cand_pxpy;
        cand_pxpy.reserve(anclajes.size());
        for (const auto& anclaje : anclajes) {
            double px = anclaje.first - var.b_minx;
            double py = anclaje.second - var.b_miny;
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
            if (!limit.active) {
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
            } else {
                if (px + var.b_minx < -0.1 || py + var.b_miny < -0.1
                    || px + var.b_maxx > w_placa + 0.1
                    || py + var.b_maxy > h_placa + 0.1) {
                    continue;
                }
            }
            cand_xy.emplace_back(px, py);
            cand_pxpy.emplace_back(px, py);
        }
        std::vector<std::uint8_t> rejected;
        if (cuda::filter_worthwhile(cand_xy.size(), state.fijas_buff_paths.size())) {
            if (!cuda_board.has_value()) {
                cuda_board = cuda::rasterize_union_occupancy(
                    state.fijas_buff_paths, w_placa, h_placa, 8.0);
            }
            rejected = cuda::filter_against_board(
                *cuda_board, to_paths_d(var.poly_buff), cand_xy, 8.0);
        }

        for (std::size_t ci = 0; ci < cand_pxpy.size(); ++ci) {
            if (!rejected.empty() && rejected[ci] != 0) {
                continue;
            }
            const double px = cand_pxpy[ci].first;
            const double py = cand_pxpy[ci].second;

            if (comprobar_colision(
                    px,
                    py,
                    var,
                    limit,
                    state.fijas_bounds,
                    state.fijas_buff_paths,
                    state.fijas_solid_paths,
                    state.fijas_es_anfitriona,
                    kerf_radio)) {
                continue;
            }

            // NO compact_slide aquí: en cada ancla válida el slide fino recorría
            // toda la placa (minutos). Se desliza una sola vez la mejor candidata.
            double score;
            if (limit.active) {
                const double lx = px - limit.bounds.minx;
                const double ly = py - limit.bounds.miny;
                score = (ly * 1000000.0) + lx;
            } else if (es_estructural_grande) {
                score = (py * 1000000.0) + px;
            } else {
                score = (py * 1000000.0) + px;
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

    if (mejor_var == nullptr) {
        return false;
    }

    compact_slide_position(
        mejor_px,
        mejor_py,
        *mejor_var,
        margin_px,
        limit,
        state.fijas_bounds,
        state.fijas_buff_paths,
        state.fijas_solid_paths,
        state.fijas_es_anfitriona,
        kerf_radio);

    const auto cand_final = translate_rings_copy(mejor_var->poly, mejor_px, mejor_py);
    const auto cand_marks_final = translate_rings_copy(mejor_var->marks, mejor_px, mejor_py);
    const auto cand_buff_final = translate_rings_copy(mejor_var->poly_buff, mejor_px, mejor_py);

    state.fijas_buff_paths.push_back(to_paths_d(cand_buff_final));
    // Sólido perforado: orificios quedan libres para Intersection/contain.
    state.fijas_solid_paths.push_back(materialize_metal(cand_final));
    state.fijas_bounds.push_back(bounds_of_rings(cand_buff_final));
    // Anfitriona SOLO con barreno anidable grande. Piezas “grandes” macizas o con
    // tornillos NO deben saltar kerf pieza↔pieza (causa empalmes en barrenos).
    PieceIn placed_in{
        p_data.nombre,
        area_pieza,
        p_data.calibre,
        p_data.material,
        cand_final,
        cand_marks_final};
    const bool es_host = pieza_es_anfitriona_huecos(placed_in);
    state.fijas_es_anfitriona.push_back(es_host ? 1 : 0);

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
        size_t holes_max = 0;
        std::vector<PieceIn> piezas;
    };

    std::vector<GrupoOrden> lista;
    lista.reserve(grupos.size());
    for (auto& kv : grupos) {
        GrupoOrden g;
        g.nombre = kv.first;
        g.piezas = std::move(kv.second);
        // Dentro del grupo: mayor área / más barrenos primero (bloque compacto).
        std::sort(g.piezas.begin(), g.piezas.end(), [](const PieceIn& a, const PieceIn& b) {
            const double aa = piece_area(a);
            const double ab = piece_area(b);
            if (std::abs(aa - ab) > 1e-3) {
                return aa > ab;
            }
            return a.rings.size() > b.rings.size();
        });
        for (const auto& p : g.piezas) {
            g.area_max = std::max(g.area_max, piece_area(p));
            g.holes_max = std::max(g.holes_max, p.rings.size() > 0 ? p.rings.size() - 1 : 0);
        }
        lista.push_back(std::move(g));
    }

    std::sort(lista.begin(), lista.end(), [](const GrupoOrden& a, const GrupoOrden& b) {
        if (a.area_max != b.area_max) {
            return a.area_max > b.area_max;
        }
        if (a.holes_max != b.holes_max) {
            return a.holes_max > b.holes_max;
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

/** Pasillos entre piezas (AABB) y corredores pieza↔borde de placa.
 *  El Difference Clipper suele unir todo en un solo polígono; sin AABB los
 *  canales entre VFM se pierden en el “patio” y las BKT se van al margen. */
std::vector<std::vector<std::vector<Point2D>>> detectar_pasillos_entre_piezas(
    const PlacementState& state,
    double w_placa,
    double h_placa,
    double margin_px) {
    std::vector<std::vector<std::vector<Point2D>>> pasillos;
    const auto& bounds = state.fijas_bounds;
    if (bounds.empty()) {
        return pasillos;
    }

    constexpr double kPasilloMinMm = 28.0;
    // Canales anchos entre barras / patio lateral (~35"+) deben ser rellenables.
    constexpr double kPasilloMaxMm = 2200.0;
    constexpr double kOverlapMinMm = 40.0;

    auto add_rect = [&](double x0, double y0, double x1, double y1) {
        const double w = x1 - x0;
        const double h = y1 - y0;
        if (w <= 0.0 || h <= 0.0) {
            return;
        }
        const double narrow = std::min(w, h);
        const double wide = std::max(w, h);
        if (narrow < kPasilloMinMm || narrow > kPasilloMaxMm) {
            return;
        }
        if (wide < kOverlapMinMm) {
            return;
        }
        if (w * h < kVoidMinAreaMm2) {
            return;
        }
        std::vector<Point2D> ring = {
            {x0, y0},
            {x1, y0},
            {x1, y1},
            {x0, y1},
            {x0, y0},
        };
        pasillos.push_back({std::move(ring)});
    };

    for (size_t i = 0; i < bounds.size(); ++i) {
        for (size_t j = i + 1; j < bounds.size(); ++j) {
            const auto& a = bounds[i];
            const auto& b = bounds[j];
            const double ox0 = std::max(a.minx, b.minx);
            const double ox1 = std::min(a.maxx, b.maxx);
            const double oy0 = std::max(a.miny, b.miny);
            const double oy1 = std::min(a.maxy, b.maxy);

            // Hueco vertical entre A y B (mismo “carril” X).
            if (ox1 - ox0 >= kOverlapMinMm) {
                double gap0 = 0.0;
                double gap1 = 0.0;
                if (a.maxy <= b.miny) {
                    gap0 = a.maxy;
                    gap1 = b.miny;
                } else if (b.maxy <= a.miny) {
                    gap0 = b.maxy;
                    gap1 = a.miny;
                }
                const double gh = gap1 - gap0;
                if (gh >= kPasilloMinMm && gh <= kPasilloMaxMm) {
                    add_rect(ox0, gap0, ox1, gap1);
                }
            }

            // Hueco horizontal entre A y B (mismo “carril” Y).
            if (oy1 - oy0 >= kOverlapMinMm) {
                double gap0 = 0.0;
                double gap1 = 0.0;
                if (a.maxx <= b.minx) {
                    gap0 = a.maxx;
                    gap1 = b.minx;
                } else if (b.maxx <= a.minx) {
                    gap0 = b.maxx;
                    gap1 = a.minx;
                }
                const double gw = gap1 - gap0;
                if (gw >= kPasilloMinMm && gw <= kPasilloMaxMm) {
                    add_rect(gap0, oy0, gap1, oy1);
                }
            }
        }
    }

    // Corredores pieza ↔ borde útil de placa (scrap lateral / superior).
    const double plate_minx = margin_px;
    const double plate_miny = margin_px;
    const double plate_maxx = w_placa - margin_px;
    const double plate_maxy = h_placa - margin_px;
    for (const auto& b : bounds) {
        const double span_x = std::min(b.maxx, plate_maxx) - std::max(b.minx, plate_minx);
        const double span_y = std::min(b.maxy, plate_maxy) - std::max(b.miny, plate_miny);
        if (span_x >= kOverlapMinMm) {
            const double y0 = std::max(b.miny, plate_miny);
            const double y1 = std::min(b.maxy, plate_maxy);
            if (b.minx - plate_minx >= kPasilloMinMm) {
                add_rect(plate_minx, y0, b.minx, y1);
            }
            if (plate_maxx - b.maxx >= kPasilloMinMm) {
                add_rect(b.maxx, y0, plate_maxx, y1);
            }
        }
        if (span_y >= kOverlapMinMm) {
            const double x0 = std::max(b.minx, plate_minx);
            const double x1 = std::min(b.maxx, plate_maxx);
            if (b.miny - plate_miny >= kPasilloMinMm) {
                add_rect(x0, plate_miny, x1, b.miny);
            }
            if (plate_maxy - b.maxy >= kPasilloMinMm) {
                add_rect(x0, b.maxy, x1, plate_maxy);
            }
        }
    }

    auto by_narrow = [](const auto& a, const auto& b) {
        const Bounds ba = bounds_of_rings(a);
        const Bounds bb = bounds_of_rings(b);
        const double min_a = std::min(ba.maxx - ba.minx, ba.maxy - ba.miny);
        const double min_b = std::min(bb.maxx - bb.minx, bb.maxy - bb.miny);
        if (std::abs(min_a - min_b) > 1.0) {
            return min_a < min_b;
        }
        return total_area(a) < total_area(b);
    };
    std::sort(pasillos.begin(), pasillos.end(), by_narrow);
    if (pasillos.size() > static_cast<size_t>(kMaxPasillos)) {
        pasillos.resize(static_cast<size_t>(kMaxPasillos));
    }
    return pasillos;
}

/** Cavidades abiertas (perfil C/U/VFM): libre dentro del AABB de la anfitriona.
 *  No son barrenos cerrados; Clipper las fusiona con el patio y se pierden. */
struct CavidadAbierta {
    size_t host_idx = 0;
    std::vector<std::vector<Point2D>> rings;
};

std::vector<CavidadAbierta> listar_cavidades_abiertas_por_host(const PlacementState& state) {
    std::vector<CavidadAbierta> cavidades;
    const size_t n = std::min(state.hoja.piezas.size(), state.fijas_buff_paths.size());
    for (size_t i = 0; i < n; ++i) {
        const auto& placed = state.hoja.piezas[i];
        if (placed.poligonos.empty()) {
            continue;
        }
        // Solo estructurales anfitrionas (VFM etc.): no rellenar con flags de BKT.
        if (i >= state.fijas_es_anfitriona.size() || !state.fijas_es_anfitriona[i]) {
            // Fallback: solo piezas grandes / perfil abierta — no BKTs con tornillos.
            const Bounds bb = bounds_of_rings(placed.poligonos);
            const double bbox_area = (bb.maxx - bb.minx) * (bb.maxy - bb.miny);
            const double area_mat = piece_area(
                PieceIn{placed.nombre, placed.area, placed.calibre, placed.material, placed.poligonos, placed.marcas});
            const bool anfitriona =
                area_mat >= kAreaEstructuralUmbralMm2
                || (bbox_area > kVoidMinAreaMm2 * 4.0 && bbox_area > 0.0 && area_mat / bbox_area < 0.85);
            if (!anfitriona) {
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

        constexpr double pad = 0.0;
        PathD aabb;
        aabb.emplace_back(bb.minx - pad, bb.miny - pad);
        aabb.emplace_back(bb.maxx + pad, bb.miny - pad);
        aabb.emplace_back(bb.maxx + pad, bb.maxy + pad);
        aabb.emplace_back(bb.minx - pad, bb.maxy + pad);

        PathsD host_solid = materialize_metal(placed.poligonos);
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

std::vector<std::vector<std::vector<Point2D>>> detectar_cavidades_abiertas_en_anfitrionas(
    const PlacementState& state) {
    std::vector<std::vector<std::vector<Point2D>>> cavidades;
    auto listed = listar_cavidades_abiertas_por_host(state);
    cavidades.reserve(listed.size());
    for (auto& c : listed) {
        cavidades.push_back(std::move(c.rings));
    }
    return cavidades;
}

/** Paso 3: morfologia — regiones libres = placa - ocupado (con kerf). */
std::vector<std::vector<std::vector<Point2D>>> detectar_huecos(
    const PlacementState& state,
    double w_placa,
    double h_placa,
    double margin_px,
    double kerf_radio,
    bool solo_interiores_y_pasillos = false) {
    PathsD sheet = {rectangulo_placa(w_placa, h_placa, margin_px)};
    // Ocupado = SÓLIDO (no buff). Si restamos buffs y luego make_void_limit
    // encoje kerf otra vez, el patio se estrecha ~1.5×kerf y las BKT no caben
    // (regresión visible vs nest “lleno” aunque ilegales en cavidad).
    PathsD occupied;
    for (const auto& piece_paths : state.fijas_solid_paths) {
        if (!piece_paths.empty()) {
            occupied.insert(occupied.end(), piece_paths.begin(), piece_paths.end());
        }
    }
    if (occupied.empty()) {
        for (const auto& piece_paths : state.fijas_buff_paths) {
            occupied.insert(occupied.end(), piece_paths.begin(), piece_paths.end());
        }
    }

    if (occupied.empty()) {
        return solo_interiores_y_pasillos
            ? std::vector<std::vector<std::vector<Point2D>>>{}
            : std::vector<std::vector<std::vector<Point2D>>>{from_paths_d(sheet)};
    }

    PathsD occ_union = Union(occupied, FillRule::NonZero);
    if (occ_union.empty()) {
        occ_union = occupied;
    }

    PathsD free_paths = Difference(sheet, occ_union, FillRule::NonZero);
    std::vector<std::vector<std::vector<Point2D>>> interiores;
    std::vector<std::vector<std::vector<Point2D>>> exteriores;

    for (const auto& placed : state.hoja.piezas) {
        if (placed.poligonos.size() < 2) {
            continue;
        }
        for (size_t ri = 1; ri < placed.poligonos.size(); ++ri) {
            const auto& hole_ring = placed.poligonos[ri];
            const double a = polygon_area_ring(hole_ring);
            // Barrenos (< ~5 in²) no caben BKTs; saturan el cupo de huecos.
            if (a < 5.0 * 645.16) {
                continue;
            }
            interiores.push_back({hole_ring});
        }
    }

    {
        auto abiertas = detectar_cavidades_abiertas_en_anfitrionas(state);
        for (auto& c : abiertas) {
            interiores.push_back(std::move(c));
        }
    }

    auto pasillos = detectar_pasillos_entre_piezas(state, w_placa, h_placa, margin_px);

    if (!solo_interiores_y_pasillos) {
        const double area_placa = std::max(1.0, (w_placa - 2.0 * margin_px) * (h_placa - 2.0 * margin_px));
        // Antes 8%: el scrap entre barras (~1 polígono grande) se descartaba y las
        // BKT iban al borde. Ahora admitimos patio grande + siempre el mayor libre.
        const double patio_umbral = area_placa * 0.55;
        PathD mayor_libre;
        double mayor_area = 0.0;
        for (const auto& path : free_paths) {
            const double a = std::abs(Area(path));
            if (a > mayor_area) {
                mayor_area = a;
                mayor_libre = path;
            }
            if (a < kVoidMinAreaMm2 || a > patio_umbral) {
                continue;
            }
            const Bounds pb = bounds_of_paths({path});
            const double cx = (pb.minx + pb.maxx) * 0.5;
            const double cy = (pb.miny + pb.maxy) * 0.5;
            bool es_cavidad_ya_listada = false;
            for (const auto& hin : interiores) {
                const Bounds hb = bounds_of_rings(hin);
                if (bounds_contiene(hb, Bounds{cx, cy, cx, cy}, 2.0)
                    || (std::abs(total_area(hin) - a) < a * 0.15
                        && bounds_contiene(hb, pb, 5.0))) {
                    es_cavidad_ya_listada = true;
                    break;
                }
            }
            for (const auto& pas : pasillos) {
                const Bounds hb = bounds_of_rings(pas);
                if (bounds_contiene(hb, Bounds{cx, cy, cx, cy}, 2.0)) {
                    es_cavidad_ya_listada = true;
                    break;
                }
            }
            if (es_cavidad_ya_listada) {
                continue;
            }
            auto rings = from_paths_d({path});
            if (!rings.empty()) {
                exteriores.push_back(std::move(rings));
            }
        }
        // Garantizar relleno del patio dominante aunque supere el umbral.
        if (mayor_area >= kVoidMinAreaMm2) {
            const Bounds pb = bounds_of_paths({mayor_libre});
            const double cx = (pb.minx + pb.maxx) * 0.5;
            const double cy = (pb.miny + pb.maxy) * 0.5;
            bool ya = false;
            for (const auto& ex : exteriores) {
                const Bounds hb = bounds_of_rings(ex);
                if (bounds_contiene(hb, Bounds{cx, cy, cx, cy}, 2.0)
                    || (std::abs(total_area(ex) - mayor_area) < mayor_area * 0.15)) {
                    ya = true;
                    break;
                }
            }
            if (!ya) {
                auto rings = from_paths_d({mayor_libre});
                if (!rings.empty()) {
                    exteriores.push_back(std::move(rings));
                }
            }
        }
    }

    auto by_area_desc = [](const auto& a, const auto& b) {
        return total_area(a) > total_area(b);
    };
    auto by_area_asc = [](const auto& a, const auto& b) {
        return total_area(a) < total_area(b);
    };
    auto by_pasillo_primero = [](const auto& a, const auto& b) {
        const Bounds ba = bounds_of_rings(a);
        const Bounds bb = bounds_of_rings(b);
        const double min_a = std::min(ba.maxx - ba.minx, ba.maxy - ba.miny);
        const double min_b = std::min(bb.maxx - bb.minx, bb.maxy - bb.miny);
        if (std::abs(min_a - min_b) > 1.0) {
            return min_a < min_b;
        }
        return total_area(a) < total_area(b);
    };
    std::sort(interiores.begin(), interiores.end(), by_area_asc);
    if (interiores.size() > static_cast<size_t>(kMaxHuecosPorPasada)) {
        interiores.resize(static_cast<size_t>(kMaxHuecosPorPasada));
    }
    std::sort(pasillos.begin(), pasillos.end(), by_pasillo_primero);
    // Patio: bolsas chicas primero (SVGNest "sand" / Deepnest: rellenar huecos
    // antes de diluir piezas en el patio grande).
    std::sort(exteriores.begin(), exteriores.end(), by_area_asc);

    std::vector<std::vector<std::vector<Point2D>>> huecos;
    huecos.reserve(interiores.size() + pasillos.size() + exteriores.size());
    for (auto& h : interiores) {
        huecos.push_back(std::move(h));
    }
    for (auto& h : pasillos) {
        huecos.push_back(std::move(h));
    }
    for (auto& h : exteriores) {
        huecos.push_back(std::move(h));
    }
    (void)kerf_radio;
    return huecos;
}

bool pieza_cabe_en_hueco(const PieceIn& p, const Bounds& hueco_bounds, double tol = 2.0) {
    const Bounds pb = bounds_of_rings(p.rings);
    const double w_p = pb.maxx - pb.minx;
    const double h_p = pb.maxy - pb.miny;
    const double w_h = hueco_bounds.maxx - hueco_bounds.minx;
    const double h_h = hueco_bounds.maxy - hueco_bounds.miny;
    // Admitir rotación 90° (intercambio largo/ancho).
    return (w_p <= w_h + tol && h_p <= h_h + tol)
        || (h_p <= w_h + tol && w_p <= h_h + tol);
}

/** Relleno directo orificio-a-orificio (como SVGNest Ultra try_part_in_part).
 *  Prueba TODAS las piezas restantes que quepan (bridas P15 en cavidad P09_2, etc.). */
void rellenar_orificios_directo(
    PlacementState& state,
    std::vector<PieceIn>& restos,
    double w_placa,
    double h_placa,
    double kerf_custom) {
    const double kerf_radio = (kerf_custom * 25.4) / 2.0;
    constexpr double kMinHoleAreaMm2 = kHostHoleMinMm2;

    if (restos.empty()) {
        return;
    }
    std::sort(restos.begin(), restos.end(), [](const PieceIn& a, const PieceIn& b) {
        return piece_area(a) < piece_area(b);
    });

    for (int guard = 0; guard < kMaxGuardRelleno && !restos.empty(); ++guard) {
        size_t colocadas = 0;
        std::vector<PieceIn> pendientes;
        pendientes.reserve(restos.size());
        for (auto& p : restos) {
            if (pieza_es_anfitriona_huecos(p)) {
                pendientes.push_back(std::move(p));
                continue;
            }
            // PIP selectivo: no meter guests demasiado grandes en barrenos.
            if (piece_area(p) > kPartInPartMaxGuestMm2) {
                pendientes.push_back(std::move(p));
                continue;
            }
            bool placed = false;
            for (const auto& host : state.hoja.piezas) {
                if (host.poligonos.size() < 2) {
                    continue;
                }
                for (size_t hi = 1; hi < host.poligonos.size(); ++hi) {
                    if (polygon_area_ring(host.poligonos[hi]) < kMinHoleAreaMm2) {
                        continue;
                    }
                    const Bounds hb = bounds_of_rings({host.poligonos[hi]});
                    if (!pieza_cabe_en_hueco(p, hb)) {
                        continue;
                    }
                    const LimitContext hole_limit = make_hole_limit(host.poligonos[hi], kerf_radio);
                    if (!hole_limit.active) {
                        continue;
                    }
                    if (colocar_pieza(p, state, w_placa, h_placa, kerf_radio, 0.0, hole_limit)) {
                        placed = true;
                        ++colocadas;
                        break;
                    }
                }
                if (placed) {
                    break;
                }
            }
            if (!placed) {
                pendientes.push_back(std::move(p));
            }
        }
        restos = std::move(pendientes);
        if (colocadas == 0) {
            break;
        }
    }
}

/** Relleno de canales abiertos C/VFM (AABB − metal), no barrenos.
 *  Balance por anfitriona: no saturar VFM inferior dejando la superior vacía. */
void rellenar_cavidades_abiertas_directo(
    PlacementState& state,
    std::vector<PieceIn>& restos,
    double w_placa,
    double h_placa,
    double kerf_custom) {
    const double kerf_radio = (kerf_custom * 25.4) / 2.0;

    std::vector<PieceIn> grandes;
    std::vector<PieceIn> pequenas;
    for (const auto& p : restos) {
        if (pieza_va_en_fase_estructural(p)) {
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
        return piece_area(a) < piece_area(b);
    });

    const size_t n_hosts = state.hoja.piezas.size();
    std::vector<int> fill_por_host(n_hosts, 0);
    std::vector<char> host_agotado(n_hosts, 0);

    auto place_one_in_host = [&](size_t host_idx, bool solo_alto) -> bool {
        auto all = listar_cavidades_abiertas_por_host(state);
        std::vector<CavidadAbierta> cavs;
        for (auto& c : all) {
            if (c.host_idx != host_idx) {
                continue;
            }
            const Bounds hb = bounds_of_rings(c.rings);
            if (solo_alto) {
                const double narrow = std::min(hb.maxx - hb.minx, hb.maxy - hb.miny);
                if (narrow < 6.0 * 25.4) {
                    continue;
                }
            }
            cavs.push_back(std::move(c));
        }
        if (cavs.empty()) {
            return false;
        }
        std::sort(cavs.begin(), cavs.end(), [](const CavidadAbierta& a, const CavidadAbierta& b) {
            const Bounds ba = bounds_of_rings(a.rings);
            const Bounds bb = bounds_of_rings(b.rings);
            // Preferir cavidades todavía “vacías” (menor área de AABB no ayuda);
            // priorizar canal largo.
            const double la = std::max(ba.maxx - ba.minx, ba.maxy - ba.miny);
            const double lb = std::max(bb.maxx - bb.minx, bb.maxy - bb.miny);
            return la > lb;
        });

        for (const auto& cav : cavs) {
            const Bounds hb = bounds_of_rings(cav.rings);
            const LimitContext limit = make_void_limit(cav.rings, kerf_radio);
            if (!limit.active) {
                continue;
            }
            // Densificar ESTE canal antes de pasar a otro (máx. 6 piezas / turno).
            int en_canal = 0;
            bool progreso = true;
            while (progreso && en_canal < 6 && !pequenas.empty()) {
                progreso = false;
                for (size_t pi = 0; pi < pequenas.size(); ++pi) {
                    PieceIn& p = pequenas[pi];
                    if (!pieza_cabe_en_hueco(p, hb, /*tol=*/1.0)) {
                        continue;
                    }
                    if (solo_alto) {
                        const Bounds pb = bounds_of_rings(p.rings);
                        const double thin = 3.80 * 25.4;
                        if (std::min(pb.maxx - pb.minx, pb.maxy - pb.miny) <= thin) {
                            continue;
                        }
                    }
                    if (colocar_pieza(p, state, w_placa, h_placa, kerf_radio, 0.0, limit)) {
                        ++fill_por_host[host_idx];
                        ++en_canal;
                        pequenas.erase(pequenas.begin() + static_cast<std::ptrdiff_t>(pi));
                        progreso = true;
                        break;
                    }
                }
            }
            if (en_canal > 0) {
                return true;
            }
        }
        return false;
    };

    auto pack_balanced = [&](bool solo_alto) {
        std::fill(host_agotado.begin(), host_agotado.end(), 0);
        for (int guard = 0; guard < kMaxGuardRelleno && !pequenas.empty(); ++guard) {
            // Elegir anfitriona con cavidades y menor relleno.
            auto all = listar_cavidades_abiertas_por_host(state);
            std::vector<char> tiene(n_hosts, 0);
            for (const auto& c : all) {
                if (c.host_idx < n_hosts) {
                    tiene[c.host_idx] = 1;
                }
            }
            int best = -1;
            int best_fill = std::numeric_limits<int>::max();
            for (size_t h = 0; h < n_hosts; ++h) {
                if (!tiene[h] || host_agotado[h]) {
                    continue;
                }
                if (fill_por_host[h] < best_fill) {
                    best_fill = fill_por_host[h];
                    best = static_cast<int>(h);
                }
            }
            if (best < 0) {
                break;
            }
            if (!place_one_in_host(static_cast<size_t>(best), solo_alto)) {
                host_agotado[static_cast<size_t>(best)] = 1;
            }
        }
    };

    pack_balanced(/*solo_alto=*/true);
    pack_balanced(/*solo_alto=*/false);

    restos.clear();
    restos.reserve(grandes.size() + pequenas.size());
    for (auto& p : grandes) {
        restos.push_back(std::move(p));
    }
    for (auto& p : pequenas) {
        restos.push_back(std::move(p));
    }
}

/** Paso 4: llenar huecos colocando EN EL MISMO estado de la placa (con colisiones). */
void rellenar_huecos_con_pequenas(
    PlacementState& state,
    std::vector<PieceIn>& restos,
    double w_placa,
    double h_placa,
    double kerf_custom,
    double margin_custom,
    bool solo_interiores_y_pasillos = false) {
    const double kerf_radio = (kerf_custom * 25.4) / 2.0;
    const double margin_px = margin_custom > 0.0 ? (margin_custom * 25.4) : 0.0;

    std::vector<PieceIn> grandes;
    std::vector<PieceIn> pequenas;
    for (const auto& p : restos) {
        if (pieza_va_en_fase_estructural(p)) {
            grandes.push_back(p);
        } else {
            pequenas.push_back(p);
        }
    }
    if (pequenas.empty()) {
        restos = std::move(grandes);
        return;
    }

    // Pequeñas primero: caben mejor en pasillos/cavidades irregulares.
    std::sort(pequenas.begin(), pequenas.end(), [](const PieceIn& a, const PieceIn& b) {
        return piece_area(a) < piece_area(b);
    });

    // Un hueco prioritario por pasada y redesdetectar: llena pasillos/cavidades
    // antes de “tragarse” las piezas en el vacío grande al final.
    for (int guard = 0; guard < kMaxGuardRelleno && !pequenas.empty(); ++guard) {
        auto huecos = detectar_huecos(
            state, w_placa, h_placa, margin_px, kerf_radio, solo_interiores_y_pasillos);
        if (huecos.empty()) {
            break;
        }
        if (huecos.size() > static_cast<size_t>(kMaxHuecosPorPasada)) {
            huecos.resize(static_cast<size_t>(kMaxHuecosPorPasada));
        }

        size_t colocadas_pasada = 0;
        for (const auto& hueco_rings : huecos) {
            const Bounds hb = bounds_of_rings(hueco_rings);
            const LimitContext limit = make_void_limit(hueco_rings, kerf_radio);
            if (!limit.active) {
                continue;
            }

            std::vector<PieceIn> pendientes;
            pendientes.reserve(pequenas.size());
            for (auto& p : pequenas) {
                if (!pieza_cabe_en_hueco(p, hb)) {
                    pendientes.push_back(std::move(p));
                    continue;
                }
                if (colocar_pieza(p, state, w_placa, h_placa, kerf_radio, 0.0, limit)) {
                    ++colocadas_pasada;
                } else {
                    pendientes.push_back(std::move(p));
                }
            }
            pequenas = std::move(pendientes);
            // Seguir con el siguiente hueco en la misma pasada (antes
            // cortaba al primer éxito y muchas cavidades quedaban vacías).
        }
        if (colocadas_pasada == 0) {
            break;
        }
    }

    std::vector<PieceIn> sin_colocar;
    for (auto& p : pequenas) {
        sin_colocar.push_back(std::move(p));
    }
    for (auto& p : grandes) {
        sin_colocar.push_back(std::move(p));
    }
    restos = std::move(sin_colocar);
}

/** Compactación final: 1 pasada slide L/B sobre piezas ya colocadas (bajo costo). */
void compact_slide_sheet_final(
    PlacementState& state,
    double w_placa,
    double h_placa,
    double kerf_radio,
    double margin_px) {
    const size_t n = state.hoja.piezas.size();
    if (n < 2
        || state.fijas_buff_paths.size() != n
        || state.fijas_solid_paths.size() != n
        || state.fijas_bounds.size() != n
        || state.fijas_es_anfitriona.size() != n) {
        return;
    }
    const LimitContext sheet_limit = make_limit_context(std::nullopt, margin_px);
    std::vector<size_t> order(n);
    std::iota(order.begin(), order.end(), 0);
    std::stable_sort(order.begin(), order.end(), [&](size_t a, size_t b) {
        return state.hoja.piezas[a].area > state.hoja.piezas[b].area;
    });

    for (const size_t idx : order) {
        PlacementState others;
        others.fijas_buff_paths.reserve(n - 1);
        others.fijas_solid_paths.reserve(n - 1);
        others.fijas_bounds.reserve(n - 1);
        others.fijas_es_anfitriona.reserve(n - 1);
        for (size_t j = 0; j < n; ++j) {
            if (j == idx) {
                continue;
            }
            others.fijas_buff_paths.push_back(state.fijas_buff_paths[j]);
            others.fijas_solid_paths.push_back(state.fijas_solid_paths[j]);
            others.fijas_bounds.push_back(state.fijas_bounds[j]);
            others.fijas_es_anfitriona.push_back(state.fijas_es_anfitriona[j]);
        }

        auto& piece = state.hoja.piezas[idx];
        if (piece.poligonos.empty()) {
            continue;
        }
        const Bounds bb = bounds_of_rings(piece.poligonos);
        Variation var;
        var.poly = translate_rings_copy(piece.poligonos, -bb.minx, -bb.miny);
        var.marks = translate_rings_copy(piece.marcas, -bb.minx, -bb.miny);
        var.poly_buff = buffer_rings(var.poly, kerf_radio);
        if (var.poly_buff.empty()) {
            var.poly_buff = buffer_rings({var.poly.front()}, kerf_radio);
        }
        if (var.poly_buff.empty()) {
            continue;
        }
        const Bounds vb = bounds_of_rings(var.poly_buff);
        const Bounds mb = bounds_of_rings(var.poly);
        var.w = bb.maxx - bb.minx;
        var.h = bb.maxy - bb.miny;
        var.b_minx = vb.minx;
        var.b_miny = vb.miny;
        var.b_maxx = vb.maxx;
        var.b_maxy = vb.maxy;
        var.m_minx = mb.minx;
        var.m_miny = mb.miny;
        var.m_maxx = mb.maxx;
        var.m_maxy = mb.maxy;

        double px = bb.minx;
        double py = bb.miny;
        compact_slide_position(
            px,
            py,
            var,
            margin_px,
            sheet_limit,
            others.fijas_bounds,
            others.fijas_buff_paths,
            others.fijas_solid_paths,
            others.fijas_es_anfitriona,
            kerf_radio);
        const double dx = px - bb.minx;
        const double dy = py - bb.miny;
        if (std::abs(dx) < 1e-6 && std::abs(dy) < 1e-6) {
            continue;
        }
        auto poly_final = translate_rings_copy(var.poly, px, py);
        auto marks_final = translate_rings_copy(var.marks, px, py);
        auto buff_final = translate_rings_copy(var.poly_buff, px, py);
        piece.poligonos = std::move(poly_final);
        piece.marcas = std::move(marks_final);
        state.fijas_buff_paths[idx] = to_paths_d(buff_final);
        state.fijas_solid_paths[idx] = materialize_metal(piece.poligonos);
        state.fijas_bounds[idx] = bounds_of_rings(buff_final);
        (void)w_placa;
        (void)h_placa;
    }
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

    const bool es_placa_completa = !limite_rings || limite_rings->empty();
    const double kerf_radio = (kerf_override * 25.4) / 2.0;

    // Empaque restringido (RTZ/hueco Python): una sola pasada como antes.
    if (!es_placa_completa) {
        auto ordenadas = orden_pizarron(piezas);
        auto [state, restos] = colocar_en_orden(
            ordenadas,
            w_placa,
            h_placa,
            kerf_override,
            margin_override,
            limite_rings);
        finalizar_eficiencia(state.hoja, w_placa, h_placa);
        out.hoja = std::move(state.hoja);
        out.restos = std::move(restos);
        return out;
    }

    // Placa madre completa:
    // 1) estructurales primero (generan cavidades interiores)
    // 2) rellenar esos huecos con TODAS las piezas pequeñas
    // 3) colocar remanentes en el resto libre de la placa
    // Hosts (barreno anidable grande) primero; el resto puede ir a part-in-part.
    std::vector<PieceIn> hosts;
    std::vector<PieceIn> no_hosts;
    hosts.reserve(piezas.size());
    no_hosts.reserve(piezas.size());
    for (const auto& p : piezas) {
        if (pieza_es_anfitriona_huecos(p)) {
            hosts.push_back(p);
        } else {
            no_hosts.push_back(p);
        }
    }

    PlacementState state;
    std::vector<PieceIn> restos;

    if (!hosts.empty()) {
        auto orden_hosts = orden_pizarron(std::move(hosts));
        auto [st, r] = colocar_en_orden(
            orden_hosts,
            w_placa,
            h_placa,
            kerf_override,
            margin_override,
            std::nullopt);
        state = std::move(st);
        no_hosts.insert(no_hosts.end(), r.begin(), r.end());
    }

    // Part-in-part ANTES del patio: bridas/medianas entran a barrenos host.
    if (!no_hosts.empty()) {
        for (int pass = 0; pass < 6 && !no_hosts.empty(); ++pass) {
            const size_t antes = no_hosts.size();
            rellenar_orificios_directo(state, no_hosts, w_placa, h_placa, kerf_override);
            if (no_hosts.size() >= antes) {
                break;
            }
        }
    }

    std::vector<PieceIn> estructurales;
    std::vector<PieceIn> pool_peq;
    for (auto& p : no_hosts) {
        if (pieza_va_en_fase_estructural(p)) {
            estructurales.push_back(std::move(p));
        } else {
            pool_peq.push_back(std::move(p));
        }
    }
    if (!estructurales.empty()) {
        auto orden_est = orden_pizarron(std::move(estructurales));
        const LimitContext limit = make_limit_context(std::nullopt, margin_px);
        std::vector<PieceIn> cola = std::move(orden_est);
        while (!cola.empty()) {
            PieceIn p_data = std::move(cola.front());
            cola.erase(cola.begin());
            if (!colocar_pieza(p_data, state, w_placa, h_placa, kerf_radio, margin_px, limit)) {
                std::vector<PieceIn> one{std::move(p_data)};
                rellenar_orificios_directo(state, one, w_placa, h_placa, kerf_override);
                if (!one.empty()) {
                    restos.push_back(std::move(one.front()));
                }
            }
            // PIP de pool_peq solo en fases dedicadas (antes/después); hacerlo tras
            // cada estructural × todos los anillos era O(n²) y bloqueaba minutos.
        }
    }

    // Fase 1.5a: canales abiertos C/VFM (AABB-metal).
    if (!pool_peq.empty()) {
        for (int pass = 0; pass < 8 && !pool_peq.empty(); ++pass) {
            const size_t antes = pool_peq.size();
            rellenar_cavidades_abiertas_directo(state, pool_peq, w_placa, h_placa, kerf_override);
            if (pool_peq.size() >= antes) {
                break;
            }
        }
    }
    // Fase 1.5b: orificios otra pasada.
    if (!pool_peq.empty()) {
        for (int pass = 0; pass < 4 && !pool_peq.empty(); ++pass) {
            const size_t antes = pool_peq.size();
            rellenar_orificios_directo(state, pool_peq, w_placa, h_placa, kerf_override);
            if (pool_peq.size() >= antes) {
                break;
            }
        }
    }

    // Fase 2a: SOLO cavidades abiertas/cerradas + pasillos (nada de patio libre).
    if (!pool_peq.empty()) {
        for (int pass = 0; pass < 6 && !pool_peq.empty(); ++pass) {
            const size_t antes = pool_peq.size();
            rellenar_huecos_con_pequenas(
                state,
                pool_peq,
                w_placa,
                h_placa,
                kerf_override,
                margin_override,
                /*solo_interiores_y_pasillos=*/true);
            if (pool_peq.size() >= antes) {
                break;
            }
        }
    }
    // Fase 2b: exteriores + patio dominante (scrap grande entre/alrededor estructurales).
    if (!pool_peq.empty()) {
        for (int pass = 0; pass < 6 && !pool_peq.empty(); ++pass) {
            const size_t antes = pool_peq.size();
            rellenar_huecos_con_pequenas(
                state,
                pool_peq,
                w_placa,
                h_placa,
                kerf_override,
                margin_override,
                /*solo_interiores_y_pasillos=*/false);
            if (pool_peq.size() >= antes) {
                break;
            }
        }
    }

    // Restos: placa libre; morfología intercalada cada 8 (Clipper caro con anillos).
    if (!pool_peq.empty()) {
        auto orden_peq = orden_pizarron(std::move(pool_peq));
        const LimitContext limit = make_limit_context(std::nullopt, margin_px);
        std::vector<PieceIn> cola = std::move(orden_peq);
        size_t since_fill = 0;
        while (!cola.empty()) {
            PieceIn p_data = std::move(cola.front());
            cola.erase(cola.begin());
            if (!colocar_pieza(p_data, state, w_placa, h_placa, kerf_radio, margin_px, limit)) {
                restos.push_back(std::move(p_data));
                continue;
            }
            ++since_fill;
            if (!cola.empty() && since_fill >= 8) {
                since_fill = 0;
                rellenar_huecos_con_pequenas(
                    state,
                    cola,
                    w_placa,
                    h_placa,
                    kerf_override,
                    margin_override,
                    /*solo_interiores_y_pasillos=*/true);
            }
        }
    }

    // Última pasada: residuales en cavidades/pasillos/patio.
    if (!restos.empty()) {
        for (int pass = 0; pass < 4 && !restos.empty(); ++pass) {
            const size_t antes = restos.size();
            rellenar_huecos_con_pequenas(
                state,
                restos,
                w_placa,
                h_placa,
                kerf_override,
                margin_override,
                /*solo_interiores_y_pasillos=*/false);
            if (restos.size() >= antes) {
                break;
            }
        }
    }

    // Un retry barato solo con chicos restantes (sin rehacer el nest).
    if (!restos.empty()) {
        std::vector<PieceIn> chicos;
        std::vector<PieceIn> grandes_resto;
        chicos.reserve(restos.size());
        for (auto& p : restos) {
            if (piece_area(p) <= kPartInPartMaxGuestMm2 && !pieza_es_anfitriona_huecos(p)) {
                chicos.push_back(std::move(p));
            } else {
                grandes_resto.push_back(std::move(p));
            }
        }
        if (!chicos.empty()) {
            rellenar_orificios_directo(state, chicos, w_placa, h_placa, kerf_override);
            rellenar_cavidades_abiertas_directo(state, chicos, w_placa, h_placa, kerf_override);
            rellenar_huecos_con_pequenas(
                state,
                chicos,
                w_placa,
                h_placa,
                kerf_override,
                margin_override,
                /*solo_interiores_y_pasillos=*/false);
        }
        restos = std::move(grandes_resto);
        restos.insert(restos.end(), chicos.begin(), chicos.end());
    }

    compact_slide_sheet_final(state, w_placa, h_placa, kerf_radio, margin_px);

    finalizar_eficiencia(state.hoja, w_placa, h_placa);
    out.hoja = std::move(state.hoja);
    out.restos = std::move(restos);
    return out;
}

}  // namespace arga
