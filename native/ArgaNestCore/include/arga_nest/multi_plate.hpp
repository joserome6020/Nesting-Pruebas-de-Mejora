#pragma once

#include "arga_nest/engine_facade.hpp"

#include <string>
#include <vector>

namespace arga::core {

struct PlateSpec {
    std::string id;
    double w = 0.0;
    double h = 0.0;
    double cost = 0.0;          // costo de usar esta placa/retazo
    bool is_remnant = false;    // priorizar remanentes
    std::string material;
    std::string calibre;
};

struct MultiPlateRequest {
    std::string engine_id = "svgnest_ultra";
    std::string profile = "first";
    double kerf = 0.2;
    double margin = 0.0;
    std::vector<PieceIn> pieces;
    std::vector<PieceConstraints> constraints;
    std::vector<PlateSpec> plates;
    int max_sheets = 32;
    bool prefer_remnants = true;
    bool enable_sa = true;
    bool enable_common_line_score = true;
};

struct MultiPlateSheet {
    PlateSpec plate;
    PackResult result;
    double score = 0.0;
    double common_line_mm = 0.0;
};

struct MultiPlateResult {
    std::vector<MultiPlateSheet> sheets;
    std::vector<PieceIn> leftovers;
};

MultiPlateResult pack_multi_plate(const MultiPlateRequest& req);

}  // namespace arga::core
