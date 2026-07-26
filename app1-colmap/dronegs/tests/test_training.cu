// SPDX-License-Identifier: MIT
#include <cuda_runtime.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include <jpeglib.h>

#include "dronegs/image.hpp"
#include "dronegs/model.hpp"
#include "dronegs/ordered_training.hpp"
#include "dronegs/rasterization.hpp"
#include "dronegs/training.hpp"

namespace {

void write_solid_jpeg(const std::filesystem::path& path) {
    auto* file = std::fopen(path.string().c_str(), "wb");
    if (file == nullptr) {
        throw std::runtime_error("cannot create JPEG training fixture");
    }
    jpeg_compress_struct compressor{};
    jpeg_error_mgr error{};
    compressor.err = jpeg_std_error(&error);
    jpeg_create_compress(&compressor);
    jpeg_stdio_dest(&compressor, file);
    compressor.image_width = 32U;
    compressor.image_height = 32U;
    compressor.input_components = 3;
    compressor.in_color_space = JCS_RGB;
    jpeg_set_defaults(&compressor);
    jpeg_set_quality(&compressor, 95, TRUE);
    jpeg_start_compress(&compressor, TRUE);
    std::array<JSAMPLE, 32U * 3U> row{};
    for (std::size_t x = 0; x < 32U; ++x) {
        row[x * 3U] = 0U;
        row[x * 3U + 1U] = 255U;
        row[x * 3U + 2U] = 0U;
    }
    while (compressor.next_scanline < compressor.image_height) {
        auto* row_pointer = row.data();
        static_cast<void>(jpeg_write_scanlines(&compressor, &row_pointer, 1U));
    }
    jpeg_finish_compress(&compressor);
    jpeg_destroy_compress(&compressor);
    std::fclose(file);
}

dronegs::Scene make_scene() {
    dronegs::Scene scene;
    scene.cameras.push_back({
        .id = 1U,
        .model_id = 1,
        .width = 32U,
        .height = 32U,
        .parameters = {30.0, 30.0, 16.0, 16.0},
    });
    scene.images.push_back({
        .id = 1U,
        .camera_id = 1U,
        .name = "frame.jpg",
        .qvec = {1.0, 0.0, 0.0, 0.0},
        .tvec = {0.0, 0.0, 0.0},
    });
    std::uint64_t id = 1U;
    for (int y = -2; y <= 2; ++y) {
        for (int x = -2; x <= 2; ++x) {
            scene.points.push_back({
                .id = id++,
                .xyz = {
                    static_cast<double>(x) * 0.2,
                    static_cast<double>(y) * 0.2,
                    2.0,
                },
                .rgb = {128U, 128U, 128U},
            });
        }
    }
    return scene;
}

double reference_ssim(
    const std::vector<float>& prediction,
    const std::vector<std::uint8_t>& target,
    std::uint32_t width, std::uint32_t height) {
    constexpr std::array<float, 11> weights{
        0.0010283801F, 0.0075987581F, 0.0360007721F,
        0.1093606895F, 0.2130055377F, 0.2660117249F,
        0.2130055377F, 0.1093606895F, 0.0360007721F,
        0.0075987581F, 0.0010283801F,
    };
    constexpr int radius = 5;
    constexpr float c1 = 0.01F * 0.01F;
    constexpr float c2 = 0.03F * 0.03F;
    double sum = 0.0;
    std::size_t count = 0U;
    for (std::uint32_t y = radius; y + radius < height; ++y) {
        for (std::uint32_t x = radius; x + radius < width; ++x) {
            for (std::size_t channel = 0U; channel < 3U; ++channel) {
                double prediction_mean = 0.0;
                double target_mean = 0.0;
                double prediction_square = 0.0;
                double target_square = 0.0;
                double cross = 0.0;
                for (int dy = -radius; dy <= radius; ++dy) {
                    for (int dx = -radius; dx <= radius; ++dx) {
                        const auto sample =
                            (static_cast<std::size_t>(
                                 static_cast<int>(y) + dy) *
                                 width +
                             static_cast<std::size_t>(
                                 static_cast<int>(x) + dx)) *
                                3U +
                            channel;
                        const double weight =
                            static_cast<double>(weights[dy + radius]) *
                            weights[dx + radius];
                        const double predicted = prediction[sample];
                        const double expected =
                            static_cast<double>(target[sample]) / 255.0;
                        prediction_mean += weight * predicted;
                        target_mean += weight * expected;
                        prediction_square +=
                            weight * predicted * predicted;
                        target_square += weight * expected * expected;
                        cross += weight * predicted * expected;
                    }
                }
                const double prediction_variance = std::max(
                    0.0,
                    prediction_square -
                        prediction_mean * prediction_mean);
                const double target_variance = std::max(
                    0.0,
                    target_square - target_mean * target_mean);
                const double covariance =
                    cross - prediction_mean * target_mean;
                sum +=
                    ((2.0 * prediction_mean * target_mean + c1) *
                     (2.0 * covariance + c2)) /
                    ((prediction_mean * prediction_mean +
                      target_mean * target_mean + c1) *
                     (prediction_variance + target_variance + c2));
                ++count;
            }
        }
    }
    return sum / static_cast<double>(count);
}

double reference_objective(
    const std::vector<float>& prediction,
    const std::vector<float>& transmittance,
    const std::vector<std::uint8_t>& target,
    std::uint32_t width, std::uint32_t height) {
    double l1_sum = 0.0;
    std::size_t active_pixels = 0U;
    for (std::size_t pixel = 0U;
         pixel < transmittance.size(); ++pixel) {
        if (transmittance[pixel] >= 1.0F) {
            continue;
        }
        ++active_pixels;
        for (std::size_t channel = 0U; channel < 3U; ++channel) {
            const auto sample = pixel * 3U + channel;
            l1_sum += std::abs(
                static_cast<double>(prediction[sample]) -
                static_cast<double>(target[sample]) / 255.0);
        }
    }
    if (active_pixels == 0U) {
        throw std::runtime_error(
            "CPU objective fixture has no active pixels");
    }
    const double l1 =
        l1_sum / (3.0 * static_cast<double>(active_pixels));
    return 0.8 * l1 +
        0.2 * (1.0 - reference_ssim(
            prediction, target, width, height));
}

}  // namespace

