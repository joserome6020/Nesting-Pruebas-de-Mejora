#pragma once

#include "packer.hpp"

#include <cstddef>
#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace arga::core {

struct NfpCacheStats {
    std::size_t hits = 0;
    std::size_t misses = 0;
    std::size_t evictions = 0;
    std::size_t entries = 0;
    std::size_t capacity = 0;
};

/** NFP outer aproximado (Minkowski) con caché L1 thread-safe. */
std::vector<std::vector<Point2D>> compute_nfp_outer_cached(
    const std::vector<std::vector<Point2D>>& rings_a,
    const std::vector<std::vector<Point2D>>& rings_b,
    double angle_a_deg = 0.0,
    double angle_b_deg = 0.0,
    double kerf_mm = 0.0);

NfpCacheStats nfp_cache_stats();
void reset_nfp_cache();
void set_nfp_cache_capacity(std::size_t capacity);

/** Caché L2 en disco (persistente entre procesos). */
void set_nfp_l2_dir(const std::string& dir);
std::string nfp_l2_dir();
NfpCacheStats nfp_l2_stats();

}  // namespace arga::core
