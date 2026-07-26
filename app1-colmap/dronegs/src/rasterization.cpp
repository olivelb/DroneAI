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
constexpr float sh_c1 = 0.4886025119029199F;
constexpr std::array<float, 5> sh_c2{
    1.0925484305920792F, -1.0925484305920792F,
    0.31539156525252005F, -1.0925484305920792F,
    0.5462742152960396F};
constexpr std::array<float, 7> sh_c3{
    -0.5900435899266435F, 2.890611442640554F,
    -0.4570457994644658F, 0.3731763325901154F,
    -0.4570457994644658F, 1.445305721320277F,
    -0.5900435899266435F};
constexpr float minimum_depth = 1.0e-4F;
constexpr float minimum_projected_variance = 0.75F * 0.75F;
constexpr float maximum_projected_variance = 8.0F * 8.0F;
constexpr float gaussian_support = 2.5F;

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

std::array<float, 3> camera_center(const RasterCamera& camera) {
    return {
        -(camera.rotation[0] * camera.translation[0] +
          camera.rotation[3] * camera.translation[1] +
          camera.rotation[6] * camera.translation[2]),
        -(camera.rotation[1] * camera.translation[0] +
          camera.rotation[4] * camera.translation[1] +
          camera.rotation[7] * camera.translation[2]),
        -(camera.rotation[2] * camera.translation[0] +
          camera.rotation[5] * camera.translation[1] +
          camera.rotation[8] * camera.translation[2]),
    };
}

std::array<float, 16> evaluate_sh_basis(
    const Gaussian& gaussian, const RasterCamera& camera,
    std::uint32_t active_degree) {
    std::array<float, 16> basis{};
    basis[0] = sh_c0;
    if (active_degree == 0U) {
        return basis;
    }
    const auto center = camera_center(camera);
    float x = gaussian.xyz[0] - center[0];
    float y = gaussian.xyz[1] - center[1];
    float z = gaussian.xyz[2] - center[2];
    const float inverse_norm = 1.0F / std::sqrt(std::max(
        1.0e-20F, x * x + y * y + z * z));
    x *= inverse_norm;
    y *= inverse_norm;
    z *= inverse_norm;
    basis[1] = -sh_c1 * y;
    basis[2] = sh_c1 * z;
    basis[3] = -sh_c1 * x;
    if (active_degree == 1U) {
        return basis;
    }
    const float xx = x * x;
    const float yy = y * y;
    const float zz = z * z;
    basis[4] = sh_c2[0] * x * y;
    basis[5] = sh_c2[1] * y * z;
    basis[6] = sh_c2[2] * (2.0F * zz - xx - yy);
    basis[7] = sh_c2[3] * x * z;
    basis[8] = sh_c2[4] * (xx - yy);
    if (active_degree == 2U) {
        return basis;
    }
    basis[9] = sh_c3[0] * y * (3.0F * xx - yy);
    basis[10] = sh_c3[1] * x * y * z;
    basis[11] = sh_c3[2] * y * (4.0F * zz - xx - yy);
    basis[12] = sh_c3[3] * z * (2.0F * zz - 3.0F * xx - 3.0F * yy);
    basis[13] = sh_c3[4] * x * (4.0F * zz - xx - yy);
    basis[14] = sh_c3[5] * z * (xx - yy);
    basis[15] = sh_c3[6] * x * (xx - 3.0F * yy);
    return basis;
}

struct ProjectedCovariance {
    float radius_x = 0.0F;
    float radius_y = 0.0F;
    float conic_xx = 0.0F;
    float conic_xy = 0.0F;
    float conic_yy = 0.0F;
};

