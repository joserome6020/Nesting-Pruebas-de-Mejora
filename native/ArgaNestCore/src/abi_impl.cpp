#include "arga_nest/abi.h"
#include "arga_nest/engine_facade.hpp"
#include "arga_nest/multi_plate.hpp"
#include "arga_nest/cu_strip.hpp"
#include "arga_nest/dxf_io.hpp"
#include "arga_nest/export_cam.hpp"
#include "arga_nest/step_export.hpp"
#include "arga_nest/nfp_cache.hpp"
#include "arga_nest/mark_engine.hpp"
#include "arga_nest/cuda_status.hpp"
#include "arga_nest/common_line.hpp"

#include <nlohmann/json.hpp>

#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>

namespace {

constexpr int kMajor = 0;
constexpr int kMinor = 5;
constexpr int kPatch = 3;

std::mutex g_err_mu;
std::string g_last_error;

void set_error(const std::string& msg) {
    std::lock_guard<std::mutex> lock(g_err_mu);
    g_last_error = msg;
}

char* dup_utf8(const std::string& s) {
    char* buf = static_cast<char*>(std::malloc(s.size() + 1));
    if (!buf) {
        return nullptr;
    }
    std::memcpy(buf, s.c_str(), s.size() + 1);
    return buf;
}

}  // namespace

