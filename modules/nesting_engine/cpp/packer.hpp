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
    bool grain_locked = false;
    std::vector<double> allowed_rotations;
};

inline std::vector<int> resolve_piece_rotations_deg(const PieceIn& piece) {
    // Vacío = el packer usa su set por defecto (ortogonal / fine / tilt).
    if (piece.grain_locked) {
        return {0};
    }
    if (!piece.allowed_rotations.empty()) {
        std::vector<int> out;
        out.reserve(piece.allowed_rotations.size());
        for (double r : piece.allowed_rotations) {
            int a = static_cast<int>(std::lround(r)) % 360;
            if (a < 0) {
                a += 360;
            }
            if (a == 360) {
                a = 0;
            }
            out.push_back(a);
        }
        return out;
    }
    return {};
}

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

/** Placa→pieza usa el metal, no el buffer de kerf (Clipper/simplify puede no cubrir un vértice). */
inline void clamp_placement_to_plate_margin(
    double& px,
    double& py,
    double metal_minx,
    double metal_miny,
    double metal_maxx,
    double metal_maxy,
    double margin_px,
    double w_placa,
    double h_placa) {
    if (margin_px <= 0.0) {
        return;
    }
    if (px + metal_minx < margin_px) {
        px = margin_px - metal_minx;
    }
    if (py + metal_miny < margin_px) {
        py = margin_px - metal_miny;
    }
    if (w_placa > 0.0 && px + metal_maxx > w_placa - margin_px) {
        px = w_placa - margin_px - metal_maxx;
    }
    if (h_placa > 0.0 && py + metal_maxy > h_placa - margin_px) {
        py = h_placa - margin_px - metal_maxy;
    }
}

inline bool placement_respects_plate_margin(
    double px,
    double py,
    double metal_minx,
    double metal_miny,
    double metal_maxx,
    double metal_maxy,
    double margin_px,
    double w_placa,
    double h_placa) {
    if (margin_px <= 0.0) {
        return true;
    }
    if (px + metal_minx + 1e-6 < margin_px || py + metal_miny + 1e-6 < margin_px) {
        return false;
    }
    if (w_placa > 0.0 && px + metal_maxx > w_placa - margin_px + 1e-6) {
        return false;
    }
    if (h_placa > 0.0 && py + metal_maxy > h_placa - margin_px + 1e-6) {
        return false;
    }
    return true;
}

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
