#include "arga_nest/common_line.hpp"

#include <algorithm>
#include <cmath>
#include <vector>

namespace arga::core {
namespace {

struct Seg {
    Point2D a, b;
    double len = 0;
};

double dist2(const Point2D& p, const Point2D& q) {
    const double dx = p.x - q.x;
    const double dy = p.y - q.y;
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

double seg_gap(const Seg& s, const Seg& t) {
    double d = 1e100;
    auto consider = [&](const Point2D& p, const Seg& other) {
        const auto q = closest_on_seg(p, other.a, other.b);
        d = std::min(d, std::sqrt(dist2(p, q)));
    };
    consider(s.a, t);
    consider(s.b, t);
    consider(t.a, s);
    consider(t.b, s);
    return d;
}

bool almost_parallel(const Seg& s, const Seg& t, double cos_tol = 0.98) {
    Point2D u{s.b.x - s.a.x, s.b.y - s.a.y};
    Point2D v{t.b.x - t.a.x, t.b.y - t.a.y};
    const double nu = std::sqrt(u.x * u.x + u.y * u.y);
    const double nv = std::sqrt(v.x * v.x + v.y * v.y);
    if (nu < 1e-9 || nv < 1e-9) {
        return false;
    }
    const double c = std::abs((u.x * v.x + u.y * v.y) / (nu * nv));
    return c >= cos_tol;
}

double overlap_length(const Seg& s, const Seg& t) {
    Point2D u{s.b.x - s.a.x, s.b.y - s.a.y};
    const double nu = std::sqrt(u.x * u.x + u.y * u.y);
    if (nu < 1e-9) {
        return 0;
    }
    u.x /= nu;
    u.y /= nu;
    auto proj = [&](const Point2D& p) {
        return (p.x - s.a.x) * u.x + (p.y - s.a.y) * u.y;
    };
    double a0 = 0.0;
    double a1 = nu;
    double b0 = proj(t.a);
    double b1 = proj(t.b);
    if (b0 > b1) {
        std::swap(b0, b1);
    }
    const double lo = std::max(a0, b0);
    const double hi = std::min(a1, b1);
    return std::max(0.0, hi - lo);
}

bool midline_of_overlap(
    const Seg& s,
    const Seg& t,
    Point2D& out0,
    Point2D& out1,
    double& out_len) {
    Point2D u{s.b.x - s.a.x, s.b.y - s.a.y};
    const double nu = std::sqrt(u.x * u.x + u.y * u.y);
    if (nu < 1e-9) {
        return false;
    }
    u.x /= nu;
    u.y /= nu;
    auto proj = [&](const Point2D& p) {
        return (p.x - s.a.x) * u.x + (p.y - s.a.y) * u.y;
    };
    double b0 = proj(t.a);
    double b1 = proj(t.b);
    if (b0 > b1) {
        std::swap(b0, b1);
    }
    const double lo = std::max(0.0, b0);
    const double hi = std::min(nu, b1);
    if (hi - lo < 1e-6) {
        return false;
    }
    Point2D s0{s.a.x + u.x * lo, s.a.y + u.y * lo};
    Point2D s1{s.a.x + u.x * hi, s.a.y + u.y * hi};
    Point2D t0 = closest_on_seg(s0, t.a, t.b);
    Point2D t1 = closest_on_seg(s1, t.a, t.b);
    out0 = {(s0.x + t0.x) * 0.5, (s0.y + t0.y) * 0.5};
    out1 = {(s1.x + t1.x) * 0.5, (s1.y + t1.y) * 0.5};
    out_len = seg_len(out0, out1);
    return out_len > 1e-6;
}

std::vector<Seg> outer_segs(const PieceOut& p) {
    std::vector<Seg> out;
    if (p.poligonos.empty()) {
        return out;
    }
    const auto& ring = p.poligonos[0];
    for (std::size_t i = 0; i + 1 < ring.size(); ++i) {
        Seg s;
        s.a = ring[i];
        s.b = ring[i + 1];
        s.len = seg_len(s.a, s.b);
        if (s.len >= 1.0) {
            out.push_back(s);
        }
    }
    return out;
}

bool endpoints_close(const Point2D& a, const Point2D& b, double tol) {
    return dist2(a, b) <= tol * tol;
}

bool colinear_mergeable(
    const Point2D& a0,
    const Point2D& a1,
    const Point2D& b0,
    const Point2D& b1,
    double join_tol) {
    Seg s{a0, a1, seg_len(a0, a1)};
    Seg t{b0, b1, seg_len(b0, b1)};
    if (!almost_parallel(s, t, 0.995)) {
        return false;
    }
    if (seg_gap(s, t) > join_tol) {
        return false;
    }
    // Extremos deben estar cerca o solaparse en proyección
    return endpoints_close(a0, b0, join_tol) || endpoints_close(a0, b1, join_tol) ||
        endpoints_close(a1, b0, join_tol) || endpoints_close(a1, b1, join_tol) ||
        overlap_length(s, t) > 0.5;
}

}  // namespace

CommonLineReport detect_common_lines(
    const PackResult& result,
    double max_gap_mm,
    double min_length_mm) {
    CommonLineReport rep;
    const auto& pcs = result.hoja.piezas;
    for (std::size_t i = 0; i < pcs.size(); ++i) {
        const auto segs_i = outer_segs(pcs[i]);
        for (std::size_t j = i + 1; j < pcs.size(); ++j) {
            const auto segs_j = outer_segs(pcs[j]);
            double best_len = 0.0;
            double best_gap = 1e100;
            Point2D best_p0{}, best_p1{};
            bool have_geom = false;
            for (const auto& a : segs_i) {
                for (const auto& b : segs_j) {
                    if (!almost_parallel(a, b)) {
                        continue;
                    }
                    const double g = seg_gap(a, b);
                    if (g > max_gap_mm) {
                        continue;
                    }
                    Point2D m0, m1;
                    double ml = 0.0;
                    if (!midline_of_overlap(a, b, m0, m1, ml)) {
                        continue;
                    }
                    if (ml > best_len) {
                        best_len = ml;
                        best_gap = g;
                        best_p0 = m0;
                        best_p1 = m1;
                        have_geom = true;
                    }
                }
            }
            if (best_len >= min_length_mm) {
                CommonLinePair pair;
                pair.a = pcs[i].nombre;
                pair.b = pcs[j].nombre;
                pair.length_mm = best_len;
                pair.gap_mm = best_gap;
                pair.has_geom = have_geom;
                pair.p0 = best_p0;
                pair.p1 = best_p1;
                rep.total_shared_mm += best_len;
                rep.pairs.push_back(std::move(pair));
            }
        }
    }
    return rep;
}

CommonCutMergeReport merge_common_cut_paths(
    const CommonLineReport& report,
    double join_tol_mm) {
    CommonCutMergeReport out;
    struct Node {
        Point2D a, b;
        bool used = false;
    };
    std::vector<Node> nodes;
    for (const auto& p : report.pairs) {
        if (!p.has_geom) {
            continue;
        }
        nodes.push_back({p.p0, p.p1, false});
    }
    out.segments_in = static_cast<int>(nodes.size());
    if (nodes.empty()) {
        return out;
    }

    auto try_extend = [&](std::vector<Point2D>& poly, bool at_front) {
        Point2D tip = at_front ? poly.front() : poly.back();
        int best = -1;
        bool flip = false;
        double best_d = join_tol_mm * join_tol_mm + 1.0;
        for (int i = 0; i < static_cast<int>(nodes.size()); ++i) {
            if (nodes[i].used) {
                continue;
            }
            const double d0 = dist2(tip, nodes[i].a);
            const double d1 = dist2(tip, nodes[i].b);
            if (d0 <= join_tol_mm * join_tol_mm && d0 < best_d) {
                best = i;
                flip = false;
                best_d = d0;
            }
            if (d1 <= join_tol_mm * join_tol_mm && d1 < best_d) {
                best = i;
                flip = true;
                best_d = d1;
            }
            // También aceptar colineales con solape aunque gap extremo un poco mayor
            if (best < 0) {
                Point2D other_a = at_front ? poly[1] : poly[poly.size() - 2];
                if (colinear_mergeable(other_a, tip, nodes[i].a, nodes[i].b, join_tol_mm)) {
                    best = i;
                    flip = dist2(tip, nodes[i].b) < dist2(tip, nodes[i].a);
                    best_d = 0;
                }
            }
        }
        if (best < 0) {
            return false;
        }
        nodes[best].used = true;
        Point2D far = flip ? nodes[best].a : nodes[best].b;
        if (at_front) {
            poly.insert(poly.begin(), far);
        } else {
            poly.push_back(far);
        }
        return true;
    };

    for (int i = 0; i < static_cast<int>(nodes.size()); ++i) {
        if (nodes[i].used) {
            continue;
        }
        nodes[i].used = true;
        std::vector<Point2D> poly = {nodes[i].a, nodes[i].b};
        int sources = 1;
        bool grew = true;
        while (grew) {
            grew = false;
            if (try_extend(poly, false)) {
                ++sources;
                grew = true;
            }
            if (try_extend(poly, true)) {
                ++sources;
                grew = true;
            }
        }
        CommonCutPath path;
        path.points = std::move(poly);
        path.source_pairs = sources;
        for (std::size_t k = 0; k + 1 < path.points.size(); ++k) {
            path.length_mm += seg_len(path.points[k], path.points[k + 1]);
        }
        out.total_path_mm += path.length_mm;
        out.paths.push_back(std::move(path));
    }

    out.paths_out = static_cast<int>(out.paths.size());
    out.pierce_saved = std::max(0, out.segments_in - out.paths_out);
    return out;
}

}  // namespace arga::core
