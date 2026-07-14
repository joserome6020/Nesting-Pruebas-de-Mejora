#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <optional>
#include <stdexcept>

#include "packer.hpp"
#include "packer_base.hpp"
#include "packer_burke_blf.hpp"
#include "packer_libnest2d.hpp"
#include "packer_svgnest_ultra.hpp"

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
           int hill_climb_iterations) {
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
                    hill_climb_iterations);
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
        py::arg("hill_climb_iterations") = 10);

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
           std::uint32_t ga_seed) {
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
                    ga_seed);
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
        py::arg("ga_population") = 30,
        py::arg("ga_generations") = 30,
        py::arg("rotation_step_deg") = 15.0,
        py::arg("part_in_part") = true,
        py::arg("ga_seed") = 0);

    m.attr("ENGINE_NAME") = "cpp_clipper2";
    m.attr("ENGINE_BASE_NAME") = "arga_base_pizarron";
    m.attr("ENGINE_VERSION") = "1.1.0";
}
