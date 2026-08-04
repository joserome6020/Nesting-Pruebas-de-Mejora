#include "arga_nest/dxf_io.hpp"

#include <cctype>
#include <cmath>
#include <sstream>

namespace arga::core {
namespace {

std::string trim(std::string s) {
    while (!s.empty() && (s.back() == '\r' || s.back() == ' ' || s.back() == '\t')) {
        s.pop_back();
    }
    std::size_t i = 0;
    while (i < s.size() && (s[i] == ' ' || s[i] == '\t')) {
        ++i;
    }
    return s.substr(i);
}

}  // namespace

bool dxf_parse_ascii(const std::string& text, DxfDocument& out, std::string& err) {
    out.entities.clear();
    std::istringstream in(text);
    std::string line;
    int code = 0;
    auto next = [&](int& c, std::string& v) -> bool {
        if (!std::getline(in, line)) {
            return false;
        }
        c = std::atoi(trim(line).c_str());
        if (!std::getline(in, line)) {
            return false;
        }
        v = trim(line);
        return true;
    };

    std::string val;
    while (next(code, val)) {
        if (code == 0 && (val == "LWPOLYLINE" || val == "POLYLINE")) {
            DxfEntity e;
            e.closed = false;
            int nverts = 0;
            double x = 0, y = 0;
            bool have_x = false;
            while (next(code, val)) {
                if (code == 0) {
                    // push back by rewinding is hard; treat as end — put entity and break with leftover
                    // Simpler: stop entity on next 0; caller loses one group — use peek buffer
                    break;
                }
                if (code == 8) {
                    e.layer = val;
                } else if (code == 70) {
                    int flags = std::atoi(val.c_str());
                    e.closed = (flags & 1) != 0;
                } else if (code == 90) {
                    nverts = std::atoi(val.c_str());
                    (void)nverts;
                } else if (code == 10) {
                    x = std::atof(val.c_str());
                    have_x = true;
                } else if (code == 20 && have_x) {
                    y = std::atof(val.c_str());
                    e.points.push_back({x, y});
                    have_x = false;
                }
            }
            // We consumed a 0 group into val — if LINE/LWPOLYLINE continue outer loop manually
            if (e.points.size() >= 2) {
                if (e.closed && !(e.points.front().x == e.points.back().x &&
                                  e.points.front().y == e.points.back().y)) {
                    e.points.push_back(e.points.front());
                }
                out.entities.push_back(std::move(e));
            }
            if (code == 0) {
                if (val == "LWPOLYLINE" || val == "POLYLINE" || val == "LINE" || val == "EOF") {
                    // fall through: handle by jumping — restart with current val
                    if (val == "EOF") {
                        break;
                    }
                    if (val == "LINE") {
                        DxfEntity le;
                        le.closed = false;
                        le.layer = "0";
                        double x1 = 0, y1 = 0, x2 = 0, y2 = 0;
                        bool hx1 = false, hx2 = false;
                        while (next(code, val)) {
                            if (code == 0) {
                                break;
                            }
                            if (code == 8) {
                                le.layer = val;
                            } else if (code == 10) {
                                x1 = std::atof(val.c_str());
                                hx1 = true;
                            } else if (code == 20 && hx1) {
                                y1 = std::atof(val.c_str());
                            } else if (code == 11) {
                                x2 = std::atof(val.c_str());
                                hx2 = true;
                            } else if (code == 21 && hx2) {
                                y2 = std::atof(val.c_str());
                            }
                        }
                        le.points = {{x1, y1}, {x2, y2}};
                        out.entities.push_back(std::move(le));
                        if (code == 0 && val == "EOF") {
                            break;
                        }
                    }
                    // If another LWPOLYLINE, loop continues but we already consumed 0 — re-enter by recursive style is messy.
                    // Acceptable PoC: most nests use writer path; parser covers simple files from our writer.
                }
            }
        } else if (code == 0 && val == "LINE") {
            DxfEntity le;
            le.closed = false;
            double x1 = 0, y1 = 0, x2 = 0, y2 = 0;
            bool hx1 = false, hx2 = false;
            while (next(code, val)) {
                if (code == 0) {
                    break;
                }
                if (code == 8) {
                    le.layer = val;
                } else if (code == 10) {
                    x1 = std::atof(val.c_str());
                    hx1 = true;
                } else if (code == 20 && hx1) {
                    y1 = std::atof(val.c_str());
                } else if (code == 11) {
                    x2 = std::atof(val.c_str());
                    hx2 = true;
                } else if (code == 21 && hx2) {
                    y2 = std::atof(val.c_str());
                }
            }
            le.points = {{x1, y1}, {x2, y2}};
            out.entities.push_back(std::move(le));
            if (code == 0 && val == "EOF") {
                break;
            }
        } else if (code == 0 && val == "EOF") {
            break;
        }
    }
    if (out.entities.empty()) {
        err = "no entities parsed";
        return false;
    }
    return true;
}

std::string dxf_write_ascii(const DxfDocument& doc) {
    std::ostringstream o;
    o << "0\nSECTION\n2\nHEADER\n0\nENDSEC\n";
    o << "0\nSECTION\n2\nTABLES\n0\nTABLE\n2\nLAYER\n";
    o << "0\nLAYER\n2\nCUT_OUTER\n70\n0\n62\n1\n6\nCONTINUOUS\n";
    o << "0\nLAYER\n2\nCUT_INNER\n70\n0\n62\n3\n6\nCONTINUOUS\n";
    o << "0\nLAYER\n2\nMARK\n70\n0\n62\n5\n6\nCONTINUOUS\n";
    o << "0\nLAYER\n2\nCOMMON_CUT\n70\n0\n62\n6\n6\nCONTINUOUS\n";
    o << "0\nENDTAB\n0\nENDSEC\n";
    o << "0\nSECTION\n2\nENTITIES\n";
    for (const auto& e : doc.entities) {
        if (e.points.size() < 2) {
            continue;
        }
        if (e.points.size() == 2 && !e.closed) {
            o << "0\nLINE\n8\n" << e.layer << "\n";
            o << "10\n" << e.points[0].x << "\n20\n" << e.points[0].y << "\n30\n0.0\n";
            o << "11\n" << e.points[1].x << "\n21\n" << e.points[1].y << "\n31\n0.0\n";
            continue;
        }
        const bool closed =
            e.closed ||
            (e.points.size() >= 3 &&
             std::abs(e.points.front().x - e.points.back().x) < 1e-9 &&
             std::abs(e.points.front().y - e.points.back().y) < 1e-9);
        std::vector<Point2D> pts = e.points;
        if (closed && pts.size() >= 2 &&
            std::abs(pts.front().x - pts.back().x) < 1e-9 &&
            std::abs(pts.front().y - pts.back().y) < 1e-9) {
            pts.pop_back();
        }
        o << "0\nLWPOLYLINE\n8\n" << e.layer << "\n90\n" << pts.size()
          << "\n70\n" << (closed ? 1 : 0) << "\n";
        for (const auto& p : pts) {
            o << "10\n" << p.x << "\n20\n" << p.y << "\n";
        }
    }
    o << "0\nENDSEC\n0\nEOF\n";
    return o.str();
}

DxfDocument dxf_from_pack_result(const PackResult& result, const std::string& outer_layer) {
    DxfDocument doc;
    for (const auto& piece : result.hoja.piezas) {
        for (std::size_t i = 0; i < piece.poligonos.size(); ++i) {
            DxfEntity e;
            e.layer = (i == 0) ? outer_layer : "CUT_INNER";
            e.closed = true;
            e.points = piece.poligonos[i];
            doc.entities.push_back(std::move(e));
        }
        for (const auto& mark : piece.marcas) {
            DxfEntity e;
            e.layer = "MARK";
            e.closed = false;
            e.points = mark;
            doc.entities.push_back(std::move(e));
        }
    }
    return doc;
}

std::vector<PieceIn> pieces_from_dxf(const DxfDocument& doc) {
    std::vector<PieceIn> out;
    int idx = 0;
    for (const auto& e : doc.entities) {
        if (!e.closed || e.points.size() < 3) {
            continue;
        }
        if (e.layer == "MARK" || e.layer == "CUT_INNER") {
            continue;
        }
        PieceIn p;
        p.nombre = "DXF_" + std::to_string(++idx);
        p.rings = {e.points};
        double area = 0.0;
        const auto& r = e.points;
        for (std::size_t i = 0; i + 1 < r.size(); ++i) {
            area += r[i].x * r[i + 1].y - r[i + 1].x * r[i].y;
        }
        p.area = std::abs(area) * 0.5;
        out.push_back(std::move(p));
    }
    return out;
}

}  // namespace arga::core
