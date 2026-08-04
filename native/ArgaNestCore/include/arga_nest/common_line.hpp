#pragma once

#include "packer.hpp"

#include <string>
#include <vector>

namespace arga::core {

struct CommonLinePair {
    std::string a;
    std::string b;
    double length_mm = 0.0;
    double gap_mm = 0.0;
    bool has_geom = false;
    Point2D p0{0, 0};
    Point2D p1{0, 0};
};

struct CommonLineReport {
    std::vector<CommonLinePair> pairs;
    double total_shared_mm = 0.0;
};

struct CommonCutPath {
    std::vector<Point2D> points;
    double length_mm = 0.0;
    int source_pairs = 0;
};

struct CommonCutMergeReport {
    std::vector<CommonCutPath> paths;
    double total_path_mm = 0.0;
    int segments_in = 0;
    int paths_out = 0;
    /** Estimación: un pierce menos por cada fusión de segmentos colineales. */
    int pierce_saved = 0;
};

/**
 * Detecta bordes casi coincidentes entre piezas (candidatos common-line).
 * Tol: distancia máxima entre segmentos colineales.
 * Rellena geometría p0/p1 (línea media del solape) cuando hay match.
 */
CommonLineReport detect_common_lines(
    const PackResult& result,
    double max_gap_mm = 0.5,
    double min_length_mm = 5.0);

/**
 * Fusiona segmentos common-line colineales/conectados en trayectorias
 * CONTINUAS (menos pierces de máquina).
 */
CommonCutMergeReport merge_common_cut_paths(
    const CommonLineReport& report,
    double join_tol_mm = 1.5);

}  // namespace arga::core
