#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace arga_v2::cuda {

// Offset de una candidata BLF, en celdas de la rejilla raster.
struct RasterOffset {
    int x = 0;
    int y = 0;
};

struct RasterFilterStats {
    bool cuda_available = false;
    bool cuda_used = false;
    std::size_t candidates_evaluated = 0;
    std::size_t safe_rejected = 0;
    std::size_t batches_evaluated = 0;
    std::size_t h2d_bytes = 0;
    std::size_t d2h_bytes = 0;
    double h2d_ms = 0.0;
    double kernel_ms = 0.0;
    double d2h_ms = 0.0;
};

struct PopulationScreenResult {
    std::vector<std::vector<std::uint8_t>> rejected_per_seed;
    RasterFilterStats stats;
};

/**
 * Rechaza solo candidatas con una celda interior común.
 *
 * fixed_inner y candidate_inner deben marcar exclusivamente celdas totalmente
 * contenidas en sus polígonos ya inflados por kerf. Por tanto, un `true`
 * implica intersección real de metal; un `false` solo significa "validar con
 * Clipper2". Nunca se acepta una candidata solo por este filtro.
 */
std::vector<std::uint8_t> safe_reject_batch(
    const std::vector<std::uint8_t>& fixed_inner,
    int fixed_w,
    int fixed_h,
    const std::vector<std::uint8_t>& candidate_inner,
    int candidate_w,
    int candidate_h,
    const std::vector<RasterOffset>& offsets,
    RasterFilterStats* stats = nullptr,
    bool prefer_cuda = true);

/**
 * Sesión reutilizable para poblaciones/semillas independientes.
 *
 * Mantiene la máscara fija residente en GPU y reutiliza buffers temporales.
 * No se usa desde el BLF secuencial del packer: está diseñada para trabajo
 * masivo donde varios lotes comparten la misma placa parcial.
 */
class RasterSession {
public:
    RasterSession(
        std::vector<std::uint8_t> fixed_inner,
        int fixed_w,
        int fixed_h,
        bool prefer_cuda = true);
    ~RasterSession();

    RasterSession(RasterSession&&) noexcept;
    RasterSession& operator=(RasterSession&&) noexcept;
    RasterSession(const RasterSession&) = delete;
    RasterSession& operator=(const RasterSession&) = delete;

    bool update_fixed(std::vector<std::uint8_t> fixed_inner, int fixed_w, int fixed_h);
    bool cuda_active() const;

    /**
     * Sube (o reutiliza) la máscara de candidata en GPU. Pensado para muchas
     * semillas que evalúan la misma geometría contra la misma placa parcial.
     */
    bool set_candidate(
        const std::vector<std::uint8_t>& candidate_inner,
        int candidate_w,
        int candidate_h);

    std::vector<std::uint8_t> safe_reject_batch(
        const std::vector<std::uint8_t>& candidate_inner,
        int candidate_w,
        int candidate_h,
        const std::vector<RasterOffset>& offsets,
        RasterFilterStats* stats = nullptr);

    /**
     * Criba varias semillas en una sola llamada C++.
     *
     * Sube la candidata una vez, reutiliza la máscara fija residente y solo
     * transfiere offsets/resultados por lote. Reduce el ir-y-venir Python↔C++
     * y el H2D de la geometría candidata.
     */
    PopulationScreenResult screen_population(
        const std::vector<std::uint8_t>& candidate_inner,
        int candidate_w,
        int candidate_h,
        const std::vector<std::vector<RasterOffset>>& offset_batches);

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

bool available();
std::string availability_detail();

}  // namespace arga_v2::cuda
