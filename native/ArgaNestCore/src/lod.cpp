#include "arga_nest/lod.hpp"

#include <cmath>

namespace arga::core {
namespace {

double dist_point_seg(const Point2D& p, const Point2D& a, const Point2D& b) {
    const double vx = b.x - a.x;
    const double vy = b.y - a.y;
    const double w = vx * vx + vy * vy;
    if (w < 1e-18) {
        const double dx = p.x - a.x;
        const double dy = p.y - a.y;
        return std::sqrt(dx * dx + dy * dy);
    }
    double t = ((p.x - a.x) * vx + (p.y - a.y) * vy) / w;
    t = std::max(0.0, std::min(1.0, t));
    const double px = a.x + t * vx;
    const double py = a.y + t * vy;
    const double dx = p.x - px;
    const double dy = p.y - py;
    return std::sqrt(dx * dx + dy * dy);
}

void dp_rec(
    const std::vector<Point2D>& ring,
    std::size_t i0,
    std::size_t i1,
    double tol,
    std::vector<char>& keep) {
    if (i1 <= i0 + 1) {
        return;
    }
    double maxd = -1.0;
    std::size_t idx = i0;
    for (std::size_t i = i0 + 1; i < i1; ++i) {
        const double d = dist_point_seg(ring[i], ring[i0], ring[i1]);
        if (d > maxd) {
            maxd = d;
            idx = i;
        }
    }
    if (maxd > tol) {
        keep[idx] = 1;
        dp_rec(ring, i0, idx, tol, keep);
        dp_rec(ring, idx, i1, tol, keep);
    }
}

}  // namespace

std::vector<Point2D> simplify_ring_dp(const std::vector<Point2D>& ring, double tol_mm) {
    if (ring.size() < 5 || tol_mm <= 0) {
        return ring;
    }
    std::vector<Point2D> pts = ring;
    if (pts.size() >= 2) {
        const auto& a = pts.front();
        const auto& b = pts.back();
        if (std::abs(a.x - b.x) < 1e-9 && std::abs(a.y - b.y) < 1e-9) {
            pts.pop_back();
        }
    }
    if (pts.size() < 4) {
        return ring;
    }
    std::vector<char> keep(pts.size(), 0);
    keep.front() = 1;
    keep.back() = 1;
    dp_rec(pts, 0, pts.size() - 1, tol_mm, keep);
    std::vector<Point2D> out;
    for (std::size_t i = 0; i < pts.size(); ++i) {
        if (keep[i]) {
            out.push_back(pts[i]);
        }
    }
    if (out.size() >= 3) {
        out.push_back(out.front());
    }
    return out.size() >= 4 ? out : ring;
}

std::vector<std::vector<Point2D>> simplify_rings_dp(
    const std::vector<std::vector<Point2D>>& rings,
    double tol_mm) {
    std::vector<std::vector<Point2D>> out;
    out.reserve(rings.size());
    for (const auto& r : rings) {
        out.push_back(simplify_ring_dp(r, tol_mm));
    }
    return out;
}

}  // namespace arga::core
