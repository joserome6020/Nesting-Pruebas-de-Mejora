#include "packer_giga_cal11.hpp"

#include <algorithm>
#include <cctype>
#include <string>
#include <vector>

namespace arga {
namespace {

std::string upper_ascii(std::string s) {
    for (char& c : s) {
        c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    }
    return s;
}

bool name_has(const std::string& nombre, const char* tag) {
    return upper_ascii(nombre).find(tag) != std::string::npos;
}

double aabb_area(const PieceIn& p) {
    bool first = true;
    double minx = 0.0, miny = 0.0, maxx = 0.0, maxy = 0.0;
    for (const auto& ring : p.rings) {
        for (const auto& pt : ring) {
            if (first) {
                minx = maxx = pt.x;
                miny = maxy = pt.y;
                first = false;
            } else {
                minx = std::min(minx, pt.x);
                maxx = std::max(maxx, pt.x);
                miny = std::min(miny, pt.y);
                maxy = std::max(maxy, pt.y);
            }
        }
    }
    if (first) {
        return 0.0;
    }
    return std::max(0.0, (maxx - minx) * (maxy - miny));
}

double piece_area_of(const PieceIn& p) {
    return p.area > 0.0 ? p.area : 0.0;
}

bool is_i_beam(const PieceIn& p) {
    if (!name_has(p.nombre, "VFM") || p.rings.empty()) {
        return false;
    }
    const double aabb = std::max(1.0, aabb_area(p));
    return (1.0 - (piece_area_of(p) / aabb)) >= 0.35;
}

bool is_long_bar(const PieceIn& p) {
    const std::string u = upper_ascii(p.nombre);
    if (u.find("HFM") != std::string::npos || u.find("SHC") != std::string::npos
        || u.find("SIHC") != std::string::npos || u.find("SIVC") != std::string::npos
        || u.find("WFM") != std::string::npos || u.find("VS-20") != std::string::npos
        || u.find("VS-") != std::string::npos) {
        return true;
    }
    return false;
}

int giga_rank(const PieceIn& p) {
    if (is_i_beam(p)) {
        const std::string u = upper_ascii(p.nombre);
        if (u.find("-101") != std::string::npos || u.find("-102") != std::string::npos) {
            return 0;
        }
        return 1;
    }
    if (is_long_bar(p)) {
        return 2;
    }
    return 3;
}

std::vector<PieceIn> order_giga_pool(const std::vector<PieceIn>& piezas) {
    std::vector<PieceIn> a101, a102, other_i, bars, rest;
    for (const auto& p : piezas) {
        const int r = giga_rank(p);
        const std::string u = upper_ascii(p.nombre);
        if (r == 0 && u.find("-101") != std::string::npos) {
            a101.push_back(p);
        } else if (r == 0 && u.find("-102") != std::string::npos) {
            a102.push_back(p);
        } else if (r <= 1) {
            other_i.push_back(p);
        } else if (r == 2) {
            bars.push_back(p);
        } else {
            rest.push_back(p);
        }
    }
    auto by_area = [](const PieceIn& a, const PieceIn& b) {
        return piece_area_of(a) > piece_area_of(b);
    };
    std::sort(other_i.begin(), other_i.end(), by_area);
    std::sort(bars.begin(), bars.end(), by_area);
    std::sort(rest.begin(), rest.end(), by_area);

    std::vector<PieceIn> out;
    out.reserve(piezas.size());
    const size_t n_pairs = std::max(a101.size(), a102.size());
    // Sin estructurales (HFM/SIVC/…): torre de pares 101/102 primero, luego
    // inyección de chicos (estilo captura torres). Con estructurales: 1 par
    // + barras + patio, y el resto de I al final (si no, 4 I llenan el 48").
    const bool tower_mode = bars.empty() && n_pairs >= 2;
    if (tower_mode) {
        for (size_t i = 0; i < n_pairs; ++i) {
            if (i < a101.size()) {
                out.push_back(a101[i]);
            }
            if (i < a102.size()) {
                out.push_back(a102[i]);
            }
        }
        out.insert(out.end(), rest.begin(), rest.end());
        out.insert(out.end(), other_i.begin(), other_i.end());
        return out;
    }
    if (!a101.empty()) {
        out.push_back(a101.front());
    }
    if (!a102.empty()) {
        out.push_back(a102.front());
    }
    out.insert(out.end(), bars.begin(), bars.end());
    out.insert(out.end(), rest.begin(), rest.end());
    for (size_t i = 1; i < n_pairs; ++i) {
        if (i < a101.size()) {
            out.push_back(a101[i]);
        }
        if (i < a102.size()) {
            out.push_back(a102[i]);
        }
    }
    out.insert(out.end(), other_i.begin(), other_i.end());
    return out;
}

}  // namespace

PackResult empaquetar_una_hoja_giga_cal11(
    const std::vector<PieceIn>& piezas,
    double w_placa,
    double h_placa,
    double kerf_override,
    double margin_override,
    const std::string& opt_override,
    const std::string& corner_override,
    const std::optional<std::vector<std::vector<Point2D>>>& limite_rings) {
    return empaquetar_una_hoja_mc(
        order_giga_pool(piezas),
        w_placa,
        h_placa,
        kerf_override,
        margin_override,
        opt_override,
        corner_override,
        limite_rings,
        nullptr,
        1,
        /*preserve_input_order=*/true,
        /*seed_bottom_alley=*/true);
}

}  // namespace arga
