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
        actual.stats.evaluated_pairs != expected.stats.evaluated_pairs ||
        actual.stats.contributing_pairs != expected.stats.contributing_pairs) {
        throw std::runtime_error(
            "tiled alpha contribution statistics differ from CPU reference");
    }
}

void compare_backward(
    const dronegs::AlphaRenderBackwardOutput& actual,
    const dronegs::AlphaRenderBackwardOutput& expected,
    float render_tolerance, float gradient_tolerance) {
    compare(actual.render, expected.render, render_tolerance);
    if (actual.gradients.dc.size() != expected.gradients.dc.size() ||
        actual.gradients.opacity_logit.size() !=
            expected.gradients.opacity_logit.size()) {
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
        if (std::abs(
                actual.gradients.opacity_logit[gaussian] -
                expected.gradients.opacity_logit[gaussian]) >
            gradient_tolerance) {
            throw std::runtime_error(
                "tiled alpha opacity gradient differs from CPU reference");
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

void test_backward_reference_parity() {
    const std::vector<dronegs::Gaussian> gaussians{
        splat({0.0F, 0.0F, 1.1F}, {0.7F, 0.2F, 0.4F}, 0.15F, 0.4F),
        splat({0.0F, 0.0F, 1.1F}, {0.2F, 0.6F, 0.8F}, 0.18F, -0.2F),
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
    compare_backward(actual, expected, 3.0e-5F, 4.0e-4F);

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
    compare_backward(actual, expected, 3.0e-5F, 5.0e-4F);
}

float cuda_objective(
    const std::vector<dronegs::Gaussian>& gaussians,
    const std::vector<float>& image_gradient) {
    const auto output =
        dronegs::render_alpha_tiled_cuda(gaussians, camera());
    float value = 0.0F;
    for (std::size_t index = 0U; index < output.rgb.size(); ++index) {
        value += output.rgb[index] * image_gradient[index];
    }
    return value;
}

void test_backward_cuda_finite_difference() {
    std::vector<dronegs::Gaussian> gaussians{
        splat({0.0F, 0.0F, 1.2F}, {0.65F, 0.25F, 0.45F}, 0.16F, 0.2F),
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
    const auto backward = dronegs::render_alpha_tiled_cuda_backward(
        gaussians, camera(), image_gradient);
    constexpr float epsilon = 1.0e-3F;

    auto plus = gaussians;
    auto minus = gaussians;
    plus[0].dc[1] += epsilon;
    minus[0].dc[1] -= epsilon;
    const float dc_finite_difference =
        (cuda_objective(plus, image_gradient) -
         cuda_objective(minus, image_gradient)) /
        (2.0F * epsilon);
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
        (cuda_objective(plus, image_gradient) -
         cuda_objective(minus, image_gradient)) /
        (2.0F * epsilon);
    if (std::abs(
            backward.gradients.opacity_logit[1] -
            opacity_finite_difference) >
        1.2e-3F) {
        throw std::runtime_error(
            "tiled alpha CUDA opacity finite difference mismatch");
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
        test_backward_reference_parity();
        test_backward_early_exit();
        test_backward_cuda_finite_difference();
        std::cout << "DroneGS tiled alpha CUDA tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "DroneGS tiled alpha CUDA test failed: "
                  << error.what() << "\n";
        return 1;
    }
}
