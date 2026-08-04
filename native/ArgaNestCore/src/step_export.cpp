#include "arga_nest/step_export.hpp"

#include <cmath>
#include <sstream>

namespace arga::core {

std::string step_from_pack_result(const PackResult& result, double thickness_mm) {
    // Escritor STEP muy simplificado: documenta piezas como COMMENT + CARTESIAN points
    // y un PRODUCT por pieza. No es BREP completo de OCCT, pero es un artefacto
    // intercambiable y testeable; la ruta OCCT Python sigue disponible en el repo.
    std::ostringstream o;
    o << "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('ArgaNestCore STEP export'),'2;1');\n";
    o << "FILE_NAME('arga_nest.step','2026-07-31',('ANS C++'),('Arga'),";
    o << "'ArgaNestCore','ArgaNestCore','');\n";
    o << "FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\nENDSEC;\nDATA;\n";

    int id = 1;
    auto emit = [&](const std::string& body) {
        o << "#" << id++ << " = " << body << ";\n";
        return id - 1;
    };

    emit("APPLICATION_CONTEXT('automotive design')");
    const int thick_id = emit(
        "LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(" + std::to_string(thickness_mm) +
        "),#1)");
    (void)thick_id;

    int pindex = 0;
    for (const auto& piece : result.hoja.piezas) {
        ++pindex;
        if (piece.poligonos.empty() || piece.poligonos[0].size() < 3) {
            continue;
        }
        // bbox
        double minx = piece.poligonos[0][0].x, maxx = minx;
        double miny = piece.poligonos[0][0].y, maxy = miny;
        for (const auto& pt : piece.poligonos[0]) {
            minx = std::min(minx, pt.x);
            maxx = std::max(maxx, pt.x);
            miny = std::min(miny, pt.y);
            maxy = std::max(maxy, pt.y);
        }
        emit(
            "PRODUCT('" + piece.nombre + "','" + piece.nombre +
            "','ArgaNest placed part',(#1))");
        emit(
            "CARTESIAN_POINT('',(" + std::to_string(minx) + "," + std::to_string(miny) +
            ",0.0))");
        emit(
            "CARTESIAN_POINT('',(" + std::to_string(maxx) + "," + std::to_string(maxy) +
            "," + std::to_string(thickness_mm) + "))");
        // Contorno 2D como polyline de puntos (trazabilidad geométrica)
        for (const auto& pt : piece.poligonos[0]) {
            emit(
                "CARTESIAN_POINT('contour',(" + std::to_string(pt.x) + "," +
                std::to_string(pt.y) + ",0.0))");
        }
    }
    o << "ENDSEC;\nEND-ISO-10303-21;\n";
    return o.str();
}

}  // namespace arga::core
