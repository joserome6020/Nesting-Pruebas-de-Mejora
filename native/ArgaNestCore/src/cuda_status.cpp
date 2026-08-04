#include "arga_nest/cuda_status.hpp"

#include "cuda/nest_accel.hpp"

namespace arga::core {

CudaStatus query_cuda_status() {
    CudaStatus s;
#if ARGA_NEST_HAS_CUDA
    s.build_has_cuda = true;
#else
    s.build_has_cuda = false;
#endif
    s.runtime_available = arga::cuda::available();
    s.env_requested = arga::cuda::requested();
    s.detail = arga::cuda::availability_detail();
    return s;
}

}  // namespace arga::core
