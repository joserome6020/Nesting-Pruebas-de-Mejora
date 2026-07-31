#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <optional>
#include <string>
#include <vector>

#include "cuda/raster_filter.hpp"
#include "packer_v2.hpp"

namespace py = pybind11;

namespace {

std::vector<arga_v2::Point2D> parse_ring(const py::list& ring) {
    std::vector<arga_v2::Point2D> out;
    out.reserve(ring.size());
    for (const auto& item : ring) {
        const py::tuple pt = py::cast<py::tuple>(item);
        if (pt.size() < 2) {
            continue;
        }
        out.push_back({py::cast<double>(pt[0]), py::cast<double>(pt[1])});
    }
    return out;
}

std::vector<std::vector<arga_v2::Point2D>> parse_paths(
    const py::object& obj,
    std::size_t min_points) {
    std::vector<std::vector<arga_v2::Point2D>> out;
    if (obj.is_none()) {
        return out;
    }
    const py::list paths = py::cast<py::list>(obj);
    for (const auto& path_obj : paths) {
        const auto path = parse_ring(py::cast<py::list>(path_obj));
        if (path.size() >= min_points) {
            out.push_back(path);
        }
    }
    return out;
}

std::vector<std::vector<arga_v2::Point2D>> parse_rings(const py::object& obj) {
    return parse_paths(obj, 3);
}

std::vector<std::vector<arga_v2::Point2D>> parse_marks(const py::object& obj) {
    return parse_paths(obj, 2);
}

arga_v2::PieceIn parse_piece(const py::dict& d) {
    arga_v2::PieceIn piece;
    piece.nombre = py::cast<std::string>(d["nombre"]);
    piece.area = py::cast<double>(d["area"]);
    piece.calibre = py::cast<std::string>(d["calibre"]);
    piece.material = py::cast<std::string>(d["material"]);
    piece.rings = parse_rings(d["rings"]);
    if (d.contains("marks")) {
        piece.marks = parse_marks(d["marks"]);
    }
    return piece;
}

py::list ring_to_py(const std::vector<arga_v2::Point2D>& ring) {
    py::list out;
    for (const auto& p : ring) {
        out.append(py::make_tuple(p.x, p.y));
    }
    return out;
}

py::list rings_to_py(const std::vector<std::vector<arga_v2::Point2D>>& rings) {
    py::list out;
    for (const auto& ring : rings) {
        out.append(ring_to_py(ring));
    }
    return out;
}

py::dict piece_out_to_py(const arga_v2::PieceOut& p) {
    py::dict d;
    d["nombre"] = p.nombre;
    d["poligonos"] = rings_to_py(p.poligonos);
    d["marcas"] = rings_to_py(p.marcas);
    d["area"] = p.area;
    d["calibre"] = p.calibre;
    d["material"] = p.material;
    return d;
}

py::dict sheet_to_py(const arga_v2::SheetOut& hoja) {
    py::dict d;
    py::list piezas;
    for (const auto& p : hoja.piezas) {
        piezas.append(piece_out_to_py(p));
    }
    d["piezas"] = piezas;
    d["area_usada"] = hoja.area_usada;
    d["eficiencia"] = hoja.eficiencia;
    py::dict cuda_raster;
    cuda_raster["enabled"] = hoja.cuda_raster.enabled;
    cuda_raster["cuda_used"] = hoja.cuda_raster.cuda_used;
    cuda_raster["candidates_evaluated"] = hoja.cuda_raster.candidates_evaluated;
    cuda_raster["safe_rejected"] = hoja.cuda_raster.safe_rejected;
    cuda_raster["h2d_bytes"] = hoja.cuda_raster.h2d_bytes;
    cuda_raster["d2h_bytes"] = hoja.cuda_raster.d2h_bytes;
    cuda_raster["h2d_ms"] = hoja.cuda_raster.h2d_ms;
    cuda_raster["kernel_ms"] = hoja.cuda_raster.kernel_ms;
    cuda_raster["d2h_ms"] = hoja.cuda_raster.d2h_ms;
    d["cuda_raster"] = cuda_raster;
    py::dict packer_timing;
    packer_timing["candidate_count"] = hoja.packer_timing.candidate_count;
    packer_timing["candidate_generation_ms"] = hoja.packer_timing.candidate_generation_ms;
    packer_timing["exact_collision_ms"] = hoja.packer_timing.exact_collision_ms;
    packer_timing["rasterization_ms"] = hoja.packer_timing.rasterization_ms;
    d["packer_timing"] = packer_timing;
    return d;
}

py::dict piece_in_to_py(const arga_v2::PieceIn& p) {
    py::dict d;
    d["nombre"] = p.nombre;
    d["area"] = p.area;
    d["calibre"] = p.calibre;
    d["material"] = p.material;
    d["rings"] = rings_to_py(p.rings);
    d["marks"] = rings_to_py(p.marks);
    return d;
}

py::dict nfp_cache_stats_to_py(const arga_v2::NfpCacheStats& stats) {
    py::dict d;
    d["hits"] = stats.hits;
    d["misses"] = stats.misses;
    d["evictions"] = stats.evictions;
    d["entries"] = stats.entries;
    d["capacity"] = stats.capacity;
    const auto requests = stats.hits + stats.misses;
    d["requests"] = requests;
    d["hit_rate"] = requests > 0
        ? static_cast<double>(stats.hits) / static_cast<double>(requests)
        : 0.0;
    return d;
}

py::dict nfp_cache_workload_to_py(const arga_v2::NfpCacheWorkloadResult& result) {
    py::dict d;
    d["calls"] = result.calls;
    d["preparation_ms"] = result.preparation_ms;
    d["lookup_ms"] = result.lookup_ms;
    d["cache"] = nfp_cache_stats_to_py(result.cache);
    return d;
}

std::vector<std::uint8_t> parse_mask(const py::object& obj) {
    std::vector<std::uint8_t> out;
    for (const auto& item : py::cast<py::iterable>(obj)) {
        out.push_back(py::cast<int>(item) != 0 ? 1 : 0);
    }
    return out;
}

std::vector<arga_v2::cuda::RasterOffset> parse_offsets(const py::object& obj) {
    std::vector<arga_v2::cuda::RasterOffset> out;
    for (const auto& item : py::cast<py::iterable>(obj)) {
        const py::tuple point = py::cast<py::tuple>(item);
        if (point.size() < 2) {
            continue;
        }
        out.push_back({py::cast<int>(point[0]), py::cast<int>(point[1])});
    }
    return out;
}

py::dict raster_stats_to_py(const arga_v2::cuda::RasterFilterStats& stats) {
    py::dict out;
    out["cuda_available"] = stats.cuda_available;
    out["cuda_used"] = stats.cuda_used;
    out["candidates_evaluated"] = stats.candidates_evaluated;
    out["safe_rejected"] = stats.safe_rejected;
    out["batches_evaluated"] = stats.batches_evaluated;
    out["h2d_bytes"] = stats.h2d_bytes;
    out["d2h_bytes"] = stats.d2h_bytes;
    out["h2d_ms"] = stats.h2d_ms;
    out["kernel_ms"] = stats.kernel_ms;
    out["d2h_ms"] = stats.d2h_ms;
    return out;
}

std::vector<std::vector<arga_v2::cuda::RasterOffset>> parse_offset_batches(
    const py::object& obj) {
    std::vector<std::vector<arga_v2::cuda::RasterOffset>> out;
    for (const auto& batch_obj : py::cast<py::iterable>(obj)) {
        out.push_back(parse_offsets(py::reinterpret_borrow<py::object>(batch_obj)));
    }
    return out;
}

py::dict population_screen_to_py(
    const arga_v2::cuda::PopulationScreenResult& result) {
    py::dict out;
    py::list rejected_py;
    for (const auto& batch : result.rejected_per_seed) {
        py::list seed_py;
        for (const auto value : batch) {
            seed_py.append(value != 0);
        }
        rejected_py.append(seed_py);
    }
    out["rejected_per_seed"] = rejected_py;
    out["stats"] = raster_stats_to_py(result.stats);
    return out;
}

}  // namespace

