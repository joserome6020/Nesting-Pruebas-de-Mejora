#pragma once

#include "packer.hpp"
#include "packer_base.hpp"
#include "packer_burke_blf.hpp"
#include "packer_libnest2d.hpp"
#include "packer_svgnest_ultra.hpp"

#include "arga_nest/certifier.hpp"
#include "arga_nest/common_line.hpp"
#include "arga_nest/constraints.hpp"
#include "arga_nest/cuda_status.hpp"
#include "arga_nest/sa_refine.hpp"

#include <string>
#include <vector>

namespace arga::core {

struct PackRequest {
    std::string engine_id = "svgnest_ultra";
    std::string profile;
    double plate_w = 0.0;
    double plate_h = 0.0;
    double kerf = 0.2;
    double margin = 0.0;
    /** Si >0, kerf se interpreta explícitamente en mm (contrato nuevo). */
    double kerf_mm = -1.0;
    std::string opt = "OPTIMIZAR LARGO Y ANCHO";
    std::string corner = "INFERIOR IZQUIERDA";
    std::vector<PieceIn> pieces;
    std::vector<PieceConstraints> constraints;  // alineado por índice
    int ga_population = 10;
    int ga_generations = 10;
    double rotation_step_deg = 90.0;
    bool part_in_part = true;
    bool certify = true;
    double min_overlap_mm2 = 1.0;
    bool enable_sa_refine = true;
    bool enable_common_line = true;
    bool enable_lod = true;
    bool enable_tabu = true;
    int tabu_seed_trials = 3;
    double lod_tol_mm = 0.5;
    int sa_iterations = -1;  // <0 → según perfil
    /** Semilla RNG (Burke hill-climb / Ultra GA / SA). 0 = no forzar en algunos motores. */
    std::uint32_t ga_seed = 1;
    /** Burke: respetar orden de pieces[] (seed_order IA). Default true. */
    bool preserve_order = true;
    /** Burke hill-climb iterations; <0 → default motor (10). */
    int hill_climb_iterations = -1;
};

struct PackResponse {
    PackResult result;
    CertifyResult certify;
    CommonLineReport common_lines;
    CommonCutMergeReport common_cut_paths;
    SaRefineStats sa;
    CudaStatus cuda;
    std::string engine_id;
    std::string profile;
    double kerf_used = 0.0;
};

PackResult pack_sheet(const PackRequest& req);
PackResponse pack_sheet_certified(const PackRequest& req);

std::string pack_response_to_json(const PackRequest& req, const PackResponse& resp);
PackRequest parse_pack_request_json(const std::string& json_utf8);

}  // namespace arga::core
