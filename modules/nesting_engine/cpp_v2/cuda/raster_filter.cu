#include "cuda/raster_filter.hpp"

#include <cuda_runtime.h>

#include <chrono>
#include <vector>

namespace arga_v2::cuda::detail {
namespace {

__global__ void safe_reject_kernel(
    const std::uint8_t* fixed_inner,
    int fixed_w,
    int fixed_h,
    const std::uint8_t* candidate_inner,
    int candidate_w,
    int candidate_h,
    const RasterOffset* offsets,
    std::size_t candidate_count,
    std::uint8_t* rejected) {
    const std::size_t candidate_index =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (candidate_index >= candidate_count) {
        return;
    }
    const RasterOffset offset = offsets[candidate_index];
    for (int y = 0; y < candidate_h; ++y) {
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
                return;
            }
        }
    }
}

bool is_success(cudaError_t status) {
    return status == cudaSuccess;
}

struct CudaRasterSession {
    std::uint8_t* device_fixed = nullptr;
    std::uint8_t* device_candidate = nullptr;
    RasterOffset* device_offsets = nullptr;
    std::uint8_t* device_rejected = nullptr;
    std::size_t fixed_capacity = 0;
    std::size_t candidate_capacity = 0;
    std::size_t offset_capacity = 0;
    std::size_t rejected_capacity = 0;
    int fixed_w = 0;
    int fixed_h = 0;
    int candidate_w = 0;
    int candidate_h = 0;
    bool candidate_loaded = false;
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

void destroy_session(CudaRasterSession* session) {
    if (!session) {
        return;
    }
    if (session->device_fixed) cudaFree(session->device_fixed);
    if (session->device_candidate) cudaFree(session->device_candidate);
    if (session->device_offsets) cudaFree(session->device_offsets);
    if (session->device_rejected) cudaFree(session->device_rejected);
    delete session;
}

}  // namespace

bool cuda_backend_available() {
    int device_count = 0;
    return is_success(cudaGetDeviceCount(&device_count)) && device_count > 0;
}

int cuda_backend_status_code() {
    int device_count = 0;
    const cudaError_t status = cudaGetDeviceCount(&device_count);
    return static_cast<int>(status);
}

void* cuda_session_create(
    const std::vector<std::uint8_t>& fixed_inner,
    int fixed_w,
    int fixed_h) {
    if (fixed_w <= 0 || fixed_h <= 0 || fixed_inner.size()
            != static_cast<std::size_t>(fixed_w) * static_cast<std::size_t>(fixed_h)) {
        return nullptr;
    }
    auto* session = new CudaRasterSession();
    session->fixed_w = fixed_w;
    session->fixed_h = fixed_h;
    if (!ensure_capacity(
            &session->device_fixed,
            &session->fixed_capacity,
            fixed_inner.size())
        || !is_success(cudaMemcpy(
            session->device_fixed,
            fixed_inner.data(),
            fixed_inner.size(),
            cudaMemcpyHostToDevice))) {
        destroy_session(session);
        return nullptr;
    }
    return session;
}

void cuda_session_destroy(void* opaque_session) {
    destroy_session(static_cast<CudaRasterSession*>(opaque_session));
}

bool cuda_session_update_fixed(
    void* opaque_session,
    const std::vector<std::uint8_t>& fixed_inner,
    int fixed_w,
    int fixed_h) {
    auto* session = static_cast<CudaRasterSession*>(opaque_session);
    if (!session || fixed_w <= 0 || fixed_h <= 0 || fixed_inner.size()
            != static_cast<std::size_t>(fixed_w) * static_cast<std::size_t>(fixed_h)
        || !ensure_capacity(
            &session->device_fixed, &session->fixed_capacity, fixed_inner.size())
        || !is_success(cudaMemcpy(
            session->device_fixed,
            fixed_inner.data(),
            fixed_inner.size(),
            cudaMemcpyHostToDevice))) {
        return false;
    }
    session->fixed_w = fixed_w;
    session->fixed_h = fixed_h;
    return true;
}

bool cuda_session_set_candidate(
    void* opaque_session,
    const std::vector<std::uint8_t>& candidate_inner,
    int candidate_w,
    int candidate_h,
    RasterFilterStats* stats) {
    auto* session = static_cast<CudaRasterSession*>(opaque_session);
    if (!session || candidate_w <= 0 || candidate_h <= 0 || candidate_inner.size()
            != static_cast<std::size_t>(candidate_w) * static_cast<std::size_t>(candidate_h)
        || !ensure_capacity(
            &session->device_candidate,
            &session->candidate_capacity,
            candidate_inner.size())
        || !is_success(cudaMemcpy(
            session->device_candidate,
            candidate_inner.data(),
            candidate_inner.size(),
            cudaMemcpyHostToDevice))) {
        return false;
    }
    session->candidate_w = candidate_w;
    session->candidate_h = candidate_h;
    session->candidate_loaded = true;
    if (stats) {
        stats->h2d_bytes += candidate_inner.size();
    }
    return true;
}

