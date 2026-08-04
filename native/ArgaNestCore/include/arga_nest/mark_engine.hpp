#pragma once

#include "packer.hpp"

#include <string>
#include <vector>

namespace arga::core {

/** Genera trazos stick-font (líneas) para un texto en mm. */
std::vector<std::vector<Point2D>> mark_stick_text(
    const std::string& text,
    double origin_x,
    double origin_y,
    double height_mm = 8.0,
    double spacing_mm = 1.5);

}  // namespace arga::core
