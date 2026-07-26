// SPDX-License-Identifier: MIT
#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <vector>

#include "dronegs/rasterization.hpp"
#include "dronegs/training.hpp"

namespace dronegs {

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
        std::size_t target_bytes);
    ImageQualityMetrics evaluate_quality(
        const RasterCamera& camera, const std::uint8_t* target_rgb,
        std::size_t target_bytes,
        std::vector<float>* prediction = nullptr);
    ImageObjectiveOutput evaluate_objective_gradient(
        const RasterCamera& camera, const std::uint8_t* target_rgb,
        std::size_t target_bytes);
    float train_step(
        const RasterCamera& camera, const std::uint8_t* target_rgb,
        std::size_t target_bytes);
    TopologyRefinementResult refine_topology(
        float gradient_threshold = 0.003F,
        float grow_fraction = 0.07F,
        std::uint64_t selection_seed = 0U,
        bool lichtfeld_pruning_bounds = false);
    MrnfLearningRates current_learning_rates() const noexcept;
    std::optional<MrnfOptimizerTelemetry>
    latest_optimizer_telemetry() const noexcept;
    std::size_t size() const noexcept;
    std::uint32_t active_sh_degree() const noexcept;
    void set_active_sh_degree(std::uint32_t degree);
    void download(std::vector<Gaussian>& gaussians) const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace dronegs
