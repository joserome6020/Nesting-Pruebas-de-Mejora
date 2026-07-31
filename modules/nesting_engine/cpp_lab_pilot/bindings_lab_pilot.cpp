#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <algorithm>
#include <optional>
#include <string>

#include "packer_lab.hpp"

#if defined(ARGA_LAB_PILOT)
#include "cuda/lab_grid_filter.hpp"
#endif

namespace py = pybind11;

namespace {

std::vector<arga::Point2D> parse_ring(const py::handle& ring_obj) {
    std::vector<arga::Point2D> ring;
    for (const auto& item : py::cast<py::iterable>(ring_obj)) {
        const auto point = py::cast<py::tuple>(item);
        if (point.size() >= 2) {
            ring.push_back({py::cast<double>(point[0]), py::cast<double>(point[1])});
        }
    }
    return ring;
}

std::vector<std::vector<arga::Point2D>> parse_paths(
    const py::object& paths_obj,
    std::size_t minimum_points) {
    std::vector<std::vector<arga::Point2D>> paths;
    if (paths_obj.is_none()) {
        return paths;
    }
    for (const auto& ring_obj : py::cast<py::iterable>(paths_obj)) {
        auto ring = parse_ring(ring_obj);
        if (ring.size() >= minimum_points) {
            paths.push_back(std::move(ring));
        }
    }
    return paths;
}

arga::PieceIn parse_piece(const py::dict& source) {
    arga::PieceIn piece;
    piece.nombre = py::cast<std::string>(source["nombre"]);
    piece.area = py::cast<double>(source["area"]);
    piece.calibre = py::cast<std::string>(source["calibre"]);
    piece.material = py::cast<std::string>(source["material"]);
    piece.rings = parse_paths(source["rings"], 3);
    if (source.contains("marks")) {
        piece.marks = parse_paths(source["marks"], 2);
    }
    return piece;
}

py::list rings_to_py(const std::vector<std::vector<arga::Point2D>>& rings) {
    py::list output;
    for (const auto& ring : rings) {
        py::list py_ring;
        for (const auto& point : ring) {
            py_ring.append(py::make_tuple(point.x, point.y));
        }
        output.append(std::move(py_ring));
    }
    return output;
}

py::dict piece_to_py(const arga::PieceOut& piece) {
    py::dict output;
    output["nombre"] = piece.nombre;
    output["poligonos"] = rings_to_py(piece.poligonos);
    output["marcas"] = rings_to_py(piece.marcas);
    output["area"] = piece.area;
    output["calibre"] = piece.calibre;
    output["material"] = piece.material;
    return output;
}

py::dict input_piece_to_py(const arga::PieceIn& piece) {
    py::dict output;
    output["nombre"] = piece.nombre;
    output["area"] = piece.area;
    output["calibre"] = piece.calibre;
    output["material"] = piece.material;
    output["rings"] = rings_to_py(piece.rings);
    output["marks"] = rings_to_py(piece.marks);
    return output;
}

py::dict timeline_to_py(const arga::TimelinePackResult& result) {
    py::dict output;
    py::dict sheet;
    py::list placed;
    for (const auto& piece : result.pack.hoja.piezas) {
        placed.append(piece_to_py(piece));
    }
    sheet["piezas"] = std::move(placed);
    sheet["area_usada"] = result.pack.hoja.area_usada;
    sheet["eficiencia"] = result.pack.hoja.eficiencia;

    py::list remains;
    for (const auto& piece : result.pack.restos) {
        remains.append(input_piece_to_py(piece));
    }
    py::list order;
    for (const auto& name : result.orden_piezas) {
        order.append(name);
    }

    output["hoja"] = std::move(sheet);
    output["restos"] = std::move(remains);
    output["orden_piezas"] = std::move(order);
    output["mc_iteracion_ganadora"] = result.mc_iteracion_ganadora;
    output["mc_orden_modo"] = result.mc_orden_modo;
    py::dict cuda_screen;
    cuda_screen["enabled"] = result.cuda_screen.enabled;
    cuda_screen["cuda_used"] = result.cuda_screen.cuda_used;
    cuda_screen["candidates_evaluated"] = result.cuda_screen.candidates_evaluated;
    cuda_screen["collisions"] = result.cuda_screen.collisions;
    cuda_screen["h2d_bytes"] = result.cuda_screen.h2d_bytes;
    cuda_screen["d2h_bytes"] = result.cuda_screen.d2h_bytes;
    cuda_screen["h2d_ms"] = result.cuda_screen.h2d_ms;
    cuda_screen["kernel_ms"] = result.cuda_screen.kernel_ms;
    cuda_screen["d2h_ms"] = result.cuda_screen.d2h_ms;
    output["cuda_screen"] = std::move(cuda_screen);
    return output;
}

}  // namespace

PYBIND11_MODULE(algorithm_cpp_lab_pilot, module) {
    module.doc() = "ARGA LAB Pilot: ruta timeline aislada y empaquetada";
#if defined(ARGA_LAB_PILOT)
    module.def(
        "lab_pilot_cuda_available",
        []() {
#if ARGA_LAB_PILOT_HAS_CUDA
            return arga::lab_cuda::available();
#else
            return false;
#endif
        });
    module.def(
        "lab_pilot_cuda_status",
        []() {
#if ARGA_LAB_PILOT_HAS_CUDA
            return arga::lab_cuda::availability_detail();
#else
            return std::string("CUDA no incluido en este build del piloto.");
#endif
        });
#endif
    module.def(
        "empaquetar_una_hoja_timeline",
        [](py::list pieces,
           double plate_width,
           double plate_height,
           double kerf,
           double margin,
           const std::string& optimization,
           const std::string& corner,
           py::object limit_rings,
           int mc_iterations) {
            std::vector<arga::PieceIn> native_pieces;
            native_pieces.reserve(pieces.size());
            for (const auto& item : pieces) {
                native_pieces.push_back(parse_piece(py::cast<py::dict>(item)));
            }
            std::optional<std::vector<std::vector<arga::Point2D>>> limit;
            if (!limit_rings.is_none()) {
                auto parsed = parse_paths(limit_rings, 3);
                if (!parsed.empty()) {
                    limit = std::move(parsed);
                }
            }
            arga::TimelinePackResult result;
            {
                py::gil_scoped_release release;
                result = arga::empaquetar_una_hoja_timeline(
                    native_pieces,
                    plate_width,
                    plate_height,
                    kerf,
                    margin,
                    optimization,
                    corner,
                    limit,
                    std::max(1, std::min(mc_iterations, 4)));
            }
            return timeline_to_py(result);
        },
        py::arg("piezas"),
        py::arg("w_placa"),
        py::arg("h_placa"),
        py::arg("kerf_override") = 0.2,
        py::arg("margin_override") = 0.0,
        py::arg("opt_override") = "OPTIMIZAR LARGO Y ANCHO",
        py::arg("corner_override") = "INFERIOR IZQUIERDA",
        py::arg("limite_rings") = py::none(),
        py::arg("mc_iterations") = 1);
    module.attr("ENGINE_NAME") = "arga_lab_pilot_timeline";
    module.attr("ENGINE_VERSION") = "0.1.0-pilot";
}
