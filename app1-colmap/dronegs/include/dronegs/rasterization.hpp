// SPDX-License-Identifier: MIT
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <vector>

#include "dronegs/types.hpp"

namespace dronegs {

inline constexpr float alpha_minimum_contribution = 1.0F / 255.0F;
inline constexpr float alpha_maximum = 0.99F;
inline constexpr float alpha_minimum_transmittance = 1.0e-4F;
inline constexpr std::uint32_t alpha_tile_width = 16U;
inline constexpr std::uint32_t alpha_tile_height = 16U;

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

struct AlphaRenderGradients {
    std::vector<std::array<float, 3>> dc;
    std::vector<std::array<float, maximum_sh_rest_values>> sh_rest;
    std::vector<float> opacity_logit;
    std::vector<std::array<float, 3>> xyz;
    std::vector<std::array<float, 3>> log_scale;
    std::vector<std::array<float, 4>> rotation;
};

struct AlphaRenderBackwardOutput {
    AlphaRenderOutput render;
    AlphaRenderGradients gradients;
};

struct ProjectedAlphaSplat {
    std::size_t source_index = 0;
    float depth = 0.0F;
    float x = 0.0F;
    float y = 0.0F;
    float radius_x = 0.0F;
    float radius_y = 0.0F;
    float conic_xx = 0.0F;
    float conic_xy = 0.0F;
    float conic_yy = 0.0F;
    float opacity = 0.0F;
    std::array<float, 3> color{};
    std::array<float, 16> sh_basis{};
};

std::vector<ProjectedAlphaSplat> project_alpha_splats(
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera,
    std::uint32_t active_sh_degree = 0U);

AlphaRenderOutput render_alpha_reference(
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera,
    const std::array<float, 3>& background = {0.0F, 0.0F, 0.0F},
    std::uint32_t active_sh_degree = 0U);

AlphaRenderOutput render_alpha_tiled_cuda(
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera,
    const std::array<float, 3>& background = {0.0F, 0.0F, 0.0F},
    std::uint32_t active_sh_degree = 0U);

AlphaRenderBackwardOutput render_alpha_reference_backward(
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera,
    const std::vector<float>& image_gradient,
    const std::array<float, 3>& background = {0.0F, 0.0F, 0.0F},
    std::uint32_t active_sh_degree = 0U);

AlphaRenderBackwardOutput render_alpha_tiled_cuda_backward(
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera,
    const std::vector<float>& image_gradient,
    const std::array<float, 3>& background = {0.0F, 0.0F, 0.0F},
    std::uint32_t active_sh_degree = 0U);

}  // namespace dronegs