bool project_covariance(
    const Gaussian& gaussian, const RasterCamera& camera,
    float camera_x, float camera_y, float camera_z,
    ProjectedCovariance& projected) {
    const float quaternion_norm = std::sqrt(
        gaussian.rotation[0] * gaussian.rotation[0] +
        gaussian.rotation[1] * gaussian.rotation[1] +
        gaussian.rotation[2] * gaussian.rotation[2] +
        gaussian.rotation[3] * gaussian.rotation[3]);
    if (!std::isfinite(quaternion_norm) ||
        quaternion_norm <= 1.0e-12F) {
        return false;
    }
    const float w = gaussian.rotation[0] / quaternion_norm;
    const float x = gaussian.rotation[1] / quaternion_norm;
    const float y = gaussian.rotation[2] / quaternion_norm;
    const float z = gaussian.rotation[3] / quaternion_norm;
    const std::array<float, 9> gaussian_rotation{
        1.0F - 2.0F * (y * y + z * z),
        2.0F * (x * y - z * w),
        2.0F * (x * z + y * w),
        2.0F * (x * y + z * w),
        1.0F - 2.0F * (x * x + z * z),
        2.0F * (y * z - x * w),
        2.0F * (x * z - y * w),
        2.0F * (y * z + x * w),
        1.0F - 2.0F * (x * x + y * y),
    };
    std::array<float, 9> camera_gaussian_rotation{};
    for (std::size_t row = 0U; row < 3U; ++row) {
        for (std::size_t column = 0U; column < 3U; ++column) {
            for (std::size_t inner = 0U; inner < 3U; ++inner) {
                camera_gaussian_rotation[row * 3U + column] +=
                    camera.rotation[row * 3U + inner] *
                    gaussian_rotation[inner * 3U + column];
            }
        }
    }
    std::array<float, 3> scale{};
    for (std::size_t axis = 0U; axis < 3U; ++axis) {
        scale[axis] = std::exp(gaussian.log_scale[axis]);
        if (!std::isfinite(scale[axis]) || scale[axis] <= 0.0F) {
            return false;
        }
    }
    const float inverse_depth = 1.0F / camera_z;
    const float inverse_depth_squared =
        inverse_depth * inverse_depth;
    const float jacobian_xx = camera.fx * inverse_depth;
    const float jacobian_xz =
        -camera.fx * camera_x * inverse_depth_squared;
    const float jacobian_yy = camera.fy * inverse_depth;
    const float jacobian_yz =
        -camera.fy * camera_y * inverse_depth_squared;
    std::array<float, 3> projected_row_x{};
    std::array<float, 3> projected_row_y{};
    for (std::size_t column = 0U; column < 3U; ++column) {
        projected_row_x[column] =
            (jacobian_xx * camera_gaussian_rotation[column] +
             jacobian_xz *
                 camera_gaussian_rotation[2U * 3U + column]) *
            scale[column];
        projected_row_y[column] =
            (jacobian_yy *
                 camera_gaussian_rotation[1U * 3U + column] +
             jacobian_yz *
                 camera_gaussian_rotation[2U * 3U + column]) *
            scale[column];
    }
    float covariance_xx = 0.0F;
    float covariance_xy = 0.0F;
    float covariance_yy = 0.0F;
    for (std::size_t column = 0U; column < 3U; ++column) {
        covariance_xx +=
            projected_row_x[column] * projected_row_x[column];
        covariance_xy +=
            projected_row_x[column] * projected_row_y[column];
        covariance_yy +=
            projected_row_y[column] * projected_row_y[column];
    }
    if (!std::isfinite(covariance_xx) ||
        !std::isfinite(covariance_xy) ||
        !std::isfinite(covariance_yy)) {
        return false;
    }
    const float trace = covariance_xx + covariance_yy;
    const float difference = covariance_xx - covariance_yy;
    const float spectral_gap = std::sqrt(std::max(
        0.0F,
        difference * difference +
            4.0F * covariance_xy * covariance_xy));
    const float eigenvalue_maximum =
        0.5F * (trace + spectral_gap);
    const float eigenvalue_minimum =
        0.5F * (trace - spectral_gap);
    const float clamped_maximum = std::clamp(
        eigenvalue_maximum, minimum_projected_variance,
        maximum_projected_variance);
    const float clamped_minimum = std::clamp(
        eigenvalue_minimum, minimum_projected_variance,
        maximum_projected_variance);
    if (spectral_gap > 1.0e-8F) {
        const float projector_xx =
            (covariance_xx - eigenvalue_minimum) / spectral_gap;
        const float projector_xy =
            covariance_xy / spectral_gap;
        const float projector_yy =
            (covariance_yy - eigenvalue_minimum) / spectral_gap;
        const float clamped_gap =
            clamped_maximum - clamped_minimum;
        covariance_xx =
            clamped_minimum + clamped_gap * projector_xx;
        covariance_xy = clamped_gap * projector_xy;
        covariance_yy =
            clamped_minimum + clamped_gap * projector_yy;
    } else {
        const float variance = std::clamp(
            0.5F * trace, minimum_projected_variance,
            maximum_projected_variance);
        covariance_xx = variance;
        covariance_xy = 0.0F;
        covariance_yy = variance;
    }
    const float determinant =
        covariance_xx * covariance_yy -
        covariance_xy * covariance_xy;
    if (!std::isfinite(determinant) || determinant <= 1.0e-12F) {
        return false;
    }
    projected.radius_x =
        gaussian_support * std::sqrt(covariance_xx);
    projected.radius_y =
        gaussian_support * std::sqrt(covariance_yy);
    projected.conic_xx = covariance_yy / determinant;
    projected.conic_xy = -covariance_xy / determinant;
    projected.conic_yy = covariance_xx / determinant;
    return std::isfinite(projected.radius_x) &&
           std::isfinite(projected.radius_y) &&
           std::isfinite(projected.conic_xx) &&
           std::isfinite(projected.conic_xy) &&
           std::isfinite(projected.conic_yy);
}

