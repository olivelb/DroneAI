// SPDX-License-Identifier: MIT
#pragma once

#include <array>
#include <optional>
#include <string_view>

#include "dronegs/training.hpp"

namespace dronegs {

enum class OptimizerProfileStatus {
    validated,
};

struct OptimizerProfileDefinition {
    std::string_view name;
    MrnfOptimizerProfile value;
    OptimizerProfileStatus status;
    std::string_view version;
    std::string_view validation_scene;
};

inline constexpr std::array<OptimizerProfileDefinition, 1>
    optimizer_profile_registry{{
        {"reference-absolute", MrnfOptimizerProfile::reference_absolute,
         OptimizerProfileStatus::validated, "dev45", "Albagnac"},
    }};

inline const OptimizerProfileDefinition* find_optimizer_profile(
    std::string_view name) {
    for (const auto& profile : optimizer_profile_registry) {
        if (profile.name == name) {
            return &profile;
        }
    }
    return nullptr;
}

inline std::optional<MrnfOptimizerProfile> optimizer_profile_from_name(
    std::string_view name) {
    const auto* profile = find_optimizer_profile(name);
    if (profile == nullptr) {
        return std::nullopt;
    }
    return profile->value;
}

}  // namespace dronegs
