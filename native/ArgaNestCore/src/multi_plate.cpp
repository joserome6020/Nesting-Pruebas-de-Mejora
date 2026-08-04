#include "arga_nest/multi_plate.hpp"

#include "arga_nest/common_line.hpp"
#include "arga_nest/cost_score.hpp"
#include "arga_nest/profiles.hpp"

namespace arga::core {

MultiPlateResult pack_multi_plate(const MultiPlateRequest& req) {
    MultiPlateResult out;
    if (req.plates.empty() || req.pieces.empty()) {
        out.leftovers = req.pieces;
        return out;
    }

    std::vector<PieceIn> pool = req.pieces;
    const int limit = std::max(1, req.max_sheets);
    CostWeights weights;
    if (req.prefer_remnants) {
        weights.remnant_bonus = 250.0;
    }

    for (int sheet_i = 0; sheet_i < limit && !pool.empty(); ++sheet_i) {
        PackResponse best_resp;
        PlateSpec best_plate;
        double best_score = -1e300;
        double best_cl = 0.0;
        bool have = false;

        for (const auto& plate : req.plates) {
            PackRequest pr;
            pr.engine_id = req.engine_id;
            pr.plate_w = plate.w;
            pr.plate_h = plate.h;
            pr.kerf = req.kerf;
            pr.margin = req.margin;
            pr.pieces = pool;
            pr.constraints = req.constraints;
            pr.enable_sa_refine = req.enable_sa;
            pr.enable_common_line = req.enable_common_line_score;
            apply_profile(pr, req.profile);
            PackResponse resp = pack_sheet_certified(pr);
            if (resp.result.hoja.piezas.empty()) {
                continue;
            }
            double cl = 0.0;
            if (req.enable_common_line_score) {
                cl = detect_common_lines(resp.result, std::max(0.5, req.kerf * 2.0)).total_shared_mm;
            }
            const double sc = score_sheet_candidate(resp.result, plate, cl, weights);
            if (!have || sc > best_score) {
                best_resp = std::move(resp);
                best_plate = plate;
                best_score = sc;
                best_cl = cl;
                have = true;
            }
        }

        if (!have || best_resp.result.hoja.piezas.empty()) {
            break;
        }

        MultiPlateSheet ms;
        ms.plate = best_plate;
        if (ms.plate.id.empty()) {
            ms.plate.id = std::string(best_plate.is_remnant ? "R" : "P") +
                std::to_string(sheet_i + 1);
        }
        ms.result = std::move(best_resp.result);
        ms.score = best_score;
        ms.common_line_mm = best_cl;
        out.sheets.push_back(std::move(ms));
        pool = best_resp.result.restos;
        // restos may be empty if pack_sheet_certified moved them - use leftovers from last
        // Actually PackResult has restos; after move best_resp.result.restos is in ms.result
        pool = out.sheets.back().result.restos;
    }

    out.leftovers = std::move(pool);
    return out;
}

}  // namespace arga::core
