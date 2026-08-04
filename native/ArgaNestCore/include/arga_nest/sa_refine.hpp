#pragma once

#include "arga_nest/certifier.hpp"
#include "packer.hpp"

#include <string>

namespace arga::core {

struct SaRefineParams {
    int iterations = 80;
    double step0_mm = 3.0;
    double step_min_mm = 0.25;
    double temp0 = 1.0;
    unsigned seed = 42;
};

struct SaRefineStats {
    int accepted = 0;
    int improved = 0;
    double best_score = 0.0;
    std::string note;
};

/**
 * Simulated Annealing local: desplaza piezas colocadas para compactar
 * (minimiza bbox usado) sin romper certify fail-closed.
 */
PackResult sa_refine_pack(
    const PackResult& input,
    double plate_w,
    double plate_h,
    double kerf,
    const SaRefineParams& params,
    SaRefineStats* stats = nullptr);

}  // namespace arga::core