float gaussian_weight(
    const ProjectedAlphaSplat& splat, float delta_x, float delta_y) {
    const float squared_distance = std::max(
        0.0F,
        splat.conic_xx * delta_x * delta_x +
            2.0F * splat.conic_xy * delta_x * delta_y +
            splat.conic_yy * delta_y * delta_y);
    return std::exp(-0.5F * squared_distance);
}

std::vector<ProjectedAlphaSplat> project_visible(
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera,
    std::uint32_t active_sh_degree) {
    std::vector<ProjectedAlphaSplat> projected;
    projected.reserve(gaussians.size());
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
        ProjectedCovariance covariance{};
        if (!std::isfinite(x) || !std::isfinite(y) ||
            !project_covariance(
                gaussian, camera, camera_x, camera_y, camera_z,
                covariance) ||
            x + covariance.radius_x < 0.0F ||
            y + covariance.radius_y < 0.0F ||
            x - covariance.radius_x >=
                static_cast<float>(camera.width) ||
            y - covariance.radius_y >=
                static_cast<float>(camera.height)) {
            continue;
        }
        const auto sh_basis =
            evaluate_sh_basis(gaussian, camera, active_sh_degree);
        std::array<float, 3> color{};
        const auto active_coefficients =
            (active_sh_degree + 1U) * (active_sh_degree + 1U) - 1U;
        for (std::size_t channel = 0U; channel < 3U; ++channel) {
            float value = 0.5F + sh_basis[0] * gaussian.dc[channel];
            for (std::size_t coefficient = 0U;
                 coefficient < active_coefficients; ++coefficient) {
                value += sh_basis[coefficient + 1U] *
                    gaussian.sh_rest[
                        channel * maximum_sh_rest_coefficients +
                        coefficient];
            }
            color[channel] = std::clamp(value, 0.0F, 1.0F);
        }
        projected.push_back({
            .source_index = index,
            .depth = camera_z,
            .x = x,
            .y = y,
            .radius_x = covariance.radius_x,
            .radius_y = covariance.radius_y,
            .conic_xx = covariance.conic_xx,
            .conic_xy = covariance.conic_xy,
            .conic_yy = covariance.conic_yy,
            .opacity = sigmoid(gaussian.opacity_logit),
            .color = color,
            .sh_basis = sh_basis,
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
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera,
    std::uint32_t active_sh_degree) {
    validate_camera(camera);
    if (active_sh_degree > maximum_sh_degree) {
        throw std::invalid_argument("active SH degree must be between 0 and 3");
    }
    return project_visible(gaussians, camera, active_sh_degree);
}

AlphaRenderOutput render_alpha_reference(
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera,
    const std::array<float, 3>& background,
    std::uint32_t active_sh_degree) {
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
    if (active_sh_degree > maximum_sh_degree) {
        throw std::invalid_argument("active SH degree must be between 0 and 3");
    }
    const auto projected =
        project_visible(gaussians, camera, active_sh_degree);
    output.stats.visible_splats = projected.size();
    for (const auto& splat : projected) {
        const int support_x =
            static_cast<int>(std::ceil(splat.radius_x));
        const int support_y =
            static_cast<int>(std::ceil(splat.radius_y));
        const int minimum_x = std::max(
            0, static_cast<int>(std::floor(splat.x)) - support_x);
        const int maximum_x = std::min(
            static_cast<int>(camera.width) - 1,
            static_cast<int>(std::floor(splat.x)) + support_x);
        const int minimum_y = std::max(
            0, static_cast<int>(std::floor(splat.y)) - support_y);
        const int maximum_y = std::min(
            static_cast<int>(camera.height) - 1,
            static_cast<int>(std::floor(splat.y)) + support_y);
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
                const float weight =
                    gaussian_weight(splat, delta_x, delta_y);
                const float alpha = std::min(
                    alpha_maximum, splat.opacity * weight);
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
    const std::array<float, 3>& background,
    std::uint32_t active_sh_degree) {
    auto render = render_alpha_reference(
        gaussians, camera, background, active_sh_degree);
    if (image_gradient.size() != render.rgb.size()) {
        throw std::invalid_argument(
            "alpha backward image gradient shape is invalid");
    }
    AlphaRenderGradients gradients{
        .dc = std::vector<std::array<float, 3>>(gaussians.size()),
        .sh_rest = std::vector<
            std::array<float, maximum_sh_rest_values>>(gaussians.size()),
        .opacity_logit = std::vector<float>(gaussians.size(), 0.0F),
        .xyz = std::vector<std::array<float, 3>>(gaussians.size()),
        .log_scale =
            std::vector<std::array<float, 3>>(gaussians.size()),
        .rotation =
            std::vector<std::array<float, 4>>(gaussians.size()),
    };
    const auto projected =
        project_visible(gaussians, camera, active_sh_degree);
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
                const int support_x =
                    static_cast<int>(std::ceil(splat.radius_x));
                const int support_y =
                    static_cast<int>(std::ceil(splat.radius_y));
                const int center_x =
                    static_cast<int>(std::floor(splat.x));
                const int center_y =
                    static_cast<int>(std::floor(splat.y));
                if (static_cast<int>(x) < center_x - support_x ||
                    static_cast<int>(x) > center_x + support_x ||
                    static_cast<int>(y) < center_y - support_y ||
                    static_cast<int>(y) > center_y + support_y) {
                    continue;
                }
                const float delta_x =
                    (static_cast<float>(x) + 0.5F) - splat.x;
                const float delta_y =
                    (static_cast<float>(y) + 0.5F) - splat.y;
                const float weight =
                    gaussian_weight(splat, delta_x, delta_y);
                const float raw_alpha = splat.opacity * weight;
                const float alpha =
                    std::min(alpha_maximum, raw_alpha);
                if (alpha < alpha_minimum_contribution) {
                    continue;
                }
                contributions.push_back({
                    .projected_index = index,
                    .transmittance_before = remaining,
                    .gaussian_weight = weight,
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
                    float unclamped_color =
                        0.5F + sh_c0 * gaussians[source].dc[channel];
                    const auto active_coefficients =
                        (active_sh_degree + 1U) *
                            (active_sh_degree + 1U) -
                        1U;
                    for (std::size_t coefficient = 0U;
                         coefficient < active_coefficients;
                         ++coefficient) {
                        unclamped_color +=
                            splat.sh_basis[coefficient + 1U] *
                            gaussians[source].sh_rest[
                                channel *
                                    maximum_sh_rest_coefficients +
                                coefficient];
                    }
                    if (unclamped_color > 0.0F &&
                        unclamped_color < 1.0F) {
                        gradients.dc[source][channel] +=
                            sh_c0 * color_gradient;
                        for (std::size_t coefficient = 0U;
                             coefficient < active_coefficients;
                             ++coefficient) {
                            gradients.sh_rest[source][
                                channel *
                                    maximum_sh_rest_coefficients +
                                coefficient] +=
                                splat.sh_basis[coefficient + 1U] *
                                color_gradient;
                        }
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
    const auto finite_difference =
        [&camera, &background, &image_gradient, active_sh_degree](
            const std::vector<Gaussian>& plus,
            const std::vector<Gaussian>& minus, float epsilon) {
            const auto plus_output =
                render_alpha_reference(
                    plus, camera, background, active_sh_degree);
            const auto minus_output =
                render_alpha_reference(
                    minus, camera, background, active_sh_degree);
            double derivative = 0.0;
            for (std::size_t index = 0U;
                 index < plus_output.rgb.size(); ++index) {
                derivative +=
                    static_cast<double>(
                        plus_output.rgb[index] -
                        minus_output.rgb[index]) *
                    static_cast<double>(image_gradient[index]);
            }
            return static_cast<float>(
                derivative / (2.0 * epsilon));
        };
    constexpr float position_epsilon = 1.0e-4F;
    constexpr float parameter_epsilon = 1.0e-3F;
    for (std::size_t gaussian = 0U;
         gaussian < gaussians.size(); ++gaussian) {
        for (std::size_t axis = 0U; axis < 3U; ++axis) {
            auto plus = gaussians;
            auto minus = gaussians;
            plus[gaussian].xyz[axis] += position_epsilon;
            minus[gaussian].xyz[axis] -= position_epsilon;
            gradients.xyz[gaussian][axis] =
                finite_difference(
                    plus, minus, position_epsilon);

            plus = gaussians;
            minus = gaussians;
            plus[gaussian].log_scale[axis] += parameter_epsilon;
            minus[gaussian].log_scale[axis] -= parameter_epsilon;
            gradients.log_scale[gaussian][axis] =
                finite_difference(
                    plus, minus, parameter_epsilon);
        }
        for (std::size_t component = 0U; component < 4U; ++component) {
            auto plus = gaussians;
            auto minus = gaussians;
            plus[gaussian].rotation[component] += parameter_epsilon;
            minus[gaussian].rotation[component] -= parameter_epsilon;
            gradients.rotation[gaussian][component] =
                finite_difference(
                    plus, minus, parameter_epsilon);
        }
    }
    return {
        .render = std::move(render),
        .gradients = std::move(gradients),
    };
}

}  // namespace dronegs
