// SPDX-License-Identifier: MIT
#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

#include "dronegs/types.hpp"

namespace dronegs {

enum class InitialScalePolicy {
    local_knn,
    projected_knn,
};

struct InitialScaleConfiguration {
    InitialScalePolicy policy = InitialScalePolicy::local_knn;
    float maximum_projected_sigma_pixels = 2.0F;
    std::uint32_t resize_factor = 1U;
    std::uint32_t maximum_image_width = 4096U;
    std::uint32_t tile_mode = 1U;
    bool adaptive_native_crop_tiles = false;
};

struct InitialScaleStatistics {
    std::size_t gaussian_count = 0U;
    std::size_t projection_supported_count = 0U;
    std::size_t projected_scale_clamped_count = 0U;
    float projected_sigma_before_p50 = 0.0F;
    float projected_sigma_before_p95 = 0.0F;
    float projected_sigma_before_maximum = 0.0F;
    float projected_sigma_after_p50 = 0.0F;
    float projected_sigma_after_p95 = 0.0F;
    float projected_sigma_after_maximum = 0.0F;
};

struct GaussianInitialization {
    std::vector<Gaussian> gaussians;
    InitialScaleStatistics statistics;
};

std::vector<Gaussian> initialize_fixed_topology(const Scene& scene);
GaussianInitialization initialize_fixed_topology(
    const Scene& scene,
    const InitialScaleConfiguration& configuration);

}  // namespace dronegs
