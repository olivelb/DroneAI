// SPDX-License-Identifier: MIT
#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <vector>

#include "dronegs/rasterization.hpp"
#include "dronegs/training.hpp"

namespace dronegs {

class OrderedAlphaTrainingContext {
public:
    OrderedAlphaTrainingContext(
        const std::vector<Gaussian>& gaussians,
        std::size_t maximum_pixels,
        std::uint64_t maximum_steps);
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
    void download(std::vector<Gaussian>& gaussians) const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace dronegs
