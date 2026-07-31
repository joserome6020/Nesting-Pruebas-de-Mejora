#pragma once

#include "cuda/nest_accel.hpp"

#include "clipper2/clipper.h"

#include <vector>

namespace arga::cuda {

struct DenseMask {
    std::vector<std::uint8_t> cells;
    int w = 0;
    int h = 0;
    double cell_mm = 8.0;
    double origin_x = 0;
    double origin_y = 0;
};

// Rasterize occupancy (cell center inside any path) — used as conservative collide.
DenseMask rasterize_paths_occupancy(
    const Clipper2Lib::PathsD& paths,
    double plate_w_mm,
    double plate_h_mm,
    double cell_mm);

// Máscara ajustada al bbox de la pieza (requerida para collide_batch).
DenseMask rasterize_paths_tight(
    const Clipper2Lib::PathsD& paths,
    double cell_mm);

// Stamp multiple placed paths onto one board mask
DenseMask rasterize_union_occupancy(
    const std::vector<Clipper2Lib::PathsD>& placed,
    double plate_w_mm,
    double plate_h_mm,
    double cell_mm);

// Convert world translation (px,py) to grid offset for a tight piece mask.
GridOffset world_to_offset(
    double world_x,
    double world_y,
    const DenseMask& board,
    const DenseMask& piece);

/**
 * Prefiltro de colocaciones: 1 = colisión raster (saltar Clipper),
 * 0 = debe validarse exactamente. Si CUDA no aplica, todos 0.
 */
std::vector<std::uint8_t> filter_translations(
    const std::vector<Clipper2Lib::PathsD>& fixed_buff,
    const Clipper2Lib::PathsD& piece_buff,
    double plate_w_mm,
    double plate_h_mm,
    const std::vector<std::pair<double, double>>& translations_xy,
    double cell_mm = 8.0);

/** True si conviene intentar cribado GPU (flag + runtime + carga). */
bool filter_worthwhile(std::size_t candidate_count, std::size_t fixed_count);

/** Cribado reutilizando máscara de tablero ya rasterizada (una por pieza). */
std::vector<std::uint8_t> filter_against_board(
    const DenseMask& board,
    const Clipper2Lib::PathsD& piece_buff,
    const std::vector<std::pair<double, double>>& translations_xy,
    double cell_mm = 8.0);

}  // namespace arga::cuda
