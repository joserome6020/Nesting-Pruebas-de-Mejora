#pragma once

#include "packer.hpp"

#include <optional>
#include <string>
#include <vector>

namespace arga {

/** Motor GIGA Cal 11 Galv: un pase MC (kernel Lite) con siembra I-primero
 *  y anclaje al patio derecho. Pool mixto. Sin NFP de Burke ni zona recortada.
 */
PackResult empaquetar_una_hoja_giga_cal11(
    const std::vector<PieceIn>& piezas,
    double w_placa,
    double h_placa,
    double kerf_override = 0.15,
    double margin_override = 0.25,
    const std::string& opt_override = "OPTIMIZAR LARGO Y ANCHO",
    const std::string& corner_override = "INFERIOR IZQUIERDA",
    const std::optional<std::vector<std::vector<Point2D>>>& limite_rings = std::nullopt);

}  // namespace arga
