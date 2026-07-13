#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <optional>
#include <stdexcept>

#include "packer.hpp"

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

py::dict placement_step_to_py(const arga::PlacementStep& s) {
    py::dict d;
    d["orden_pool"] = s.orden_pool;
    d["nombre"] = s.nombre;
    d["colocada"] = s.colocada;
    d["px"] = s.px;
    d["py"] = s.py;
    d["score"] = s.score;
    d["rotacion_grados"] = s.rotacion_grados;
    d["categoria"] = s.categoria;
    d["bbox_w_mm"] = s.bbox_w_mm;
    d["bbox_h_mm"] = s.bbox_h_mm;
    d["variaciones_evaluadas"] = s.variaciones_evaluadas;
    if (s.colocada) {
        d["pieza"] = piece_out_to_py(s.pieza_colocada);
    }
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

            const arga::PackResult result = arga::empaquetar_una_hoja_mc(
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
        "empaquetar_una_hoja_timeline",
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

            const arga::TimelinePackResult result = arga::empaquetar_una_hoja_timeline(
                piezas,
                w_placa,
                h_placa,
                kerf_override,
                margin_override,
                opt_override,
                corner_override,
                limite,
                mc_iterations);

            py::dict out;
            py::list restos;
            for (const auto& p : result.pack.restos) {
                restos.append(piece_in_to_py(p));
            }
            py::list pasos;
            for (const auto& s : result.pasos) {
                pasos.append(placement_step_to_py(s));
            }
            py::list orden;
            for (const auto& n : result.orden_piezas) {
                orden.append(n);
            }
            out["hoja"] = sheet_to_py(result.pack.hoja);
            out["restos"] = restos;
            out["pasos"] = pasos;
            out["mc_iteracion_ganadora"] = result.mc_iteracion_ganadora;
            out["mc_orden_modo"] = result.mc_orden_modo;
            out["orden_piezas"] = orden;
            return out;
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

    m.attr("ENGINE_NAME") = "cpp_clipper2";
    m.attr("ENGINE_VERSION") = "1.0.0";
}
