// SPDX-License-Identifier: MIT
#pragma once

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string_view>
#include <utility>
#include <vector>

#include "dronegs/types.hpp"

namespace dronegs {

struct DatasetSplit {
    std::vector<std::size_t> training;
    std::vector<std::size_t> held_out;
    std::vector<std::size_t> ignored;
};

struct ImageQualityMetrics {
    float mse = 0.0F;
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
    bool compacted = false;
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
    double topology_refinement_seconds = 0.0;
    double periodic_checkpoint_seconds = 0.0;
    std::uint64_t image_cache_hits = 0;
    std::uint64_t image_cache_misses = 0;
    std::uint64_t image_cache_evictions = 0;
    std::uint64_t image_cache_capacity_bytes = 0;
    std::uint64_t image_cache_working_set_bytes = 0;
    std::uint64_t peak_image_cache_bytes = 0;
    std::uint64_t image_prefetch_started = 0;
    std::uint64_t image_prefetch_consumed = 0;
    std::uint64_t image_prefetch_ready = 0;
    std::uint64_t training_image_count = 0;
    std::uint64_t held_out_image_count = 0;
    std::uint64_t ignored_image_count = 0;
    std::uint64_t frame_descriptor_count = 0;
    std::uint64_t training_frame_count = 0;
    std::uint64_t held_out_frame_count = 0;
    std::uint64_t ignored_frame_count = 0;
    std::uint64_t topology_refinements = 0;
    std::uint64_t gaussians_added = 0;
    std::uint64_t gaussians_pruned = 0;
    std::uint64_t gaussian_slots_reused = 0;
    std::uint64_t topology_compactions = 0;
    std::uint32_t final_active_sh_degree = 0U;
    double evaluation_seconds = 0.0;
    std::optional<float> initial_held_out_psnr;
    std::optional<float> initial_held_out_ssim;
    std::optional<float> initial_pixel_weighted_psnr;
    std::optional<float> initial_pixel_weighted_ssim;
    std::optional<float> final_held_out_psnr;
    std::optional<float> final_held_out_ssim;
    std::optional<float> final_pixel_weighted_psnr;
    std::optional<float> final_pixel_weighted_ssim;
};

inline constexpr std::uint64_t topology_refinement_interval = 200U;

inline std::uint64_t topology_growth_end_iteration(
    std::uint64_t iterations) {
    const auto half_budget = iterations / 2U;
    if (half_budget == 0U) {
        return 0U;
    }
    // Refinement runs on multiples of 200 and must remain strictly inside
    // the first half of the requested budget. This reproduces iteration
    // 14,800 for 30k while scaling to every operator-selected duration.
    return ((half_budget - 1U) / topology_refinement_interval) *
        topology_refinement_interval;
}

inline std::uint64_t topology_refinement_end_iteration(
    std::uint64_t iterations,
    std::uint64_t topology_cooldown,
    bool adaptive_growth_target) {
    const auto configured_end = iterations - topology_cooldown;
    if (!adaptive_growth_target) {
        return configured_end;
    }
    const auto target_end = std::min(
        configured_end, topology_growth_end_iteration(iterations));
    return (target_end / topology_refinement_interval) *
        topology_refinement_interval;
}

inline float adaptive_capacity_growth_fraction(
    std::size_t current_gaussians,
    std::size_t target_gaussians,
    std::uint64_t iteration,
    std::uint64_t growth_end_iteration) {
    constexpr double estimated_pruning_fraction = 0.03;
    constexpr double estimated_candidate_fraction = 0.93;
    constexpr float minimum_growth_fraction = 0.07F;
    // Short preview budgets can start from sparse blocks with only tens of
    // thousands of seeds. A 25% ceiling made their requested capacity
    // mathematically unreachable even though every split remains bounded by
    // target_gaussians. Keep a safety ceiling, but let the closed-form
    // schedule request enough candidates to converge within its own windows.
    constexpr float maximum_growth_fraction = 0.50F;

    if (current_gaussians == 0U) {
        throw std::invalid_argument(
            "adaptive growth requires a non-empty Gaussian model");
    }
    if (iteration > growth_end_iteration) {
        return 0.0F;
    }
    const auto remaining_windows =
        ((growth_end_iteration - iteration) /
         topology_refinement_interval) + 1U;
    if (target_gaussians <= current_gaussians) {
        // The final refinement still prunes before it grows. Request the
        // minimum split budget so capacity freed by that pruning is recycled
        // and the frozen topology finishes at the target instead of below it.
        return remaining_windows == 1U
            ? minimum_growth_fraction
            : 0.0F;
    }

    const double required_net_growth = std::exp(
        std::log(
            static_cast<double>(target_gaussians) /
            static_cast<double>(current_gaussians)) /
        static_cast<double>(remaining_windows)) - 1.0;
    const double requested_fraction =
        (required_net_growth + estimated_pruning_fraction) /
        estimated_candidate_fraction;
    return std::clamp(
        static_cast<float>(requested_fraction),
        minimum_growth_fraction, maximum_growth_fraction);
}

// Return the same floor-index percentile as sorting the complete input, while
// avoiding an O(N log N) sort during every topology refinement. Callers pass
// finite values; keeping this helper exact is important because the result is
// used by scientific pruning gates.
inline float exact_floor_percentile(
    std::vector<float> values, float fraction) {
    if (values.empty()) {
        return 0.0F;
    }
    if (!std::isfinite(fraction) || fraction < 0.0F || fraction > 1.0F) {
        throw std::invalid_argument(
            "percentile fraction must be finite and between zero and one");
    }
    const auto index = static_cast<std::size_t>(std::floor(
        static_cast<float>(values.size() - 1U) * fraction));
    std::nth_element(
        values.begin(), values.begin() + index, values.end());
    return values[index];
}

inline std::pair<float, float> exact_floor_percentile_pair(
    std::vector<float> values,
    float lower_fraction,
    float upper_fraction) {
    if (values.empty()) {
        return {0.0F, 0.0F};
    }
    if (!std::isfinite(lower_fraction) ||
        !std::isfinite(upper_fraction) ||
        lower_fraction < 0.0F || upper_fraction > 1.0F ||
        lower_fraction > upper_fraction) {
        throw std::invalid_argument(
            "percentile fractions must be finite, ordered and between zero and one");
    }
    const auto lower_index = static_cast<std::size_t>(std::floor(
        static_cast<float>(values.size() - 1U) * lower_fraction));
    const auto upper_index = static_cast<std::size_t>(std::floor(
        static_cast<float>(values.size() - 1U) * upper_fraction));
    std::nth_element(
        values.begin(), values.begin() + lower_index, values.end());
    const float lower = values[lower_index];
    if (upper_index == lower_index) {
        return {lower, lower};
    }
    std::nth_element(
        values.begin() + lower_index + 1U,
        values.begin() + upper_index,
        values.end());
    return {lower, values[upper_index]};
}

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
