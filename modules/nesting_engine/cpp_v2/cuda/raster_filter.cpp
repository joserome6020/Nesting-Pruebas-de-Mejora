#include "cuda/raster_filter.hpp"

#include <algorithm>
#include <chrono>
#include <utility>

#if ARGA_CPP_V2_HAS_CUDA
namespace arga_v2::cuda::detail {
bool cuda_backend_available();
int cuda_backend_status_code();
void* cuda_session_create(
    const std::vector<std::uint8_t>& fixed_inner,
    int fixed_w,
    int fixed_h);
void cuda_session_destroy(void* session);
bool cuda_session_update_fixed(
    void* session,
    const std::vector<std::uint8_t>& fixed_inner,
    int fixed_w,
    int fixed_h);
bool cuda_session_set_candidate(
    void* session,
    const std::vector<std::uint8_t>& candidate_inner,
    int candidate_w,
    int candidate_h,
    RasterFilterStats* stats);
bool cuda_session_safe_reject_offsets(
    void* session,
    const std::vector<RasterOffset>& offsets,
    std::vector<std::uint8_t>* rejected,
    RasterFilterStats* stats);
bool cuda_session_safe_reject_batch(
    void* session,
    const std::vector<std::uint8_t>& candidate_inner,
    int candidate_w,
    int candidate_h,
    const std::vector<RasterOffset>& offsets,
    std::vector<std::uint8_t>* rejected,
    RasterFilterStats* stats);
bool cuda_session_screen_population(
    void* session,
    const std::vector<std::uint8_t>& candidate_inner,
    int candidate_w,
    int candidate_h,
    const std::vector<std::vector<RasterOffset>>& offset_batches,
    std::vector<std::vector<std::uint8_t>>* rejected_per_seed,
    RasterFilterStats* stats);
bool cuda_backend_safe_reject_batch(
    const std::vector<std::uint8_t>& fixed_inner,
    int fixed_w,
    int fixed_h,
    const std::vector<std::uint8_t>& candidate_inner,
    int candidate_w,
    int candidate_h,
    const std::vector<RasterOffset>& offsets,
    std::vector<std::uint8_t>* rejected,
    RasterFilterStats* stats);
}  // namespace arga_v2::cuda::detail
#endif

namespace arga_v2::cuda {
namespace {

bool valid_mask(
    const std::vector<std::uint8_t>& mask,
    int width,
    int height) {
    return width > 0 && height > 0
        && mask.size() == static_cast<std::size_t>(width) * static_cast<std::size_t>(height);
}

std::vector<std::uint8_t> cpu_safe_reject_batch(
    const std::vector<std::uint8_t>& fixed_inner,
    int fixed_w,
    int fixed_h,
    const std::vector<std::uint8_t>& candidate_inner,
    int candidate_w,
    int candidate_h,
    const std::vector<RasterOffset>& offsets,
    RasterFilterStats* stats) {
    std::vector<std::uint8_t> rejected(offsets.size(), 0);
    if (!valid_mask(fixed_inner, fixed_w, fixed_h)
        || !valid_mask(candidate_inner, candidate_w, candidate_h)) {
        return rejected;  // Fail-open: Clipper2 debe validar todas las candidatas.
    }

    const auto started = std::chrono::steady_clock::now();
    for (std::size_t candidate_index = 0; candidate_index < offsets.size(); ++candidate_index) {
        const auto offset = offsets[candidate_index];
        for (int y = 0; y < candidate_h && !rejected[candidate_index]; ++y) {
            const int fixed_y = offset.y + y;
            if (fixed_y < 0 || fixed_y >= fixed_h) {
                continue;
            }
            for (int x = 0; x < candidate_w; ++x) {
                if (candidate_inner[static_cast<std::size_t>(y) * candidate_w + x] == 0) {
                    continue;
                }
                const int fixed_x = offset.x + x;
                if (fixed_x < 0 || fixed_x >= fixed_w) {
                    continue;
                }
                if (fixed_inner[static_cast<std::size_t>(fixed_y) * fixed_w + fixed_x] != 0) {
                    rejected[candidate_index] = 1;
                    break;
                }
            }
        }
    }
    if (stats) {
        stats->kernel_ms = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - started).count();
    }
    return rejected;
}

}  // namespace

bool available() {
#if ARGA_CPP_V2_HAS_CUDA
    return detail::cuda_backend_available();
#else
    return false;
#endif
}

