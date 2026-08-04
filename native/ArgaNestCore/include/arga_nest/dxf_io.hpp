#pragma once

#include "packer.hpp"

#include <string>
#include <vector>

namespace arga::core {

struct DxfEntity {
    std::string layer = "CUT_OUTER";
    std::vector<Point2D> points;  // LWPOLYLINE
    bool closed = true;
};

struct DxfDocument {
    std::vector<DxfEntity> entities;
};

/** Parser mínimo DXF ASCII: LWPOLYLINE + LINE. */
bool dxf_parse_ascii(const std::string& text, DxfDocument& out, std::string& err);

/** Writer DXF R12-ish con capas de corte. */
std::string dxf_write_ascii(const DxfDocument& doc);

/** Construye DXF de nest desde PackResult. */
DxfDocument dxf_from_pack_result(const PackResult& result, const std::string& outer_layer = "CUT_OUTER");

/** Extrae PieceIn desde LWPOLYLINE exteriores (una pieza por entidad cerrada). */
std::vector<PieceIn> pieces_from_dxf(const DxfDocument& doc);

}  // namespace arga::core
