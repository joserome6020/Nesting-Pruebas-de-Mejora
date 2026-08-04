#include "arga_nest/cost_score.hpp"

#include <algorithm>

namespace arga::core {

double score_sheet_candidate(
    const PackResult& result,
    const PlateSpec& plate,
    double common_line_mm,
    const CostWeights& w) {
    const double placed = static_cast<double>(result.hoja.piezas.size());
    const double efi = result.hoja.eficiencia;
    const double plate_area = std::max(1.0, plate.w * plate.h);
    const double used = result.hoja.area_usada;
    const double waste = std::max(0.0, plate_area - used);
    const double cost = plate.cost > 0 ? plate.cost : plate_area * 0.0001;
    double s = placed * 1.0e9 + efi * 1.0e3;
    s -= w.sheet_cost_weight * cost;
    s -= w.waste_area_weight * waste;
    if (plate.is_remnant) {
        s += w.remnant_bonus;
    }
    s += w.common_line_bonus * common_line_mm;
    return s;
}

}  // namespace arga::core