bool cuda_session_safe_reject_offsets(
    void* opaque_session,
    const std::vector<RasterOffset>& offsets,
    std::vector<std::uint8_t>* rejected,
    RasterFilterStats* stats) {
    auto* session = static_cast<CudaRasterSession*>(opaque_session);
    if (!session || !rejected || !stats || !session->candidate_loaded
        || session->candidate_w <= 0 || session->candidate_h <= 0) {
        return false;
    }
    if (offsets.empty()) {
        rejected->clear();
        return true;
    }
    if (!ensure_capacity(
            &session->device_offsets,
            &session->offset_capacity,
            offsets.size())
        || !ensure_capacity(
            &session->device_rejected,
            &session->rejected_capacity,
            offsets.size())) {
        return false;
    }

    cudaEvent_t started = nullptr;
    cudaEvent_t after_h2d = nullptr;
    cudaEvent_t after_kernel = nullptr;
    std::vector<std::uint8_t> host_rejected(offsets.size(), 0);
    bool ok = false;
    do {
        if (!is_success(cudaEventCreate(&started))) break;
        if (!is_success(cudaEventCreate(&after_h2d))) break;
        if (!is_success(cudaEventCreate(&after_kernel))) break;
        if (!is_success(cudaEventRecord(started))) break;
        if (!is_success(cudaMemcpy(
                session->device_offsets,
                offsets.data(),
                offsets.size() * sizeof(RasterOffset),
                cudaMemcpyHostToDevice))) break;
        if (!is_success(cudaMemset(session->device_rejected, 0, offsets.size()))) break;
        if (!is_success(cudaEventRecord(after_h2d))) break;

        constexpr int threads_per_block = 128;
        const int blocks = static_cast<int>(
            (offsets.size() + threads_per_block - 1) / threads_per_block);
        safe_reject_kernel<<<blocks, threads_per_block>>>(
            session->device_fixed,
            session->fixed_w,
            session->fixed_h,
            session->device_candidate,
            session->candidate_w,
            session->candidate_h,
            session->device_offsets,
            offsets.size(),
            session->device_rejected);
        if (!is_success(cudaGetLastError())) break;
        if (!is_success(cudaEventRecord(after_kernel))) break;
        if (!is_success(cudaEventSynchronize(after_kernel))) break;
        const auto d2h_started = std::chrono::steady_clock::now();
        if (!is_success(cudaMemcpy(
                host_rejected.data(),
                session->device_rejected,
                host_rejected.size(),
                cudaMemcpyDeviceToHost))) break;

        float h2d_ms = 0.0F;
        float kernel_ms = 0.0F;
        if (!is_success(cudaEventElapsedTime(&h2d_ms, started, after_h2d))) break;
        if (!is_success(cudaEventElapsedTime(&kernel_ms, after_h2d, after_kernel))) break;
        stats->h2d_ms += h2d_ms;
        stats->kernel_ms += kernel_ms;
        stats->d2h_ms += std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - d2h_started).count();
        stats->h2d_bytes += offsets.size() * sizeof(RasterOffset);
        stats->d2h_bytes += host_rejected.size();
        ok = true;
    } while (false);

    if (started) cudaEventDestroy(started);
    if (after_h2d) cudaEventDestroy(after_h2d);
    if (after_kernel) cudaEventDestroy(after_kernel);
    if (!ok) {
        return false;
    }
    *rejected = std::move(host_rejected);
    return true;
}

bool cuda_session_safe_reject_batch(
    void* opaque_session,
    const std::vector<std::uint8_t>& candidate_inner,
    int candidate_w,
    int candidate_h,
    const std::vector<RasterOffset>& offsets,
    std::vector<std::uint8_t>* rejected,
    RasterFilterStats* stats) {
    if (!cuda_session_set_candidate(
            opaque_session, candidate_inner, candidate_w, candidate_h, stats)) {
        return false;
    }
    return cuda_session_safe_reject_offsets(opaque_session, offsets, rejected, stats);
}

bool cuda_session_screen_population(
    void* opaque_session,
    const std::vector<std::uint8_t>& candidate_inner,
    int candidate_w,
    int candidate_h,
    const std::vector<std::vector<RasterOffset>>& offset_batches,
    std::vector<std::vector<std::uint8_t>>* rejected_per_seed,
    RasterFilterStats* stats) {
    if (!rejected_per_seed || !stats) {
        return false;
    }
    rejected_per_seed->clear();
    rejected_per_seed->reserve(offset_batches.size());
    if (!cuda_session_set_candidate(
            opaque_session, candidate_inner, candidate_w, candidate_h, stats)) {
        return false;
    }
    for (const auto& offsets : offset_batches) {
        std::vector<std::uint8_t> rejected;
        if (!cuda_session_safe_reject_offsets(
                opaque_session, offsets, &rejected, stats)) {
            return false;
        }
        rejected_per_seed->push_back(std::move(rejected));
    }
    return true;
}