extern "C" {

int arga_nest_version_major(void) { return kMajor; }
int arga_nest_version_minor(void) { return kMinor; }
int arga_nest_version_patch(void) { return kPatch; }

int arga_nest_version_string(char** out_utf8) {
    if (!out_utf8) {
        set_error("out_utf8 is null");
        return ARGA_NEST_E_INVALID_ARG;
    }
    const std::string s =
        "ArgaNestCore " + std::to_string(kMajor) + "." + std::to_string(kMinor) +
        "." + std::to_string(kPatch) + " (ANS C++ product line)";
    *out_utf8 = dup_utf8(s);
    if (!*out_utf8) {
        set_error("out of memory");
        return ARGA_NEST_E_NO_MEMORY;
    }
    return ARGA_NEST_OK;
}

int arga_nest_pack_sheet_json(const char* request_json, char** out_response_json) {
    if (!request_json || !out_response_json) {
        set_error("null argument");
        return ARGA_NEST_E_INVALID_ARG;
    }
    *out_response_json = nullptr;
    try {
        const auto req = arga::core::parse_pack_request_json(request_json);
        const auto resp = arga::core::pack_sheet_certified(req);
        const std::string out = arga::core::pack_response_to_json(req, resp);
        *out_response_json = dup_utf8(out);
        if (!*out_response_json) {
            set_error("out of memory");
            return ARGA_NEST_E_NO_MEMORY;
        }
        set_error(resp.certify.ok ? "" : "certify failed");
        return resp.certify.ok ? ARGA_NEST_OK : ARGA_NEST_E_CERTIFY;
    } catch (const nlohmann::json::exception& ex) {
        set_error(std::string("json: ") + ex.what());
        return ARGA_NEST_E_PARSE;
    } catch (const std::exception& ex) {
        set_error(ex.what());
        return ARGA_NEST_E_ENGINE;
    } catch (...) {
        set_error("unknown internal error");
        return ARGA_NEST_E_INTERNAL;
    }
}

int arga_nest_pack_job_json(const char* request_json, char** out_response_json) {
    if (!request_json || !out_response_json) {
        set_error("null argument");
        return ARGA_NEST_E_INVALID_ARG;
    }
    *out_response_json = nullptr;
    try {
        auto root = nlohmann::json::parse(request_json);
        arga::core::MultiPlateRequest job;
        job.engine_id = root.value("engine", std::string("svgnest_ultra"));
        job.profile = root.value("profile", std::string("first"));
        job.kerf = root.value("kerf", 0.2);
        if (root.contains("kerf_mm") && !root["kerf_mm"].is_null()) {
            // Multi-placa: si hay contrato kerf_mm, úsalo como kerf efectivo.
            job.kerf = root["kerf_mm"].get<double>();
        }
        job.margin = root.value("margin", 0.0);
        job.max_sheets = root.value("max_sheets", 32);
        for (const auto& pj : root.at("pieces")) {
            // Reuse single-sheet parser by wrapping
            nlohmann::json wrap = {
                {"plate_w", 1},
                {"plate_h", 1},
                {"pieces", nlohmann::json::array({pj})}};
            auto tmp = arga::core::parse_pack_request_json(wrap.dump());
            job.pieces.push_back(tmp.pieces[0]);
        }
        for (const auto& pl : root.at("plates")) {
            arga::core::PlateSpec s;
            s.id = pl.value("id", std::string(""));
            s.w = pl.at("w").get<double>();
            s.h = pl.at("h").get<double>();
            s.cost = pl.value("cost", 0.0);
            s.is_remnant = pl.value("is_remnant", false);
            s.material = pl.value("material", std::string(""));
            s.calibre = pl.value("calibre", std::string(""));
            job.plates.push_back(s);
        }
        job.prefer_remnants = root.value("prefer_remnants", true);
        job.enable_sa = root.value("enable_sa", true);
        job.enable_common_line_score = root.value("enable_common_line_score", true);
        const auto result = arga::core::pack_multi_plate(job);
        nlohmann::json out;
        out["ok"] = result.leftovers.empty();
        out["core"] = "ArgaNestCore";
        out["sheets"] = nlohmann::json::array();
        for (const auto& sh : result.sheets) {
            arga::core::PackRequest req;
            req.engine_id = job.engine_id;
            req.profile = job.profile;
            req.plate_w = sh.plate.w;
            req.plate_h = sh.plate.h;
            req.kerf = job.kerf;
            req.certify = true;
            arga::core::PackResponse resp;
            resp.engine_id = job.engine_id;
            resp.profile = job.profile;
            resp.result = sh.result;
            resp.kerf_used = job.kerf;
            resp.cuda = arga::core::query_cuda_status();
            resp.common_lines = arga::core::detect_common_lines(
                sh.result, std::max(0.35, job.kerf * 2.0));
            resp.certify = arga::core::certify_sheet(
                sh.result, sh.plate.w, sh.plate.h, job.kerf, 1.0);
            auto sheet_json = nlohmann::json::parse(
                arga::core::pack_response_to_json(req, resp));
            sheet_json["plate"] = {
                {"id", sh.plate.id},
                {"w", sh.plate.w},
                {"h", sh.plate.h},
                {"cost", sh.plate.cost},
                {"is_remnant", sh.plate.is_remnant},
            };
            sheet_json["score"] = sh.score;
            sheet_json["common_line_mm"] = sh.common_line_mm;
            out["sheets"].push_back(sheet_json);
            if (!resp.certify.ok) {
                out["ok"] = false;
            }
        }
        nlohmann::json left = nlohmann::json::array();
        for (const auto& p : result.leftovers) {
            left.push_back(p.nombre);
        }
        out["leftovers"] = left;
        *out_response_json = dup_utf8(out.dump());
        set_error("");
        return ARGA_NEST_OK;
    } catch (const std::exception& ex) {
        set_error(ex.what());
        return ARGA_NEST_E_ENGINE;
    }
}

int arga_nest_pack_cu_strip_json(const char* request_json, char** out_response_json) {
    if (!request_json || !out_response_json) {
        set_error("null argument");
        return ARGA_NEST_E_INVALID_ARG;
    }
    *out_response_json = nullptr;
    try {
        auto root = nlohmann::json::parse(request_json);
        arga::core::CuStripRequest req;
        req.strip_length_mm = root.at("strip_length_mm").get<double>();
        req.strip_width_mm = root.at("strip_width_mm").get<double>();
        req.kerf_mm = root.value("kerf_mm", 0.2);
        req.gap_mm = root.value("gap_mm", 0.0);
        for (const auto& pj : root.at("pieces")) {
            arga::core::CuStripPiece p;
            p.nombre = pj.value("nombre", std::string("cu"));
            p.length_mm = pj.at("length_mm").get<double>();
            p.width_mm = pj.at("width_mm").get<double>();
            p.area = pj.value("area", p.length_mm * p.width_mm);
            p.calibre = pj.value("calibre", std::string(""));
            p.material = pj.value("material", std::string("CU"));
            req.pieces.push_back(p);
        }
        const auto cu = arga::core::pack_cu_strip(req);
        const auto pack = arga::core::cu_strip_to_pack_result(cu);
        arga::core::PackRequest preq;
        preq.engine_id = "cu_strip";
        preq.plate_w = req.strip_length_mm;
        preq.plate_h = req.strip_width_mm;
        preq.kerf = req.kerf_mm;
        arga::core::PackResponse resp;
        resp.engine_id = "cu_strip";
        resp.result = pack;
        resp.certify = arga::core::certify_sheet(
            pack, req.strip_length_mm, req.strip_width_mm, req.kerf_mm, 1.0);
        *out_response_json = dup_utf8(arga::core::pack_response_to_json(preq, resp));
        set_error("");
        return resp.certify.ok ? ARGA_NEST_OK : ARGA_NEST_E_CERTIFY;
    } catch (const std::exception& ex) {
        set_error(ex.what());
        return ARGA_NEST_E_ENGINE;
    }
}

int arga_nest_export_dxf_json(const char* request_json, char** out_dxf_utf8) {
    if (!request_json || !out_dxf_utf8) {
        set_error("null argument");
        return ARGA_NEST_E_INVALID_ARG;
    }
    *out_dxf_utf8 = nullptr;
    try {
        const auto req = arga::core::parse_pack_request_json(request_json);
        auto resp = arga::core::pack_sheet_certified(req);
        // Inject mark text if requested
        auto root = nlohmann::json::parse(request_json);
        if (root.contains("mark_text")) {
            const std::string txt = root["mark_text"].get<std::string>();
            auto strokes = arga::core::mark_stick_text(txt, 5.0, 5.0, 8.0);
            if (!resp.result.hoja.piezas.empty()) {
                resp.result.hoja.piezas[0].marcas = strokes;
            }
        }
        const bool with_cl = root.value("common_cut_layer", true);
        const bool machine_path = root.value("machine_path", true);
        const double edge_tol = root.value("edge_match_tol_mm", 1.0);
        arga::core::DxfDocument doc;
        if (with_cl && (!resp.common_lines.pairs.empty() || !resp.common_cut_paths.paths.empty())) {
            doc = arga::core::dxf_from_pack_with_common_paths(
                resp.result,
                resp.common_lines,
                resp.common_cut_paths,
                "CUT_OUTER",
                machine_path,
                edge_tol);
        } else if (with_cl) {
            doc = arga::core::dxf_from_pack_with_common_line(
                resp.result, resp.common_lines, "CUT_OUTER", machine_path, edge_tol);
        } else {
            doc = arga::core::dxf_from_pack_result(resp.result);
        }
        *out_dxf_utf8 = dup_utf8(arga::core::dxf_write_ascii(doc));
        set_error("");
        return ARGA_NEST_OK;
    } catch (const std::exception& ex) {
        set_error(ex.what());
        return ARGA_NEST_E_ENGINE;
    }
}

int arga_nest_certify_dxf_json(const char* request_json, char** out_json) {
    if (!request_json || !out_json) {
        set_error("null argument");
        return ARGA_NEST_E_INVALID_ARG;
    }
    *out_json = nullptr;
    try {
        auto root = nlohmann::json::parse(request_json);
        const std::string dxf = root.at("dxf").get<std::string>();
        const auto cert = arga::core::certify_dxf_ascii(dxf);
        nlohmann::json out = {
            {"ok", cert.ok},
            {"entity_count", cert.entity_count},
            {"closed_outers", cert.closed_outers},
            {"open_outer_segments", cert.open_outer_segments},
            {"common_cut_segments", cert.common_cut_segments},
            {"machine_path", cert.machine_path},
            {"issues", cert.issues},
        };
        *out_json = dup_utf8(out.dump());
        set_error("");
        return cert.ok ? ARGA_NEST_OK : ARGA_NEST_E_CERTIFY;
    } catch (const std::exception& ex) {
        set_error(ex.what());
        return ARGA_NEST_E_ENGINE;
    }
}

int arga_nest_export_step_json(const char* request_json, char** out_step_utf8) {
    if (!request_json || !out_step_utf8) {
        set_error("null argument");
        return ARGA_NEST_E_INVALID_ARG;
    }
    *out_step_utf8 = nullptr;
    try {
        auto root = nlohmann::json::parse(request_json);
        const double thick = root.value("thickness_mm", 6.0);
        const auto req = arga::core::parse_pack_request_json(request_json);
        const auto resp = arga::core::pack_sheet_certified(req);
        *out_step_utf8 = dup_utf8(arga::core::step_from_pack_result(resp.result, thick));
        set_error("");
        return ARGA_NEST_OK;
    } catch (const std::exception& ex) {
        set_error(ex.what());
        return ARGA_NEST_E_ENGINE;
    }
}

int arga_nest_nfp_cache_stats_json(char** out_json) {
    if (!out_json) {
        return ARGA_NEST_E_INVALID_ARG;
    }
    const auto st = arga::core::nfp_cache_stats();
    nlohmann::json j = {
        {"hits", st.hits},
        {"misses", st.misses},
        {"evictions", st.evictions},
        {"entries", st.entries},
        {"capacity", st.capacity},
    };
    *out_json = dup_utf8(j.dump());
    return ARGA_NEST_OK;
}

void arga_nest_nfp_cache_reset(void) { arga::core::reset_nfp_cache(); }

int arga_nest_cuda_status_json(char** out_json) {
    if (!out_json) {
        return ARGA_NEST_E_INVALID_ARG;
    }
    const auto st = arga::core::query_cuda_status();
    nlohmann::json j = {
        {"build_has_cuda", st.build_has_cuda},
        {"runtime_available", st.runtime_available},
        {"env_requested", st.env_requested},
        {"detail", st.detail},
    };
    *out_json = dup_utf8(j.dump());
    return ARGA_NEST_OK;
}

const char* arga_nest_last_error(void) {
    std::lock_guard<std::mutex> lock(g_err_mu);
    return g_last_error.c_str();
}

void arga_nest_free(void* p) { std::free(p); }

}  // extern "C"