std::string availability_detail() {
#if ARGA_CPP_V2_HAS_CUDA
    const int code = detail::cuda_backend_status_code();
    return code == 0
        ? "CUDA runtime disponible."
        : "CUDA runtime no disponible; cudaGetDeviceCount code=" + std::to_string(code);
#else
    return "CUDA no fue incluido en este build; fallback CPU activo.";
#endif
}

std::vector<std::uint8_t> safe_reject_batch(
    const std::vector<std::uint8_t>& fixed_inner,
    int fixed_w,
    int fixed_h,
    const std::vector<std::uint8_t>& candidate_inner,
    int candidate_w,
    int candidate_h,
    const std::vector<RasterOffset>& offsets,
    RasterFilterStats* stats,
    bool prefer_cuda) {
    RasterFilterStats local;
    local.cuda_available = available();
    local.candidates_evaluated = offsets.size();

    std::vector<std::uint8_t> rejected;
#if ARGA_CPP_V2_HAS_CUDA
    if (prefer_cuda && local.cuda_available
        && detail::cuda_backend_safe_reject_batch(
            fixed_inner,
            fixed_w,
            fixed_h,
            candidate_inner,
            candidate_w,
            candidate_h,
            offsets,
            &rejected,
            &local)) {
        local.cuda_used = true;
    } else {
        rejected = cpu_safe_reject_batch(
            fixed_inner,
            fixed_w,
            fixed_h,
            candidate_inner,
            candidate_w,
            candidate_h,
            offsets,
            &local);
    }
#else
    rejected = cpu_safe_reject_batch(
        fixed_inner,
        fixed_w,
        fixed_h,
        candidate_inner,
        candidate_w,
        candidate_h,
        offsets,
        &local);
#endif
    local.safe_rejected = static_cast<std::size_t>(
        std::count(rejected.begin(), rejected.end(), static_cast<std::uint8_t>(1)));
    if (stats) {
        *stats = local;
    }
    return rejected;
}

struct RasterSession::Impl {
    std::vector<std::uint8_t> fixed_inner;
    int fixed_w = 0;
    int fixed_h = 0;
    std::vector<std::uint8_t> candidate_inner;
    int candidate_w = 0;
    int candidate_h = 0;
    bool prefer_cuda = true;
    void* cuda_session = nullptr;

    ~Impl() {
#if ARGA_CPP_V2_HAS_CUDA
        if (cuda_session) {
            detail::cuda_session_destroy(cuda_session);
        }
#endif
    }
};

RasterSession::RasterSession(
    std::vector<std::uint8_t> fixed_inner,
    int fixed_w,
    int fixed_h,
    bool prefer_cuda)
    : impl_(std::make_unique<Impl>()) {
    impl_->fixed_inner = std::move(fixed_inner);
    impl_->fixed_w = fixed_w;
    impl_->fixed_h = fixed_h;
    impl_->prefer_cuda = prefer_cuda;
#if ARGA_CPP_V2_HAS_CUDA
    if (impl_->prefer_cuda && available()
        && valid_mask(impl_->fixed_inner, impl_->fixed_w, impl_->fixed_h)) {
        impl_->cuda_session = detail::cuda_session_create(
            impl_->fixed_inner, impl_->fixed_w, impl_->fixed_h);
    }
#endif
}

RasterSession::~RasterSession() = default;

RasterSession::RasterSession(RasterSession&&) noexcept = default;
RasterSession& RasterSession::operator=(RasterSession&&) noexcept = default;

bool RasterSession::update_fixed(
    std::vector<std::uint8_t> fixed_inner,
    int fixed_w,
    int fixed_h) {
    if (!impl_ || !valid_mask(fixed_inner, fixed_w, fixed_h)) {
        return false;
    }
    impl_->fixed_inner = std::move(fixed_inner);
    impl_->fixed_w = fixed_w;
    impl_->fixed_h = fixed_h;
#if ARGA_CPP_V2_HAS_CUDA
    if (impl_->cuda_session && !detail::cuda_session_update_fixed(
            impl_->cuda_session, impl_->fixed_inner, fixed_w, fixed_h)) {
        detail::cuda_session_destroy(impl_->cuda_session);
        impl_->cuda_session = nullptr;
    } else if (!impl_->cuda_session && impl_->prefer_cuda && available()) {
        impl_->cuda_session = detail::cuda_session_create(
            impl_->fixed_inner, fixed_w, fixed_h);
    }
#endif
    return true;
}

bool RasterSession::cuda_active() const {
#if ARGA_CPP_V2_HAS_CUDA
    return impl_ && impl_->cuda_session != nullptr;
#else
    return false;
#endif
}

