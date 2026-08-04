#include "arga_nest/constraints.hpp"

#include <algorithm>
#include <cmath>

namespace arga::core {

bool rotation_allowed(const PieceConstraints& c, double angle_deg, double tol) {
    const double a = norm_deg(angle_deg);
    if (c.grain_locked) {
        if (std::abs(a - 0.0) <= tol || std::abs(a - 360.0) <= tol) {
            return true;
        }
        if (c.allow_flip_180 && std::abs(a - 180.0) <= tol) {
            return true;
        }
        return false;
    }
    if (c.allowed_rotations_deg.empty()) {
        return true;
    }
    for (double r : c.allowed_rotations_deg) {
        if (std::abs(norm_deg(r) - a) <= tol) {
            return true;
        }
    }
    return false;
}

double resolve_rotation_step(
    double requested_step,
    const std::vector<PieceConstraints>& constraints) {
    if (constraints.empty()) {
        return std::max(1.0, requested_step);
    }
    bool any_limit = false;
    bool all_grain = true;
    for (const auto& c : constraints) {
        if (c.grain_locked || !c.allowed_rotations_deg.empty()) {
            any_limit = true;
        }
        if (!c.grain_locked) {
            all_grain = false;
        }
    }
    if (!any_limit) {
        return std::max(1.0, requested_step);
    }
    if (all_grain) {
        // Si todas permiten flip → 180; si no → 360 (solo 0°)
        bool any_no_flip = false;
        for (const auto& c : constraints) {
            if (c.grain_locked && !c.allow_flip_180) {
                any_no_flip = true;
                break;
            }
        }
        return any_no_flip ? 360.0 : 180.0;
    }
    // Step = GCD aproximado de ángulos permitidos y requested_step
    auto approx_gcd = [](double a, double b) {
        long long A = std::llround(std::abs(a) * 100.0);
        long long B = std::llround(std::abs(b) * 100.0);
        while (B) {
            long long t = A % B;
            A = B;
            B = t;
        }
        return A / 100.0;
    };
    double g = std::max(1.0, requested_step);
    for (const auto& c : constraints) {
        if (c.grain_locked) {
            g = approx_gcd(g, c.allow_flip_180 ? 180.0 : 360.0);
            continue;
        }
        for (double r : c.allowed_rotations_deg) {
            if (std::abs(r) < 1e-9) {
                continue;
            }
            g = approx_gcd(g, norm_deg(r));
        }
    }
    return std::max(1.0, g);
}

std::vector<Point2D> rotate_ring(const std::vector<Point2D>& ring, double deg) {
    if (ring.empty()) {
        return {};
    }
    double cx = 0, cy = 0;
    for (const auto& p : ring) {
        cx += p.x;
        cy += p.y;
    }
    cx /= static_cast<double>(ring.size());
    cy /= static_cast<double>(ring.size());
    const double rad = deg * 3.14159265358979323846 / 180.0;
    const double c = std::cos(rad);
    const double s = std::sin(rad);
    std::vector<Point2D> out;
    out.reserve(ring.size());
    for (const auto& p : ring) {
        const double dx = p.x - cx;
        const double dy = p.y - cy;
        out.push_back({cx + dx * c - dy * s, cy + dx * s + dy * c});
    }
    return out;
}

}  // namespace arga::core
