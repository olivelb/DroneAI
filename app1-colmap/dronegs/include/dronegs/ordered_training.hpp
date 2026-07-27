// SPDX-License-Identifier: MIT
#pragma once

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "dronegs/rasterization.hpp"
#include "dronegs/training.hpp"

namespace dronegs {

struct TrainingCheckpointProgress {
    std::uint64_t completed_iteration = 0U;
    std::uint64_t topology_refinements = 0U;
    std::uint64_t gaussians_added = 0U;
    std::uint64_t gaussians_pruned = 0U;
    std::uint64_t gaussian_slots_reused = 0U;
    std::uint64_t topology_compactions = 0U;
    float initial_loss = 0.0F;
    std::optional<float> initial_held_out_psnr;
    std::optional<float> initial_held_out_ssim;
};

class OrderedAlphaTrainingContext {
public:
    OrderedAlphaTrainingContext(
        const std::vector<Gaussian>& gaussians,
        std::size_t maximum_pixels,
        std::uint64_t maximum_steps,
        std::size_t maximum_gaussians = 0U,
        MrnfOptimizerProfile optimizer_profile =
            MrnfOptimizerProfile::dronegs_dev16,
        std::uint32_t maximum_sh_degree = 0U,
        std::uint32_t sh_degree_interval = 1000U,
        std::uint64_t noise_seed = 0U,
        std::optional<bool> fastgs_compatibility_override =
            std::nullopt);
    ~OrderedAlphaTrainingContext();

    OrderedAlphaTrainingContext(
        const OrderedAlphaTrainingContext&) = delete;
    OrderedAlphaTrainingContext& operator=(
        const OrderedAlphaTrainingContext&) = delete;
    OrderedAlphaTrainingContext(
        OrderedAlphaTrainingContext&&) noexcept;
    OrderedAlphaTrainingContext& operator=(
        OrderedAlphaTrainingContext&&) noexcept;

    float evaluate(
        const RasterCamera& camera, const std::uint8_t* target_rgb,
        std::size_t target_bytes, float mse_blend = 0.0F);
    ImageQualityMetrics evaluate_quality(
        const RasterCamera& camera, const std::uint8_t* target_rgb,
        std::size_t target_bytes,
        std::vector<float>* prediction = nullptr);
    ImageObjectiveOutput evaluate_objective_gradient(
        const RasterCamera& camera, const std::uint8_t* target_rgb,
        std::size_t target_bytes, float mse_blend = 0.0F);
    float train_step(
        const RasterCamera& camera, const std::uint8_t* target_rgb,
        std::size_t target_bytes, float mse_blend = 0.0F);
    TopologyRefinementResult refine_topology(
        float gradient_threshold = 0.003F,
        float grow_fraction = 0.07F,
        std::uint64_t selection_seed = 0U,
        bool spatial_pruning_bounds = false);
    MrnfLearningRates current_learning_rates() const noexcept;
    std::optional<MrnfOptimizerTelemetry>
    latest_optimizer_telemetry() const noexcept;
    std::size_t size() const noexcept;
    std::uint32_t active_sh_degree() const noexcept;
    void set_active_sh_degree(std::uint32_t degree);
    void download(std::vector<Gaussian>& gaussians) const;
    void save_checkpoint(
        const std::filesystem::path& path,
        const TrainingCheckpointProgress& progress,
        const std::string& dataset_fingerprint,
        const std::string& configuration_fingerprint) const;
    TrainingCheckpointProgress load_checkpoint(
        const std::filesystem::path& path,
        const std::string& expected_dataset_fingerprint,
        const std::string& expected_configuration_fingerprint);

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace dronegs
