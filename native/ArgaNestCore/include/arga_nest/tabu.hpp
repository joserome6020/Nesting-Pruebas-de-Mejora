#pragma once

#include <cstddef>
#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_set>
#include <vector>

namespace arga::core {

struct TabuStats {
    std::size_t trials = 0;
    std::size_t tabu_hits = 0;
    std::size_t accepted_seed = 0;
};

/**
 * Memoria tabú de semillas/órdenes recientes (hash) para diversificar GA.
 * Thread-safe por proceso.
 */
class TabuMemory {
public:
    explicit TabuMemory(std::size_t capacity = 64);

    bool is_tabu(std::uint64_t key) const;
    void remember(std::uint64_t key);
    void clear();
    std::size_t size() const;

private:
    mutable std::mutex mu_;
    std::size_t capacity_;
    std::vector<std::uint64_t> order_;
    std::unordered_set<std::uint64_t> set_;
};

TabuMemory& global_tabu();

std::uint64_t hash_pack_fingerprint(
    const std::string& engine,
    double plate_w,
    double plate_h,
    double kerf,
    std::size_t n_pieces,
    std::uint32_t seed);

}  // namespace arga::core
