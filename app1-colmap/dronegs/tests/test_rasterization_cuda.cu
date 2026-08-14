// SPDX-License-Identifier: MIT
#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include "dronegs/rasterization.hpp"

namespace {

constexpr float sh_c0 = 0.28209479177387814F;

dronegs::Gaussian splat(
    const std::array<float, 3>& xyz,
    const std::array<float, 3>& color,
    float scale, float opacity_logit) {
    dronegs::Gaussian gaussian;
    gaussian.xyz = xyz;
    for (std::size_t channel = 0; channel < 3U; ++channel) {
        gaussian.dc[channel] = (color[channel] - 0.5F) / sh_c0;
    }
    gaussian.log_scale = {
        std::log(scale), std::log(scale), std::log(scale)};
    gaussian.opacity_logit = opacity_logit;
    return gaussian;
}

dronegs::Gaussian anisotropic_splat(
    const std::array<float, 3>& xyz,
    const std::array<float, 3>& color,
    const std::array<float, 3>& scale,
    const std::array<float, 4>& rotation,
    float opacity_logit) {
    auto gaussian = splat(xyz, color, scale[0], opacity_logit);
    for (std::size_t axis = 0U; axis < 3U; ++axis) {
        gaussian.log_scale[axis] = std::log(scale[axis]);
    }
    gaussian.rotation = rotation;
    return gaussian;
}

dronegs::RasterCamera camera() {
    return {
        .fx = 18.0F,
        .fy = 17.0F,
        .cx = 9.0F,
        .cy = 8.0F,
        .width = 19U,
        .height = 18U,
    };
}

dronegs::RasterCamera multi_tile_camera() {
    return {
        .fx = 42.0F,
        .fy = 40.0F,
        .cx = 25.0F,
        .cy = 18.0F,
        .width = 51U,
        .height = 37U,
    };
}

void compare(
    const dronegs::AlphaRenderOutput& actual,
    const dronegs::AlphaRenderOutput& expected,
    float tolerance) {
    if (actual.width != expected.width || actual.height != expected.height ||
        actual.rgb.size() != expected.rgb.size() ||
        actual.transmittance.size() != expected.transmittance.size()) {
        throw std::runtime_error("tiled alpha output shape mismatch");
    }
    for (std::size_t index = 0; index < actual.rgb.size(); ++index) {
        if (std::abs(actual.rgb[index] - expected.rgb[index]) > tolerance) {
            throw std::runtime_error(
                "tiled alpha RGB differs from CPU reference");
        }
    }
    for (std::size_t index = 0; index < actual.transmittance.size(); ++index) {
        if (std::abs(
                actual.transmittance[index] -
                expected.transmittance[index]) > tolerance) {
            throw std::runtime_error(
                "tiled alpha transmittance differs from CPU reference");
        }
    }
    if (actual.stats.visible_splats != expected.stats.visible_splats ||
        actual.stats.evaluated_pairs > expected.stats.evaluated_pairs ||
        actual.stats.contributing_pairs != expected.stats.contributing_pairs) {
        throw std::runtime_error(
            "tiled alpha contribution statistics differ from CPU reference");
    }
}

void compare_backward(
    const dronegs::AlphaRenderBackwardOutput& actual,
    const dronegs::AlphaRenderBackwardOutput& expected,
    float render_tolerance, float gradient_tolerance,
    bool compare_geometry = true) {
    compare(actual.render, expected.render, render_tolerance);
    if (actual.gradients.dc.size() != expected.gradients.dc.size() ||
        actual.gradients.sh_rest.size() != expected.gradients.sh_rest.size() ||
        actual.gradients.opacity_logit.size() !=
            expected.gradients.opacity_logit.size() ||
        actual.gradients.opacity_sh.size() !=
            expected.gradients.opacity_sh.size() ||
        actual.gradients.xyz.size() != expected.gradients.xyz.size() ||
        actual.gradients.log_scale.size() !=
            expected.gradients.log_scale.size() ||
        actual.gradients.rotation.size() !=
            expected.gradients.rotation.size()) {
        throw std::runtime_error("tiled alpha gradient shape mismatch");
    }
    for (std::size_t gaussian = 0U;
         gaussian < actual.gradients.dc.size(); ++gaussian) {
        for (std::size_t channel = 0U; channel < 3U; ++channel) {
            if (std::abs(
                    actual.gradients.dc[gaussian][channel] -
                    expected.gradients.dc[gaussian][channel]) >
                gradient_tolerance) {
                throw std::runtime_error(
                    "tiled alpha DC gradient differs from CPU reference");
            }
        }
        for (std::size_t coefficient = 0U;
             coefficient < dronegs::maximum_sh_rest_values;
             ++coefficient) {
            if (std::abs(
                    actual.gradients.sh_rest[gaussian][coefficient] -
                    expected.gradients.sh_rest[gaussian][coefficient]) >
                gradient_tolerance) {
                throw std::runtime_error(
                    "tiled alpha SH gradient differs from CPU reference");
            }
        }
        if (std::abs(
                actual.gradients.opacity_logit[gaussian] -
                expected.gradients.opacity_logit[gaussian]) >
            gradient_tolerance) {
            throw std::runtime_error(
                "tiled alpha opacity gradient differs from CPU reference");
        }
        for (std::size_t coefficient = 0U;
             coefficient < dronegs::maximum_opacity_sh_coefficients;
             ++coefficient) {
            if (std::abs(
                    actual.gradients.opacity_sh[gaussian][coefficient] -
                    expected.gradients.opacity_sh[gaussian][coefficient]) >
                gradient_tolerance) {
                throw std::runtime_error(
                    "tiled alpha opacity-SH gradient differs from CPU reference");
            }
        }
        if (!compare_geometry) {
            continue;
        }
        for (std::size_t axis = 0U; axis < 3U; ++axis) {
            if (std::abs(
                    actual.gradients.xyz[gaussian][axis] -
                    expected.gradients.xyz[gaussian][axis]) >
                gradient_tolerance) {
                throw std::runtime_error(
                    "tiled alpha position gradient differs from CPU reference"
                    " at gaussian " + std::to_string(gaussian) +
                    " axis " + std::to_string(axis) + ": actual=" +
                    std::to_string(actual.gradients.xyz[gaussian][axis]) +
                    " expected=" +
                    std::to_string(expected.gradients.xyz[gaussian][axis]));
            }
            if (std::abs(
                    actual.gradients.log_scale[gaussian][axis] -
                    expected.gradients.log_scale[gaussian][axis]) >
                gradient_tolerance) {
                throw std::runtime_error(
                    "tiled alpha scale gradient differs from CPU reference"
                    " at gaussian " + std::to_string(gaussian) +
                    " axis " + std::to_string(axis) + ": actual=" +
                    std::to_string(
                        actual.gradients.log_scale[gaussian][axis]) +
                    " expected=" +
                    std::to_string(
                        expected.gradients.log_scale[gaussian][axis]));
            }
        }
        for (std::size_t component = 0U; component < 4U; ++component) {
            if (std::abs(
                    actual.gradients.rotation[gaussian][component] -
                    expected.gradients.rotation[gaussian][component]) >
                gradient_tolerance) {
                throw std::runtime_error(
                    "tiled alpha rotation gradient differs from CPU reference"
                    " at gaussian " + std::to_string(gaussian) +
                    " component " + std::to_string(component) + ": actual=" +
                    std::to_string(
                        actual.gradients.rotation[gaussian][component]) +
                    " expected=" +
                    std::to_string(
                        expected.gradients.rotation[gaussian][component]));
            }
        }
    }
}

void test_reference_parity() {
    const std::vector<dronegs::Gaussian> gaussians{
        splat({0.0F, 0.0F, 1.0F}, {1.0F, 0.1F, 0.0F}, 0.12F, 1.0F),
        splat({0.0F, 0.0F, 2.0F}, {0.0F, 0.2F, 1.0F}, 0.2F, 0.2F),
        splat({-0.45F, 0.2F, 1.5F}, {0.1F, 1.0F, 0.2F}, 0.1F, -0.3F),
        splat({0.5F, -0.3F, 1.8F}, {0.8F, 0.2F, 0.7F}, 0.16F, 0.7F),
        splat({0.0F, 0.0F, -1.0F}, {1.0F, 1.0F, 1.0F}, 0.2F, 2.0F),
    };
    constexpr std::array<float, 3> background{0.03F, 0.05F, 0.07F};
    const auto expected =
        dronegs::render_alpha_reference(gaussians, camera(), background);
    const auto actual =
        dronegs::render_alpha_tiled_cuda(gaussians, camera(), background);
    compare(actual, expected, 3.0e-5F);

    auto reversed = gaussians;
    std::reverse(reversed.begin(), reversed.end());
    const auto reversed_actual =
        dronegs::render_alpha_tiled_cuda(reversed, camera(), background);
    compare(reversed_actual, expected, 3.0e-5F);

    auto sh_gaussians = gaussians;
    sh_gaussians[0].sh_rest[0] = 0.08F;
    sh_gaussians[0].sh_rest[16] = -0.04F;
    sh_gaussians[1].sh_rest[30] = 0.06F;
    sh_gaussians[0].opacity_sh[0] = 0.12F;
    sh_gaussians[1].opacity_sh[2] = -0.08F;
    const auto sh_expected = dronegs::render_alpha_reference(
        sh_gaussians, camera(), background, 3U);
    const auto sh_actual = dronegs::render_alpha_tiled_cuda(
        sh_gaussians, camera(), background, 3U);
    compare(sh_actual, sh_expected, 4.0e-5F);
    const std::vector<float> upstream(sh_expected.rgb.size(), 0.1F);
    const auto sh_backward_expected =
        dronegs::render_alpha_reference_backward(
            sh_gaussians, camera(), upstream, background, 3U);
    const auto sh_backward_actual =
        dronegs::render_alpha_tiled_cuda_backward(
            sh_gaussians, camera(), upstream, background, 3U);
    compare_backward(
        sh_backward_actual, sh_backward_expected,
        4.0e-5F, 5.0e-5F, false);
}

void test_empty_scene() {
    constexpr std::array<float, 3> background{0.2F, 0.3F, 0.4F};
    const auto expected =
        dronegs::render_alpha_reference({}, camera(), background);
    const auto actual =
        dronegs::render_alpha_tiled_cuda({}, camera(), background);
    compare(actual, expected, 0.0F);

    const auto value_count =
        static_cast<std::size_t>(camera().width) * camera().height * 3U;
    const std::vector<float> image_gradient(value_count, 0.1F);
    const auto expected_backward =
        dronegs::render_alpha_reference_backward(
            {}, camera(), image_gradient, background);
    const auto actual_backward =
        dronegs::render_alpha_tiled_cuda_backward(
            {}, camera(), image_gradient, background);
    compare_backward(actual_backward, expected_backward, 0.0F, 0.0F);
}

void test_early_transmittance_exit() {
    std::vector<dronegs::Gaussian> gaussians;
    for (std::size_t index = 0; index < 12U; ++index) {
        gaussians.push_back(splat(
            {0.0F, 0.0F, 1.0F + static_cast<float>(index) * 0.01F},
            {0.8F, 0.4F, 0.2F}, 0.2F, 20.0F));
    }
    const auto expected =
        dronegs::render_alpha_reference(gaussians, camera());
    const auto actual =
        dronegs::render_alpha_tiled_cuda(gaussians, camera());
    compare(actual, expected, 3.0e-5F);
    const std::size_t center_pixel = 8U * camera().width + 9U;
    if (actual.transmittance[center_pixel] >
        dronegs::alpha_minimum_transmittance) {
        throw std::runtime_error(
            "tiled alpha renderer did not reach early-exit transmittance");
    }
}

void test_equal_depth_stability() {
    const std::vector<dronegs::Gaussian> red_first{
        splat({0.0F, 0.0F, 1.25F}, {1.0F, 0.0F, 0.0F}, 0.18F, 1.5F),
        splat({0.0F, 0.0F, 1.25F}, {0.0F, 0.0F, 1.0F}, 0.18F, 1.5F),
    };
    auto blue_first = red_first;
    std::reverse(blue_first.begin(), blue_first.end());

    const auto red_first_expected =
        dronegs::render_alpha_reference(red_first, camera());
    const auto red_first_actual =
        dronegs::render_alpha_tiled_cuda(red_first, camera());
    compare(red_first_actual, red_first_expected, 3.0e-5F);

    const auto blue_first_expected =
        dronegs::render_alpha_reference(blue_first, camera());
    const auto blue_first_actual =
        dronegs::render_alpha_tiled_cuda(blue_first, camera());
    compare(blue_first_actual, blue_first_expected, 3.0e-5F);

    const std::size_t center = (8U * camera().width + 9U) * 3U;
    if (red_first_actual.rgb[center] <= blue_first_actual.rgb[center] ||
        blue_first_actual.rgb[center + 2U] <=
            red_first_actual.rgb[center + 2U]) {
        throw std::runtime_error(
            "equal-depth source order was not preserved");
    }
}

void test_multi_tile_scene() {
    const std::vector<dronegs::Gaussian> gaussians{
        splat({0.0F, 0.0F, 1.0F}, {0.9F, 0.2F, 0.1F}, 0.24F, 1.2F),
        splat({-0.35F, -0.15F, 1.3F}, {0.1F, 0.8F, 0.3F}, 0.18F, 0.4F),
        splat({0.45F, 0.22F, 1.6F}, {0.2F, 0.3F, 1.0F}, 0.28F, 0.8F),
        splat({-0.7F, 0.45F, 2.0F}, {0.8F, 0.7F, 0.1F}, 0.2F, -0.2F),
    };
    constexpr std::array<float, 3> background{0.02F, 0.04F, 0.06F};
    const auto expected = dronegs::render_alpha_reference(
        gaussians, multi_tile_camera(), background);
    const auto actual = dronegs::render_alpha_tiled_cuda(
        gaussians, multi_tile_camera(), background);
    compare(actual, expected, 3.0e-5F);
    if (actual.stats.visible_splats != gaussians.size()) {
        throw std::runtime_error(
            "multi-tile scene unexpectedly culled a splat");
    }
}

void test_anisotropic_reference_parity() {
    constexpr float cosine_pi_over_eight = 0.9238795325112867F;
    constexpr float sine_pi_over_eight = 0.3826834323650898F;
    const std::vector<dronegs::Gaussian> gaussians{
        anisotropic_splat(
            {0.0F, 0.0F, 1.2F}, {0.9F, 0.2F, 0.1F},
            {0.28F, 0.06F, 0.1F},
            {1.0F, 0.0F, 0.0F, 0.0F}, 0.8F),
        anisotropic_splat(
            {-0.22F, 0.18F, 1.5F}, {0.1F, 0.8F, 0.3F},
            {0.25F, 0.05F, 0.09F},
            {cosine_pi_over_eight, 0.0F, 0.0F,
             sine_pi_over_eight},
            0.4F),
        anisotropic_splat(
            {0.36F, -0.2F, 1.9F}, {0.2F, 0.3F, 1.0F},
            {0.08F, 0.24F, 0.12F},
            {0.9659258263F, 0.2588190451F, 0.0F, 0.0F},
            -0.2F),
    };
    auto raster_camera = multi_tile_camera();
    constexpr float camera_cosine = 0.9961946981F;
    constexpr float camera_sine = 0.0871557427F;
    raster_camera.rotation = {
        camera_cosine, 0.0F, camera_sine,
        0.0F, 1.0F, 0.0F,
        -camera_sine, 0.0F, camera_cosine,
    };
    constexpr std::array<float, 3> background{0.01F, 0.03F, 0.05F};
    const auto projected =
        dronegs::project_alpha_splats(gaussians, raster_camera);
    if (projected.size() != gaussians.size() ||
        std::abs(projected[1].conic_xy) < 1.0e-4F) {
        throw std::runtime_error(
            "anisotropic fixture did not produce rotated conics");
    }
    const auto expected = dronegs::render_alpha_reference(
        gaussians, raster_camera, background);
    const auto actual = dronegs::render_alpha_tiled_cuda(
        gaussians, raster_camera, background);
    compare(actual, expected, 5.0e-5F);
    if (actual.stats.evaluated_pairs >= expected.stats.evaluated_pairs) {
        throw std::runtime_error(
            "anisotropic tile culling did not reduce evaluated pairs");
    }

    const auto value_count =
        static_cast<std::size_t>(raster_camera.width) *
        raster_camera.height * 3U;
    std::vector<float> image_gradient(value_count);
    for (std::size_t index = 0U; index < value_count; ++index) {
        image_gradient[index] =
            static_cast<float>(
                static_cast<int>(index % 11U) - 5) *
            0.013F;
    }
    const auto expected_backward =
        dronegs::render_alpha_reference_backward(
            gaussians, raster_camera, image_gradient, background);
    const auto actual_backward =
        dronegs::render_alpha_tiled_cuda_backward(
            gaussians, raster_camera, image_gradient, background);
    compare_backward(
        actual_backward, expected_backward, 5.0e-5F, 6.0e-4F, false);
}

void test_backward_reference_parity() {
    const std::vector<dronegs::Gaussian> gaussians{
        splat({0.0F, 0.0F, 1.1F}, {1.5F, 0.2F, 0.4F}, 0.15F, 0.4F),
        splat({0.0F, 0.0F, 1.3F}, {0.2F, 0.6F, 0.8F}, 0.18F, -0.2F),
        splat({-0.35F, 0.2F, 1.6F}, {0.4F, 0.75F, 0.3F}, 0.14F, 0.7F),
        splat({0.4F, -0.25F, 1.9F}, {0.8F, 0.3F, 0.65F}, 0.2F, -0.5F),
    };
    const auto raster_camera = multi_tile_camera();
    const auto value_count =
        static_cast<std::size_t>(raster_camera.width) *
        raster_camera.height * 3U;
    std::vector<float> image_gradient(value_count);
    for (std::size_t index = 0U; index < value_count; ++index) {
        image_gradient[index] =
            static_cast<float>(
                static_cast<int>(index % 13U) - 6) *
            0.017F;
    }
    constexpr std::array<float, 3> background{0.03F, 0.06F, 0.09F};
    const auto expected = dronegs::render_alpha_reference_backward(
        gaussians, raster_camera, image_gradient, background);
    const auto actual = dronegs::render_alpha_tiled_cuda_backward(
        gaussians, raster_camera, image_gradient, background);
    compare_backward(actual, expected, 3.0e-5F, 4.0e-4F, false);

    bool rejected = false;
    try {
        static_cast<void>(dronegs::render_alpha_tiled_cuda_backward(
            gaussians, raster_camera, {1.0F}, background));
    } catch (const std::invalid_argument&) {
        rejected = true;
    }
    if (!rejected) {
        throw std::runtime_error(
            "invalid tiled alpha backward gradient shape was accepted");
    }
}

void test_backward_early_exit() {
    std::vector<dronegs::Gaussian> gaussians;
    for (std::size_t index = 0U; index < 12U; ++index) {
        gaussians.push_back(splat(
            {0.0F, 0.0F, 1.0F + static_cast<float>(index) * 0.01F},
            {0.25F, 0.5F, 0.75F}, 0.2F, 20.0F));
    }
    const auto value_count =
        static_cast<std::size_t>(camera().width) * camera().height * 3U;
    const std::vector<float> image_gradient(value_count, 0.05F);
    const auto expected = dronegs::render_alpha_reference_backward(
        gaussians, camera(), image_gradient);
    const auto actual = dronegs::render_alpha_tiled_cuda_backward(
        gaussians, camera(), image_gradient);
    compare_backward(actual, expected, 3.0e-5F, 5.0e-4F, false);
}

float cuda_finite_difference(
    const std::vector<dronegs::Gaussian>& plus,
    const std::vector<dronegs::Gaussian>& minus,
    const std::vector<float>& image_gradient,
    const dronegs::RasterCamera& raster_camera, float epsilon) {
    const auto plus_output =
        dronegs::render_alpha_tiled_cuda(plus, raster_camera);
    const auto minus_output =
        dronegs::render_alpha_tiled_cuda(minus, raster_camera);
    double derivative = 0.0;
    for (std::size_t index = 0U; index < plus_output.rgb.size(); ++index) {
        derivative +=
            static_cast<double>(
                plus_output.rgb[index] -
                minus_output.rgb[index]) *
            static_cast<double>(image_gradient[index]);
    }
    return static_cast<float>(derivative / (2.0 * epsilon));
}

void test_backward_cuda_finite_difference() {
    std::vector<dronegs::Gaussian> gaussians{
        anisotropic_splat(
            {0.03F, -0.04F, 1.2F}, {0.65F, 0.25F, 0.45F},
            {0.16F, 0.11F, 0.08F}, {0.93F, 0.11F, -0.17F, 0.28F}, 0.2F),
        splat({0.1F, -0.05F, 1.7F}, {0.2F, 0.7F, 0.6F}, 0.2F, -0.3F),
    };
    const auto value_count =
        static_cast<std::size_t>(camera().width) * camera().height * 3U;
    std::vector<float> image_gradient(value_count);
    for (std::size_t index = 0U; index < value_count; ++index) {
        image_gradient[index] =
            static_cast<float>(
                static_cast<int>(index % 9U) - 4) *
            0.01F;
    }
    const auto raster_camera = camera();
    const auto backward = dronegs::render_alpha_tiled_cuda_backward(
        gaussians, raster_camera, image_gradient);
    constexpr float epsilon = 1.0e-3F;
    constexpr float position_epsilon = 1.0e-4F;

    auto plus = gaussians;
    auto minus = gaussians;
    plus[0].dc[1] += epsilon;
    minus[0].dc[1] -= epsilon;
    const float dc_finite_difference =
        cuda_finite_difference(
            plus, minus, image_gradient, raster_camera, epsilon);
    if (std::abs(
            backward.gradients.dc[0][1] -
            dc_finite_difference) >
        8.0e-4F) {
        throw std::runtime_error(
            "tiled alpha CUDA DC finite difference mismatch");
    }

    plus = gaussians;
    minus = gaussians;
    plus[1].opacity_logit += epsilon;
    minus[1].opacity_logit -= epsilon;
    const float opacity_finite_difference =
        cuda_finite_difference(
            plus, minus, image_gradient, raster_camera, epsilon);
    if (std::abs(
            backward.gradients.opacity_logit[1] -
            opacity_finite_difference) >
        1.2e-3F) {
        throw std::runtime_error(
            "tiled alpha CUDA opacity finite difference mismatch");
    }

    for (std::size_t axis = 0U; axis < 3U; ++axis) {
        plus = gaussians;
        minus = gaussians;
        plus[0].xyz[axis] += position_epsilon;
        minus[0].xyz[axis] -= position_epsilon;
        const float position_finite_difference =
            cuda_finite_difference(
                plus, minus, image_gradient, raster_camera,
                position_epsilon);
        if (std::abs(
                backward.gradients.xyz[0][axis] -
                position_finite_difference) >
            2.0e-3F) {
            throw std::runtime_error(
                "tiled alpha CUDA position finite difference mismatch"
                " at axis " + std::to_string(axis) + ": actual=" +
                std::to_string(backward.gradients.xyz[0][axis]) +
                " expected=" +
                std::to_string(position_finite_difference));
        }

        plus = gaussians;
        minus = gaussians;
        plus[0].log_scale[axis] += epsilon;
        minus[0].log_scale[axis] -= epsilon;
        const float scale_finite_difference =
            cuda_finite_difference(
                plus, minus, image_gradient, raster_camera, epsilon);
        if (std::abs(
                backward.gradients.log_scale[0][axis] -
                scale_finite_difference) >
            2.0e-3F) {
            throw std::runtime_error(
                "tiled alpha CUDA scale finite difference mismatch"
                " at axis " + std::to_string(axis) + ": actual=" +
                std::to_string(
                    backward.gradients.log_scale[0][axis]) +
                " expected=" +
                std::to_string(scale_finite_difference));
        }
    }

    for (std::size_t component = 0U; component < 4U; ++component) {
        plus = gaussians;
        minus = gaussians;
        plus[0].rotation[component] += epsilon;
        minus[0].rotation[component] -= epsilon;
        const float rotation_finite_difference =
            cuda_finite_difference(
                plus, minus, image_gradient, raster_camera, epsilon);
        if (std::abs(
                backward.gradients.rotation[0][component] -
                rotation_finite_difference) >
            2.0e-3F) {
            throw std::runtime_error(
                "tiled alpha CUDA rotation finite difference mismatch"
                " at component " + std::to_string(component) +
                ": actual=" +
                std::to_string(
                    backward.gradients.rotation[0][component]) +
                " expected=" +
                std::to_string(rotation_finite_difference));
        }
    }
}

}  // namespace

int main() {
    try {
        test_reference_parity();
        test_empty_scene();
        test_early_transmittance_exit();
        test_equal_depth_stability();
        test_multi_tile_scene();
        test_backward_cuda_finite_difference();
        test_anisotropic_reference_parity();
        test_backward_reference_parity();
        test_backward_early_exit();
        std::cout << "DroneGS tiled alpha CUDA tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "DroneGS tiled alpha CUDA test failed: "
                  << error.what() << "\n";
        return 1;
    }
}
