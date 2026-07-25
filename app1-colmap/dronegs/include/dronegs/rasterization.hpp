// SPDX-License-Identifier: MIT
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <vector>

#include "dronegs/types.hpp"

namespace dronegs {

struct RasterCamera {
    std::array<float, 9> rotation{
        1.0F, 0.0F, 0.0F,
        0.0F, 1.0F, 0.0F,
        0.0F, 0.0F, 1.0F,
    };
    std::array<float, 3> translation{};
    float fx = 0.0F;
    float fy = 0.0F;
    float cx = 0.0F;
    float cy = 0.0F;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
};

struct AlphaRenderStats {
    std::size_t visible_splats = 0;
    std::uint64_t evaluated_pairs = 0;
    std::uint64_t contributing_pairs = 0;
};

struct AlphaRenderOutput {
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::vector<float> rgb;
    std::vector<float> transmittance;
    AlphaRenderStats stats;
};

AlphaRenderOutput render_alpha_reference(
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera,
    const std::array<float, 3>& background = {0.0F, 0.0F, 0.0F});

}  // namespace dronegs
