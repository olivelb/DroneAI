// SPDX-License-Identifier: MIT
#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string_view>
#include <vector>

#include "dronegs/types.hpp"

namespace dronegs {

struct DatasetSplit {
    std::vector<std::size_t> training;
    std::vector<std::size_t> held_out;
    std::vector<std::size_t> ignored;
};

struct ImageQualityMetrics {
    float psnr = 0.0F;
    float ssim = 0.0F;
    float active_pixel_fraction = 0.0F;
};

struct ImageObjectiveOutput {
    float loss = 0.0F;
    std::vector<float> prediction;
    std::vector<float> gradient;
    std::vector<float> transmittance;
};

struct MrnfLearningRates {
    float position = 0.0F;
    float dc = 0.0F;
    float opacity = 0.0F;
    float scale = 0.0F;
    float rotation = 0.0F;
    float position_epsilon = 0.0F;
    float dc_epsilon = 0.0F;
    float opacity_epsilon = 0.0F;
    float scale_epsilon = 0.0F;
    float rotation_epsilon = 0.0F;
};

enum class MrnfOptimizerProfile {
    dronegs_dev16,
    reference_absolute,
    reference_absolute_absgrad025,
    reference_absolute_absgrad050,
    reference_dc_only,
    reference_position_only,
    reference_opacity_only,
    reference_scale_only,
    reference_rotation_only,
    reference_dc_opacity,
    calibrated_dc_005_opacity,
    calibrated_dc_010_opacity,
    calibrated_dc_020_opacity,
    calibrated_dc_010_opacity_024,
    calibrated_dc_010_opacity_048,
    calibrated_dc_010_opacity_096,
    dev34_opacity096_reference_scale,
    dev34_opacity096_reference_rotation,
    dev34_opacity096_reference_scale_rotation,
    dev35_opacity096_reference_scale_staged_rotation004,
    dev35_opacity096_reference_scale_staged_rotation008,
    dev36_staged_rotation008_absgrad025,
    dev36_staged_rotation008_absgrad050,
    dev37_staged_rotation008_absgrad050_aa005,
    dev37_staged_rotation008_absgrad050_aa015,
    dev37_staged_rotation008_absgrad050_aa030,
    dev38_staged_rotation008_absgrad050_fastgs,
};

inline constexpr bool uses_reference_absolute_optimizer(
    MrnfOptimizerProfile profile) {
    return profile == MrnfOptimizerProfile::reference_absolute ||
        profile ==
            MrnfOptimizerProfile::reference_absolute_absgrad025 ||
        profile ==
            MrnfOptimizerProfile::reference_absolute_absgrad050;
}

inline constexpr float mrnf_absgrad_score_weight(
    MrnfOptimizerProfile profile) {
    if (profile ==
            MrnfOptimizerProfile::reference_absolute_absgrad025 ||
        profile ==
            MrnfOptimizerProfile::dev36_staged_rotation008_absgrad025) {
        return 0.25F;
    }
    if (profile ==
            MrnfOptimizerProfile::reference_absolute_absgrad050 ||
        profile ==
            MrnfOptimizerProfile::dev36_staged_rotation008_absgrad050 ||
        profile ==
            MrnfOptimizerProfile::dev37_staged_rotation008_absgrad050_aa005 ||
        profile ==
            MrnfOptimizerProfile::dev37_staged_rotation008_absgrad050_aa015 ||
        profile ==
            MrnfOptimizerProfile::dev37_staged_rotation008_absgrad050_aa030 ||
        profile ==
            MrnfOptimizerProfile::dev38_staged_rotation008_absgrad050_fastgs) {
        return 0.50F;
    }
    return 0.0F;
}

struct MrnfParameterTelemetry {
    float gradient_rms = 0.0F;
    float update_rms = 0.0F;
    float parameter_rms = 0.0F;
    std::uint64_t samples = 0U;
};

struct MrnfOptimizerTelemetry {
    std::uint64_t step = 0U;
    MrnfParameterTelemetry dc;
    MrnfParameterTelemetry opacity;
    MrnfParameterTelemetry position;
    MrnfParameterTelemetry scale;
    MrnfParameterTelemetry rotation;
};

struct TopologyRefinementResult {
    std::size_t candidates = 0U;
    std::size_t pruned = 0U;
    std::size_t pruned_non_finite = 0U;
    std::size_t pruned_opacity = 0U;
    std::size_t pruned_scale_small = 0U;
    std::size_t pruned_scale_large = 0U;
    std::size_t pruned_spatial = 0U;
    std::size_t added = 0U;
    std::size_t reused = 0U;
    std::size_t appended = 0U;
    std::size_t gaussian_count = 0U;
    bool in_place_recycled = false;
};

struct TrainingMetrics {
    float initial_loss = 0.0F;
    float final_loss = 0.0F;
    std::uint64_t iterations = 0;
    std::uint64_t completed_iterations = 0;
    bool completed = true;
    double image_loading_seconds = 0.0;
    double image_decode_seconds = 0.0;
    double setup_seconds = 0.0;
    double training_seconds = 0.0;
    std::uint64_t image_cache_hits = 0;
    std::uint64_t image_cache_misses = 0;
    std::uint64_t image_cache_evictions = 0;
    std::uint64_t image_cache_capacity_bytes = 0;
    std::uint64_t peak_image_cache_bytes = 0;
    std::uint64_t image_prefetch_started = 0;
    std::uint64_t image_prefetch_consumed = 0;
    std::uint64_t image_prefetch_ready = 0;
    std::uint64_t training_image_count = 0;
    std::uint64_t held_out_image_count = 0;
    std::uint64_t ignored_image_count = 0;
    std::uint64_t topology_refinements = 0;
    std::uint64_t gaussians_added = 0;
    std::uint64_t gaussians_pruned = 0;
    std::uint64_t gaussian_slots_reused = 0;
    std::uint64_t topology_compactions = 0;
    std::uint32_t final_active_sh_degree = 0U;
    double evaluation_seconds = 0.0;
    std::optional<float> initial_held_out_psnr;
    std::optional<float> initial_held_out_ssim;
    std::optional<float> final_held_out_psnr;
    std::optional<float> final_held_out_ssim;
};

DatasetSplit make_dataset_split(
    std::size_t image_count, std::uint32_t test_every);
DatasetSplit make_dataset_split(
    const Scene& scene, std::uint32_t test_every,
    std::string_view test_split, std::uint32_t test_guard_percent);

TrainingMetrics train_fixed_topology(const Options& options, const Scene& scene,
                                     std::vector<Gaussian>& gaussians);

TrainingMetrics train_ordered_mrnf(
    const Options& options, const Scene& scene,
    std::vector<Gaussian>& gaussians);

}  // namespace dronegs
