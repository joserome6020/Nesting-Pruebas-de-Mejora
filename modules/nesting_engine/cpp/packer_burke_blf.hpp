#pragma once

#include "packer.hpp"

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace arga {

/** Motor Burke BLF + NFP (Clipper2 Minkowski).
 *
 *  ``preserve_input_order=true``: respeta el orden de ``piezas`` (seed_order IA / FFD).
 *  ``rng_seed``: hill-climb determinista; ``0`` → random_device (legado).
 */
PackResult empaquetar_una_hoja_burke_blf(
    const std::vector<PieceIn>& piezas,
    double w_placa,
    double h_placa,
    double kerf_override = 0.2,
    double margin_override = 0.0,
    const std::string& opt_override = "OPTIMIZAR LARGO Y ANCHO",
    const std::string& corner_override = "INFERIOR IZQUIERDA",
    const std::optional<std::vector<std::vector<Point2D>>>& limite_rings = std::nullopt,
    int hill_climb_iterations = 10,
    std::uint32_t rng_seed = 1,
    bool preserve_input_order = true);

}  // namespace arga
