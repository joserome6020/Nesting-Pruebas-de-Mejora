#pragma once

#include "packer.hpp"

#include <cmath>
#include <vector>

namespace arga::core {

struct PieceConstraints {
    /** Vacío = todas las rotaciones del step global. */
    std::vector<double> allowed_rotations_deg;
    bool grain_locked = false;  // fuerza solo 0° (y 180° si allow_flip)
    bool allow_flip_180 = true;
};

/** Normaliza ángulo a [0,360). */
inline double norm_deg(double a) {
    double x = std::fmod(a, 360.0);
    if (x < 0) {
        x += 360.0;
    }
    return x;
}

bool rotation_allowed(const PieceConstraints& c, double angle_deg, double tol = 0.5);

/**
 * Ajusta rotation_step_deg del request a un step compatible con todas las
 * restricciones (mínimo común). Si grain_locked en todas → step 180 o 360.
 */
double resolve_rotation_step(
    double requested_step,
    const std::vector<PieceConstraints>& constraints);

/** Rota un anillo alrededor de su centroide. */
std::vector<Point2D> rotate_ring(const std::vector<Point2D>& ring, double deg);

}  // namespace arga::core
