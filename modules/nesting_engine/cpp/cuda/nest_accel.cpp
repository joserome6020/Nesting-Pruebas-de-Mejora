#include "cuda/nest_accel.hpp"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <utility>

#if ARGA_NEST_HAS_CUDA
namespace arga::cuda::detail {
bool cuda_backend_available();
int cuda_backend_status_code();
void* cuda_session_create(
    const std::vector<std::uint8_t>& board,
    int board_w,
    int board_h);
void cuda_session_destroy(void* session);
bool cuda_session_update_board(
    void* session,
    const std::vector<std::uint8_t>& board,
    int board_w,
    int board_h);
bool cuda_session_collide_batch(
    void* session,
    const std::vector<std::uint8_t>& piece,
    int piece_w,
    int piece_h,
    const std::vector<GridOffset>& offsets,
    std::vector<std::uint8_t>* collided,
    GridFilterStats* stats);
}  // namespace arga::cuda::detail
#endif

namespace arga::cuda {
namespace {

bool valid_mask(const std::vector<std::uint8_t>& mask, int width, int height) {
    return width > 0 && height > 0
        && mask.size() == static_cast<std::size_t>(width) * static_cast<std::size_t>(height);
}

bool env_flag_on(const char* name) {
    const char* value = std::getenv(name);
    if (!value) {
        return false;
    }
    return std::strcmp(value, "1") == 0
        || std::strcmp(value, "true") == 0
        || std::strcmp(value, "TRUE") == 0
        || std::strcmp(value, "on") == 0
        || std::strcmp(value, "ON") == 0;
}

std::vector<std::uint8_t> cpu_collide_batch(
    const std::vector<std::uint8_t>& board,
    int board_w,
    int board_h,
    const std::vector<std::uint8_t>& piece,
    int piece_w,
    int piece_h,
    const std::vector<GridOffset>& offsets,
    GridFilterStats* stats) {
    std::vector<std::uint8_t> collided(offsets.size(), 0);
    if (!valid_mask(board, board_w, board_h) || !valid_mask(piece, piece_w, piece_h)) {
        // Máscara inválida → no rechazar (Clipper decide).
        return collided;
    }
    const auto started = std::chrono::steady_clock::now();
    for (std::size_t i = 0; i < offsets.size(); ++i) {
        const auto offset = offsets[i];
        if (offset.x < 0 || offset.y < 0
            || offset.x + piece_w > board_w
            || offset.y + piece_h > board_h) {
            // Fuera de máscara: no es rechazo seguro.
            continue;
        }
        for (int y = 0; y < piece_h && !collided[i]; ++y) {
            for (int x = 0; x < piece_w; ++x) {
                if (piece[static_cast<std::size_t>(y) * piece_w + x] == 0) {
                    continue;
                }
                const auto board_index =
                    static_cast<std::size_t>(offset.y + y) * board_w
                    + static_cast<std::size_t>(offset.x + x);
                if (board[board_index] != 0) {
                    collided[i] = 1;
                    break;
                }
            }
        }
    }
    if (stats) {
        stats->kernel_ms += std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - started).count();
    }
    return collided;
}

}  // namespace

bool available() {
#if ARGA_NEST_HAS_CUDA
    return detail::cuda_backend_available();
#else
    return false;
#endif
}

bool requested() {
    return env_flag_on("ARGA_NEST_CUDA")
        || env_flag_on("ARGA_ULTRA_CUDA")
        || env_flag_on("ARGA_FORCE_CUDA")
        || env_flag_on("ARGA_LITE_CUDA")
        || env_flag_on("ARGA_BURKE_CUDA")
        || env_flag_on("ARGA_VENOM_CUDA");
}

std::string availability_detail() {
#if ARGA_NEST_HAS_CUDA
    const int code = detail::cuda_backend_status_code();
    return code == 0
        ? "CUDA runtime disponible para algorithm_cpp."
        : "CUDA runtime no disponible para algorithm_cpp; code=" + std::to_string(code);
#else
    return "CUDA no incluido en este build de algorithm_cpp; fallback CPU.";
#endif
}

struct GridSession::Impl {
    std::vector<std::uint8_t> board;
    int board_w = 0;
    int board_h = 0;
    bool prefer_cuda = true;
    void* cuda_session = nullptr;

    ~Impl() {
#if ARGA_NEST_HAS_CUDA
        if (cuda_session) {
            detail::cuda_session_destroy(cuda_session);
        }
#endif
    }
};

