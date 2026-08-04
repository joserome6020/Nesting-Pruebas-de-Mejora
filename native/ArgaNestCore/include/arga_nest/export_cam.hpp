#pragma once

#include "arga_nest/common_line.hpp"
#include "arga_nest/dxf_io.hpp"
#include "packer.hpp"

namespace arga::core {

/**
 * DXF nest con COMMON_CUT (trayectorias fusionadas).
 * Si machine_path=true, omite aristas compartidas de CUT_OUTER
 * (el corte común vive solo en COMMON_CUT → un pierce / un pase).
 */
DxfDocument dxf_from_pack_with_common_line(
    const PackResult& result,
    const CommonLineReport& common,
    const std::string& outer_layer = "CUT_OUTER",
    bool machine_path = true,
    double edge_match_tol_mm = 1.0);

DxfDocument dxf_from_pack_with_common_paths(
    const PackResult& result,
    const CommonLineReport& common,
    const CommonCutMergeReport& merged,
    const std::string& outer_layer = "CUT_OUTER",
    bool machine_path = true,
    double edge_match_tol_mm = 1.0);

/** Certifica un DXF ASCII exportado (capas, cerrados/abiertos, solapes approx). */
struct DxfCertifyResult {
    bool ok = false;
    int entity_count = 0;
    int closed_outers = 0;
    int open_outer_segments = 0;
    int common_cut_segments = 0;
    bool machine_path = false;
    std::vector<std::string> issues;
};

DxfCertifyResult certify_dxf_ascii(const std::string& dxf_text);

}  // namespace arga::core
