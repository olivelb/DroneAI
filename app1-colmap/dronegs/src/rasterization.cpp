// SPDX-License-Identifier: MIT
#include "dronegs/rasterization.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <utility>
#include <vector>

namespace dronegs {
namespace {

constexpr float sh_c0 = 0.28209479177387814F;
constexpr float minimum_depth = 1.0e-4F;

struct AlphaPixelContribution {
    std::size_t projected_index = 0U;
    float transmittance_before = 1.0F;
    float gaussian_weight = 0.0F;
    float alpha = 0.0F;
    bool alpha_is_unclamped = false;
};

float sigmoid(float value) {
    return 1.0F / (1.0F + std::exp(-value));
}

std::vector<ProjectedAlphaSplat> project_visible(
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera) {
    std::vector<ProjectedAlphaSplat> projected;
    projected.reserve(gaussians.size());
    const float focal = 0.5F * (camera.fx + camera.fy);
    for (std::size_t index = 0; index < gaussians.size(); ++index) {
        const auto& gaussian = gaussians[index];
        const float camera_x =
            camera.rotation[0] * gaussian.xyz[0] +
            camera.rotation[1] * gaussian.xyz[1] +
            camera.rotation[2] * gaussian.xyz[2] + camera.translation[0];
        const float camera_y =
            camera.rotation[3] * gaussian.xyz[0] +
            camera.rotation[4] * gaussian.xyz[1] +
            camera.rotation[5] * gaussian.xyz[2] + camera.translation[1];
        const float camera_z =
            camera.rotation[6] * gaussian.xyz[0] +
            camera.rotation[7] * gaussian.xyz[1] +
            camera.rotation[8] * gaussian.xyz[2] + camera.translation[2];
        if (camera_z <= minimum_depth) {
            continue;
        }
        const float x = camera.fx * camera_x / camera_z + camera.cx;
        const float y = camera.fy * camera_y / camera_z + camera.cy;
        const float world_sigma = std::exp(
            (gaussian.log_scale[0] + gaussian.log_scale[1] +
             gaussian.log_scale[2]) / 3.0F);
        const float sigma = std::clamp(
            world_sigma * focal / camera_z, 0.75F, 8.0F);
        const float support = 2.5F * sigma;
        if (x + support < 0.0F || y + support < 0.0F ||
            x - support >= static_cast<float>(camera.width) ||
            y - support >= static_cast<float>(camera.height)) {
            continue;
        }
        projected.push_back({
            .source_index = index,
            .depth = camera_z,
            .x = x,
            .y = y,
            .sigma = sigma,
            .opacity = sigmoid(gaussian.opacity_logit),
            .color = {
                std::clamp(0.5F + sh_c0 * gaussian.dc[0], 0.0F, 1.0F),
                std::clamp(0.5F + sh_c0 * gaussian.dc[1], 0.0F, 1.0F),
                std::clamp(0.5F + sh_c0 * gaussian.dc[2], 0.0F, 1.0F),
            },
        });
    }
    std::stable_sort(
        projected.begin(), projected.end(),
        [](const ProjectedAlphaSplat& left, const ProjectedAlphaSplat& right) {
            if (left.depth != right.depth) {
                return left.depth < right.depth;
            }
            return left.source_index < right.source_index;
        });
    return projected;
}

void validate_camera(const RasterCamera& camera) {
    if (camera.width == 0U || camera.height == 0U ||
        !std::isfinite(camera.fx) || !std::isfinite(camera.fy) ||
        camera.fx <= 0.0F || camera.fy <= 0.0F) {
        throw std::invalid_argument("alpha reference requires a valid pinhole camera");
    }
    const auto pixels =
        static_cast<std::uint64_t>(camera.width) * camera.height;
    if (pixels > std::numeric_limits<std::size_t>::max() / 3U) {
        throw std::invalid_argument("alpha reference image dimensions are too large");
    }
}

}  // namespace

std::vector<ProjectedAlphaSplat> project_alpha_splats(
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera) {
    validate_camera(camera);
    return project_visible(gaussians, camera);
}

AlphaRenderOutput render_alpha_reference(
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera,
    const std::array<float, 3>& background) {
    validate_camera(camera);
    for (const float channel : background) {
        if (!std::isfinite(channel) || channel < 0.0F || channel > 1.0F) {
            throw std::invalid_argument("alpha reference background is invalid");
        }
    }
    const auto pixel_count =
        static_cast<std::size_t>(camera.width) * camera.height;
    AlphaRenderOutput output{
        .width = camera.width,
        .height = camera.height,
        .rgb = std::vector<float>(pixel_count * 3U, 0.0F),
        .transmittance = std::vector<float>(pixel_count, 1.0F),
        .stats = {},
    };
    const auto projected = project_visible(gaussians, camera);
    output.stats.visible_splats = projected.size();
    for (const auto& splat : projected) {
        const int support = static_cast<int>(std::ceil(2.5F * splat.sigma));
        const int minimum_x = std::max(
            0, static_cast<int>(std::floor(splat.x)) - support);
        const int maximum_x = std::min(
            static_cast<int>(camera.width) - 1,
            static_cast<int>(std::floor(splat.x)) + support);
        const int minimum_y = std::max(
            0, static_cast<int>(std::floor(splat.y)) - support);
        const int maximum_y = std::min(
            static_cast<int>(camera.height) - 1,
            static_cast<int>(std::floor(splat.y)) + support);
        const float inverse_two_variance =
            0.5F / (splat.sigma * splat.sigma);
        for (int y = minimum_y; y <= maximum_y; ++y) {
            for (int x = minimum_x; x <= maximum_x; ++x) {
                const auto pixel =
                    static_cast<std::size_t>(y) * camera.width +
                    static_cast<std::size_t>(x);
                if (output.transmittance[pixel] <=
                    alpha_minimum_transmittance) {
                    continue;
                }
                ++output.stats.evaluated_pairs;
                const float delta_x =
                    (static_cast<float>(x) + 0.5F) - splat.x;
                const float delta_y =
                    (static_cast<float>(y) + 0.5F) - splat.y;
                const float gaussian_weight = std::exp(
                    -(delta_x * delta_x + delta_y * delta_y) *
                    inverse_two_variance);
                const float alpha = std::min(
                    alpha_maximum, splat.opacity * gaussian_weight);
                if (alpha < alpha_minimum_contribution) {
                    continue;
                }
                ++output.stats.contributing_pairs;
                const float contribution =
                    output.transmittance[pixel] * alpha;
                for (std::size_t channel = 0; channel < 3U; ++channel) {
                    output.rgb[pixel * 3U + channel] +=
                        contribution * splat.color[channel];
                }
                output.transmittance[pixel] *= 1.0F - alpha;
            }
        }
    }
    for (std::size_t pixel = 0; pixel < pixel_count; ++pixel) {
        for (std::size_t channel = 0; channel < 3U; ++channel) {
            output.rgb[pixel * 3U + channel] +=
                output.transmittance[pixel] * background[channel];
        }
    }
    return output;
}

AlphaRenderBackwardOutput render_alpha_reference_backward(
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera,
    const std::vector<float>& image_gradient,
    const std::array<float, 3>& background) {
    auto render = render_alpha_reference(gaussians, camera, background);
    if (image_gradient.size() != render.rgb.size()) {
        throw std::invalid_argument(
            "alpha backward image gradient shape is invalid");
    }
    AlphaRenderGradients gradients{
        .dc = std::vector<std::array<float, 3>>(gaussians.size()),
        .opacity_logit = std::vector<float>(gaussians.size(), 0.0F),
    };
    const auto projected = project_visible(gaussians, camera);
    std::vector<AlphaPixelContribution> contributions;
    contributions.reserve(projected.size());
    for (std::uint32_t y = 0U; y < camera.height; ++y) {
        for (std::uint32_t x = 0U; x < camera.width; ++x) {
            contributions.clear();
            float remaining = 1.0F;
            for (std::size_t index = 0U; index < projected.size(); ++index) {
                if (remaining <= alpha_minimum_transmittance) {
                    break;
                }
                const auto& splat = projected[index];
                const int support =
                    static_cast<int>(std::ceil(2.5F * splat.sigma));
                const int center_x =
                    static_cast<int>(std::floor(splat.x));
                const int center_y =
                    static_cast<int>(std::floor(splat.y));
                if (static_cast<int>(x) < center_x - support ||
                    static_cast<int>(x) > center_x + support ||
                    static_cast<int>(y) < center_y - support ||
                    static_cast<int>(y) > center_y + support) {
                    continue;
                }
                const float delta_x =
                    (static_cast<float>(x) + 0.5F) - splat.x;
                const float delta_y =
                    (static_cast<float>(y) + 0.5F) - splat.y;
                const float gaussian_weight = std::exp(
                    -(delta_x * delta_x + delta_y * delta_y) *
                    (0.5F / (splat.sigma * splat.sigma)));
                const float raw_alpha = splat.opacity * gaussian_weight;
                const float alpha =
                    std::min(alpha_maximum, raw_alpha);
                if (alpha < alpha_minimum_contribution) {
                    continue;
                }
                contributions.push_back({
                    .projected_index = index,
                    .transmittance_before = remaining,
                    .gaussian_weight = gaussian_weight,
                    .alpha = alpha,
                    .alpha_is_unclamped = raw_alpha < alpha_maximum,
                });
                remaining *= 1.0F - alpha;
            }

            const auto pixel =
                static_cast<std::size_t>(y) * camera.width + x;
            const std::array<float, 3> upstream{
                image_gradient[pixel * 3U],
                image_gradient[pixel * 3U + 1U],
                image_gradient[pixel * 3U + 2U],
            };
            std::array<float, 3> tail = background;
            for (auto iterator = contributions.rbegin();
                 iterator != contributions.rend(); ++iterator) {
                const auto& contribution = *iterator;
                const auto& splat =
                    projected[contribution.projected_index];
                const auto source = splat.source_index;
                float alpha_gradient = 0.0F;
                for (std::size_t channel = 0U; channel < 3U; ++channel) {
                    const float color_gradient =
                        upstream[channel] *
                        contribution.transmittance_before *
                        contribution.alpha;
                    const float unclamped_color =
                        0.5F + sh_c0 * gaussians[source].dc[channel];
                    if (unclamped_color > 0.0F &&
                        unclamped_color < 1.0F) {
                        gradients.dc[source][channel] +=
                            sh_c0 * color_gradient;
                    }
                    alpha_gradient +=
                        upstream[channel] *
                        contribution.transmittance_before *
                        (splat.color[channel] - tail[channel]);
                }
                if (contribution.alpha_is_unclamped) {
                    const float opacity = splat.opacity;
                    gradients.opacity_logit[source] +=
                        alpha_gradient * contribution.gaussian_weight *
                        opacity * (1.0F - opacity);
                }
                for (std::size_t channel = 0U; channel < 3U; ++channel) {
                    tail[channel] =
                        contribution.alpha * splat.color[channel] +
                        (1.0F - contribution.alpha) * tail[channel];
                }
            }
        }
    }
    return {
        .render = std::move(render),
        .gradients = std::move(gradients),
    };
}

}  // namespace dronegs
