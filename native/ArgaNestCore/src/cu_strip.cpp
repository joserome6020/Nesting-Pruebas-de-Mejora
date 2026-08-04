#include "arga_nest/cu_strip.hpp"

#include <algorithm>

namespace arga::core {

CuStripResult pack_cu_strip(const CuStripRequest& req) {
    CuStripResult out;
    if (req.strip_length_mm <= 0 || req.strip_width_mm <= 0) {
        out.leftovers = req.pieces;
        return out;
    }

    std::vector<CuStripPiece> sorted = req.pieces;
    std::sort(
        sorted.begin(),
        sorted.end(),
        [](const CuStripPiece& a, const CuStripPiece& b) {
            return (a.length_mm * a.width_mm) > (b.length_mm * b.width_mm);
        });

    struct Row {
        double y = 0.0;
        double height = 0.0;
        double cursor_x = 0.0;
    };
    std::vector<Row> rows;
    const double pitch = req.kerf_mm + req.gap_mm;

    for (const auto& piece : sorted) {
        if (piece.length_mm <= 0 || piece.width_mm <= 0) {
            out.leftovers.push_back(piece);
            continue;
        }
        bool placed = false;
        for (auto& row : rows) {
            if (piece.width_mm <= row.height + 1e-9 &&
                row.cursor_x + piece.length_mm <= req.strip_length_mm + 1e-9) {
                CuStripPlacement pl;
                pl.nombre = piece.nombre;
                pl.x = row.cursor_x;
                pl.y = row.y;
                pl.length_mm = piece.length_mm;
                pl.width_mm = piece.width_mm;
                pl.area = piece.area > 0 ? piece.area : piece.length_mm * piece.width_mm;
                pl.calibre = piece.calibre;
                pl.material = piece.material;
                out.placed.push_back(pl);
                row.cursor_x += piece.length_mm + pitch;
                out.used_length_mm = std::max(out.used_length_mm, row.cursor_x - pitch);
                placed = true;
                break;
            }
        }
        if (placed) {
            continue;
        }
        // Nueva fila
        double y = 0.0;
        if (!rows.empty()) {
            y = rows.back().y + rows.back().height + pitch;
        }
        if (y + piece.width_mm > req.strip_width_mm + 1e-9 ||
            piece.length_mm > req.strip_length_mm + 1e-9) {
            out.leftovers.push_back(piece);
            continue;
        }
        Row row;
        row.y = y;
        row.height = piece.width_mm;
        row.cursor_x = 0.0;
        CuStripPlacement pl;
        pl.nombre = piece.nombre;
        pl.x = 0.0;
        pl.y = y;
        pl.length_mm = piece.length_mm;
        pl.width_mm = piece.width_mm;
        pl.area = piece.area > 0 ? piece.area : piece.length_mm * piece.width_mm;
        pl.calibre = piece.calibre;
        pl.material = piece.material;
        out.placed.push_back(pl);
        row.cursor_x = piece.length_mm + pitch;
        out.used_length_mm = std::max(out.used_length_mm, piece.length_mm);
        rows.push_back(row);
    }

    const double plate_area = req.strip_length_mm * req.strip_width_mm;
    double used = 0.0;
    for (const auto& p : out.placed) {
        used += p.area;
    }
    out.efficiency = plate_area > 0 ? (100.0 * used / plate_area) : 0.0;
    return out;
}

PackResult cu_strip_to_pack_result(const CuStripResult& cu) {
    PackResult r;
    for (const auto& p : cu.placed) {
        PieceOut o;
        o.nombre = p.nombre;
        o.area = p.area;
        o.calibre = p.calibre;
        o.material = p.material;
        o.poligonos = {{
            {p.x, p.y},
            {p.x + p.length_mm, p.y},
            {p.x + p.length_mm, p.y + p.width_mm},
            {p.x, p.y + p.width_mm},
            {p.x, p.y},
        }};
        r.hoja.piezas.push_back(std::move(o));
        r.hoja.area_usada += p.area;
    }
    r.hoja.eficiencia = cu.efficiency;
    for (const auto& L : cu.leftovers) {
        PieceIn pi;
        pi.nombre = L.nombre;
        pi.area = L.area;
        pi.calibre = L.calibre;
        pi.material = L.material;
        pi.rings = {{
            {0, 0},
            {L.length_mm, 0},
            {L.length_mm, L.width_mm},
            {0, L.width_mm},
            {0, 0},
        }};
        r.restos.push_back(std::move(pi));
    }
    return r;
}

}  // namespace arga::core