PYBIND11_MODULE(algorithm_cpp_v2, m) {
    m.doc() = "Motor nesting C++ v2 PoC (aislado; no producción)";

    m.def(
        "echo_rings",
        [](py::object rings_obj) {
            return rings_to_py(arga_v2::echo_rings(parse_rings(rings_obj)));
        },
        py::arg("rings"));

    m.def(
        "polygons_overlap",
        [](py::object rings_a, py::object rings_b) {
            return arga_v2::polygons_overlap(parse_rings(rings_a), parse_rings(rings_b));
        },
        py::arg("rings_a"),
        py::arg("rings_b"));

    m.def(
        "compute_nfp_outer",
        [](py::object rings_a, py::object rings_b) {
            return rings_to_py(
                arga_v2::compute_nfp_outer(parse_rings(rings_a), parse_rings(rings_b)));
        },
        py::arg("rings_a"),
        py::arg("rings_b"),
        "NFP outer via Clipper2 MinkowskiSum(inv(B), A). Ver packer_v2.hpp.");

    m.def(
        "compute_nfp_outer_cached",
        [](py::object rings_a,
           py::object rings_b,
           double angle_a_deg,
           double angle_b_deg,
           double kerf_mm) {
            const auto a = parse_rings(rings_a);
            const auto b = parse_rings(rings_b);
            std::vector<std::vector<arga_v2::Point2D>> nfp;
            {
                py::gil_scoped_release release;
                nfp = arga_v2::compute_nfp_outer_cached(
                    a, b, angle_a_deg, angle_b_deg, kerf_mm);
            }
            return rings_to_py(nfp);
        },
        py::arg("rings_a"),
        py::arg("rings_b"),
        py::arg("angle_a_deg") = 0.0,
        py::arg("angle_b_deg") = 0.0,
        py::arg("kerf_mm") = 0.0,
        "NFP outer con caché L1 canónica thread-safe.");

    m.def(
        "nfp_cache_stats",
        []() { return nfp_cache_stats_to_py(arga_v2::nfp_cache_stats()); },
        "Estadísticas del caché NFP L1.");
    m.def("reset_nfp_cache", []() { arga_v2::reset_nfp_cache(); });
    m.def(
        "set_nfp_cache_capacity",
        [](std::size_t capacity) { arga_v2::set_nfp_cache_capacity(capacity); },
        py::arg("capacity"));
    m.def(
        "run_nfp_cache_workload",
        [](py::list piezas_in, std::size_t iterations, double kerf_mm) {
            std::vector<arga_v2::PieceIn> piezas;
            piezas.reserve(piezas_in.size());
            for (const auto& item : piezas_in) {
                piezas.push_back(parse_piece(py::cast<py::dict>(item)));
            }
            arga_v2::NfpCacheWorkloadResult result;
            {
                py::gil_scoped_release release;
                result = arga_v2::run_nfp_cache_workload(piezas, iterations, kerf_mm);
            }
            return nfp_cache_workload_to_py(result);
        },
        py::arg("piezas"),
        py::arg("iterations") = 1,
        py::arg("kerf_mm") = 0.0,
        "Workload NFP con normalización por lote y caché L1.");

    m.def(
        "cuda_raster_filter_available",
        []() { return arga_v2::cuda::available(); },
        "True si el filtro raster CUDA opcional está disponible.");
    m.def(
        "cuda_raster_filter_status",
        []() { return arga_v2::cuda::availability_detail(); },
        "Diagnóstico del runtime CUDA o del fallback CPU.");
    py::class_<arga_v2::cuda::RasterSession>(m, "CudaRasterSession")
        .def(
            py::init([](
                py::object fixed_inner,
                int fixed_w,
                int fixed_h,
                bool prefer_cuda) {
                return arga_v2::cuda::RasterSession(
                    parse_mask(fixed_inner), fixed_w, fixed_h, prefer_cuda);
            }),
            py::arg("fixed_inner"),
            py::arg("fixed_w"),
            py::arg("fixed_h"),
            py::arg("prefer_cuda") = true)
        .def("cuda_active", &arga_v2::cuda::RasterSession::cuda_active)
        .def(
            "update_fixed",
            [](arga_v2::cuda::RasterSession& session,
               py::object fixed_inner,
               int fixed_w,
               int fixed_h) {
                return session.update_fixed(parse_mask(fixed_inner), fixed_w, fixed_h);
            },
            py::arg("fixed_inner"),
            py::arg("fixed_w"),
            py::arg("fixed_h"))
        .def(
            "set_candidate",
            [](arga_v2::cuda::RasterSession& session,
               py::object candidate_inner,
               int candidate_w,
               int candidate_h) {
                return session.set_candidate(
                    parse_mask(candidate_inner), candidate_w, candidate_h);
            },
            py::arg("candidate_inner"),
            py::arg("candidate_w"),
            py::arg("candidate_h"),
            "Deja la máscara candidata residente para varias semillas.")
        .def(
            "safe_reject_batch",
            [](arga_v2::cuda::RasterSession& session,
               py::object candidate_inner,
               int candidate_w,
               int candidate_h,
               py::object offsets) {
                arga_v2::cuda::RasterFilterStats stats;
                const auto rejected = session.safe_reject_batch(
                    parse_mask(candidate_inner),
                    candidate_w,
                    candidate_h,
                    parse_offsets(offsets),
                    &stats);
                py::dict out;
                py::list rejected_py;
                for (const auto value : rejected) {
                    rejected_py.append(value != 0);
                }
                out["rejected"] = rejected_py;
                out["stats"] = raster_stats_to_py(stats);
                return out;
            },
            py::arg("candidate_inner"),
            py::arg("candidate_w"),
            py::arg("candidate_h"),
            py::arg("offsets"),
            "Evalúa un lote reutilizando la máscara fija residente.")
        .def(
            "screen_population",
            [](arga_v2::cuda::RasterSession& session,
               py::object candidate_inner,
               int candidate_w,
               int candidate_h,
               py::object offset_batches) {
                const auto candidate = parse_mask(candidate_inner);
                const auto batches = parse_offset_batches(offset_batches);
                arga_v2::cuda::PopulationScreenResult result;
                {
                    py::gil_scoped_release release;
                    result = session.screen_population(
                        candidate, candidate_w, candidate_h, batches);
                }
                return population_screen_to_py(result);
            },
            py::arg("candidate_inner"),
            py::arg("candidate_w"),
            py::arg("candidate_h"),
            py::arg("offset_batches"),
            "Criba muchas semillas en una sola llamada C++/CUDA.");
    m.def(
        "cuda_raster_screen_population",
        [](py::object fixed_inner,
           int fixed_w,
           int fixed_h,
           py::object candidate_inner,
           int candidate_w,
           int candidate_h,
           py::object offset_batches,
           bool prefer_cuda) {
            arga_v2::cuda::RasterSession session(
                parse_mask(fixed_inner), fixed_w, fixed_h, prefer_cuda);
            const auto candidate = parse_mask(candidate_inner);
            const auto batches = parse_offset_batches(offset_batches);
            arga_v2::cuda::PopulationScreenResult result;
            {
                py::gil_scoped_release release;
                result = session.screen_population(
                    candidate, candidate_w, candidate_h, batches);
            }
            auto out = population_screen_to_py(result);
            out["cuda_active"] = session.cuda_active();
            return out;
        },
        py::arg("fixed_inner"),
        py::arg("fixed_w"),
        py::arg("fixed_h"),
        py::arg("candidate_inner"),
        py::arg("candidate_w"),
        py::arg("candidate_h"),
        py::arg("offset_batches"),
        py::arg("prefer_cuda") = true,
        "API de una llamada: abre sesión, criba población y cierra.");
    m.def(
        "cuda_raster_safe_reject_batch",
        [](py::object fixed_inner,
           int fixed_w,
           int fixed_h,
           py::object candidate_inner,
           int candidate_w,
           int candidate_h,
           py::object offsets,
           bool prefer_cuda) {
            arga_v2::cuda::RasterFilterStats stats;
            const auto rejected = arga_v2::cuda::safe_reject_batch(
                parse_mask(fixed_inner),
                fixed_w,
                fixed_h,
                parse_mask(candidate_inner),
                candidate_w,
                candidate_h,
                parse_offsets(offsets),
                &stats,
                prefer_cuda);
            py::dict out;
            py::list rejected_py;
            for (const auto value : rejected) {
                rejected_py.append(value != 0);
            }
            out["rejected"] = rejected_py;
            out["stats"] = raster_stats_to_py(stats);
            return out;
        },
        py::arg("fixed_inner"),
        py::arg("fixed_w"),
        py::arg("fixed_h"),
        py::arg("candidate_inner"),
        py::arg("candidate_w"),
        py::arg("candidate_h"),
        py::arg("offsets"),
        py::arg("prefer_cuda") = true,
        "Filtro raster conservador: solo rechaza colisiones demostradas.");

    m.def(
        "empaquetar_una_hoja_poc",
        [](py::list piezas_in,
           double w_placa,
           double h_placa,
           double kerf_override,
           double margin_override,
           const std::string& opt_override,
           const std::string& corner_override,
           py::object limite_rings_obj) {
            std::vector<arga_v2::PieceIn> piezas;
            piezas.reserve(piezas_in.size());
            for (const auto& item : piezas_in) {
                piezas.push_back(parse_piece(py::cast<py::dict>(item)));
            }

            std::optional<std::vector<std::vector<arga_v2::Point2D>>> limite;
            if (!limite_rings_obj.is_none()) {
                auto rings = parse_rings(limite_rings_obj);
                if (!rings.empty()) {
                    limite = rings;
                }
            }

            arga_v2::PackResult result;
            {
                py::gil_scoped_release release;
                result = arga_v2::empaquetar_una_hoja_poc(
                    piezas,
                    w_placa,
                    h_placa,
                    kerf_override,
                    margin_override,
                    opt_override,
                    corner_override,
                    limite);
            }

            py::list restos;
            for (const auto& p : result.restos) {
                restos.append(piece_in_to_py(p));
            }
            return py::make_tuple(sheet_to_py(result.hoja), restos);
        },
        py::arg("piezas"),
        py::arg("w_placa"),
        py::arg("h_placa"),
        py::arg("kerf_override") = 0.3,
        py::arg("margin_override") = 0.0,
        py::arg("opt_override") = "OPTIMIZAR LARGO Y ANCHO",
        py::arg("corner_override") = "INFERIOR IZQUIERDA",
        py::arg("limite_rings") = py::none());

    m.attr("ENGINE_NAME") = "cpp_v2_poc";
    m.attr("ENGINE_VERSION") = "0.1.0-poc";
}