bool RasterSession::set_candidate(
    const std::vector<std::uint8_t>& candidate_inner,
    int candidate_w,
    int candidate_h) {
    if (!impl_ || !valid_mask(candidate_inner, candidate_w, candidate_h)) {
        return false;
    }
    impl_->candidate_inner = candidate_inner;
    impl_->candidate_w = candidate_w;
    impl_->candidate_h = candidate_h;
#if ARGA_CPP_V2_HAS_CUDA
    if (impl_->cuda_session) {
        RasterFilterStats unused;
        if (!detail::cuda_session_set_candidate(
                impl_->cuda_session,
                candidate_inner,
                candidate_w,
                candidate_h,
                &unused)) {
            detail::cuda_session_destroy(impl_->cuda_session);
            impl_->cuda_session = nullptr;
            return false;
        }
    }
#endif
    return true;
}

std::vector<std::uint8_t> RasterSession::safe_reject_batch(
    const std::vector<std::uint8_t>& candidate_inner,
    int candidate_w,
    int candidate_h,
    const std::vector<RasterOffset>& offsets,
    RasterFilterStats* stats) {
    RasterFilterStats local;
    local.cuda_available = available();
    local.candidates_evaluated = offsets.size();
    local.batches_evaluated = offsets.empty() ? 0 : 1;
    if (!impl_ || !valid_mask(
            candidate_inner, candidate_w, candidate_h)) {
        if (stats) {
            *stats = local;
        }
        return std::vector<std::uint8_t>(offsets.size(), 0);
    }

    std::vector<std::uint8_t> rejected;
#if ARGA_CPP_V2_HAS_CUDA
    if (impl_->cuda_session && detail::cuda_session_safe_reject_batch(
            impl_->cuda_session,
            candidate_inner,
            candidate_w,
            candidate_h,
            offsets,
            &rejected,
            &local)) {
        local.cuda_used = true;
    } else {
        rejected = cpu_safe_reject_batch(
            impl_->fixed_inner,
            impl_->fixed_w,
            impl_->fixed_h,
            candidate_inner,
            candidate_w,
            candidate_h,
            offsets,
            &local);
    }
#else
    rejected = cpu_safe_reject_batch(
        impl_->fixed_inner,
        impl_->fixed_w,
        impl_->fixed_h,
        candidate_inner,
        candidate_w,
        candidate_h,
        offsets,
        &local);
#endif
    local.safe_rejected = static_cast<std::size_t>(
        std::count(rejected.begin(), rejected.end(), static_cast<std::uint8_t>(1)));
    if (stats) {
        *stats = local;
    }
    return rejected;
}

PopulationScreenResult RasterSession::screen_population(
    const std::vector<std::uint8_t>& candidate_inner,
    int candidate_w,
    int candidate_h,
    const std::vector<std::vector<RasterOffset>>& offset_batches) {
    PopulationScreenResult result;
    result.stats.cuda_available = available();
    result.stats.batches_evaluated = offset_batches.size();
    for (const auto& batch : offset_batches) {
        result.stats.candidates_evaluated += batch.size();
    }
    result.rejected_per_seed.reserve(offset_batches.size());

    if (!impl_ || !valid_mask(candidate_inner, candidate_w, candidate_h)) {
        for (const auto& batch : offset_batches) {
            result.rejected_per_seed.emplace_back(batch.size(), 0);
        }
        return result;
    }

#if ARGA_CPP_V2_HAS_CUDA
    if (impl_->cuda_session
        && detail::cuda_session_screen_population(
            impl_->cuda_session,
            candidate_inner,
            candidate_w,
            candidate_h,
            offset_batches,
            &result.rejected_per_seed,
            &result.stats)) {
        result.stats.cuda_used = true;
    } else {
#endif
        for (const auto& batch : offset_batches) {
            RasterFilterStats batch_stats;
            result.rejected_per_seed.push_back(cpu_safe_reject_batch(
                impl_->fixed_inner,
                impl_->fixed_w,
                impl_->fixed_h,
                candidate_inner,
                candidate_w,
                candidate_h,
                batch,
                &batch_stats));
            result.stats.kernel_ms += batch_stats.kernel_ms;
        }
#if ARGA_CPP_V2_HAS_CUDA
    }
#endif

    result.stats.safe_rejected = 0;
    for (const auto& batch : result.rejected_per_seed) {
        result.stats.safe_rejected += static_cast<std::size_t>(
            std::count(batch.begin(), batch.end(), static_cast<std::uint8_t>(1)));
    }
    return result;
}

}  // namespace arga_v2::cuda
