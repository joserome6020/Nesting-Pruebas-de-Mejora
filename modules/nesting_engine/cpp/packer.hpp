#pragma once

#include <cmath>
#include <optional>
#include <random>
#include <string>
#include <tuple>
#include <vector>

namespace arga {

struct Point2D {
    double x = 0.0;
    double y = 0.0;
};

struct PieceIn {
    std::string nombre;
    double area = 0.0;
    std::string calibre;
    std::string material;
    std::vector<std::vector<Point2D>> rings;
    std::vector<std::vector<Point2D>> marks;
};

struct PieceOut {
    std::string nombre;
    std::vector<std::vector<Point2D>> poligonos;
    std::vector<std::vector<Point2D>> marcas;
    double area = 0.0;
    std::string calibre;
    std::string material;
};

struct SheetOut {
    std::vector<PieceOut> piezas;
    double area_usada = 0.0;
    double eficiencia = 0.0;
};

struct PackResult {
    SheetOut hoja;
    std::vector<PieceIn> restos;
    /** Orden GA del mejor individuo (índices sobre `piezas` de entrada). Vacío si N/A. */
    std::vector<size_t> orden;
};

constexpr int kMonteCarloIterationsDefault = 15;
constexpr double kAreaEstructuralUmbralMm2 = 2'500'000.0;
constexpr double kSlideStepCoarseMm = 3.0;
constexpr double kSlideStepFineMm = 0.5;

PackResult empaquetar_una_hoja_mc(
    const std::vector<PieceIn>& piezas,
    double w_placa,
    double h_placa,
    double kerf_override = 0.2,
    double margin_override = 0.0,
    const std::string& opt_override = "OPTIMIZAR LARGO Y ANCHO",
    const std::string& corner_override = "INFERIOR IZQUIERDA",
    const std::optional<std::vector<std::vector<Point2D>>>& limite_rings = std::nullopt,
    std::mt19937* rng = nullptr,
    int mc_iterations = kMonteCarloIterationsDefault);

}  // namespace arga
