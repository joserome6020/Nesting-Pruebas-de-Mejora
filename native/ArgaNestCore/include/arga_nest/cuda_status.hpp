#pragma once

#include <string>

namespace arga::core {

struct CudaStatus {
    bool build_has_cuda = false;
    bool runtime_available = false;
    bool env_requested = false;
    std::string detail;
};

CudaStatus query_cuda_status();

}  // namespace arga::core
