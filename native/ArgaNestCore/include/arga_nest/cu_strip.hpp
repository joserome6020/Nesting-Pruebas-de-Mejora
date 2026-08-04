#pragma once

#include "packer.hpp"

#include <string>
#include <vector>

namespace arga::core {

struct CuStripPiece {
    std::string nombre;
    double length_mm = 0.0;  // a lo largo de la tira
    double width_mm = 0.0;   // ancho transversal
    double area = 0.0;
    std::string calibre;
    std::string material;
};

struct CuStripRequest {
    double strip_length_mm = 0.0;
    double strip_width_mm = 0.0;
    double kerf_mm = 0.2;
    double gap_mm = 0.0;
    std::vector<CuStripPiece> pieces;
};

struct CuStripPlacement {
    std::string nombre;
    double x = 0.0;
    double y = 0.0;
    double length_mm = 0.0;
    double width_mm = 0.0;
    double area = 0.0;
    std::string calibre;
    std::string material;
};

struct CuStripResult {
    std::vector<CuStripPlacement> placed;
    std::vector<CuStripPiece> leftovers;
    double used_length_mm = 0.0;
    double efficiency = 0.0;
};

/** Nesting 1.5D de tiras de cobre (BLF a lo largo + filas por ancho). */
CuStripResult pack_cu_strip(const CuStripRequest& req);

/** Convierte resultado CU a PackResult genérico (rectángulos). */
PackResult cu_strip_to_pack_result(const CuStripResult& cu);

}  // namespace arga::core
