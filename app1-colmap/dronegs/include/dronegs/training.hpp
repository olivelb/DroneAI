// SPDX-License-Identifier: MIT
#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <vector>

#include "dronegs/types.hpp"

namespace dronegs {

struct DatasetSplit {
    std::vector<std::size_t> training;
    std::vector<std::size_t> held_out;
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
    lichtfeld_absolute,
    lichtfeld_dc_only,
    lichtfeld_position_only,
    lichtfeld_opacity_only,
    lichtfeld_scale_only,
    lichtfeld_rotation_only,
    lichtfeld_dc_opacity,
    calibrated_dc_005_opacity,
    calibrated_dc_010_opacity,
    calibrated_dc_020_opacity,
};

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
    std::size_t added = 0U;
    std::size_t gaussian_count = 0U;
};

struct TrainingMetrics {
    float initial_loss = 0.0F;
    float final_loss = 0.0F;
    std::uint64_t iterations = 0;
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
    std::uint64_t topology_refinements = 0;
    std::uint64_t gaussians_added = 0;
    double evaluation_seconds = 0.0;
    std::optional<float> initial_held_out_psnr;
    std::optional<float> initial_held_out_ssim;
    std::optional<float> final_held_out_psnr;
    std::optional<float> final_held_out_ssim;
};

DatasetSplit make_dataset_split(
    std::size_t image_count, std::uint32_t test_every);

TrainingMetrics train_fixed_topology(const Options& options, const Scene& scene,
                                     std::vector<Gaussian>& gaussians);

TrainingMetrics train_ordered_mrnf(
    const Options& options, const Scene& scene,
    std::vector<Gaussian>& gaussians);

}  // namespace dronegs
