#include "arga_nest/sa_refine.hpp"

#include <algorithm>
#include <cmath>
#include <random>

namespace arga::core {
namespace {

struct BBox {
    double minx = 0, miny = 0, maxx = 0, maxy = 0;
};

BBox piece_bbox(const PieceOut& p) {
    BBox b{1e100, 1e100, -1e100, -1e100};
    for (const auto& ring : p.poligonos) {
        for (const auto& q : ring) {
            b.minx = std::min(b.minx, q.x);
            b.miny = std::min(b.miny, q.y);
            b.maxx = std::max(b.maxx, q.x);
            b.maxy = std::max(b.maxy, q.y);
        }
    }
    return b;
}

void translate_piece(PieceOut& p, double dx, double dy) {
    for (auto& ring : p.poligonos) {
        for (auto& q : ring) {
            q.x += dx;
            q.y += dy;
        }
    }
    for (auto& ring : p.marcas) {
        for (auto& q : ring) {
            q.x += dx;
            q.y += dy;
        }
    }
}

double score_pack(const PackResult& r) {
    if (r.hoja.piezas.empty()) {
        return 1e100;
    }
    double minx = 1e100, miny = 1e100, maxx = -1e100, maxy = -1e100;
    for (const auto& p : r.hoja.piezas) {
        const auto b = piece_bbox(p);
        minx = std::min(minx, b.minx);
        miny = std::min(miny, b.miny);
        maxx = std::max(maxx, b.maxx);
        maxy = std::max(maxy, b.maxy);
    }
    const double w = std::max(0.0, maxx - minx);
    const double h = std::max(0.0, maxy - miny);
    // Preferir bbox compacto + anclado abajo-izquierda
    return w * h + 0.01 * (minx + miny) - 1.0 * r.hoja.eficiencia;
}

}  // namespace

PackResult sa_refine_pack(
    const PackResult& input,
    double plate_w,
    double plate_h,
    double kerf,
    const SaRefineParams& params,
    SaRefineStats* stats) {
    PackResult best = input;
    PackResult cur = input;
    double best_s = score_pack(best);
    double cur_s = best_s;
    SaRefineStats local;
    local.best_score = best_s;

    if (cur.hoja.piezas.size() < 2 || params.iterations <= 0) {
        local.note = "skip_sa";
        if (stats) {
            *stats = local;
        }
        return best;
    }

    std::mt19937 rng(params.seed);
    std::uniform_int_distribution<int> pick(
        0, static_cast<int>(cur.hoja.piezas.size()) - 1);
    std::uniform_real_distribution<double> uni(0.0, 1.0);

    double step = params.step0_mm;
    double temp = params.temp0;

    for (int it = 0; it < params.iterations; ++it) {
        PackResult cand = cur;
        const int idx = pick(rng);
        // Empujar hacia origen (compactación BL-ish)
        const auto bb = piece_bbox(cand.hoja.piezas[idx]);
        double dx = 0.0;
        double dy = 0.0;
        const double r = uni(rng);
        if (r < 0.5) {
            dx = -step * (0.3 + 0.7 * uni(rng));
        } else if (r < 0.75) {
            dy = -step * (0.3 + 0.7 * uni(rng));
        } else {
            dx = (uni(rng) - 0.5) * step;
            dy = (uni(rng) - 0.5) * step;
        }
        // No sacar de placa (bbox)
        if (bb.minx + dx < 0) {
            dx = -bb.minx;
        }
        if (bb.miny + dy < 0) {
            dy = -bb.miny;
        }
        if (bb.maxx + dx > plate_w) {
            dx = plate_w - bb.maxx;
        }
        if (bb.maxy + dy > plate_h) {
            dy = plate_h - bb.maxy;
        }
        if (std::abs(dx) < 1e-9 && std::abs(dy) < 1e-9) {
            temp *= 0.97;
            step = std::max(params.step_min_mm, step * 0.98);
            continue;
        }
        translate_piece(cand.hoja.piezas[idx], dx, dy);
        const auto cert = certify_sheet(cand, plate_w, plate_h, kerf, 1.0);
        if (!cert.ok) {
            temp *= 0.995;
            continue;
        }
        const double s = score_pack(cand);
        const double delta = s - cur_s;
        bool accept = false;
        if (delta <= 0) {
            accept = true;
            ++local.improved;
        } else {
            const double prob = std::exp(-delta / std::max(1e-9, temp));
            accept = uni(rng) < prob;
        }
        if (accept) {
            cur = std::move(cand);
            cur_s = s;
            ++local.accepted;
            if (s < best_s) {
                best = cur;
                best_s = s;
                local.best_score = best_s;
            }
        }
        temp *= 0.97;
        step = std::max(params.step_min_mm, step * 0.98);
    }

    local.note = "sa_done";
    if (stats) {
        *stats = local;
    }
    return best;
}

}  // namespace arga::core
