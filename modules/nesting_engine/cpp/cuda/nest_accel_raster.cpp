#include "cuda/nest_accel_raster.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>

namespace arga::cuda {
namespace {

constexpr double kScale = 1000.0;

DenseMask make_empty_mask(double plate_w_mm, double plate_h_mm, double cell_mm) {
    DenseMask mask;
    const double cell = (cell_mm > 0.0) ? cell_mm : 8.0;
    mask.cell_mm = cell;
    mask.origin_x = 0.0;
    mask.origin_y = 0.0;
    mask.w = static_cast<int>(std::ceil(std::max(0.0, plate_w_mm) / cell));
    mask.h = static_cast<int>(std::ceil(std::max(0.0, plate_h_mm) / cell));
    if (mask.w < 1) {
        mask.w = 1;
    }
    if (mask.h < 1) {
        mask.h = 1;
    }
    mask.cells.assign(
        static_cast<std::size_t>(mask.w) * static_cast<std::size_t>(mask.h), 0);
    return mask;
}

Clipper2Lib::Paths64 paths_to_scaled64(const Clipper2Lib::PathsD& paths) {
    Clipper2Lib::Paths64 paths64;
    paths64.reserve(paths.size());
    for (const auto& path : paths) {
        Clipper2Lib::Path64 p64;
        p64.reserve(path.size());
        for (const auto& pt : path) {
            p64.emplace_back(
                static_cast<std::int64_t>(std::llround(pt.x * kScale)),
                static_cast<std::int64_t>(std::llround(pt.y * kScale)));
        }
        paths64.push_back(std::move(p64));
    }
    return paths64;
}

bool cell_center_occupied(
    const Clipper2Lib::Paths64& paths64,
    double origin_x,
    double origin_y,
    double cell_mm,
    int x,
    int y) {
    const double px = origin_x + static_cast<double>(x) * cell_mm + cell_mm * 0.5;
    const double py = origin_y + static_cast<double>(y) * cell_mm + cell_mm * 0.5;
    const Clipper2Lib::Point64 pt(
        static_cast<std::int64_t>(std::llround(px * kScale)),
        static_cast<std::int64_t>(std::llround(py * kScale)));

    // Even-odd fill (same as packer_lab): holes cancel outer solids.
    int wind_cnt = 0;
    for (const auto& p64 : paths64) {
        if (Clipper2Lib::PointInPolygon(pt, p64)
            != Clipper2Lib::PointInPolygonResult::IsOutside) {
            ++wind_cnt;
        }
    }
    return (wind_cnt % 2) != 0;
}

void stamp_paths_onto_mask(DenseMask* mask, const Clipper2Lib::PathsD& paths) {
    if (!mask || paths.empty() || mask->w <= 0 || mask->h <= 0) {
        return;
    }
    bool any_pt = false;
    double minx = 0.0;
    double miny = 0.0;
    double maxx = 0.0;
    double maxy = 0.0;
    for (const auto& path : paths) {
        for (const auto& pt : path) {
            if (!any_pt) {
                minx = maxx = pt.x;
                miny = maxy = pt.y;
                any_pt = true;
            } else {
                minx = std::min(minx, pt.x);
                miny = std::min(miny, pt.y);
                maxx = std::max(maxx, pt.x);
                maxy = std::max(maxy, pt.y);
            }
        }
    }
    if (!any_pt) {
        return;
    }
    const auto paths64 = paths_to_scaled64(paths);
    const double cell = mask->cell_mm > 0.0 ? mask->cell_mm : 8.0;
    const int x0 = std::max(
        0, static_cast<int>(std::floor((minx - mask->origin_x) / cell)) - 1);
    const int y0 = std::max(
        0, static_cast<int>(std::floor((miny - mask->origin_y) / cell)) - 1);
    const int x1 = std::min(
        mask->w - 1, static_cast<int>(std::ceil((maxx - mask->origin_x) / cell)) + 1);
    const int y1 = std::min(
        mask->h - 1, static_cast<int>(std::ceil((maxy - mask->origin_y) / cell)) + 1);
    for (int y = y0; y <= y1; ++y) {
        for (int x = x0; x <= x1; ++x) {
            if (cell_center_occupied(
                    paths64, mask->origin_x, mask->origin_y, cell, x, y)) {
                mask->cells[static_cast<std::size_t>(y) * mask->w
                    + static_cast<std::size_t>(x)] = 1;
            }
        }
    }
}

}  // namespace

DenseMask rasterize_paths_occupancy(
    const Clipper2Lib::PathsD& paths,
    double plate_w_mm,
    double plate_h_mm,
    double cell_mm) {
    DenseMask mask = make_empty_mask(plate_w_mm, plate_h_mm, cell_mm);
    stamp_paths_onto_mask(&mask, paths);
    return mask;
}

DenseMask rasterize_paths_tight(
    const Clipper2Lib::PathsD& paths,
    double cell_mm) {
    DenseMask mask;
    const double cell = (cell_mm > 0.0) ? cell_mm : 4.0;
    mask.cell_mm = cell;
    if (paths.empty() || paths[0].empty()) {
        mask.w = 1;
        mask.h = 1;
        mask.cells.assign(1, 0);
        return mask;
    }
    double minx = paths[0][0].x;
    double miny = paths[0][0].y;
    double maxx = minx;
    double maxy = miny;
    for (const auto& path : paths) {
        for (const auto& pt : path) {
            minx = std::min(minx, pt.x);
            miny = std::min(miny, pt.y);
            maxx = std::max(maxx, pt.x);
            maxy = std::max(maxy, pt.y);
        }
    }
    mask.origin_x = minx;
    mask.origin_y = miny;
    mask.w = std::max(1, static_cast<int>(std::ceil((maxx - minx) / cell)) + 1);
    mask.h = std::max(1, static_cast<int>(std::ceil((maxy - miny) / cell)) + 1);
    mask.cells.assign(
        static_cast<std::size_t>(mask.w) * static_cast<std::size_t>(mask.h), 0);
    stamp_paths_onto_mask(&mask, paths);
    return mask;
}

DenseMask rasterize_union_occupancy(
    const std::vector<Clipper2Lib::PathsD>& placed,
    double plate_w_mm,
    double plate_h_mm,
    double cell_mm) {
    DenseMask mask = make_empty_mask(plate_w_mm, plate_h_mm, cell_mm);
    for (const auto& paths : placed) {
        stamp_paths_onto_mask(&mask, paths);
    }
    return mask;
}

GridOffset world_to_offset(
    double world_x,
    double world_y,
    const DenseMask& board,
    const DenseMask& piece) {
    const double cell = (board.cell_mm > 0.0) ? board.cell_mm
        : ((piece.cell_mm > 0.0) ? piece.cell_mm : 4.0);
    return GridOffset{
        static_cast<int>(std::llround(
            (world_x + piece.origin_x - board.origin_x) / cell)),
        static_cast<int>(std::llround(
            (world_y + piece.origin_y - board.origin_y) / cell)),
    };
}

std::vector<std::uint8_t> filter_translations(
    const std::vector<Clipper2Lib::PathsD>& fixed_buff,
    const Clipper2Lib::PathsD& piece_buff,
    double plate_w_mm,
    double plate_h_mm,
    const std::vector<std::pair<double, double>>& translations_xy,
    double cell_mm) {
    std::vector<std::uint8_t> out(translations_xy.size(), 0);
    if (!filter_worthwhile(translations_xy.size(), fixed_buff.size())
        || piece_buff.empty()) {
        return out;
    }
    const DenseMask board = rasterize_union_occupancy(
        fixed_buff, plate_w_mm, plate_h_mm, cell_mm > 0.0 ? cell_mm : 8.0);
    return filter_against_board(board, piece_buff, translations_xy, cell_mm);
}

bool filter_worthwhile(std::size_t candidate_count, std::size_t fixed_count) {
    // Sin GPU / flag / carga baja → camino idéntico a hoy (cero overhead).
    // Umbral alto: en jobs chicos el raster+H2D cuesta más que Clipper.
    return requested() && available() && fixed_count >= 6 && candidate_count >= 320;
}

std::vector<std::uint8_t> filter_against_board(
    const DenseMask& board,
    const Clipper2Lib::PathsD& piece_buff,
    const std::vector<std::pair<double, double>>& translations_xy,
    double cell_mm) {
    std::vector<std::uint8_t> out(translations_xy.size(), 0);
    if (!requested() || !available() || translations_xy.size() < 320
        || piece_buff.empty() || board.cells.empty()) {
        return out;
    }
    const DenseMask piece = rasterize_paths_tight(
        piece_buff, cell_mm > 0.0 ? cell_mm : 8.0);
    if (piece.cells.empty()) {
        return out;
    }
    std::vector<GridOffset> offsets;
    offsets.reserve(translations_xy.size());
    for (const auto& xy : translations_xy) {
        offsets.push_back(world_to_offset(xy.first, xy.second, board, piece));
    }
    GridFilterStats stats;
    return collide_batch(
        board.cells,
        board.w,
        board.h,
        piece.cells,
        piece.w,
        piece.h,
        offsets,
        &stats,
        true);
}

}  // namespace arga::cuda
