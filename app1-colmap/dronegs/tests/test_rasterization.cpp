// SPDX-License-Identifier: MIT
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

void check(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

void check_close(float actual, float expected, float tolerance,
                 const std::string& message) {
    if (std::abs(actual - expected) > tolerance) {
        throw std::runtime_error(message);
    }
}

dronegs::Gaussian centered(float depth, const std::array<float, 3>& color,
                           float opacity_logit = 0.0F) {
    dronegs::Gaussian gaussian;
    gaussian.xyz = {0.0F, 0.0F, depth};
    for (std::size_t channel = 0; channel < 3U; ++channel) {
        gaussian.dc[channel] = (color[channel] - 0.5F) / sh_c0;
    }
    gaussian.log_scale = {
        std::log(0.1F), std::log(0.1F), std::log(0.1F)};
    gaussian.opacity_logit = opacity_logit;
    return gaussian;
}

dronegs::RasterCamera camera() {
    return {
        .fx = 10.0F,
        .fy = 10.0F,
        .cx = 1.0F,
        .cy = 1.0F,
        .width = 2U,
        .height = 2U,
    };
}

void test_single_splat() {
    const auto output = dronegs::render_alpha_reference(
        {centered(1.0F, {1.0F, 0.0F, 0.0F})}, camera());
    check(output.stats.visible_splats == 1U, "single splat was not visible");
    const std::size_t pixel = 0U;
    const float delta_squared = 0.5F;
    const float weight = std::exp(-delta_squared * 0.5F);
    const float alpha = 0.5F * weight;
    check_close(output.rgb[pixel * 3U], alpha, 1.0e-6F,
                "single-splat red contribution mismatch");
    check_close(output.rgb[pixel * 3U + 1U], 0.0F, 1.0e-6F,
                "single-splat green contribution mismatch");
    check_close(output.transmittance[pixel], 1.0F - alpha, 1.0e-6F,
                "single-splat transmittance mismatch");
}

void test_depth_order_is_input_independent() {
    const auto near_red = centered(1.0F, {1.0F, 0.0F, 0.0F});
    const auto far_blue = centered(2.0F, {0.0F, 0.0F, 1.0F});
    const auto forward = dronegs::render_alpha_reference(
        {near_red, far_blue}, camera());
    const auto reversed = dronegs::render_alpha_reference(
        {far_blue, near_red}, camera());
    check(forward.rgb.size() == reversed.rgb.size(), "render size mismatch");
    for (std::size_t index = 0; index < forward.rgb.size(); ++index) {
        check_close(forward.rgb[index], reversed.rgb[index], 1.0e-6F,
                    "depth ordering depends on input order");
    }
    check(forward.rgb[0] > forward.rgb[2],
          "near red splat did not dominate far blue splat");
}

void test_background_and_culling() {
    auto behind = centered(-1.0F, {1.0F, 0.0F, 0.0F});
    const auto background = std::array<float, 3>{0.1F, 0.2F, 0.3F};
    const auto output = dronegs::render_alpha_reference(
        {behind}, camera(), background);
    check(output.stats.visible_splats == 0U, "behind-camera splat was visible");
    for (std::size_t pixel = 0; pixel < 4U; ++pixel) {
        for (std::size_t channel = 0; channel < 3U; ++channel) {
            check_close(
                output.rgb[pixel * 3U + channel], background[channel],
                1.0e-6F, "background composition mismatch");
        }
        check_close(output.transmittance[pixel], 1.0F, 1.0e-6F,
                    "empty-pixel transmittance mismatch");
    }
}

void test_invalid_camera() {
    auto invalid = camera();
    invalid.fx = 0.0F;
    bool rejected = false;
    try {
        static_cast<void>(dronegs::render_alpha_reference({}, invalid));
    } catch (const std::invalid_argument&) {
        rejected = true;
    }
    check(rejected, "invalid alpha-reference camera was accepted");
}

}  // namespace

int main() {
    try {
        test_single_splat();
        test_depth_order_is_input_independent();
        test_background_and_culling();
        test_invalid_camera();
        std::cout << "DroneGS alpha reference tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "DroneGS alpha reference test failed: "
                  << error.what() << "\n";
        return 1;
    }
}