GridSession::GridSession(
    std::vector<std::uint8_t> board,
    int board_w,
    int board_h,
    bool prefer_cuda)
    : impl_(std::make_unique<Impl>()) {
    impl_->board = std::move(board);
    impl_->board_w = board_w;
    impl_->board_h = board_h;
    impl_->prefer_cuda = prefer_cuda;
#if ARGA_NEST_HAS_CUDA
    if (impl_->prefer_cuda && available()
        && valid_mask(impl_->board, impl_->board_w, impl_->board_h)) {
        impl_->cuda_session = detail::cuda_session_create(
            impl_->board, impl_->board_w, impl_->board_h);
    }
#endif
}

GridSession::~GridSession() = default;
GridSession::GridSession(GridSession&&) noexcept = default;
GridSession& GridSession::operator=(GridSession&&) noexcept = default;

bool GridSession::update_board(
    std::vector<std::uint8_t> board,
    int board_w,
    int board_h) {
    if (!impl_ || !valid_mask(board, board_w, board_h)) {
        return false;
    }
    impl_->board = std::move(board);
    impl_->board_w = board_w;
    impl_->board_h = board_h;
#if ARGA_NEST_HAS_CUDA
    if (impl_->cuda_session
        && !detail::cuda_session_update_board(
            impl_->cuda_session, impl_->board, board_w, board_h)) {
        detail::cuda_session_destroy(impl_->cuda_session);
        impl_->cuda_session = nullptr;
    } else if (!impl_->cuda_session && impl_->prefer_cuda && available()) {
        impl_->cuda_session = detail::cuda_session_create(
            impl_->board, board_w, board_h);
    }
#endif
    return true;
}

bool GridSession::cuda_active() const {
#if ARGA_NEST_HAS_CUDA
    return impl_ && impl_->cuda_session != nullptr;
#else
    return false;
#endif
}

std::vector<std::uint8_t> GridSession::collide_batch(
    const std::vector<std::uint8_t>& piece,
    int piece_w,
    int piece_h,
    const std::vector<GridOffset>& offsets,
    GridFilterStats* stats) {
    GridFilterStats local;
    local.cuda_available = available();
    local.candidates_evaluated = offsets.size();
    if (!impl_ || !valid_mask(piece, piece_w, piece_h)) {
        if (stats) {
            *stats = local;
        }
        return std::vector<std::uint8_t>(offsets.size(), 1);
    }

    std::vector<std::uint8_t> collided;
#if ARGA_NEST_HAS_CUDA
    if (impl_->cuda_session
        && detail::cuda_session_collide_batch(
            impl_->cuda_session,
            piece,
            piece_w,
            piece_h,
            offsets,
            &collided,
            &local)) {
        local.cuda_used = true;
    } else {
        collided = cpu_collide_batch(
            impl_->board,
            impl_->board_w,
            impl_->board_h,
            piece,
            piece_w,
            piece_h,
            offsets,
            &local);
    }
#else
    collided = cpu_collide_batch(
        impl_->board,
        impl_->board_w,
        impl_->board_h,
        piece,
        piece_w,
        piece_h,
        offsets,
        &local);
#endif
    local.collisions = static_cast<std::size_t>(
        std::count(collided.begin(), collided.end(), static_cast<std::uint8_t>(1)));
    if (stats) {
        *stats = local;
    }
    return collided;
}

std::vector<std::uint8_t> collide_batch(
    const std::vector<std::uint8_t>& board,
    int board_w,
    int board_h,
    const std::vector<std::uint8_t>& piece,
    int piece_w,
    int piece_h,
    const std::vector<GridOffset>& offsets,
    GridFilterStats* stats,
    bool prefer_cuda) {
    GridFilterStats local;
    local.cuda_available = available();
    local.candidates_evaluated = offsets.size();

    if (!valid_mask(board, board_w, board_h) || !valid_mask(piece, piece_w, piece_h)) {
        if (stats) {
            *stats = local;
        }
        return std::vector<std::uint8_t>(offsets.size(), 0);
    }

    std::vector<std::uint8_t> collided;
    if (prefer_cuda && local.cuda_available) {
        GridSession session(board, board_w, board_h, true);
        if (session.cuda_active()) {
            collided = session.collide_batch(piece, piece_w, piece_h, offsets, &local);
            if (stats) {
                *stats = local;
            }
            return collided;
        }
    }

    collided = cpu_collide_batch(
        board, board_w, board_h, piece, piece_w, piece_h, offsets, &local);
    local.collisions = static_cast<std::size_t>(
        std::count(collided.begin(), collided.end(), static_cast<std::uint8_t>(1)));
    if (stats) {
        *stats = local;
    }
    return collided;
}

}  // namespace arga::cuda