bool cuda_backend_safe_reject_batch(
    const std::vector<std::uint8_t>& fixed_inner,
    int fixed_w,
    int fixed_h,
    const std::vector<std::uint8_t>& candidate_inner,
    int candidate_w,
    int candidate_h,
    const std::vector<RasterOffset>& offsets,
    std::vector<std::uint8_t>* rejected,
    RasterFilterStats* stats) {
    if (!rejected || !stats || fixed_w <= 0 || fixed_h <= 0 || candidate_w <= 0
        || candidate_h <= 0 || fixed_inner.size()
            != static_cast<std::size_t>(fixed_w) * static_cast<std::size_t>(fixed_h)
        || candidate_inner.size()
            != static_cast<std::size_t>(candidate_w) * static_cast<std::size_t>(candidate_h)) {
        return false;
    }
    if (offsets.empty()) {
        rejected->clear();
        return true;
    }

    std::uint8_t* device_fixed = nullptr;
    std::uint8_t* device_candidate = nullptr;
    RasterOffset* device_offsets = nullptr;
    std::uint8_t* device_rejected = nullptr;
    cudaEvent_t started = nullptr;
    cudaEvent_t after_h2d = nullptr;
    cudaEvent_t after_kernel = nullptr;
    bool ok = false;
    RasterFilterStats local = *stats;
    std::vector<std::uint8_t> host_rejected(offsets.size(), 0);

    do {
        if (!is_success(cudaMalloc(&device_fixed, fixed_inner.size()))) break;
        if (!is_success(cudaMalloc(&device_candidate, candidate_inner.size()))) break;
        if (!is_success(cudaMalloc(
                &device_offsets, offsets.size() * sizeof(RasterOffset)))) break;
        if (!is_success(cudaMalloc(&device_rejected, offsets.size()))) break;
        if (!is_success(cudaEventCreate(&started))) break;
        if (!is_success(cudaEventCreate(&after_h2d))) break;
        if (!is_success(cudaEventCreate(&after_kernel))) break;

        if (!is_success(cudaEventRecord(started))) break;
        if (!is_success(cudaMemcpy(
                device_fixed,
                fixed_inner.data(),
                fixed_inner.size(),
                cudaMemcpyHostToDevice))) break;
        if (!is_success(cudaMemcpy(
                device_candidate,
                candidate_inner.data(),
                candidate_inner.size(),
                cudaMemcpyHostToDevice))) break;
        if (!is_success(cudaMemcpy(
                device_offsets,
                offsets.data(),
                offsets.size() * sizeof(RasterOffset),
                cudaMemcpyHostToDevice))) break;
        if (!is_success(cudaMemset(device_rejected, 0, offsets.size()))) break;
        if (!is_success(cudaEventRecord(after_h2d))) break;

        constexpr int threads_per_block = 128;
        const int blocks = static_cast<int>(
            (offsets.size() + threads_per_block - 1) / threads_per_block);
        safe_reject_kernel<<<blocks, threads_per_block>>>(
            device_fixed,
            fixed_w,
            fixed_h,
            device_candidate,
            candidate_w,
            candidate_h,
            device_offsets,
            offsets.size(),
            device_rejected);
        if (!is_success(cudaGetLastError())) break;
        if (!is_success(cudaEventRecord(after_kernel))) break;
        if (!is_success(cudaEventSynchronize(after_kernel))) break;
        const auto d2h_started = std::chrono::steady_clock::now();
        if (!is_success(cudaMemcpy(
                host_rejected.data(),
                device_rejected,
                host_rejected.size(),
                cudaMemcpyDeviceToHost))) break;

        float h2d_ms = 0.0F;
        float kernel_ms = 0.0F;
        if (!is_success(cudaEventElapsedTime(&h2d_ms, started, after_h2d))) break;
        if (!is_success(cudaEventElapsedTime(&kernel_ms, after_h2d, after_kernel))) break;
        local.h2d_ms = h2d_ms;
        local.kernel_ms = kernel_ms;
        local.d2h_ms = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - d2h_started).count();
        local.h2d_bytes = fixed_inner.size() + candidate_inner.size()
            + offsets.size() * sizeof(RasterOffset);
        local.d2h_bytes = host_rejected.size();
        ok = true;
    } while (false);

    if (started) cudaEventDestroy(started);
    if (after_h2d) cudaEventDestroy(after_h2d);
    if (after_kernel) cudaEventDestroy(after_kernel);
    if (device_fixed) cudaFree(device_fixed);
    if (device_candidate) cudaFree(device_candidate);
    if (device_offsets) cudaFree(device_offsets);
    if (device_rejected) cudaFree(device_rejected);
    if (!ok) {
        return false;
    }
    *rejected = std::move(host_rejected);
    *stats = local;
    return true;
}

}  // namespace arga_v2::cuda::detail
