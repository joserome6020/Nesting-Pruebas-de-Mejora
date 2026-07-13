#pragma once

#include "packer.hpp"

#include <optional>
#include <string>
#include <vector>

namespace arga {

/** Motor Burke BLF + NFP (Clipper2 Minkowski). */
PackResult empaquetar_una_hoja_burke_blf(
    const std::vector<PieceIn>& piezas,
    double w_placa,
    double h_placa,
    double kerf_override = 0.2,
    double margin_override = 0.0,
    const std::string& opt_override = "OPTIMIZAR LARGO Y ANCHO",
    const std::string& corner_override = "INFERIOR IZQUIERDA",
    const std::optional<std::vector<std::vector<Point2D>>>& limite_rings = std::nullopt,
    int hill_climb_iterations = 10);

}  // namespace arga
