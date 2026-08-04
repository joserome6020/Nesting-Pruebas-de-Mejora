#include "arga_nest/tabu.hpp"

namespace arga::core {

TabuMemory::TabuMemory(std::size_t capacity) : capacity_(std::max<std::size_t>(8, capacity)) {}

bool TabuMemory::is_tabu(std::uint64_t key) const {
    std::lock_guard<std::mutex> lock(mu_);
    return set_.count(key) > 0;
}

void TabuMemory::remember(std::uint64_t key) {
    std::lock_guard<std::mutex> lock(mu_);
    if (set_.count(key)) {
        return;
    }
    order_.push_back(key);
    set_.insert(key);
    while (order_.size() > capacity_) {
        set_.erase(order_.front());
        order_.erase(order_.begin());
    }
}

void TabuMemory::clear() {
    std::lock_guard<std::mutex> lock(mu_);
    order_.clear();
    set_.clear();
}

std::size_t TabuMemory::size() const {
    std::lock_guard<std::mutex> lock(mu_);
    return set_.size();
}

TabuMemory& global_tabu() {
    static TabuMemory t(96);
    return t;
}

std::uint64_t hash_pack_fingerprint(
    const std::string& engine,
    double plate_w,
    double plate_h,
    double kerf,
    std::size_t n_pieces,
    std::uint32_t seed) {
    std::uint64_t h = 1469598103934665603ull;
    auto mix = [&](std::uint64_t v) {
        h ^= v;
        h *= 1099511628211ull;
    };
    for (char c : engine) {
        mix(static_cast<std::uint64_t>(c));
    }
    mix(static_cast<std::uint64_t>(plate_w * 1000.0));
    mix(static_cast<std::uint64_t>(plate_h * 1000.0));
    mix(static_cast<std::uint64_t>(kerf * 10000.0));
    mix(static_cast<std::uint64_t>(n_pieces));
    mix(seed);
    return h;
}

}  // namespace arga::core