int main() {
    const auto suffix = std::chrono::steady_clock::now().time_since_epoch().count();
    const auto root = std::filesystem::temp_directory_path() /
                      ("dronegs-training-test-" + std::to_string(suffix));
    try {
        std::filesystem::create_directories(root / "images");
        write_solid_jpeg(root / "images" / "frame.jpg");
        const auto full_decode = dronegs::load_training_image(
            root / "images" / "frame.jpg", 4U, 32U, false);
        const auto scaled_decode = dronegs::load_training_image(
            root / "images" / "frame.jpg", 4U, 32U, true);
        if (full_decode.width != 8U || full_decode.height != 8U ||
            scaled_decode.width != full_decode.width ||
            scaled_decode.height != full_decode.height ||
            scaled_decode.rgb.size() != full_decode.rgb.size() ||
            scaled_decode.source_to_image_x != 0.25F ||
            scaled_decode.source_to_image_y != 0.25F) {
            throw std::runtime_error(
                "scaled-IDCT training image contract mismatch");
        }
        const auto scene = make_scene();
        const auto initialized =
            dronegs::initialize_fixed_topology(scene);
        const auto split = dronegs::make_dataset_split(17U, 8U);
        if (split.training.size() != 14U ||
            split.held_out !=
                std::vector<std::size_t>{0U, 8U, 16U} ||
            std::find(
                split.training.begin(), split.training.end(), 8U) !=
                split.training.end()) {
            throw std::runtime_error(
                "LichtFeld-compatible held-out split mismatch");
        }
        const auto quality_target = dronegs::load_training_image(
            root / "images" / "frame.jpg", 1U, 32U, false);
        dronegs::OrderedAlphaTrainingContext quality_context(
            initialized, 32U * 32U, 1U);
        const dronegs::RasterCamera quality_camera{
            .fx = 30.0F,
            .fy = 30.0F,
            .cx = 16.0F,
            .cy = 16.0F,
            .width = 32U,
            .height = 32U,
        };
        std::vector<float> quality_prediction;
        const auto quality = quality_context.evaluate_quality(
            quality_camera, quality_target.rgb.data(),
            quality_target.rgb.size(), &quality_prediction);
        double squared_error_sum = 0.0;
        for (std::size_t sample = 0U;
             sample < quality_prediction.size(); ++sample) {
            const double target =
                static_cast<double>(quality_target.rgb[sample]) / 255.0;
            const double difference =
                static_cast<double>(quality_prediction[sample]) - target;
            squared_error_sum += difference * difference;
        }
        const double mse = std::max(
            squared_error_sum /
                static_cast<double>(quality_prediction.size()),
            1.0e-10);
        const float expected_psnr =
            static_cast<float>(10.0 * std::log10(1.0 / mse));
        const float expected_ssim = static_cast<float>(reference_ssim(
            quality_prediction, quality_target.rgb, 32U, 32U));
        if (std::abs(quality.psnr - expected_psnr) > 2.0e-4F ||
            std::abs(quality.ssim - expected_ssim) > 2.0e-4F ||
            quality.active_pixel_fraction <= 0.0F ||
            quality.active_pixel_fraction > 1.0F) {
            throw std::runtime_error(
                "GPU held-out PSNR/SSIM mismatch");
        }
        const auto objective =
            quality_context.evaluate_objective_gradient(
                quality_camera, quality_target.rgb.data(),
                quality_target.rgb.size());
        const double expected_objective = reference_objective(
            objective.prediction, objective.transmittance,
            quality_target.rgb, 32U, 32U);
        if (std::abs(objective.loss - expected_objective) > 2.0e-4F ||
            objective.gradient.size() != objective.prediction.size() ||
            objective.transmittance.size() != 32U * 32U) {
            throw std::runtime_error(
                "GPU L1+DSSIM objective mismatch");
        }
        constexpr float finite_difference_epsilon = 1.0e-3F;
        const std::array<std::size_t, 8> gradient_samples{
            (6U * 32U + 7U) * 3U,
            (8U * 32U + 14U) * 3U + 1U,
            (12U * 32U + 12U) * 3U + 2U,
            (16U * 32U + 16U) * 3U,
            (18U * 32U + 20U) * 3U + 1U,
            (22U * 32U + 10U) * 3U + 2U,
            (25U * 32U + 24U) * 3U,
            (29U * 32U + 28U) * 3U + 1U,
        };
        std::size_t checked_gradients = 0U;
        auto perturbed_prediction = objective.prediction;
        for (const auto sample : gradient_samples) {
            const float target =
                static_cast<float>(quality_target.rgb[sample]) / 255.0F;
            if (std::abs(objective.prediction[sample] - target) <
                5.0e-3F) {
                continue;
            }
            perturbed_prediction[sample] =
                objective.prediction[sample] +
                finite_difference_epsilon;
            const double plus = reference_objective(
                perturbed_prediction, objective.transmittance,
                quality_target.rgb, 32U, 32U);
            perturbed_prediction[sample] =
                objective.prediction[sample] -
                finite_difference_epsilon;
            const double minus = reference_objective(
                perturbed_prediction, objective.transmittance,
                quality_target.rgb, 32U, 32U);
            perturbed_prediction[sample] =
                objective.prediction[sample];
            const double expected_gradient =
                (plus - minus) /
                (2.0 * finite_difference_epsilon);
            if (std::abs(
                    static_cast<double>(objective.gradient[sample]) -
                    expected_gradient) > 1.0e-4) {
                throw std::runtime_error(
                    "analytic DSSIM image gradient mismatch");
            }
            ++checked_gradients;
        }
        if (checked_gradients < 6U) {
            throw std::runtime_error(
                "insufficient DSSIM finite-difference probes");
        }
        dronegs::Gaussian split_parent{
            .xyz = {-0.2F, 0.1F, 2.0F},
            .dc = {0.0F, 0.0F, 0.0F},
            .log_scale = {
                std::log(0.2F),
                std::log(0.1F),
                std::log(0.08F),
            },
            .rotation = {1.0F, 0.0F, 0.0F, 0.0F},
            .opacity_logit = 0.0F,
        };
        std::vector<std::uint8_t> split_target(32U * 32U * 3U, 0U);
        for (std::size_t y = 0U; y < 32U; ++y) {
            for (std::size_t x = 16U; x < 32U; ++x) {
                split_target[(y * 32U + x) * 3U + 1U] = 255U;
            }
        }
        std::vector<dronegs::Gaussian> rate_fixture(
            8U, split_parent);
        for (std::size_t index = 0U;
             index < rate_fixture.size(); ++index) {
            const float coordinate = static_cast<float>(index);
            rate_fixture[index].xyz = {
                -0.035F + 0.01F * coordinate,
                -0.07F + 0.02F * coordinate,
                2.0F + 0.03F * coordinate,
            };
        }
        dronegs::OrderedAlphaTrainingContext progressive_sh_context(
            rate_fixture, 32U * 32U, 2U, 8U,
            dronegs::MrnfOptimizerProfile::dronegs_dev16,
            2U, 1U);
        if (progressive_sh_context.active_sh_degree() != 0U) {
            throw std::runtime_error(
                "progressive SH did not start at degree zero");
        }
        static_cast<void>(progressive_sh_context.train_step(
            quality_camera, split_target.data(), split_target.size()));
        if (progressive_sh_context.active_sh_degree() != 1U) {
            throw std::runtime_error(
                "progressive SH did not activate degree one");
        }
        static_cast<void>(progressive_sh_context.train_step(
            quality_camera, split_target.data(), split_target.size()));
        if (progressive_sh_context.active_sh_degree() != 2U) {
            throw std::runtime_error(
                "progressive SH did not activate requested maximum degree");
        }
        dronegs::OrderedAlphaTrainingContext rate_context(
            rate_fixture, 32U * 32U, 2U, 8U,
            dronegs::MrnfOptimizerProfile::lichtfeld_absolute);
        auto noise_parent = split_parent;
        noise_parent.opacity_logit = -4.0F;
        auto noise_neighbor = noise_parent;
        noise_neighbor.xyz[0] += 0.1F;
        dronegs::OrderedAlphaTrainingContext noise_first(
            {noise_parent, noise_neighbor}, 32U * 32U, 2U, 2U,
            dronegs::MrnfOptimizerProfile::dronegs_dev16,
            0U, 1000U, 42U);
        dronegs::OrderedAlphaTrainingContext noise_repeat(
            {noise_parent, noise_neighbor}, 32U * 32U, 2U, 2U,
            dronegs::MrnfOptimizerProfile::dronegs_dev16,
            0U, 1000U, 42U);
        dronegs::OrderedAlphaTrainingContext noise_other(
            {noise_parent, noise_neighbor}, 32U * 32U, 2U, 2U,
            dronegs::MrnfOptimizerProfile::dronegs_dev16,
            0U, 1000U, 43U);
        static_cast<void>(noise_first.train_step(
            quality_camera, split_target.data(), split_target.size()));
        static_cast<void>(noise_repeat.train_step(
            quality_camera, split_target.data(), split_target.size()));
        static_cast<void>(noise_other.train_step(
            quality_camera, split_target.data(), split_target.size()));
        std::vector<dronegs::Gaussian> noise_first_gaussians;
        std::vector<dronegs::Gaussian> noise_repeat_gaussians;
        std::vector<dronegs::Gaussian> noise_other_gaussians;
        noise_first.download(noise_first_gaussians);
        noise_repeat.download(noise_repeat_gaussians);
        noise_other.download(noise_other_gaussians);
        bool different_seed_changed_position = false;
        for (std::size_t axis = 0U; axis < 3U; ++axis) {
            if (noise_first_gaussians[0].xyz[axis] !=
                noise_repeat_gaussians[0].xyz[axis]) {
                throw std::runtime_error(
                    "MRNF means noise is not deterministic");
            }
            different_seed_changed_position =
                different_seed_changed_position ||
                noise_first_gaussians[0].xyz[axis] !=
                    noise_other_gaussians[0].xyz[axis];
        }
        if (!different_seed_changed_position) {
            throw std::runtime_error(
                "MRNF means noise seed has no effect");
        }
        const auto initial_rates =
            rate_context.current_learning_rates();
        const auto require_rate = [](
            float actual, float expected, float tolerance,
            const char* label) {
            if (std::abs(actual - expected) > tolerance) {
                throw std::runtime_error(
                    std::string("MRNF learning rate mismatch: ") +
                    label);
            }
        };
        require_rate(initial_rates.position, 2.4e-6F, 1.0e-10F, "position");
        require_rate(initial_rates.dc, 2.0e-3F, 1.0e-8F, "dc");
        require_rate(initial_rates.opacity, 1.2e-2F, 1.0e-8F, "opacity");
        require_rate(initial_rates.scale, 7.0e-3F, 1.0e-8F, "scale");
        require_rate(initial_rates.rotation, 2.0e-3F, 1.0e-8F, "rotation");
        require_rate(
            initial_rates.position_epsilon,
            1.0e-15F, 1.0e-20F, "position epsilon");
        require_rate(
            initial_rates.dc_epsilon,
            1.0e-15F, 1.0e-20F, "dc epsilon");
        require_rate(
            initial_rates.opacity_epsilon,
            1.0e-15F, 1.0e-20F, "opacity epsilon");
        require_rate(
            initial_rates.scale_epsilon,
            1.0e-15F, 1.0e-20F, "scale epsilon");
        require_rate(
            initial_rates.rotation_epsilon,
            1.0e-15F, 1.0e-20F, "rotation epsilon");
        dronegs::OrderedAlphaTrainingContext native_rate_context(
            rate_fixture, 32U * 32U, 2U, 8U,
            dronegs::MrnfOptimizerProfile::dronegs_dev16);
        const auto native_rates =
            native_rate_context.current_learning_rates();
        const float native_diagonal = std::sqrt(
            0.07F * 0.07F +
            0.14F * 0.14F +
            0.21F * 0.21F);
        require_rate(
            native_rates.position,
            native_diagonal * 1.6e-4F,
            1.0e-9F, "dev16 position");
        require_rate(native_rates.dc, 5.0e-2F, 1.0e-8F, "dev16 dc");
        require_rate(
            native_rates.opacity, 1.0e-2F,
            1.0e-8F, "dev16 opacity");
        require_rate(
            native_rates.scale, 5.0e-3F,
            1.0e-8F, "dev16 scale");
        require_rate(
            native_rates.rotation, 1.0e-3F,
            1.0e-8F, "dev16 rotation");
        require_rate(
            native_rates.position_epsilon, 1.0e-8F,
            1.0e-12F, "dev16 position epsilon");
        require_rate(
            native_rates.dc_epsilon, 1.0e-8F,
            1.0e-12F, "dev16 dc epsilon");
        require_rate(
            native_rates.opacity_epsilon, 1.0e-8F,
            1.0e-12F, "dev16 opacity epsilon");
        require_rate(
            native_rates.scale_epsilon, 1.0e-8F,
            1.0e-12F, "dev16 scale epsilon");
        require_rate(
            native_rates.rotation_epsilon, 1.0e-8F,
            1.0e-12F, "dev16 rotation epsilon");
        dronegs::OrderedAlphaTrainingContext dc_only_context(
            rate_fixture, 32U * 32U, 2U, 8U,
            dronegs::MrnfOptimizerProfile::lichtfeld_dc_only);
        const auto dc_only_rates =
            dc_only_context.current_learning_rates();
        require_rate(
            dc_only_rates.position, native_rates.position,
            1.0e-9F, "DC-only position isolation");
        require_rate(
            dc_only_rates.dc, initial_rates.dc,
            1.0e-8F, "DC-only DC");
        require_rate(
            dc_only_rates.opacity, native_rates.opacity,
            1.0e-8F, "DC-only opacity isolation");
        require_rate(
            dc_only_rates.scale, native_rates.scale,
            1.0e-8F, "DC-only scale isolation");
        require_rate(
            dc_only_rates.rotation, native_rates.rotation,
            1.0e-8F, "DC-only rotation isolation");
        require_rate(
            dc_only_rates.dc_epsilon, 1.0e-15F,
            1.0e-20F, "DC-only DC epsilon");
        require_rate(
            dc_only_rates.position_epsilon, 1.0e-8F,
            1.0e-12F, "DC-only position epsilon isolation");
        dronegs::OrderedAlphaTrainingContext position_only_context(
            rate_fixture, 32U * 32U, 2U, 8U,
            dronegs::MrnfOptimizerProfile::lichtfeld_position_only);
        const auto position_only_rates =
            position_only_context.current_learning_rates();
        require_rate(
            position_only_rates.position, initial_rates.position,
            1.0e-10F, "position-only position");
        require_rate(
            position_only_rates.dc, native_rates.dc,
            1.0e-8F, "position-only DC isolation");
        require_rate(
            position_only_rates.opacity, native_rates.opacity,
            1.0e-8F, "position-only opacity isolation");
        require_rate(
            position_only_rates.scale, native_rates.scale,
            1.0e-8F, "position-only scale isolation");
        require_rate(
            position_only_rates.rotation, native_rates.rotation,
            1.0e-8F, "position-only rotation isolation");
        require_rate(
            position_only_rates.position_epsilon, 1.0e-15F,
            1.0e-20F, "position-only position epsilon");
        require_rate(
            position_only_rates.dc_epsilon, 1.0e-8F,
            1.0e-12F, "position-only DC epsilon isolation");
        dronegs::OrderedAlphaTrainingContext opacity_only_context(
            rate_fixture, 32U * 32U, 2U, 8U,
            dronegs::MrnfOptimizerProfile::lichtfeld_opacity_only);
        const auto opacity_only_rates =
            opacity_only_context.current_learning_rates();
        require_rate(
            opacity_only_rates.opacity, initial_rates.opacity,
            1.0e-8F, "opacity-only opacity");
        require_rate(
            opacity_only_rates.opacity_epsilon, 1.0e-15F,
            1.0e-20F, "opacity-only epsilon");
        require_rate(
            opacity_only_rates.dc, native_rates.dc,
            1.0e-8F, "opacity-only DC isolation");
        dronegs::OrderedAlphaTrainingContext dc_opacity_context(
            rate_fixture, 32U * 32U, 2U, 8U,
            dronegs::MrnfOptimizerProfile::lichtfeld_dc_opacity);
        const auto dc_opacity_rates =
            dc_opacity_context.current_learning_rates();
        require_rate(
            dc_opacity_rates.dc, initial_rates.dc,
            1.0e-8F, "DC-opacity DC");
        require_rate(
            dc_opacity_rates.opacity, initial_rates.opacity,
            1.0e-8F, "DC-opacity opacity");
        require_rate(
            dc_opacity_rates.position, native_rates.position,
            1.0e-9F, "DC-opacity position isolation");
        require_rate(
            dc_opacity_rates.scale, native_rates.scale,
            1.0e-8F, "DC-opacity scale isolation");
        require_rate(
            dc_opacity_rates.rotation, native_rates.rotation,
            1.0e-8F, "DC-opacity rotation isolation");
        require_rate(
            dc_opacity_rates.dc_epsilon, 1.0e-15F,
            1.0e-20F, "DC-opacity DC epsilon");
        require_rate(
            dc_opacity_rates.opacity_epsilon, 1.0e-15F,
            1.0e-20F, "DC-opacity opacity epsilon");
        require_rate(
            dc_opacity_rates.position_epsilon, 1.0e-8F,
            1.0e-12F, "DC-opacity position epsilon isolation");
        const auto require_calibrated_dc =
            [&](dronegs::MrnfOptimizerProfile profile,
                float expected_dc, const char* label) {
                dronegs::OrderedAlphaTrainingContext context(
                    rate_fixture, 32U * 32U, 2U, 8U, profile);
                const auto rates = context.current_learning_rates();
                require_rate(
                    rates.dc, expected_dc, 1.0e-8F, label);
                require_rate(
                    rates.opacity, initial_rates.opacity,
                    1.0e-8F, "calibrated opacity");
                require_rate(
                    rates.position, native_rates.position,
                    1.0e-9F, "calibrated position isolation");
                require_rate(
                    rates.scale, native_rates.scale,
                    1.0e-8F, "calibrated scale isolation");
                require_rate(
                    rates.rotation, native_rates.rotation,
                    1.0e-8F, "calibrated rotation isolation");
                require_rate(
                    rates.dc_epsilon, 1.0e-15F,
                    1.0e-20F, "calibrated DC epsilon");
                require_rate(
                    rates.opacity_epsilon, 1.0e-15F,
                    1.0e-20F, "calibrated opacity epsilon");
                require_rate(
                    rates.position_epsilon, 1.0e-8F,
                    1.0e-12F, "calibrated position epsilon");
            };
        require_calibrated_dc(
            dronegs::MrnfOptimizerProfile::calibrated_dc_005_opacity,
            5.0e-3F, "calibrated DC 0.005");
        require_calibrated_dc(
            dronegs::MrnfOptimizerProfile::calibrated_dc_010_opacity,
            1.0e-2F, "calibrated DC 0.010");
        require_calibrated_dc(
            dronegs::MrnfOptimizerProfile::calibrated_dc_020_opacity,
            2.0e-2F, "calibrated DC 0.020");
        const auto require_opacity_candidate =
            [&](dronegs::MrnfOptimizerProfile profile,
                float expected_opacity, const char* label) {
                dronegs::OrderedAlphaTrainingContext context(
                    rate_fixture, 32U * 32U, 2U, 8U, profile);
                const auto rates = context.current_learning_rates();
                require_rate(rates.dc, 1.0e-2F, 1.0e-8F, label);
                require_rate(
                    rates.opacity, expected_opacity,
                    1.0e-8F, label);
                require_rate(
                    rates.position, native_rates.position,
                    1.0e-9F, "opacity candidate position isolation");
                require_rate(
                    rates.scale, native_rates.scale,
                    1.0e-8F, "opacity candidate scale isolation");
                require_rate(
                    rates.rotation, native_rates.rotation,
                    1.0e-8F, "opacity candidate rotation isolation");
                require_rate(
                    rates.dc_epsilon, 1.0e-15F,
                    1.0e-20F, "opacity candidate DC epsilon");
                require_rate(
                    rates.opacity_epsilon, 1.0e-15F,
                    1.0e-20F, "opacity candidate opacity epsilon");
            };
        require_opacity_candidate(
            dronegs::MrnfOptimizerProfile::
                calibrated_dc_010_opacity_024,
            2.4e-2F, "opacity 0.024");
        require_opacity_candidate(
            dronegs::MrnfOptimizerProfile::
                calibrated_dc_010_opacity_048,
            4.8e-2F, "opacity 0.048");
        require_opacity_candidate(
            dronegs::MrnfOptimizerProfile::
                calibrated_dc_010_opacity_096,
            9.6e-2F, "opacity 0.096");
        const auto require_geometry_candidate =
            [&](dronegs::MrnfOptimizerProfile profile,
                bool use_lf_scale, bool use_lf_rotation,
                const char* label) {
                dronegs::OrderedAlphaTrainingContext context(
                    rate_fixture, 32U * 32U, 2U, 8U, profile);
                const auto rates = context.current_learning_rates();
                require_rate(rates.dc, 1.0e-2F, 1.0e-8F, label);
                require_rate(
                    rates.opacity, 9.6e-2F, 1.0e-8F, label);
                require_rate(
                    rates.position, native_rates.position,
                    1.0e-9F, "geometry candidate position isolation");
                require_rate(
                    rates.scale,
                    use_lf_scale
                        ? initial_rates.scale
                        : native_rates.scale,
                    1.0e-8F, "geometry candidate scale");
                require_rate(
                    rates.rotation,
                    use_lf_rotation
                        ? initial_rates.rotation
                        : native_rates.rotation,
                    1.0e-8F, "geometry candidate rotation");
                require_rate(
                    rates.scale_epsilon,
                    use_lf_scale ? 1.0e-15F : 1.0e-8F,
                    1.0e-12F, "geometry candidate scale epsilon");
                require_rate(
                    rates.rotation_epsilon,
                    use_lf_rotation ? 1.0e-15F : 1.0e-8F,
                    1.0e-12F, "geometry candidate rotation epsilon");
                require_rate(
                    rates.dc_epsilon, 1.0e-15F,
                    1.0e-20F, "geometry candidate DC epsilon");
                require_rate(
                    rates.opacity_epsilon, 1.0e-15F,
                    1.0e-20F, "geometry candidate opacity epsilon");
            };
        require_geometry_candidate(
            dronegs::MrnfOptimizerProfile::dev34_opacity096_lf_scale,
            true, false, "dev34 scale");
        require_geometry_candidate(
            dronegs::MrnfOptimizerProfile::dev34_opacity096_lf_rotation,
            false, true, "dev34 rotation");
        require_geometry_candidate(
            dronegs::MrnfOptimizerProfile::
                dev34_opacity096_lf_scale_rotation,
            true, true, "dev34 scale rotation");
        dronegs::OrderedAlphaTrainingContext scale_only_context(
            rate_fixture, 32U * 32U, 2U, 8U,
            dronegs::MrnfOptimizerProfile::lichtfeld_scale_only);
        const auto scale_only_rates =
            scale_only_context.current_learning_rates();
        require_rate(
            scale_only_rates.scale, initial_rates.scale,
            1.0e-8F, "scale-only scale");
        require_rate(
            scale_only_rates.scale_epsilon, 1.0e-15F,
            1.0e-20F, "scale-only epsilon");
        require_rate(
            scale_only_rates.position, native_rates.position,
            1.0e-9F, "scale-only position isolation");
        dronegs::OrderedAlphaTrainingContext rotation_only_context(
            rate_fixture, 32U * 32U, 2U, 8U,
            dronegs::MrnfOptimizerProfile::lichtfeld_rotation_only);
        const auto rotation_only_rates =
            rotation_only_context.current_learning_rates();
        require_rate(
            rotation_only_rates.rotation, initial_rates.rotation,
            1.0e-8F, "rotation-only rotation");
        require_rate(
            rotation_only_rates.rotation_epsilon, 1.0e-15F,
            1.0e-20F, "rotation-only epsilon");
        require_rate(
            rotation_only_rates.scale, native_rates.scale,
            1.0e-8F, "rotation-only scale isolation");
        static_cast<void>(rate_context.train_step(
            quality_camera, split_target.data(), split_target.size()));
        const auto first_telemetry =
            rate_context.latest_optimizer_telemetry();
        if (!first_telemetry.has_value() ||
            first_telemetry->step != 1U ||
            first_telemetry->dc.samples == 0U ||
            first_telemetry->position.samples == 0U ||
            !std::isfinite(first_telemetry->dc.gradient_rms) ||
            !std::isfinite(first_telemetry->dc.update_rms) ||
            !std::isfinite(first_telemetry->dc.parameter_rms) ||
            first_telemetry->dc.update_rms <= 0.0F) {
            throw std::runtime_error(
                "MRNF optimizer telemetry mismatch");
        }
        const auto first_step_rates =
            rate_context.current_learning_rates();
        require_rate(
            first_step_rates.position,
            initial_rates.position, 1.0e-12F, "first position");
        require_rate(
            first_step_rates.scale,
            initial_rates.scale, 1.0e-12F, "first scale");
        static_cast<void>(rate_context.train_step(
            quality_camera, split_target.data(), split_target.size()));
        const auto second_step_rates =
            rate_context.current_learning_rates();
        require_rate(
            second_step_rates.position, 2.4e-7F,
            1.0e-11F, "decayed position");
        require_rate(
            second_step_rates.scale,
            std::sqrt(7.0e-3F * 5.0e-3F),
            1.0e-8F, "decayed scale");
        dronegs::OrderedAlphaTrainingContext split_context(
            {split_parent}, 32U * 32U, 2U, 2U);
        static_cast<void>(split_context.train_step(
            quality_camera, split_target.data(), split_target.size()));
        const auto refinement =
            split_context.refine_topology(0.0F, 1.0F);
        std::vector<dronegs::Gaussian> split_gaussians;
        split_context.download(split_gaussians);
        if (refinement.candidates != 1U ||
            refinement.added != 1U ||
            refinement.gaussian_count != 2U ||
            split_context.size() != 2U ||
            split_gaussians.size() != 2U) {
            throw std::runtime_error(
                "deterministic topology growth count mismatch");
        }
        const auto& first_split = split_gaussians[0];
        const auto& second_split = split_gaussians[1];
        double split_distance_squared = 0.0;
        for (std::size_t axis = 0U; axis < 3U; ++axis) {
            const double difference =
                static_cast<double>(first_split.xyz[axis]) -
                second_split.xyz[axis];
            split_distance_squared += difference * difference;
            if (std::abs(
                    first_split.log_scale[axis] -
                    second_split.log_scale[axis]) > 1.0e-6F ||
                std::abs(
                    first_split.dc[axis] -
                    second_split.dc[axis]) > 1.0e-6F) {
                throw std::runtime_error(
                    "long-axis split child parameter mismatch");
            }
        }
        const auto longest_axis = static_cast<std::size_t>(
            std::max_element(
                first_split.log_scale.begin(),
                first_split.log_scale.end()) -
            first_split.log_scale.begin());
        const double expected_split_distance =
            2.0 * std::exp(first_split.log_scale[longest_axis]) /
            0.999F;
        if (std::abs(
                std::sqrt(split_distance_squared) -
                expected_split_distance) > 2.0e-5 ||
            std::abs(
                first_split.opacity_logit -
                second_split.opacity_logit) > 1.0e-6F) {
            throw std::runtime_error(
                "long-axis split geometry/opacity mismatch");
        }
        const auto empty_refinement =
            split_context.refine_topology(0.0F, 1.0F);
        if (empty_refinement.candidates != 0U ||
            empty_refinement.added != 0U ||
            empty_refinement.gaussian_count != 2U) {
            throw std::runtime_error(
                "topology statistics did not reset after refinement");
        }
        auto pruned_parent = split_parent;
        pruned_parent.opacity_logit = -20.0F;
        dronegs::OrderedAlphaTrainingContext reuse_context(
            {split_parent, pruned_parent},
            32U * 32U, 2U, 2U);
        static_cast<void>(reuse_context.train_step(
            quality_camera, split_target.data(), split_target.size()));
        const auto reuse_refinement =
            reuse_context.refine_topology(0.0F, 1.0F, 7U);
        if (reuse_refinement.pruned != 1U ||
            reuse_refinement.added != 1U ||
            reuse_refinement.reused != 1U ||
            reuse_refinement.appended != 0U ||
            reuse_refinement.gaussian_count != 2U) {
            throw std::runtime_error(
                "MRNF prune/compact/reuse count mismatch");
        }
        std::vector<dronegs::Gaussian> gumbel_parents(8U, split_parent);
        for (std::size_t index = 0U;
             index < gumbel_parents.size(); ++index) {
            gumbel_parents[index].xyz[0] +=
                0.002F * static_cast<float>(index);
            gumbel_parents[index].dc[0] =
                -0.05F + 0.01F * static_cast<float>(index);
        }
        dronegs::OrderedAlphaTrainingContext seeded_first(
            gumbel_parents, 32U * 32U, 2U, 10U);
        dronegs::OrderedAlphaTrainingContext seeded_repeat(
            gumbel_parents, 32U * 32U, 2U, 10U);
        dronegs::OrderedAlphaTrainingContext seeded_other(
            gumbel_parents, 32U * 32U, 2U, 10U);
        static_cast<void>(seeded_first.train_step(
            quality_camera, split_target.data(), split_target.size()));
        static_cast<void>(seeded_repeat.train_step(
            quality_camera, split_target.data(), split_target.size()));
        static_cast<void>(seeded_other.train_step(
            quality_camera, split_target.data(), split_target.size()));
        const auto seeded_first_result =
            seeded_first.refine_topology(0.0F, 0.25F, 42U);
        const auto seeded_repeat_result =
            seeded_repeat.refine_topology(0.0F, 0.25F, 42U);
        const auto seeded_other_result =
            seeded_other.refine_topology(0.0F, 0.25F, 43U);
        std::vector<dronegs::Gaussian> seeded_first_gaussians;
        std::vector<dronegs::Gaussian> seeded_repeat_gaussians;
        std::vector<dronegs::Gaussian> seeded_other_gaussians;
        seeded_first.download(seeded_first_gaussians);
        seeded_repeat.download(seeded_repeat_gaussians);
        seeded_other.download(seeded_other_gaussians);
        const auto geometry_matches = [](
            const std::vector<dronegs::Gaussian>& left,
            const std::vector<dronegs::Gaussian>& right) {
            if (left.size() != right.size()) {
                return false;
            }
            for (std::size_t index = 0U;
                 index < left.size(); ++index) {
                for (std::size_t axis = 0U; axis < 3U; ++axis) {
                    if (left[index].xyz[axis] !=
                            right[index].xyz[axis] ||
                        left[index].log_scale[axis] !=
                            right[index].log_scale[axis]) {
                        return false;
                    }
                }
            }
            return true;
        };
        if (seeded_first_result.candidates != 8U ||
            seeded_first_result.added != 2U ||
            seeded_repeat_result.candidates != 8U ||
            seeded_repeat_result.added != 2U ||
            !geometry_matches(
                seeded_first_gaussians,
                seeded_repeat_gaussians)) {
            throw std::runtime_error(
                "weighted Gumbel selection is not reproducible");
        }
        if (seeded_other_result.candidates != 8U ||
            seeded_other_result.added != 2U ||
            geometry_matches(
                seeded_first_gaussians,
                seeded_other_gaussians)) {
            throw std::runtime_error(
                "weighted Gumbel selection ignored its seed");
        }
        auto additive_gaussians = initialized;
        auto ordered_initial = initialized;
        ordered_initial.front().log_scale[0] += std::log(1.7F);
        ordered_initial.front().log_scale[1] += std::log(0.65F);
        constexpr float rotation_w = 0.9659258263F;
        constexpr float rotation_z = 0.2588190451F;
        ordered_initial.front().rotation = {
            rotation_w, 0.0F, 0.0F, rotation_z};
        auto ordered_gaussians = ordered_initial;
        const dronegs::Options options{
            .data_path = root,
            .output_path = root.parent_path() / "unused-output",
            .run_manifest = root.parent_path() / "unused-output" / "trainer_run.json",
            .iterations = 30U,
            .strategy = "mrnf",
            .sh_degree = 0U,
            .max_cap = 100U,
            .resize_factor = 1U,
            .max_width = 32U,
            .tile_mode = 1U,
            .seed = 7U,
        };
        const auto additive_metrics = dronegs::train_fixed_topology(
            options, scene, additive_gaussians);
        const auto ordered_metrics =
            dronegs::train_ordered_mrnf(
                options, scene, ordered_gaussians);
        if (!(additive_metrics.final_loss <
              additive_metrics.initial_loss * 0.95F)) {
            throw std::runtime_error(
                "additive fixed-topology control did not reduce anchor loss");
        }
        if (!(ordered_metrics.final_loss <
              ordered_metrics.initial_loss * 0.95F)) {
            throw std::runtime_error(
                "ordered fixed-topology training did not reduce anchor loss");
        }
        if (additive_metrics.iterations != options.iterations ||
            ordered_metrics.iterations != options.iterations ||
            additive_metrics.training_seconds <= 0.0 ||
            ordered_metrics.training_seconds <= 0.0) {
            throw std::runtime_error("invalid fixed-topology training metrics");
        }
        float maximum_position_delta = 0.0F;
        float maximum_scale_delta = 0.0F;
        float maximum_rotation_delta = 0.0F;
        for (std::size_t gaussian = 0U;
             gaussian < ordered_gaussians.size(); ++gaussian) {
            float quaternion_norm_squared = 0.0F;
            for (std::size_t axis = 0U; axis < 3U; ++axis) {
                maximum_position_delta = std::max(
                    maximum_position_delta,
                    std::abs(
                        ordered_gaussians[gaussian].xyz[axis] -
                        ordered_initial[gaussian].xyz[axis]));
                maximum_scale_delta = std::max(
                    maximum_scale_delta,
                    std::abs(
                        ordered_gaussians[gaussian].log_scale[axis] -
                        ordered_initial[gaussian].log_scale[axis]));
                if (!std::isfinite(
                        ordered_gaussians[gaussian].xyz[axis]) ||
                    !std::isfinite(
                        ordered_gaussians[gaussian].log_scale[axis])) {
                    throw std::runtime_error(
                        "ordered geometry update produced non-finite values");
                }
            }
            for (std::size_t component = 0U;
                 component < 4U; ++component) {
                maximum_rotation_delta = std::max(
                    maximum_rotation_delta,
                    std::abs(
                        ordered_gaussians[gaussian].rotation[component] -
                        ordered_initial[gaussian].rotation[component]));
                quaternion_norm_squared +=
                    ordered_gaussians[gaussian].rotation[component] *
                    ordered_gaussians[gaussian].rotation[component];
            }
            if (std::abs(std::sqrt(quaternion_norm_squared) - 1.0F) >
                2.0e-5F) {
                throw std::runtime_error(
                    "ordered geometry update did not normalize rotation");
            }
        }
        if (maximum_position_delta <= 1.0e-7F ||
            maximum_scale_delta <= 1.0e-7F ||
            maximum_rotation_delta <= 1.0e-7F) {
            throw std::runtime_error(
                "ordered geometry parameters did not all update");
        }
        std::filesystem::remove_all(root);
        std::cout
            << "DroneGS ordered MRNF-growth convergence test passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::filesystem::remove_all(root);
        std::cerr
                  << "DroneGS ordered MRNF-growth convergence test failed: "
                  << error.what() << "\n";
        return 1;
    }
}
