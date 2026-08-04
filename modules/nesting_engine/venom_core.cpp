#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <vector>
#include <algorithm>
#include <cmath>

namespace py = pybind11;

// Estructura simple de Bounding Box para la simulación de colisión
struct PieceBox {
    int id;
    double minX;
    double minY;
    double maxX;
    double maxY;
    double shiftX = 0.0;
    double shiftY = 0.0;
    
    // Distancia al origen para ordenar (piezas más cercanas se procesan primero)
    double distToOrigin() const {
        return minX + minY;
    }
};

// Chequeo de colisión AABB con kerf (separación mínima entre piezas)
bool checkCollisionWithKerf(const PieceBox& p1, const PieceBox& p2, double kerf_half) {
    if (p1.id == p2.id) return false;
    
    // Expandir cada pieza por kerf/2 en cada lado para garantizar separación
    if ((p1.maxX + kerf_half) <= (p2.minX - kerf_half) || 
        (p1.minX - kerf_half) >= (p2.maxX + kerf_half)) return false;
    if ((p1.maxY + kerf_half) <= (p2.minY - kerf_half) || 
        (p1.minY - kerf_half) >= (p2.maxY + kerf_half)) return false;
    
    return true; // Hay solapamiento (incluyendo zona de kerf)
}

// Función principal de pulido (Nudging) expuesta a Python.
// plate_w/plate_h opcionales (<=0 = sin límite superior).
std::vector<std::tuple<int, double, double>> compact_plate(
        std::vector<std::tuple<int, double, double, double, double>> pieces_data, 
        double vx, 
        double vy,
        double kerf_mm,
        double plate_w = 0.0,
        double plate_h = 0.0) {
    
    std::vector<PieceBox> pieces;
    double kerf_half = std::max(0.0, kerf_mm / 2.0);
    
    for (const auto& data : pieces_data) {
        pieces.push_back({
            std::get<0>(data), // id (índice secuencial)
            std::get<1>(data), // minX
            std::get<2>(data), // minY
            std::get<3>(data), // maxX
            std::get<4>(data)  // maxY
        });
    }
    
    // Ordenamos por cercanía al origen (0,0) — las piezas más cercanas se asientan primero
    std::sort(pieces.begin(), pieces.end(), [](const PieceBox& a, const PieceBox& b) {
        return a.distToOrigin() < b.distToOrigin();
    });
    
    // Límite de iteraciones para evitar loops infinitos
    const int MAX_ITERATIONS = 5000;
    int total_nudges = 0;
    
    for (size_t i = 0; i < pieces.size(); ++i) {
        bool moved = true;
        int iter_count = 0;
        
        while (moved && iter_count < MAX_ITERATIONS) {
            moved = false;
            iter_count++;
            
            // Intento mover en X (dirección del vector de gravedad)
            double new_minX = pieces[i].minX + vx;
            double new_maxX = pieces[i].maxX + vx;
            const bool x_in_plate =
                (new_minX >= kerf_half) &&
                (plate_w <= 0.0 || new_maxX <= plate_w - kerf_half);
            if (x_in_plate) {
                pieces[i].minX += vx;
                pieces[i].maxX += vx;
                
                bool collision = false;
                for (size_t j = 0; j < pieces.size(); ++j) {
                    if (i != j && checkCollisionWithKerf(pieces[i], pieces[j], kerf_half)) {
                        collision = true;
                        break;
                    }
                }
                
                if (collision) {
                    // Revertir
                    pieces[i].minX -= vx;
                    pieces[i].maxX -= vx;
                } else {
                    pieces[i].shiftX += vx;
                    moved = true;
                    total_nudges++;
                }
            }
            
            // Intento mover en Y (dirección del vector de gravedad)
            double new_minY = pieces[i].minY + vy;
            double new_maxY = pieces[i].maxY + vy;
            const bool y_in_plate =
                (new_minY >= kerf_half) &&
                (plate_h <= 0.0 || new_maxY <= plate_h - kerf_half);
            if (y_in_plate) {
                pieces[i].minY += vy;
                pieces[i].maxY += vy;
                
                bool collision = false;
                for (size_t j = 0; j < pieces.size(); ++j) {
                    if (i != j && checkCollisionWithKerf(pieces[i], pieces[j], kerf_half)) {
                        collision = true;
                        break;
                    }
                }
                
                if (collision) {
                    // Revertir
                    pieces[i].minY -= vy;
                    pieces[i].maxY -= vy;
                } else {
                    pieces[i].shiftY += vy;
                    moved = true;
                    total_nudges++;
                }
            }
        }
    }
    
    std::vector<std::tuple<int, double, double>> results;
    for (const auto& p : pieces) {
        results.push_back(std::make_tuple(p.id, p.shiftX, p.shiftY));
    }
    
    return results;
}

PYBIND11_MODULE(venom_core, m) {
    m.doc() = "Venom Polisher C++ Core — Motor de compactación por gravedad artificial";
    m.def("compact_plate", &compact_plate, 
          "Compacta una placa empujando piezas hacia el vector de gravedad respetando kerf",
          py::arg("pieces_data"),
          py::arg("vx"),
          py::arg("vy"),
          py::arg("kerf_mm") = 0.0,
          py::arg("plate_w") = 0.0,
          py::arg("plate_h") = 0.0);
}
