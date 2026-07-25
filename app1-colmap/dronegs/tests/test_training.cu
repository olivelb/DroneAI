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

float reference_ssim(
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
    return static_cast<float>(sum / static_cast<double>(count));
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
        const float expected_ssim = reference_ssim(
            quality_prediction, quality_target.rgb, 32U, 32U);
        if (std::abs(quality.psnr - expected_psnr) > 2.0e-4F ||
            std::abs(quality.ssim - expected_ssim) > 2.0e-4F ||
            quality.active_pixel_fraction <= 0.0F ||
            quality.active_pixel_fraction > 1.0F) {
            throw std::runtime_error(
                "GPU held-out PSNR/SSIM mismatch");
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
            dronegs::train_fixed_topology_ordered(
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
            << "DroneGS ordered fixed-topology convergence test passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::filesystem::remove_all(root);
        std::cerr
                  << "DroneGS ordered fixed-topology convergence test failed: "
                  << error.what() << "\n";
        return 1;
    }
}
