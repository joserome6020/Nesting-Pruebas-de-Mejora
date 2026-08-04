#pragma once

#include "arga_nest/engine_facade.hpp"

#include <string>

namespace arga::core {

struct ProfileParams {
    int ga_population = 10;
    int ga_generations = 10;
    double rotation_step_deg = 90.0;
    bool part_in_part = true;
    std::string name = "first";
};

/** first|fast|standard|max — perfiles de ingeniería del core. */
ProfileParams resolve_profile(const std::string& profile_name);

/** Aplica perfil sobre request (no pisa overrides ya seteados si force=false). */
void apply_profile(PackRequest& req, const std::string& profile_name);

}  // namespace arga::core
