#include "cuda/lab_grid_filter.hpp"

#include <cuda_runtime.h>

#include <chrono>
#include <vector>

namespace arga::lab_cuda::detail {
namespace {

__global__ void collide_kernel(
    const std::uint8_t* board,
    int board_w,
    int board_h,
    const std::uint8_t* piece,
    int piece_w,
    int piece_h,
    const GridOffset* offsets,
    std::size_t candidate_count,
    std::uint8_t* collided) {
    const std::size_t index =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (index >= candidate_count) {
        return;
    }
    const GridOffset offset = offsets[index];
    if (offset.x < 0 || offset.y < 0
        || offset.x + piece_w > board_w
        || offset.y + piece_h > board_h) {
        collided[index] = 1;
        return;
    }
    for (int y = 0; y < piece_h; ++y) {
        const int board_y = offset.y + y;
        for (int x = 0; x < piece_w; ++x) {
            if (piece[static_cast<std::size_t>(y) * piece_w + x] == 0) {
                continue;
            }
            const int board_x = offset.x + x;
            if (board[static_cast<std::size_t>(board_y) * board_w + board_x] != 0) {
                collided[index] = 1;
                return;
            }
        }
    }
}

bool is_success(cudaError_t status) {
    return status == cudaSuccess;
}

struct CudaGridSession {
    std::uint8_t* device_board = nullptr;
    std::uint8_t* device_piece = nullptr;
    GridOffset* device_offsets = nullptr;
    std::uint8_t* device_collided = nullptr;
    std::size_t board_capacity = 0;
    std::size_t piece_capacity = 0;
    std::size_t offset_capacity = 0;
    std::size_t collided_capacity = 0;
    int board_w = 0;
    int board_h = 0;
};

template <typename T>
bool ensure_capacity(T** buffer, std::size_t* capacity, std::size_t required) {
    if (*buffer && *capacity >= required) {
        return true;
    }
    if (*buffer) {
        cudaFree(*buffer);
        *buffer = nullptr;
        *capacity = 0;
    }
    if (required == 0) {
        return true;
    }
    if (!is_success(cudaMalloc(reinterpret_cast<void**>(buffer), required * sizeof(T)))) {
        return false;
    }
    *capacity = required;
    return true;
}

void destroy_session(CudaGridSession* session) {
    if (!session) {
        return;
    }
    if (session->device_board) cudaFree(session->device_board);
    if (session->device_piece) cudaFree(session->device_piece);
    if (session->device_offsets) cudaFree(session->device_offsets);
    if (session->device_collided) cudaFree(session->device_collided);
    delete session;
}

}  // namespace

bool cuda_backend_available() {
    int device_count = 0;
    return is_success(cudaGetDeviceCount(&device_count)) && device_count > 0;
}

int cuda_backend_status_code() {
    int device_count = 0;
    return static_cast<int>(cudaGetDeviceCount(&device_count));
}

void* cuda_session_create(
    const std::vector<std::uint8_t>& board,
    int board_w,
    int board_h) {
    if (board_w <= 0 || board_h <= 0
        || board.size() != static_cast<std::size_t>(board_w) * board_h) {
        return nullptr;
    }
    auto* session = new CudaGridSession();
    session->board_w = board_w;
    session->board_h = board_h;
    if (!ensure_capacity(&session->device_board, &session->board_capacity, board.size())
        || !is_success(cudaMemcpy(
            session->device_board,
            board.data(),
            board.size(),
            cudaMemcpyHostToDevice))) {
        destroy_session(session);
        return nullptr;
    }
    return session;
}

void cuda_session_destroy(void* opaque) {
    destroy_session(static_cast<CudaGridSession*>(opaque));
}

bool cuda_session_update_board(
    void* opaque,
    const std::vector<std::uint8_t>& board,
    int board_w,
    int board_h) {
    auto* session = static_cast<CudaGridSession*>(opaque);
    if (!session || board_w <= 0 || board_h <= 0
        || board.size() != static_cast<std::size_t>(board_w) * board_h
        || !ensure_capacity(&session->device_board, &session->board_capacity, board.size())
        || !is_success(cudaMemcpy(
            session->device_board,
            board.data(),
            board.size(),
            cudaMemcpyHostToDevice))) {
        return false;
    }
    session->board_w = board_w;
    session->board_h = board_h;
    return true;
}

bool cuda_session_collide_batch(
    void* opaque,
    const std::vector<std::uint8_t>& piece,
    int piece_w,
    int piece_h,
    const std::vector<GridOffset>& offsets,
    std::vector<std::uint8_t>* collided,
    GridFilterStats* stats) {
    auto* session = static_cast<CudaGridSession*>(opaque);
    if (!session || !collided || !stats || piece_w <= 0 || piece_h <= 0
        || piece.size() != static_cast<std::size_t>(piece_w) * piece_h) {
        return false;
    }
    if (offsets.empty()) {
        collided->clear();
        return true;
    }
    if (!ensure_capacity(&session->device_piece, &session->piece_capacity, piece.size())
        || !ensure_capacity(&session->device_offsets, &session->offset_capacity, offsets.size())
        || !ensure_capacity(
            &session->device_collided, &session->collided_capacity, offsets.size())) {
        return false;
    }

    cudaEvent_t started = nullptr;
    cudaEvent_t after_h2d = nullptr;
    cudaEvent_t after_kernel = nullptr;
    std::vector<std::uint8_t> host_collided(offsets.size(), 0);
    bool ok = false;
    do {
        if (!is_success(cudaEventCreate(&started))) break;
        if (!is_success(cudaEventCreate(&after_h2d))) break;
        if (!is_success(cudaEventCreate(&after_kernel))) break;
        if (!is_success(cudaEventRecord(started))) break;
        if (!is_success(cudaMemcpy(
                session->device_piece,
                piece.data(),
                piece.size(),
                cudaMemcpyHostToDevice))) break;
        if (!is_success(cudaMemcpy(
                session->device_offsets,
                offsets.data(),
                offsets.size() * sizeof(GridOffset),
                cudaMemcpyHostToDevice))) break;
        if (!is_success(cudaMemset(session->device_collided, 0, offsets.size()))) break;
        if (!is_success(cudaEventRecord(after_h2d))) break;

        constexpr int threads_per_block = 128;
        const int blocks = static_cast<int>(
            (offsets.size() + threads_per_block - 1) / threads_per_block);
        collide_kernel<<<blocks, threads_per_block>>>(
            session->device_board,
            session->board_w,
            session->board_h,
            session->device_piece,
            piece_w,
            piece_h,
            session->device_offsets,
            offsets.size(),
            session->device_collided);
        if (!is_success(cudaGetLastError())) break;
        if (!is_success(cudaEventRecord(after_kernel))) break;
        if (!is_success(cudaEventSynchronize(after_kernel))) break;
        const auto d2h_started = std::chrono::steady_clock::now();
        if (!is_success(cudaMemcpy(
                host_collided.data(),
                session->device_collided,
                host_collided.size(),
                cudaMemcpyDeviceToHost))) break;

        float h2d_ms = 0.0F;
        float kernel_ms = 0.0F;
        if (!is_success(cudaEventElapsedTime(&h2d_ms, started, after_h2d))) break;
        if (!is_success(cudaEventElapsedTime(&kernel_ms, after_h2d, after_kernel))) break;
        stats->h2d_ms += h2d_ms;
        stats->kernel_ms += kernel_ms;
        stats->d2h_ms += std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - d2h_started).count();
        stats->h2d_bytes += piece.size() + offsets.size() * sizeof(GridOffset);
        stats->d2h_bytes += host_collided.size();
        ok = true;
    } while (false);

    if (started) cudaEventDestroy(started);
    if (after_h2d) cudaEventDestroy(after_h2d);
    if (after_kernel) cudaEventDestroy(after_kernel);
    if (!ok) {
        return false;
    }
    *collided = std::move(host_collided);
    return true;
}

}  // namespace arga::lab_cuda::detail
