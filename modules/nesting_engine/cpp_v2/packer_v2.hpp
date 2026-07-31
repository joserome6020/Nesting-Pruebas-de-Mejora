#pragma once

#include <optional>
#include <cstddef>
#include <string>
#include <vector>

namespace arga_v2 {

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
};

struct PieceOut {
    std::string nombre;
    std::vector<std::vector<Point2D>> poligonos;
    std::vector<std::vector<Point2D>> marcas;
    double area = 0.0;
    std::string calibre;
    std::string material;
};

struct CudaRasterMetrics {
    bool enabled = false;
    bool cuda_used = false;
    std::size_t candidates_evaluated = 0;
    std::size_t safe_rejected = 0;
    std::size_t h2d_bytes = 0;
    std::size_t d2h_bytes = 0;
    double h2d_ms = 0.0;
    double kernel_ms = 0.0;
    double d2h_ms = 0.0;
};

struct PackerTimingMetrics {
    std::size_t candidate_count = 0;
    double candidate_generation_ms = 0.0;
    double exact_collision_ms = 0.0;
    double rasterization_ms = 0.0;
};

struct SheetOut {
    std::vector<PieceOut> piezas;
    double area_usada = 0.0;
    double eficiencia = 0.0;
    CudaRasterMetrics cuda_raster;
    PackerTimingMetrics packer_timing;
};

struct PackResult {
    SheetOut hoja;
    std::vector<PieceIn> restos;
};

/**
 * Round-trip de anillos (S0 smoke).
 */
std::vector<std::vector<Point2D>> echo_rings(
    const std::vector<std::vector<Point2D>>& rings);

/**
 * true si Clipper2::Intersect(A,B) tiene área > epsilon.
 */
bool polygons_overlap(
    const std::vector<std::vector<Point2D>>& rings_a,
    const std::vector<std::vector<Point2D>>& rings_b,
    double epsilon = 1e-6);

/**
 * NFP exterior aproximado vía Clipper2 MinkowskiSum.
 *
 * Método (documentado):
 *   1. Tomar anillo exterior de A y de B.
 *   2. inv(B) = {(-x, -y) for (x,y) in B}.
 *   3. NFP ≈ MinkowskiSum(inv(B), A, closed=true, decimal_precision=3).
 *
 * Resultado: anillos del lugar geométrico de la referencia de B donde el
 * polígono orbitante toca A sin solaparse (NFP outer). Correcto para
 * rectángulos y polígonos simples; agujeros internos no se propagan (PoC).
 */
std::vector<std::vector<Point2D>> compute_nfp_outer(
    const std::vector<std::vector<Point2D>>& rings_a,
    const std::vector<std::vector<Point2D>>& rings_b);

/**
 * Métricas del caché NFP L1 por proceso.
 *
 * La llave usa geometrías canónicas (coordenadas cuantizadas a 0.001 mm,
 * normalizadas por su origen y sin depender de punto inicial/orientación del
 * anillo), los ángulos, kerf y la versión de algoritmo.
 */
struct NfpCacheStats {
    std::size_t hits = 0;
    std::size_t misses = 0;
    std::size_t evictions = 0;
    std::size_t entries = 0;
    std::size_t capacity = 0;
};

std::vector<std::vector<Point2D>> compute_nfp_outer_cached(
    const std::vector<std::vector<Point2D>>& rings_a,
    const std::vector<std::vector<Point2D>>& rings_b,
    double angle_a_deg = 0.0,
    double angle_b_deg = 0.0,
    double kerf_mm = 0.0);

NfpCacheStats nfp_cache_stats();
void reset_nfp_cache();
void set_nfp_cache_capacity(std::size_t capacity);

/**
 * Workload de NFP con geometrías normalizadas una vez por lote.
 *
 * Representa el patrón que usará el packer cuando precompute sus variaciones:
 * coste de preparación separado de los lookups de pares repetidos.
 */
struct NfpCacheWorkloadResult {
    std::size_t calls = 0;
    double preparation_ms = 0.0;
    double lookup_ms = 0.0;
    NfpCacheStats cache;
};

NfpCacheWorkloadResult run_nfp_cache_workload(
    const std::vector<PieceIn>& piezas,
    std::size_t iterations,
    double kerf_mm);

/**
 * Packer PoC S0: orden por área desc, rotación 0/90, bottom-left con rejilla
 * gruesa, no-solape Clipper2 + kerf por InflatePaths, dentro de placa.
 *
 * Contrato de salida igual que bindings.cpp: (hoja, restos) con polígonos
 * absolutos en mm, area_usada, eficiencia (%).
 *
 * Unidades de kerf/margin: mismas que producción (pulgadas → mm / 2 para kerf).
 */
PackResult empaquetar_una_hoja_poc(
    const std::vector<PieceIn>& piezas,
    double w_placa,
    double h_placa,
    double kerf_override = 0.3,
    double margin_override = 0.0,
    const std::string& opt_override = "OPTIMIZAR LARGO Y ANCHO",
    const std::string& corner_override = "INFERIOR IZQUIERDA",
    const std::optional<std::vector<std::vector<Point2D>>>& limite_rings = std::nullopt);

}  // namespace arga_v2
