#pragma once

#include "packer.hpp"

#include <vector>

namespace arga::core {

/** Douglas-Peucker: reduce vértices manteniendo forma (tol en mm). */
std::vector<Point2D> simplify_ring_dp(const std::vector<Point2D>& ring, double tol_mm);

std::vector<std::vector<Point2D>> simplify_rings_dp(
    const std::vector<std::vector<Point2D>>& rings,
    double tol_mm);

}  // namespace arga::core
