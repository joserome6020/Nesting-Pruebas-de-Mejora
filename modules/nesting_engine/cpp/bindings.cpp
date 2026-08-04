#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <cstdint>
#include <optional>
#include <stdexcept>

#include "packer.hpp"
#include "packer_base.hpp"
#include "packer_burke_blf.hpp"
#include "packer_libnest2d.hpp"
#include "packer_svgnest_ultra.hpp"
#include "cuda/nest_accel.hpp"
#include "cuda/nest_accel_raster.hpp"

namespace py = pybind11;

namespace {

std::vector<arga::Point2D> parse_ring(const py::list& ring) {
    std::vector<arga::Point2D> out;
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

std::vector<std::vector<arga::Point2D>> parse_paths(
    const py::object& obj,
    std::size_t min_points) {
    std::vector<std::vector<arga::Point2D>> out;
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

std::vector<std::vector<arga::Point2D>> parse_rings(const py::object& obj) {
    return parse_paths(obj, 3);
}

std::vector<std::vector<arga::Point2D>> parse_marks(const py::object& obj) {
    // Marcaje DXF: trazos abiertos (mín. 2 vértices), no anillos cerrados.
    return parse_paths(obj, 2);
}

arga::PieceIn parse_piece(const py::dict& d) {
    arga::PieceIn piece;
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

py::list ring_to_py(const std::vector<arga::Point2D>& ring) {
    py::list out;
    for (const auto& p : ring) {
        out.append(py::make_tuple(p.x, p.y));
    }
    return out;
}

py::list rings_to_py(const std::vector<std::vector<arga::Point2D>>& rings) {
    py::list out;
    for (const auto& ring : rings) {
        out.append(ring_to_py(ring));
    }
    return out;
}

py::dict piece_out_to_py(const arga::PieceOut& p) {
    py::dict d;
    d["nombre"] = p.nombre;
    d["poligonos"] = rings_to_py(p.poligonos);
    d["marcas"] = rings_to_py(p.marcas);
    d["area"] = p.area;
    d["calibre"] = p.calibre;
    d["material"] = p.material;
    return d;
}

py::dict sheet_to_py(const arga::SheetOut& hoja) {
    py::dict d;
    py::list piezas;
    for (const auto& p : hoja.piezas) {
        piezas.append(piece_out_to_py(p));
    }
    d["piezas"] = piezas;
    d["area_usada"] = hoja.area_usada;
    d["eficiencia"] = hoja.eficiencia;
    return d;
}

py::dict piece_in_to_py(const arga::PieceIn& p) {
    py::dict d;
    d["nombre"] = p.nombre;
    d["area"] = p.area;
    d["calibre"] = p.calibre;
    d["material"] = p.material;
    d["rings"] = rings_to_py(p.rings);
    d["marks"] = rings_to_py(p.marks);
    return d;
}

}  // namespace

PYBIND11_MODULE(algorithm_cpp, m) {
    m.doc() = "Motor de nesting nativo C++ (Arga Suite)";

    m.def(
        "empaquetar_una_hoja_mc",
        [](py::list piezas_in,
           double w_placa,
           double h_placa,
           double kerf_override,
           double margin_override,
           const std::string& opt_override,
           const std::string& corner_override,
           py::object limite_rings_obj,
           int mc_iterations) {
            std::vector<arga::PieceIn> piezas;
            piezas.reserve(piezas_in.size());
            for (const auto& item : piezas_in) {
                piezas.push_back(parse_piece(py::cast<py::dict>(item)));
            }

            std::optional<std::vector<std::vector<arga::Point2D>>> limite;
            if (!limite_rings_obj.is_none()) {
                auto rings = parse_rings(limite_rings_obj);
                if (!rings.empty()) {
                    limite = rings;
                }
            }

            arga::PackResult result;
            {
                py::gil_scoped_release release;
                result = arga::empaquetar_una_hoja_mc(
                    piezas,
                    w_placa,
                    h_placa,
                    kerf_override,
                    margin_override,
                    opt_override,
                    corner_override,
                    limite,
                    nullptr,
                    mc_iterations);
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
        py::arg("kerf_override") = 0.2,
        py::arg("margin_override") = 0.0,
        py::arg("opt_override") = "OPTIMIZAR LARGO Y ANCHO",
        py::arg("corner_override") = "INFERIOR IZQUIERDA",
        py::arg("limite_rings") = py::none(),
        py::arg("mc_iterations") = arga::kMonteCarloIterationsDefault);

    m.def(
        "empaquetar_una_hoja_base",
        [](py::list piezas_in,
           double w_placa,
           double h_placa,
           double kerf_override,
           double margin_override,
           const std::string& opt_override,
           const std::string& corner_override,
           py::object limite_rings_obj) {
            std::vector<arga::PieceIn> piezas;
            piezas.reserve(piezas_in.size());
            for (const auto& item : piezas_in) {
                piezas.push_back(parse_piece(py::cast<py::dict>(item)));
            }

            std::optional<std::vector<std::vector<arga::Point2D>>> limite;
            if (!limite_rings_obj.is_none()) {
                auto rings = parse_rings(limite_rings_obj);
                if (!rings.empty()) {
                    limite = rings;
                }
            }

            arga::PackResult result;
            {
                py::gil_scoped_release release;
                result = arga::empaquetar_una_hoja_base(
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
        py::arg("kerf_override") = 0.2,
        py::arg("margin_override") = 0.0,
        py::arg("opt_override") = "OPTIMIZAR LARGO Y ANCHO",
        py::arg("corner_override") = "INFERIOR IZQUIERDA",
        py::arg("limite_rings") = py::none());

    m.def(
        "empaquetar_una_hoja_burke_blf",
        [](py::list piezas_in,
           double w_placa,
           double h_placa,
           double kerf_override,
           double margin_override,
           const std::string& opt_override,
           const std::string& corner_override,
           py::object limite_rings_obj,
           int hill_climb_iterations,
           std::uint32_t rng_seed,
           bool preserve_input_order) {
            std::vector<arga::PieceIn> piezas;
            piezas.reserve(piezas_in.size());
            for (const auto& item : piezas_in) {
                piezas.push_back(parse_piece(py::cast<py::dict>(item)));
            }

            std::optional<std::vector<std::vector<arga::Point2D>>> limite;
            if (!limite_rings_obj.is_none()) {
                auto rings = parse_rings(limite_rings_obj);
                if (!rings.empty()) {
                    limite = rings;
                }
            }

            arga::PackResult result;
            {
                py::gil_scoped_release release;
                result = arga::empaquetar_una_hoja_burke_blf(
                    piezas,
                    w_placa,
                    h_placa,
                    kerf_override,
                    margin_override,
                    opt_override,
                    corner_override,
                    limite,
                    hill_climb_iterations,
                    rng_seed,
                    preserve_input_order);
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
        py::arg("kerf_override") = 0.2,
        py::arg("margin_override") = 0.0,
        py::arg("opt_override") = "OPTIMIZAR LARGO Y ANCHO",
        py::arg("corner_override") = "INFERIOR IZQUIERDA",
        py::arg("limite_rings") = py::none(),
        py::arg("hill_climb_iterations") = 10,
        py::arg("rng_seed") = 1,
        py::arg("preserve_input_order") = true);

    m.def(
        "empaquetar_una_hoja_libnest2d",
        [](py::list piezas_in,
           double w_placa,
           double h_placa,
           double kerf_override,
           double margin_override,
           const std::string& opt_override,
           const std::string& corner_override,
           py::object limite_rings_obj,
           int selector_iterations) {
            std::vector<arga::PieceIn> piezas;
            piezas.reserve(piezas_in.size());
            for (const auto& item : piezas_in) {
                piezas.push_back(parse_piece(py::cast<py::dict>(item)));
            }

            std::optional<std::vector<std::vector<arga::Point2D>>> limite;
            if (!limite_rings_obj.is_none()) {
                auto rings = parse_rings(limite_rings_obj);
                if (!rings.empty()) {
                    limite = rings;
                }
            }

            arga::PackResult result;
            {
                py::gil_scoped_release release;
                result = arga::empaquetar_una_hoja_libnest2d(
                    piezas,
                    w_placa,
                    h_placa,
                    kerf_override,
                    margin_override,
                    opt_override,
                    corner_override,
                    limite,
                    selector_iterations);
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
        py::arg("kerf_override") = 0.2,
        py::arg("margin_override") = 0.0,
        py::arg("opt_override") = "OPTIMIZAR LARGO Y ANCHO",
        py::arg("corner_override") = "INFERIOR IZQUIERDA",
        py::arg("limite_rings") = py::none(),
        py::arg("selector_iterations") = 8);

    m.def(
        "empaquetar_una_hoja_svgnest_ultra",
        [](py::list piezas_in,
           double w_placa,
           double h_placa,
           double kerf_override,
           double margin_override,
           const std::string& opt_override,
           const std::string& corner_override,
           py::object limite_rings_obj,
           int ga_population,
           int ga_generations,
           double rotation_step_deg,
           bool part_in_part,
           std::uint32_t ga_seed,
           py::object seed_order_obj) {
            std::vector<arga::PieceIn> piezas;
            piezas.reserve(piezas_in.size());
            for (const auto& item : piezas_in) {
                piezas.push_back(parse_piece(py::cast<py::dict>(item)));
            }

            std::optional<std::vector<std::vector<arga::Point2D>>> limite;
            if (!limite_rings_obj.is_none()) {
                auto rings = parse_rings(limite_rings_obj);
                if (!rings.empty()) {
                    limite = rings;
                }
            }

            std::vector<size_t> seed_order;
            const std::vector<size_t>* seed_ptr = nullptr;
            if (!seed_order_obj.is_none()) {
                py::sequence seq = py::cast<py::sequence>(seed_order_obj);
                seed_order.reserve(seq.size());
                for (auto item : seq) {
                    seed_order.push_back(static_cast<size_t>(py::cast<py::int_>(item)));
                }
                if (!seed_order.empty()) {
                    seed_ptr = &seed_order;
                }
            }

            arga::PackResult result;
            {
                py::gil_scoped_release release;
                result = arga::empaquetar_una_hoja_svgnest_ultra(
                    piezas,
                    w_placa,
                    h_placa,
                    kerf_override,
                    margin_override,
                    opt_override,
                    corner_override,
                    limite,
                    ga_population,
                    ga_generations,
                    rotation_step_deg,
                    part_in_part,
                    ga_seed,
                    seed_ptr);
            }

            py::list restos;
            for (const auto& p : result.restos) {
                restos.append(piece_in_to_py(p));
            }
            py::list orden;
            for (size_t idx : result.orden) {
                orden.append(static_cast<int>(idx));
            }
            return py::make_tuple(sheet_to_py(result.hoja), restos, orden);
        },
        py::arg("piezas"),
        py::arg("w_placa"),
        py::arg("h_placa"),
        py::arg("kerf_override") = 0.2,
        py::arg("margin_override") = 0.0,
        py::arg("opt_override") = "OPTIMIZAR LARGO Y ANCHO",
        py::arg("corner_override") = "INFERIOR IZQUIERDA",
        py::arg("limite_rings") = py::none(),
        py::arg("ga_population") = 30,
        py::arg("ga_generations") = 30,
        py::arg("rotation_step_deg") = 15.0,
        py::arg("part_in_part") = true,
        py::arg("ga_seed") = 0,
        py::arg("seed_order") = py::none());

    m.def(
        "nest_cuda_available",
        []() { return arga::cuda::available(); },
        "True si el build/runtime CUDA está disponible en algorithm_cpp.");
    m.def(
        "nest_cuda_status",
        []() { return arga::cuda::availability_detail(); },
        "Detalle textual del estado CUDA.");
    m.def(
        "nest_cuda_requested",
        []() { return arga::cuda::requested(); },
        "True si ARGA_NEST_CUDA (u override por motor) está activo.");
    m.def(
        "nest_filter_translations",
        [](const py::list& fixed_rings_list,
           const py::list& piece_rings,
           double plate_w_mm,
           double plate_h_mm,
           const py::list& translations_xy,
           double cell_mm) {
            std::vector<Clipper2Lib::PathsD> fixed;
            fixed.reserve(fixed_rings_list.size());
            for (const auto& item : fixed_rings_list) {
                Clipper2Lib::PathsD paths;
                const py::list rings = py::cast<py::list>(item);
                for (const auto& ring_obj : rings) {
                    const auto ring = parse_ring(py::cast<py::list>(ring_obj));
                    if (ring.size() < 3) {
                        continue;
                    }
                    Clipper2Lib::PathD path;
                    path.reserve(ring.size());
                    for (const auto& p : ring) {
                        path.emplace_back(p.x, p.y);
                    }
                    paths.push_back(std::move(path));
                }
                if (!paths.empty()) {
                    fixed.push_back(std::move(paths));
                }
            }

            Clipper2Lib::PathsD piece;
            for (const auto& ring_obj : piece_rings) {
                const auto ring = parse_ring(py::cast<py::list>(ring_obj));
                if (ring.size() < 3) {
                    continue;
                }
                Clipper2Lib::PathD path;
                path.reserve(ring.size());
                for (const auto& p : ring) {
                    path.emplace_back(p.x, p.y);
                }
                piece.push_back(std::move(path));
            }

            std::vector<std::pair<double, double>> xy;
            xy.reserve(translations_xy.size());
            for (const auto& t : translations_xy) {
                const py::tuple pt = py::cast<py::tuple>(t);
                if (pt.size() < 2) {
                    continue;
                }
                xy.emplace_back(py::cast<double>(pt[0]), py::cast<double>(pt[1]));
            }

            const auto rejected = arga::cuda::filter_translations(
                fixed, piece, plate_w_mm, plate_h_mm, xy, cell_mm);
            py::list out;
            for (auto flag : rejected) {
                out.append(static_cast<int>(flag));
            }
            return out;
        },
        py::arg("fixed_rings_list"),
        py::arg("piece_rings"),
        py::arg("plate_w_mm"),
        py::arg("plate_h_mm"),
        py::arg("translations_xy"),
        py::arg("cell_mm") = 8.0,
        "Cribado batch: 1=rechazo seguro raster, 0=validar exacto (Clipper/Shapely).");

    m.attr("ENGINE_NAME") = "cpp_clipper2";
    m.attr("ENGINE_BASE_NAME") = "arga_base_pizarron";
    m.attr("ENGINE_VERSION") = "1.1.0";
}
