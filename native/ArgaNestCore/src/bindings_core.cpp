#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "arga_nest/abi.h"
#include "arga_nest/engine_facade.hpp"
#include "arga_nest/nfp_cache.hpp"
#include "arga_nest/common_line.hpp"
#include "arga_nest/export_cam.hpp"
#include "arga_nest/dxf_io.hpp"
#include "packer.hpp"

#include <string>

namespace py = pybind11;

namespace {

std::string call_json_api(int (*fn)(const char*, char**), const std::string& request_json) {
    char* out = nullptr;
    const int rc = fn(request_json.c_str(), &out);
    if (!out) {
        throw std::runtime_error(
            std::string(arga_nest_last_error() ? arga_nest_last_error() : "error") +
            " (code=" + std::to_string(rc) + ")");
    }
    std::string out_s(out);
    arga_nest_free(out);
    // CERTIFY (6) still returns payload — caller checks certify.ok / ok
    if (rc != ARGA_NEST_OK && rc != ARGA_NEST_E_CERTIFY) {
        throw std::runtime_error(
            std::string(arga_nest_last_error() ? arga_nest_last_error() : "error") +
            " (code=" + std::to_string(rc) + ") body=" + out_s.substr(0, 200));
    }
    return out_s;
}

py::dict pack_sheet_dict(const py::dict& request) {
    py::module_ json = py::module_::import("json");
    const std::string req_s = py::cast<std::string>(json.attr("dumps")(request));
    const std::string out_s = call_json_api(&arga_nest_pack_sheet_json, req_s);
    return py::cast<py::dict>(json.attr("loads")(out_s));
}

}  // namespace

