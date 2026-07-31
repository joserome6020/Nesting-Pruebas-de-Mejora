#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace arga::lab_cuda {

struct GridOffset {
    int x = 0;
    int y = 0;
};

struct GridFilterStats {
    bool cuda_available = false;
    bool cuda_used = false;
    std::size_t candidates_evaluated = 0;
    std::size_t collisions = 0;
    std::size_t h2d_bytes = 0;
    std::size_t d2h_bytes = 0;
    double h2d_ms = 0.0;
    double kernel_ms = 0.0;
    double d2h_ms = 0.0;
};

/**
 * Acelerador del escaneo raster del piloto LAB.
 *
 * Misma semántica que grid_collide del packer: 1 = colisión ocupada.
 * No decide colocación; Clipper2 sigue validando la shortlist.
 */
class GridSession {
public:
    GridSession(
        std::vector<std::uint8_t> board,
        int board_w,
        int board_h,
        bool prefer_cuda = true);
    ~GridSession();

    GridSession(GridSession&&) noexcept;
    GridSession& operator=(GridSession&&) noexcept;
    GridSession(const GridSession&) = delete;
    GridSession& operator=(const GridSession&) = delete;

    bool update_board(std::vector<std::uint8_t> board, int board_w, int board_h);
    bool cuda_active() const;

    std::vector<std::uint8_t> collide_batch(
        const std::vector<std::uint8_t>& piece,
        int piece_w,
        int piece_h,
        const std::vector<GridOffset>& offsets,
        GridFilterStats* stats = nullptr);

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

bool available();
bool requested();  // ARGA_LAB_PILOT_CUDA=1
std::string availability_detail();

}  // namespace arga::lab_cuda
