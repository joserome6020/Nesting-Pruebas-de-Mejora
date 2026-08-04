#include "arga_nest/certifier.hpp"

#include "clipper2/clipper.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <limits>

namespace arga::core {
namespace {

using namespace Clipper2Lib;

PathD ring_to_path(const std::vector<Point2D>& ring) {
    PathD path;
    path.reserve(ring.size());
    for (const auto& p : ring) {
        path.push_back(PointD(p.x, p.y));
    }
    if (path.size() >= 2) {
        const auto& a = path.front();
        const auto& b = path.back();
        if (std::abs(a.x - b.x) < 1e-9 && std::abs(a.y - b.y) < 1e-9) {
            path.pop_back();
        }
    }
    return path;
}

PathsD piece_paths(const PieceOut& piece) {
    // Metal sólido = outer XOR/diff holes (poligonos[0] exterior, resto agujeros).
    PathsD outers;
    PathsD holes;
    if (piece.poligonos.empty()) {
        return {};
    }
    auto outer = ring_to_path(piece.poligonos[0]);
    if (outer.size() < 3) {
        return {};
    }
    outers.push_back(std::move(outer));
    for (std::size_t i = 1; i < piece.poligonos.size(); ++i) {
        auto h = ring_to_path(piece.poligonos[i]);
        if (h.size() >= 3) {
            holes.push_back(std::move(h));
        }
    }
    if (holes.empty()) {
        return outers;
    }
    return Difference(outers, holes, FillRule::NonZero);
}

double paths_area(const PathsD& paths) {
    double a = 0.0;
    for (const auto& p : paths) {
        a += std::abs(Area(p));
    }
    return a;
}

bool bbox_overlap(
    const PathsD& a,
    const PathsD& b,
    double pad) {
    if (a.empty() || b.empty()) {
        return false;
    }
    auto bb = [](const PathsD& paths) {
        double minx = std::numeric_limits<double>::infinity();
        double miny = std::numeric_limits<double>::infinity();
        double maxx = -std::numeric_limits<double>::infinity();
        double maxy = -std::numeric_limits<double>::infinity();
        for (const auto& path : paths) {
            for (const auto& pt : path) {
                minx = std::min(minx, pt.x);
                miny = std::min(miny, pt.y);
                maxx = std::max(maxx, pt.x);
                maxy = std::max(maxy, pt.y);
            }
        }
        return std::array<double, 4>{minx, miny, maxx, maxy};
    };
    const auto A = bb(a);
    const auto B = bb(b);
    return !(A[2] + pad < B[0] || B[2] + pad < A[0] || A[3] + pad < B[1] || B[3] + pad < A[1]);
}

}  // namespace

CertifyResult certify_sheet(
    const PackResult& result,
    double plate_w,
    double plate_h,
    double kerf_mm,
    double min_overlap_mm2) {
    CertifyResult out;
    out.placed_count = result.hoja.piezas.size();
    out.min_gap_mm = std::numeric_limits<double>::infinity();
    out.ok = true;

    if (result.hoja.piezas.empty()) {
        out.ok = false;
        out.issues.push_back({"empty", "no pieces placed"});
        out.min_gap_mm = 0.0;
        return out;
    }

    std::vector<PathsD> geoms;
    geoms.reserve(result.hoja.piezas.size());
    for (const auto& p : result.hoja.piezas) {
        auto paths = piece_paths(p);
        if (paths.empty()) {
            out.ok = false;
            out.issues.push_back(
                {"internal", std::string("invalid geometry: ") + p.nombre});
            continue;
        }
        // Dentro de placa (bbox rápida)
        for (const auto& path : paths) {
            for (const auto& pt : path) {
                if (pt.x < -1e-3 || pt.y < -1e-3 || pt.x > plate_w + 1e-3 ||
                    pt.y > plate_h + 1e-3) {
                    out.ok = false;
                    out.issues.push_back(
                        {"internal",
                         std::string("out_of_plate: ") + p.nombre});
                    break;
                }
            }
        }
        geoms.push_back(std::move(paths));
    }

    const double kerf_gate = 0.92 * std::max(0.0, kerf_mm);
    const double inflate = kerf_gate * 0.5;

    for (std::size_t i = 0; i < geoms.size(); ++i) {
        for (std::size_t j = i + 1; j < geoms.size(); ++j) {
            if (!bbox_overlap(geoms[i], geoms[j], inflate + 1.0)) {
                continue;
            }
            const auto inter = Intersect(geoms[i], geoms[j], FillRule::NonZero);
            const double area = paths_area(inter);
            if (area >= min_overlap_mm2) {
                out.ok = false;
                out.issues.push_back(
                    {"overlap",
                     result.hoja.piezas[i].nombre + " x " +
                         result.hoja.piezas[j].nombre + " area=" +
                         std::to_string(area)});
            }

            if (kerf_mm > 1e-9) {
                // Si al inflar half-kerf aún hay intersección ⇒ gap < kerf.
                auto ai = InflatePaths(geoms[i], inflate, JoinType::Round, EndType::Polygon);
                auto bi = InflatePaths(geoms[j], inflate, JoinType::Round, EndType::Polygon);
                const auto kinter = Intersect(ai, bi, FillRule::NonZero);
                const double karea = paths_area(kinter);
                if (karea > 1e-3) {
                    out.ok = false;
                    out.min_gap_mm = std::min(out.min_gap_mm, kerf_gate);
                    out.issues.push_back(
                        {"kerf",
                         result.hoja.piezas[i].nombre + " x " +
                             result.hoja.piezas[j].nombre +
                             " gap_lt_0.92_kerf"});
                } else {
                    out.min_gap_mm = std::min(out.min_gap_mm, kerf_mm);
                }
            }
        }
    }

    if (!std::isfinite(out.min_gap_mm)) {
        out.min_gap_mm = kerf_mm > 0 ? kerf_mm : 0.0;
    }
    return out;
}

}  // namespace arga::core
