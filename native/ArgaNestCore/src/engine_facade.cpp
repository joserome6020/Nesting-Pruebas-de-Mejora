#include "arga_nest/engine_facade.hpp"

#include "arga_nest/lod.hpp"
#include "arga_nest/nfp_cache.hpp"
#include "arga_nest/profiles.hpp"
#include "arga_nest/tabu.hpp"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <stdexcept>

namespace arga::core {
namespace {

using json = nlohmann::json;

std::vector<Point2D> parse_ring(const json& ring) {
    std::vector<Point2D> out;
    if (!ring.is_array()) {
        return out;
    }
    for (const auto& pt : ring) {
        if (!pt.is_array() || pt.size() < 2) {
            continue;
        }
        out.push_back({pt[0].get<double>(), pt[1].get<double>()});
    }
    return out;
}

PieceIn parse_piece(const json& d) {
    PieceIn piece;
    piece.nombre = d.value("nombre", std::string("piece"));
    piece.area = d.value("area", 0.0);
    piece.calibre = d.value("calibre", std::string(""));
    piece.material = d.value("material", std::string(""));
    if (d.contains("rings") && d["rings"].is_array()) {
        for (const auto& ring_j : d["rings"]) {
            auto ring = parse_ring(ring_j);
            if (ring.size() >= 3) {
                piece.rings.push_back(std::move(ring));
            }
        }
    }
    if (d.contains("marks") && d["marks"].is_array()) {
        for (const auto& mark_j : d["marks"]) {
            auto mark = parse_ring(mark_j);
            if (mark.size() >= 2) {
                piece.marks.push_back(std::move(mark));
            }
        }
    }
    return piece;
}

PieceConstraints parse_constraints(const json& d) {
    PieceConstraints c;
    c.grain_locked = d.value("grain_locked", false);
    c.allow_flip_180 = d.value("allow_flip_180", true);
    if (d.contains("allowed_rotations") && d["allowed_rotations"].is_array()) {
        for (const auto& a : d["allowed_rotations"]) {
            c.allowed_rotations_deg.push_back(a.get<double>());
        }
    }
    return c;
}

json ring_to_json(const std::vector<Point2D>& ring) {
    json out = json::array();
    for (const auto& p : ring) {
        out.push_back(json::array({p.x, p.y}));
    }
    return out;
}

double effective_kerf(const PackRequest& req) {
    if (req.kerf_mm >= 0.0) {
        return req.kerf_mm;
    }
    return req.kerf;
}

}  // namespace

PackRequest parse_pack_request_json(const std::string& json_utf8) {
    json root = json::parse(json_utf8);
    PackRequest req;
    req.engine_id = root.value("engine", std::string("svgnest_ultra"));
    req.profile = root.value("profile", std::string(""));
    if (req.profile.empty() && root.contains("mode")) {
        req.profile = root.value("mode", std::string(""));
    }
    req.plate_w = root.at("plate_w").get<double>();
    req.plate_h = root.at("plate_h").get<double>();
    req.kerf = root.value("kerf", 0.2);
    req.kerf_mm = root.value("kerf_mm", -1.0);
    req.margin = root.value("margin", 0.0);
    req.opt = root.value("opt", std::string("OPTIMIZAR LARGO Y ANCHO"));
    req.corner = root.value("corner", std::string("INFERIOR IZQUIERDA"));
    req.ga_population = root.value("ga_population", 10);
    req.ga_generations = root.value("ga_generations", 10);
    req.rotation_step_deg = root.value("rotation_step_deg", 90.0);
    req.part_in_part = root.value("part_in_part", true);
    req.certify = root.value("certify", true);
    req.min_overlap_mm2 = root.value("min_overlap_mm2", 1.0);
    req.enable_sa_refine = root.value("enable_sa_refine", true);
    req.enable_common_line = root.value("enable_common_line", true);
    req.enable_lod = root.value("enable_lod", true);
    req.enable_tabu = root.value("enable_tabu", true);
    req.tabu_seed_trials = root.value("tabu_seed_trials", 3);
    req.lod_tol_mm = root.value("lod_tol_mm", 0.5);
    req.sa_iterations = root.value("sa_iterations", -1);
    req.ga_seed = root.value("ga_seed", static_cast<std::uint32_t>(1));
    req.preserve_order = root.value("preserve_order", true);
    req.hill_climb_iterations = root.value("hill_climb_iterations", -1);
    apply_profile(req, req.profile);
    if (root.contains("ga_population")) {
        req.ga_population = root["ga_population"].get<int>();
    }
    if (root.contains("ga_generations")) {
        req.ga_generations = root["ga_generations"].get<int>();
    }
    if (root.contains("rotation_step_deg")) {
        req.rotation_step_deg = root["rotation_step_deg"].get<double>();
    }
    if (root.contains("part_in_part")) {
        req.part_in_part = root["part_in_part"].get<bool>();
    }
    if (!root.contains("pieces") || !root["pieces"].is_array()) {
        throw std::runtime_error("pack request: missing pieces[]");
    }
    for (const auto& pj : root["pieces"]) {
        req.pieces.push_back(parse_piece(pj));
        if (pj.contains("constraints")) {
            req.constraints.push_back(parse_constraints(pj["constraints"]));
        } else {
            req.constraints.push_back(parse_constraints(pj));
        }
    }
    // LOD opcional sobre geometría de entrada (acelera NFP internos del packer vía menos detalle)
    if (req.enable_lod && req.lod_tol_mm > 0) {
        for (auto& p : req.pieces) {
            p.rings = simplify_rings_dp(p.rings, req.lod_tol_mm);
        }
    }
    req.rotation_step_deg =
        resolve_rotation_step(req.rotation_step_deg, req.constraints);
    return req;
}

PackResult pack_sheet(const PackRequest& req) {
    const double kerf = effective_kerf(req);
    const std::string& id = req.engine_id;

    auto run_ultra = [&](std::uint32_t seed) {
        return empaquetar_una_hoja_svgnest_ultra(
            req.pieces, req.plate_w, req.plate_h, kerf, req.margin, req.opt,
            req.corner, std::nullopt, req.ga_population, req.ga_generations,
            req.rotation_step_deg, req.part_in_part, seed, nullptr);
    };

    if (id == "arga_force" || id == "arga_base") {
        return empaquetar_una_hoja_base(
            req.pieces, req.plate_w, req.plate_h, kerf, req.margin, req.opt,
            req.corner, std::nullopt);
    }
    if (id == "burke_blf") {
        const int hc = req.hill_climb_iterations >= 0 ? req.hill_climb_iterations : 10;
        return empaquetar_una_hoja_burke_blf(
            req.pieces, req.plate_w, req.plate_h, kerf, req.margin, req.opt,
            req.corner, std::nullopt, hc, req.ga_seed, req.preserve_order);
    }
    if (id == "libnest2d") {
        return empaquetar_una_hoja_libnest2d(
            req.pieces, req.plate_w, req.plate_h, kerf, req.margin, req.opt,
            req.corner, std::nullopt);
    }

    // Ultra + Tabu: prueba varias semillas evitando fingerprints recientes
    const std::uint32_t base_seed = req.ga_seed != 0 ? req.ga_seed : 1u;
    if (!req.enable_tabu || req.tabu_seed_trials <= 1) {
        return run_ultra(base_seed);
    }
    PackResult best;
    bool have = false;
    auto& tabu = global_tabu();
    for (int t = 0; t < req.tabu_seed_trials; ++t) {
        const std::uint32_t seed = static_cast<std::uint32_t>(base_seed + t * 97);
        const auto fp = hash_pack_fingerprint(
            id, req.plate_w, req.plate_h, kerf, req.pieces.size(), seed);
        if (tabu.is_tabu(fp) && t + 1 < req.tabu_seed_trials) {
            continue;
        }
        PackResult cand = run_ultra(seed);
        tabu.remember(fp);
        if (!have || cand.hoja.piezas.size() > best.hoja.piezas.size() ||
            (cand.hoja.piezas.size() == best.hoja.piezas.size() &&
             cand.hoja.eficiencia > best.hoja.eficiencia)) {
            best = std::move(cand);
            have = true;
        }
    }
    return have ? best : run_ultra(base_seed);
}

PackResponse pack_sheet_certified(const PackRequest& req) {
    PackResponse resp;
    resp.engine_id = req.engine_id;
    resp.profile = req.profile;
    resp.kerf_used = effective_kerf(req);
    resp.cuda = query_cuda_status();
    resp.result = pack_sheet(req);

    if (req.enable_sa_refine && resp.result.hoja.piezas.size() >= 2) {
        SaRefineParams sp;
        if (req.sa_iterations >= 0) {
            sp.iterations = req.sa_iterations;
        } else if (req.profile == "max") {
            sp.iterations = 120;
        } else if (req.profile == "standard") {
            sp.iterations = 80;
        } else if (req.profile == "fast") {
            sp.iterations = 40;
        } else {
            sp.iterations = 25;  // first
        }
        if (req.ga_seed != 0) {
            sp.seed = static_cast<unsigned>(req.ga_seed);
        }
        resp.result = sa_refine_pack(
            resp.result, req.plate_w, req.plate_h, resp.kerf_used, sp, &resp.sa);
    }

    if (req.enable_common_line) {
        resp.common_lines =
            detect_common_lines(resp.result, std::max(0.35, resp.kerf_used * 2.0));
        resp.common_cut_paths = merge_common_cut_paths(resp.common_lines);
    }

    if (req.certify) {
        resp.certify = certify_sheet(
            resp.result, req.plate_w, req.plate_h, resp.kerf_used, req.min_overlap_mm2);
    } else {
        resp.certify.ok = true;
        resp.certify.placed_count = resp.result.hoja.piezas.size();
    }
    return resp;
}

std::string pack_response_to_json(const PackRequest& req, const PackResponse& resp) {
    json root;
    root["ok"] = resp.certify.ok;
    root["engine"] = req.engine_id;
    root["profile"] = req.profile.empty() ? json(nullptr) : json(req.profile);
    root["core"] = "ArgaNestCore";
    root["abi"] = "1.4.0";
    root["kerf_used"] = resp.kerf_used;

    json placed = json::array();
    for (const auto& p : resp.result.hoja.piezas) {
        json pj;
        pj["nombre"] = p.nombre;
        pj["area"] = p.area;
        pj["calibre"] = p.calibre;
        pj["material"] = p.material;
        json polys = json::array();
        for (const auto& ring : p.poligonos) {
            polys.push_back(ring_to_json(ring));
        }
        pj["poligonos"] = polys;
        placed.push_back(pj);
    }
    root["placed"] = placed;

    json leftovers = json::array();
    for (const auto& p : resp.result.restos) {
        leftovers.push_back(p.nombre);
    }
    root["leftovers"] = leftovers;

    root["metrics"] = {
        {"area_usada", resp.result.hoja.area_usada},
        {"eficiencia", resp.result.hoja.eficiencia},
        {"placed_count", resp.result.hoja.piezas.size()},
        {"leftover_count", resp.result.restos.size()},
        {"min_gap_mm", resp.certify.min_gap_mm},
        {"common_line_mm", resp.common_lines.total_shared_mm},
        {"common_cut_paths", resp.common_cut_paths.paths_out},
        {"pierce_saved", resp.common_cut_paths.pierce_saved},
        {"sa_accepted", resp.sa.accepted},
        {"sa_improved", resp.sa.improved},
    };

    json issues = json::array();
    for (const auto& iss : resp.certify.issues) {
        issues.push_back({{"code", iss.code}, {"detail", iss.detail}});
    }
    root["certify"] = {
        {"ok", resp.certify.ok},
        {"placed_count", resp.certify.placed_count},
        {"min_gap_mm", resp.certify.min_gap_mm},
        {"issues", issues},
    };

    json cl = json::array();
    for (const auto& p : resp.common_lines.pairs) {
        json pj = {
            {"a", p.a},
            {"b", p.b},
            {"length_mm", p.length_mm},
            {"gap_mm", p.gap_mm},
            {"has_geom", p.has_geom},
        };
        if (p.has_geom) {
            pj["p0"] = {p.p0.x, p.p0.y};
            pj["p1"] = {p.p1.x, p.p1.y};
        }
        cl.push_back(pj);
    }
    root["common_lines"] = {
        {"total_shared_mm", resp.common_lines.total_shared_mm},
        {"pairs", cl},
        {"merged_paths", resp.common_cut_paths.paths_out},
        {"segments_in", resp.common_cut_paths.segments_in},
        {"pierce_saved", resp.common_cut_paths.pierce_saved},
        {"total_path_mm", resp.common_cut_paths.total_path_mm},
    };
    root["sa"] = {
        {"accepted", resp.sa.accepted},
        {"improved", resp.sa.improved},
        {"best_score", resp.sa.best_score},
        {"note", resp.sa.note},
    };
    root["cuda"] = {
        {"build_has_cuda", resp.cuda.build_has_cuda},
        {"runtime_available", resp.cuda.runtime_available},
        {"env_requested", resp.cuda.env_requested},
        {"detail", resp.cuda.detail},
    };
    root["features"] = {
        {"sa_refine", req.enable_sa_refine},
        {"common_line", req.enable_common_line},
        {"lod", req.enable_lod},
        {"tabu", req.enable_tabu},
        {"rotation_step_deg", req.rotation_step_deg},
        {"kerf_mm_contract", req.kerf_mm >= 0.0},
    };
    return root.dump();
}

}  // namespace arga::core