PYBIND11_MODULE(arga_nest_core, m) {
    m.doc() = "ArgaNestCore — núcleo nativo producto (ANS C++)";

    m.def("version_major", &arga_nest_version_major);
    m.def("version_minor", &arga_nest_version_minor);
    m.def("version_patch", &arga_nest_version_patch);
    m.def("version_string", []() {
        char* s = nullptr;
        if (arga_nest_version_string(&s) != ARGA_NEST_OK || !s) {
            throw std::runtime_error(arga_nest_last_error());
        }
        std::string out(s);
        arga_nest_free(s);
        return out;
    });

    m.def(
        "pack_sheet_json",
        [](const std::string& request_json) {
            return call_json_api(&arga_nest_pack_sheet_json, request_json);
        },
        py::arg("request_json"));
    m.def("pack_sheet", &pack_sheet_dict, py::arg("request"));

    m.def(
        "pack_job_json",
        [](const std::string& request_json) {
            return call_json_api(&arga_nest_pack_job_json, request_json);
        },
        py::arg("request_json"));
    m.def(
        "pack_cu_strip_json",
        [](const std::string& request_json) {
            return call_json_api(&arga_nest_pack_cu_strip_json, request_json);
        },
        py::arg("request_json"));
    m.def(
        "export_dxf_json",
        [](const std::string& request_json) {
            return call_json_api(&arga_nest_export_dxf_json, request_json);
        },
        py::arg("request_json"));
    m.def(
        "certify_dxf_json",
        [](const std::string& request_json) {
            return call_json_api(&arga_nest_certify_dxf_json, request_json);
        },
        py::arg("request_json"));
    m.def(
        "export_step_json",
        [](const std::string& request_json) {
            return call_json_api(&arga_nest_export_step_json, request_json);
        },
        py::arg("request_json"));

    m.def("nfp_cache_stats", []() {
        char* s = nullptr;
        arga_nest_nfp_cache_stats_json(&s);
        py::module_ json = py::module_::import("json");
        py::dict d = py::cast<py::dict>(json.attr("loads")(std::string(s ? s : "{}")));
        arga_nest_free(s);
        return d;
    });
    m.def("nfp_cache_reset", &arga_nest_nfp_cache_reset);
    m.def("nfp_l2_stats", []() {
        const auto st = arga::core::nfp_l2_stats();
        py::dict d;
        d["hits"] = st.hits;
        d["misses"] = st.misses;
        d["writes"] = st.evictions;
        d["dir"] = arga::core::nfp_l2_dir();
        return d;
    });

    m.def("cuda_status", []() {
        char* s = nullptr;
        arga_nest_cuda_status_json(&s);
        py::module_ json = py::module_::import("json");
        py::dict d = py::cast<py::dict>(json.attr("loads")(std::string(s ? s : "{}")));
        arga_nest_free(s);
        return d;
    });

    m.def("nfp_outer_cached", [](py::list a, py::list b, double kerf) {
        auto to_rings = [](py::list rings_py) {
            std::vector<std::vector<arga::Point2D>> rings;
            for (auto ring_obj : rings_py) {
                std::vector<arga::Point2D> ring;
                for (auto pt_obj : py::cast<py::list>(ring_obj)) {
                    auto pt = py::cast<py::tuple>(pt_obj);
                    ring.push_back({py::cast<double>(pt[0]), py::cast<double>(pt[1])});
                }
                rings.push_back(std::move(ring));
            }
            return rings;
        };
        auto out = arga::core::compute_nfp_outer_cached(to_rings(a), to_rings(b), 0, 0, kerf);
        py::list result;
        for (const auto& ring : out) {
            py::list r;
            for (const auto& p : ring) {
                r.append(py::make_tuple(p.x, p.y));
            }
            result.append(r);
        }
        return result;
    });

    m.def(
        "common_line_analyze",
        [](py::list placed, double max_gap_mm, double min_length_mm, double join_tol_mm) {
            arga::PackResult pr;
            for (auto obj : placed) {
                py::dict d = py::cast<py::dict>(obj);
                arga::PieceOut p;
                p.nombre = py::cast<std::string>(d["nombre"]);
                py::list polys = py::cast<py::list>(d["poligonos"]);
                for (auto ring_obj : polys) {
                    std::vector<arga::Point2D> ring;
                    for (auto pt_obj : py::cast<py::list>(ring_obj)) {
                        auto pt = py::cast<py::tuple>(pt_obj);
                        ring.push_back({py::cast<double>(pt[0]), py::cast<double>(pt[1])});
                    }
                    p.poligonos.push_back(std::move(ring));
                }
                pr.hoja.piezas.push_back(std::move(p));
            }
            const auto rep = arga::core::detect_common_lines(pr, max_gap_mm, min_length_mm);
            const auto merged = arga::core::merge_common_cut_paths(rep, join_tol_mm);
            py::dict out;
            out["total_shared_mm"] = rep.total_shared_mm;
            out["pair_count"] = static_cast<int>(rep.pairs.size());
            out["segments_in"] = merged.segments_in;
            out["merged_paths"] = merged.paths_out;
            out["pierce_saved"] = merged.pierce_saved;
            out["total_path_mm"] = merged.total_path_mm;
            py::list pairs;
            for (const auto& p : rep.pairs) {
                py::dict pj;
                pj["a"] = p.a;
                pj["b"] = p.b;
                pj["length_mm"] = p.length_mm;
                pj["gap_mm"] = p.gap_mm;
                pj["has_geom"] = p.has_geom;
                if (p.has_geom) {
                    pj["p0"] = py::make_tuple(p.p0.x, p.p0.y);
                    pj["p1"] = py::make_tuple(p.p1.x, p.p1.y);
                }
                pairs.append(pj);
            }
            out["pairs"] = pairs;
            return out;
        },
        py::arg("placed"),
        py::arg("max_gap_mm") = 0.5,
        py::arg("min_length_mm") = 5.0,
        py::arg("join_tol_mm") = 1.5);

    m.def(
        "export_machine_dxf",
        [](py::list placed, double max_gap_mm, double edge_tol_mm, bool machine_path) {
            arga::PackResult pr;
            for (auto obj : placed) {
                py::dict d = py::cast<py::dict>(obj);
                arga::PieceOut p;
                p.nombre = py::cast<std::string>(d["nombre"]);
                py::list polys = py::cast<py::list>(d["poligonos"]);
                for (auto ring_obj : polys) {
                    std::vector<arga::Point2D> ring;
                    for (auto pt_obj : py::cast<py::list>(ring_obj)) {
                        auto pt = py::cast<py::tuple>(pt_obj);
                        ring.push_back({py::cast<double>(pt[0]), py::cast<double>(pt[1])});
                    }
                    p.poligonos.push_back(std::move(ring));
                }
                pr.hoja.piezas.push_back(std::move(p));
            }
            const auto rep = arga::core::detect_common_lines(pr, max_gap_mm, 5.0);
            const auto merged = arga::core::merge_common_cut_paths(rep);
            const auto doc = arga::core::dxf_from_pack_with_common_paths(
                pr, rep, merged, "CUT_OUTER", machine_path, edge_tol_mm);
            return arga::core::dxf_write_ascii(doc);
        },
        py::arg("placed"),
        py::arg("max_gap_mm") = 0.5,
        py::arg("edge_tol_mm") = 1.0,
        py::arg("machine_path") = true);

    m.def("last_error", []() {
        const char* e = arga_nest_last_error();
        return e ? std::string(e) : std::string();
    });

    m.attr("ABI_VERSION") = "1.4.0";
    m.attr("CORE_NAME") = "ArgaNestCore";
}
