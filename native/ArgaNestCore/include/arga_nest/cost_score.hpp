#pragma once

#include "arga_nest/engine_facade.hpp"
#include "arga_nest/multi_plate.hpp"

namespace arga::core {

struct CostWeights {
    double sheet_cost_weight = 1.0;
    double remnant_bonus = 50.0;      // preferir retazo
    double waste_area_weight = 0.001; // penaliza área libre
    double common_line_bonus = 0.01;  // por mm compartido
};

/** Score mayor = mejor. */
double score_sheet_candidate(
    const PackResult& result,
    const PlateSpec& plate,
    double common_line_mm,
    const CostWeights& w);

}  // namespace arga::core
