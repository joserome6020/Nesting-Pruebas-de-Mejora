#pragma once

#include "packer.hpp"

#include <string>
#include <vector>

namespace arga::core {

struct CertifyIssue {
    std::string code;   // overlap | kerf | empty | internal
    std::string detail;
};

struct CertifyResult {
    bool ok = false;
    std::vector<CertifyIssue> issues;
    double min_gap_mm = 0.0;
    std::size_t placed_count = 0;
};

/**
 * Fail-closed: ok=false si solape metal (área > min_overlap_mm2)
 * o si gap mínimo entre piezas < 0.92 * kerf_mm (cuando kerf_mm > 0).
 */
CertifyResult certify_sheet(
    const PackResult& result,
    double plate_w,
    double plate_h,
    double kerf_mm,
    double min_overlap_mm2 = 1.0);

}  // namespace arga::core
