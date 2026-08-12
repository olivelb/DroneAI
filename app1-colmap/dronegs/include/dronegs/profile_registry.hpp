// SPDX-License-Identifier: MIT
#pragma once

#include <array>
#include <optional>
#include <string_view>

#include "dronegs/training.hpp"

namespace dronegs {

enum class OptimizerProfileStatus {
    validated,
    experimental,
    deprecated,
};

struct OptimizerProfileDefinition {
    std::string_view name;
    MrnfOptimizerProfile value;
    OptimizerProfileStatus status;
    std::string_view version;
    std::string_view validation_scene;
};

inline constexpr std::array<OptimizerProfileDefinition, 27>
    optimizer_profile_registry{{
        {"dronegs-dev16", MrnfOptimizerProfile::dronegs_dev16,
         OptimizerProfileStatus::deprecated, "dev16", "Gajan"},
        {"reference-absolute", MrnfOptimizerProfile::reference_absolute,
         OptimizerProfileStatus::validated, "dev45", "Albagnac"},
        {"reference-absolute-absgrad025",
         MrnfOptimizerProfile::reference_absolute_absgrad025,
         OptimizerProfileStatus::experimental, "candidate-v1", "pending"},
        {"reference-absolute-absgrad050",
         MrnfOptimizerProfile::reference_absolute_absgrad050,
         OptimizerProfileStatus::experimental, "candidate-v1", "pending"},
        {"reference-dc-only", MrnfOptimizerProfile::reference_dc_only,
         OptimizerProfileStatus::experimental, "dev19", "Gajan"},
        {"reference-position-only", MrnfOptimizerProfile::reference_position_only,
         OptimizerProfileStatus::experimental, "dev19", "Gajan"},
        {"reference-opacity-only", MrnfOptimizerProfile::reference_opacity_only,
         OptimizerProfileStatus::experimental, "dev19", "Gajan"},
        {"reference-scale-only", MrnfOptimizerProfile::reference_scale_only,
         OptimizerProfileStatus::experimental, "dev19", "Gajan"},
        {"reference-rotation-only", MrnfOptimizerProfile::reference_rotation_only,
         OptimizerProfileStatus::experimental, "dev19", "Gajan"},
        {"reference-dc-opacity", MrnfOptimizerProfile::reference_dc_opacity,
         OptimizerProfileStatus::experimental, "dev20", "Gajan"},
        {"calibrated-dc-0.005-opacity",
         MrnfOptimizerProfile::calibrated_dc_005_opacity,
         OptimizerProfileStatus::experimental, "dev33", "Gajan"},
        {"calibrated-dc-0.010-opacity",
         MrnfOptimizerProfile::calibrated_dc_010_opacity,
         OptimizerProfileStatus::experimental, "dev33", "Gajan"},
        {"calibrated-dc-0.020-opacity",
         MrnfOptimizerProfile::calibrated_dc_020_opacity,
         OptimizerProfileStatus::experimental, "dev33", "Gajan"},
        {"calibrated-dc-0.010-opacity-0.024",
         MrnfOptimizerProfile::calibrated_dc_010_opacity_024,
         OptimizerProfileStatus::experimental, "dev33", "Gajan"},
        {"calibrated-dc-0.010-opacity-0.048",
         MrnfOptimizerProfile::calibrated_dc_010_opacity_048,
         OptimizerProfileStatus::experimental, "dev33", "Gajan"},
        {"calibrated-dc-0.010-opacity-0.096",
         MrnfOptimizerProfile::calibrated_dc_010_opacity_096,
         OptimizerProfileStatus::experimental, "dev33", "Gajan"},
        {"dev34-opacity096-reference-scale",
         MrnfOptimizerProfile::dev34_opacity096_reference_scale,
         OptimizerProfileStatus::experimental, "dev34", "Gajan"},
        {"dev34-opacity096-reference-rotation",
         MrnfOptimizerProfile::dev34_opacity096_reference_rotation,
         OptimizerProfileStatus::experimental, "dev34", "Gajan"},
        {"dev34-opacity096-reference-scale-rotation",
         MrnfOptimizerProfile::dev34_opacity096_reference_scale_rotation,
         OptimizerProfileStatus::experimental, "dev34", "Gajan"},
        {"dev35-opacity096-reference-scale-staged-rotation004",
         MrnfOptimizerProfile::
             dev35_opacity096_reference_scale_staged_rotation004,
         OptimizerProfileStatus::experimental, "dev35", "Gajan"},
        {"dev35-opacity096-reference-scale-staged-rotation008",
         MrnfOptimizerProfile::
             dev35_opacity096_reference_scale_staged_rotation008,
         OptimizerProfileStatus::experimental, "dev35", "Gajan"},
        {"dev36-staged-rotation008-absgrad025",
         MrnfOptimizerProfile::dev36_staged_rotation008_absgrad025,
         OptimizerProfileStatus::experimental, "dev36", "Gajan"},
        {"dev36-staged-rotation008-absgrad050",
         MrnfOptimizerProfile::dev36_staged_rotation008_absgrad050,
         OptimizerProfileStatus::experimental, "dev36", "Gajan"},
        {"dev37-staged-rotation008-absgrad050-aa005",
         MrnfOptimizerProfile::dev37_staged_rotation008_absgrad050_aa005,
         OptimizerProfileStatus::experimental, "dev37", "Gajan"},
        {"dev37-staged-rotation008-absgrad050-aa015",
         MrnfOptimizerProfile::dev37_staged_rotation008_absgrad050_aa015,
         OptimizerProfileStatus::experimental, "dev37", "Gajan"},
        {"dev37-staged-rotation008-absgrad050-aa030",
         MrnfOptimizerProfile::dev37_staged_rotation008_absgrad050_aa030,
         OptimizerProfileStatus::experimental, "dev37", "Gajan"},
        {"dev38-staged-rotation008-absgrad050-fastgs",
         MrnfOptimizerProfile::dev38_staged_rotation008_absgrad050_fastgs,
         OptimizerProfileStatus::experimental, "dev38", "Albagnac"},
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
