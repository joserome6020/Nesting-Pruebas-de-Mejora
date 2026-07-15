#pragma once

#include "packer.hpp"

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace arga {

/** Motor SVGNest Ultra: Inner/Outer NFP (Deepnest) + GA + part-in-part + cavidades VFM. */
PackResult empaquetar_una_hoja_svgnest_ultra(
    const std::vector<PieceIn>& piezas,
    double w_placa,
    double h_placa,
    double kerf_override = 0.2,
    double margin_override = 0.0,
    const std::string& opt_override = "OPTIMIZAR LARGO Y ANCHO",
    const std::string& corner_override = "INFERIOR IZQUIERDA",
    const std::optional<std::vector<std::vector<Point2D>>>& limite_rings = std::nullopt,
    int ga_population = 30,
    int ga_generations = 30,
    double rotation_step_deg = 15.0,
    bool part_in_part = true,
    std::uint32_t ga_seed = 0,
    const std::vector<size_t>* seed_order = nullptr);

}  // namespace arga
