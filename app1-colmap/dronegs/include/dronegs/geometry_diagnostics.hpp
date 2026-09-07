// SPDX-License-Identifier: MIT
#pragma once
#include "dronegs/rasterization.hpp"

namespace dronegs {
// Interleaved float32 channels. Depth is camera Z, not distance along a ray.
inline constexpr std::size_t geometry_channel_count = 12U;
struct GeometryRenderOptions {
    bool fastgs = true;
    bool opacity_sh = false;
    std::uint32_t sh_degree = 3U;
};
struct GeometryRenderOutput {
    AlphaRenderOutput appearance;
    // coverage, center_z, center_std_z, covariance_sigma_z, minimum_axis_sigma,
    // normal_x/y/z (camera frame), normal_support_coherence,
    // plane_z, plane_std_z, plane_support. Unsupported values are zero;
    // coverage/plane_support MUST accompany any interpretation of depth.
    // Plane depth is an experimental tangent-plane proxy, not a surface oracle.
    std::vector<float> channels;
};
GeometryRenderOutput render_geometry_tiled_cuda(
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera,
    const GeometryRenderOptions& options = {});
}  // namespace dronegs
