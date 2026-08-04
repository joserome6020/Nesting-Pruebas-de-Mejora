#include "arga_nest/profiles.hpp"

#include <algorithm>
#include <cctype>

namespace arga::core {
namespace {

std::string lower(std::string s) {
    for (char& c : s) {
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    }
    return s;
}

}  // namespace

ProfileParams resolve_profile(const std::string& profile_name) {
    const std::string p = lower(profile_name);
    ProfileParams out;
    out.name = p.empty() ? "first" : p;
    if (p == "max") {
        out.ga_population = 40;
        out.ga_generations = 40;
        out.rotation_step_deg = 15.0;
        out.part_in_part = true;
    } else if (p == "standard") {
        out.ga_population = 20;
        out.ga_generations = 20;
        out.rotation_step_deg = 30.0;
        out.part_in_part = true;
    } else if (p == "fast") {
        out.ga_population = 10;
        out.ga_generations = 8;
        out.rotation_step_deg = 45.0;
        out.part_in_part = true;
    } else {
        // first (default producto)
        out.name = "first";
        out.ga_population = 8;
        out.ga_generations = 1;
        out.rotation_step_deg = 90.0;
        out.part_in_part = true;
    }
    return out;
}

void apply_profile(PackRequest& req, const std::string& profile_name) {
    if (profile_name.empty()) {
        return;
    }
    const auto p = resolve_profile(profile_name);
    req.ga_population = p.ga_population;
    req.ga_generations = p.ga_generations;
    req.rotation_step_deg = p.rotation_step_deg;
    req.part_in_part = p.part_in_part;
}

}  // namespace arga::core
