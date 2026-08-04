#include "arga_nest/export_cam.hpp"

#include "arga_nest/certifier.hpp"

#include <algorithm>
#include <cmath>
#include <string>
#include <vector>

namespace arga::core {
namespace {

double dist2(const Point2D& a, const Point2D& b) {
    const double dx = a.x - b.x;
    const double dy = a.y - b.y;
    return dx * dx + dy * dy;
}

double seg_len(const Point2D& a, const Point2D& b) {
    return std::sqrt(dist2(a, b));
}

Point2D closest_on_seg(const Point2D& p, const Point2D& a, const Point2D& b) {
    const double vx = b.x - a.x;
    const double vy = b.y - a.y;
    const double w = vx * vx + vy * vy;
    if (w < 1e-18) {
        return a;
    }
    double t = ((p.x - a.x) * vx + (p.y - a.y) * vy) / w;
    t = std::max(0.0, std::min(1.0, t));
    return {a.x + t * vx, a.y + t * vy};
}

bool almost_parallel(
    const Point2D& a0,
    const Point2D& a1,
    const Point2D& b0,
    const Point2D& b1,
    double cos_tol = 0.98) {
    Point2D u{a1.x - a0.x, a1.y - a0.y};
    Point2D v{b1.x - b0.x, b1.y - b0.y};
    const double nu = std::sqrt(u.x * u.x + u.y * u.y);
    const double nv = std::sqrt(v.x * v.x + v.y * v.y);
    if (nu < 1e-9 || nv < 1e-9) {
        return false;
    }
    return std::abs((u.x * v.x + u.y * v.y) / (nu * nv)) >= cos_tol;
}

double overlap_len(
    const Point2D& a0,
    const Point2D& a1,
    const Point2D& b0,
    const Point2D& b1) {
    Point2D u{a1.x - a0.x, a1.y - a0.y};
    const double nu = std::sqrt(u.x * u.x + u.y * u.y);
    if (nu < 1e-9) {
        return 0.0;
    }
    u.x /= nu;
    u.y /= nu;
    auto proj = [&](const Point2D& p) {
        return (p.x - a0.x) * u.x + (p.y - a0.y) * u.y;
    };
    double p0 = proj(b0);
    double p1 = proj(b1);
    if (p0 > p1) {
        std::swap(p0, p1);
    }
    const double lo = std::max(0.0, p0);
    const double hi = std::min(nu, p1);
    return std::max(0.0, hi - lo);
}

double min_seg_gap(
    const Point2D& a0,
    const Point2D& a1,
    const Point2D& b0,
    const Point2D& b1) {
    double d = 1e100;
    auto consider = [&](const Point2D& p, const Point2D& q0, const Point2D& q1) {
        const auto c = closest_on_seg(p, q0, q1);
        d = std::min(d, std::sqrt(dist2(p, c)));
    };
    consider(a0, b0, b1);
    consider(a1, b0, b1);
    consider(b0, a0, a1);
    consider(b1, a0, a1);
    return d;
}

bool edge_matches_shared(
    const Point2D& e0,
    const Point2D& e1,
    const Point2D& s0,
    const Point2D& s1,
    double tol_mm) {
    if (!almost_parallel(e0, e1, s0, s1)) {
        return false;
    }
    if (min_seg_gap(e0, e1, s0, s1) > tol_mm) {
        return false;
    }
    const double el = seg_len(e0, e1);
    if (el < 1.0) {
        return false;
    }
    return overlap_len(e0, e1, s0, s1) >= std::min(el * 0.55, el - 0.5);
}

bool edge_is_shared(
    const Point2D& e0,
    const Point2D& e1,
    const CommonLineReport& common,
    const CommonCutMergeReport& merged,
    double tol_mm) {
    for (const auto& p : common.pairs) {
        if (p.has_geom && edge_matches_shared(e0, e1, p.p0, p.p1, tol_mm)) {
            return true;
        }
    }
    for (const auto& path : merged.paths) {
        for (std::size_t i = 0; i + 1 < path.points.size(); ++i) {
            if (edge_matches_shared(e0, e1, path.points[i], path.points[i + 1], tol_mm)) {
                return true;
            }
        }
    }
    return false;
}

struct OuterSplit {
    std::vector<std::vector<Point2D>> parts;
    bool omitted_any = false;
};

OuterSplit split_outer_omitting_shared(
    const std::vector<Point2D>& ring_in,
    const CommonLineReport& common,
    const CommonCutMergeReport& merged,
    double tol_mm) {
    OuterSplit out;
    if (ring_in.size() < 3) {
        return out;
    }
    std::vector<Point2D> ring = ring_in;
    if (seg_len(ring.front(), ring.back()) > 1e-6) {
        ring.push_back(ring.front());
    }
    const int nseg = static_cast<int>(ring.size()) - 1;
    if (nseg < 3) {
        return out;
    }

    std::vector<char> shared(static_cast<std::size_t>(nseg), 0);
    int n_shared = 0;
    for (int i = 0; i < nseg; ++i) {
        if (edge_is_shared(ring[i], ring[i + 1], common, merged, tol_mm)) {
            shared[static_cast<std::size_t>(i)] = 1;
            ++n_shared;
        }
    }
    if (n_shared == 0) {
        out.parts.push_back(ring_in);
        out.omitted_any = false;
        return out;
    }
    out.omitted_any = true;

    int start = 0;
    for (int i = 0; i < nseg; ++i) {
        if (shared[static_cast<std::size_t>(i)]) {
            start = (i + 1) % nseg;
            break;
        }
    }

    auto flush = [&](std::vector<Point2D>& run) {
        if (run.size() >= 2) {
            out.parts.push_back(run);
        }
        run.clear();
    };

    std::vector<Point2D> run;
    for (int k = 0; k < nseg; ++k) {
        const int i = (start + k) % nseg;
        if (shared[static_cast<std::size_t>(i)]) {
            flush(run);
            continue;
        }
        if (run.empty()) {
            run.push_back(ring[i]);
        }
        run.push_back(ring[i + 1]);
    }
    flush(run);
    return out;
}

}  // namespace

DxfDocument dxf_from_pack_with_common_paths(
    const PackResult& result,
    const CommonLineReport& common,
    const CommonCutMergeReport& merged,
    const std::string& outer_layer,
    bool machine_path,
    double edge_match_tol_mm) {
    DxfDocument doc;
    const bool omit = machine_path && (!merged.paths.empty() || !common.pairs.empty());

    for (const auto& piece : result.hoja.piezas) {
        for (std::size_t ri = 0; ri < piece.poligonos.size(); ++ri) {
            if (ri == 0 && omit) {
                const auto split = split_outer_omitting_shared(
                    piece.poligonos[0], common, merged, edge_match_tol_mm);
                for (const auto& pts : split.parts) {
                    DxfEntity e;
                    e.layer = outer_layer;
                    e.closed = !split.omitted_any;
                    e.points = pts;
                    doc.entities.push_back(std::move(e));
                }
                continue;
            }
            DxfEntity e;
            e.layer = (ri == 0) ? outer_layer : "CUT_INNER";
            e.closed = true;
            e.points = piece.poligonos[ri];
            doc.entities.push_back(std::move(e));
        }
        for (const auto& mark : piece.marcas) {
            DxfEntity e;
            e.layer = "MARK";
            e.closed = false;
            e.points = mark;
            doc.entities.push_back(std::move(e));
        }
    }

    for (const auto& path : merged.paths) {
        if (path.points.size() < 2) {
            continue;
        }
        DxfEntity e;
        e.layer = "COMMON_CUT";
        e.closed = false;
        e.points = path.points;
        doc.entities.push_back(std::move(e));
    }
    return doc;
}

DxfDocument dxf_from_pack_with_common_line(
    const PackResult& result,
    const CommonLineReport& common,
    const std::string& outer_layer,
    bool machine_path,
    double edge_match_tol_mm) {
    const auto merged = merge_common_cut_paths(common);
    return dxf_from_pack_with_common_paths(
        result, common, merged, outer_layer, machine_path, edge_match_tol_mm);
}

DxfCertifyResult certify_dxf_ascii(const std::string& dxf_text) {
    DxfCertifyResult out;
    DxfDocument doc;
    std::string err;
    if (!dxf_parse_ascii(dxf_text, doc, err)) {
        out.issues.push_back(std::string("parse: ") + err);
        return out;
    }
    out.entity_count = static_cast<int>(doc.entities.size());
    PackResult fake;
    for (const auto& e : doc.entities) {
        if (e.layer == "COMMON_CUT") {
            ++out.common_cut_segments;
            continue;
        }
        if (e.layer == "MARK" || e.layer == "CUT_INNER") {
            continue;
        }
        if (e.closed && e.points.size() >= 3) {
            ++out.closed_outers;
            PieceOut p;
            p.nombre = "E" + std::to_string(out.closed_outers);
            p.poligonos = {e.points};
            fake.hoja.piezas.push_back(std::move(p));
        } else if (!e.closed && e.points.size() >= 2) {
            ++out.open_outer_segments;
        }
    }
    out.machine_path = out.common_cut_segments > 0 && out.open_outer_segments > 0;

    if (out.closed_outers <= 0 && out.open_outer_segments <= 0) {
        out.issues.push_back("no_outer_geometry");
        return out;
    }

    // Certify geométrico solo si hay contornos cerrados (modo clásico).
    // En machine_path los outers abiertos + COMMON_CUT son válidos sin overlap check.
    if (out.closed_outers > 0) {
        double maxx = 0, maxy = 0;
        for (const auto& p : fake.hoja.piezas) {
            for (const auto& q : p.poligonos[0]) {
                maxx = std::max(maxx, q.x);
                maxy = std::max(maxy, q.y);
            }
        }
        const auto cert = certify_sheet(fake, maxx + 10.0, maxy + 10.0, 0.2, 1.0);
        if (!cert.ok) {
            for (const auto& iss : cert.issues) {
                out.issues.push_back(iss.code + ":" + iss.detail);
            }
            return out;
        }
    } else if (out.common_cut_segments <= 0) {
        out.issues.push_back("open_outers_without_common_cut");
        return out;
    }

    out.ok = true;
    return out;
}

}  // namespace arga::core
