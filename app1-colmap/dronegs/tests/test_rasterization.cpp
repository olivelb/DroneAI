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

dronegs::Gaussian anisotropic_centered(
    const std::array<float, 3>& scale,
    const std::array<float, 4>& rotation = {1.0F, 0.0F, 0.0F, 0.0F}) {
    auto gaussian = centered(1.0F, {0.8F, 0.4F, 0.2F});
    for (std::size_t axis = 0U; axis < 3U; ++axis) {
        gaussian.log_scale[axis] = std::log(scale[axis]);
    }
    gaussian.rotation = rotation;
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

void test_anisotropic_projection() {
    const auto horizontal = anisotropic_centered(
        {0.2F, 0.05F, 0.05F});
    const auto horizontal_projection =
        dronegs::project_alpha_splats({horizontal}, camera());
    check(horizontal_projection.size() == 1U,
          "anisotropic horizontal splat was culled");
    check_close(horizontal_projection.front().radius_x, 5.0F, 1.0e-5F,
                "anisotropic horizontal radius-x mismatch");
    check_close(horizontal_projection.front().radius_y, 1.875F, 1.0e-5F,
                "anisotropic horizontal radius-y mismatch");
    check_close(horizontal_projection.front().conic_xx, 0.25F, 1.0e-5F,
                "anisotropic horizontal conic-x mismatch");
    check_close(
        horizontal_projection.front().conic_yy,
        1.0F / (0.75F * 0.75F), 1.0e-5F,
        "anisotropic horizontal conic-y mismatch");
    check_close(horizontal_projection.front().conic_xy, 0.0F, 1.0e-6F,
                "axis-aligned anisotropic conic has cross term");

    constexpr float inverse_sqrt_two = 0.7071067811865475F;
    const auto vertical = anisotropic_centered(
        {0.2F, 0.05F, 0.05F},
        {inverse_sqrt_two, 0.0F, 0.0F, inverse_sqrt_two});
    const auto vertical_projection =
        dronegs::project_alpha_splats({vertical}, camera());
    check(vertical_projection.size() == 1U,
          "rotated anisotropic splat was culled");
    check_close(vertical_projection.front().radius_x, 1.875F, 1.0e-5F,
                "rotation did not swap anisotropic radius-x");
    check_close(vertical_projection.front().radius_y, 5.0F, 1.0e-5F,
                "rotation did not swap anisotropic radius-y");
    check_close(
        vertical_projection.front().conic_xx,
        1.0F / (0.75F * 0.75F), 1.0e-5F,
        "rotation did not swap anisotropic conic-x");
    check_close(vertical_projection.front().conic_yy, 0.25F, 1.0e-5F,
                "rotation did not swap anisotropic conic-y");

    auto extreme = anisotropic_centered({1000.0F, 0.1F, 0.1F});
    const auto extreme_projection =
        dronegs::project_alpha_splats({extreme}, camera());
    check(extreme_projection.size() == 1U,
          "spectrally clamped splat was culled");
    check(extreme_projection.front().radius_x <= 20.0001F,
          "anisotropic maximum eigenvalue clamp failed");

    auto invalid = horizontal;
    invalid.rotation = {0.0F, 0.0F, 0.0F, 0.0F};
    check(dronegs::project_alpha_splats({invalid}, camera()).empty(),
          "zero quaternion produced a projected covariance");
}

float finite_difference(
    const std::vector<dronegs::Gaussian>& plus,
    const std::vector<dronegs::Gaussian>& minus,
    const std::vector<float>& image_gradient,
    const std::array<float, 3>& background, float epsilon) {
    const auto plus_output =
        dronegs::render_alpha_reference(plus, camera(), background);
    const auto minus_output =
        dronegs::render_alpha_reference(minus, camera(), background);
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

void test_backward_finite_difference() {
    std::vector<dronegs::Gaussian> gaussians{
        centered(1.0F, {0.7F, 0.2F, 0.4F}, 0.3F),
        centered(1.8F, {0.1F, 0.6F, 0.8F}, -0.4F),
    };
    const std::vector<float> image_gradient{
        0.2F, -0.1F, 0.3F,
        -0.4F, 0.5F, 0.1F,
        0.3F, 0.2F, -0.2F,
        -0.1F, 0.4F, 0.25F,
    };
    constexpr std::array<float, 3> background{0.05F, 0.1F, 0.15F};
    const auto backward = dronegs::render_alpha_reference_backward(
        gaussians, camera(), image_gradient, background);
    check(backward.gradients.xyz.size() == gaussians.size(),
          "alpha-reference position gradient shape mismatch");
    check(backward.gradients.log_scale.size() == gaussians.size(),
          "alpha-reference scale gradient shape mismatch");
    check(backward.gradients.rotation.size() == gaussians.size(),
          "alpha-reference rotation gradient shape mismatch");
    constexpr float epsilon = 1.0e-3F;
    constexpr float position_epsilon = 1.0e-4F;
    for (std::size_t gaussian = 0U; gaussian < gaussians.size();
         ++gaussian) {
        for (std::size_t channel = 0U; channel < 3U; ++channel) {
            auto plus = gaussians;
            auto minus = gaussians;
            plus[gaussian].dc[channel] += epsilon;
            minus[gaussian].dc[channel] -= epsilon;
            const float finite_difference =
                ::finite_difference(
                    plus, minus, image_gradient, background, epsilon);
            check_close(
                backward.gradients.dc[gaussian][channel],
                finite_difference, 2.0e-4F,
                "alpha-reference DC gradient mismatch");
        }
        auto plus = gaussians;
        auto minus = gaussians;
        plus[gaussian].opacity_logit += epsilon;
        minus[gaussian].opacity_logit -= epsilon;
        const float finite_difference =
            ::finite_difference(
                plus, minus, image_gradient, background, epsilon);
        check_close(
            backward.gradients.opacity_logit[gaussian],
            finite_difference, 3.0e-4F,
            "alpha-reference opacity gradient mismatch");
        for (std::size_t axis = 0U; axis < 3U; ++axis) {
            plus = gaussians;
            minus = gaussians;
            plus[gaussian].xyz[axis] += position_epsilon;
            minus[gaussian].xyz[axis] -= position_epsilon;
            const float position_finite_difference =
                ::finite_difference(
                    plus, minus, image_gradient, background,
                    position_epsilon);
            check_close(
                backward.gradients.xyz[gaussian][axis],
                position_finite_difference, 1.0e-6F,
                "alpha-reference position gradient mismatch");

            plus = gaussians;
            minus = gaussians;
            plus[gaussian].log_scale[axis] += epsilon;
            minus[gaussian].log_scale[axis] -= epsilon;
            const float scale_finite_difference =
                ::finite_difference(
                    plus, minus, image_gradient, background, epsilon);
            check_close(
                backward.gradients.log_scale[gaussian][axis],
                scale_finite_difference, 1.0e-6F,
                "alpha-reference scale gradient mismatch");
        }
        for (std::size_t component = 0U; component < 4U; ++component) {
            plus = gaussians;
            minus = gaussians;
            plus[gaussian].rotation[component] += epsilon;
            minus[gaussian].rotation[component] -= epsilon;
            const float rotation_finite_difference =
                ::finite_difference(
                    plus, minus, image_gradient, background, epsilon);
            check_close(
                backward.gradients.rotation[gaussian][component],
                rotation_finite_difference, 1.0e-6F,
                "alpha-reference rotation gradient mismatch");
        }
    }

    bool rejected = false;
    try {
        static_cast<void>(dronegs::render_alpha_reference_backward(
            gaussians, camera(), {1.0F}, background));
    } catch (const std::invalid_argument&) {
        rejected = true;
    }
    check(rejected, "invalid alpha backward gradient shape was accepted");
}

void test_progressive_sh_color_and_gradient() {
    auto gaussian = centered(1.0F, {0.5F, 0.5F, 0.5F}, 0.4F);
    gaussian.xyz[0] = 0.1F;
    gaussian.sh_rest[2] = 0.12F;
    const auto dc_only =
        dronegs::render_alpha_reference({gaussian}, camera());
    const auto degree_one = dronegs::render_alpha_reference(
        {gaussian}, camera(), {0.0F, 0.0F, 0.0F}, 1U);
    check(
        std::abs(dc_only.rgb[0] - degree_one.rgb[0]) > 1.0e-5F,
        "degree-one SH did not change view-dependent color");

    const std::vector<float> upstream(degree_one.rgb.size(), 0.25F);
    const auto backward = dronegs::render_alpha_reference_backward(
        {gaussian}, camera(), upstream, {0.0F, 0.0F, 0.0F}, 1U);
    constexpr float epsilon = 1.0e-3F;
    auto plus = gaussian;
    auto minus = gaussian;
    plus.sh_rest[2] += epsilon;
    minus.sh_rest[2] -= epsilon;
    const auto plus_render = dronegs::render_alpha_reference(
        {plus}, camera(), {0.0F, 0.0F, 0.0F}, 1U);
    const auto minus_render = dronegs::render_alpha_reference(
        {minus}, camera(), {0.0F, 0.0F, 0.0F}, 1U);
    double numerical = 0.0;
    for (std::size_t index = 0U; index < upstream.size(); ++index) {
        numerical += static_cast<double>(
            plus_render.rgb[index] - minus_render.rgb[index]) *
            upstream[index];
    }
    numerical /= 2.0 * epsilon;
    check_close(
        backward.gradients.sh_rest[0][2],
        static_cast<float>(numerical), 3.0e-4F,
        "degree-one SH coefficient gradient mismatch");
}

}  // namespace

int main() {
    try {
        test_single_splat();
        test_depth_order_is_input_independent();
        test_background_and_culling();
        test_invalid_camera();
        test_anisotropic_projection();
        test_backward_finite_difference();
        test_progressive_sh_color_and_gradient();
        std::cout << "DroneGS alpha reference tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "DroneGS alpha reference test failed: "
                  << error.what() << "\n";
        return 1;
    }
}
