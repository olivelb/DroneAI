// SPDX-License-Identifier: MIT
#include <array>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <iostream>
#include <stdexcept>

#include <jpeglib.h>

#include "dronegs/model.hpp"
#include "dronegs/ordered_training.hpp"
#include "dronegs/training.hpp"

namespace {

void write_fixture_image(const std::filesystem::path& path) {
    auto* file = std::fopen(path.string().c_str(), "wb");
    if (file == nullptr) {
        throw std::runtime_error("cannot create tile training fixture");
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
    for (std::size_t x = 0U; x < 32U; ++x) {
        row[x * 3U] = static_cast<JSAMPLE>(x * 7U);
        row[x * 3U + 1U] = 180U;
        row[x * 3U + 2U] = static_cast<JSAMPLE>(255U - x * 7U);
    }
    while (compressor.next_scanline < compressor.image_height) {
        auto* pointer = row.data();
        static_cast<void>(jpeg_write_scanlines(&compressor, &pointer, 1U));
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
        .name = "tile.jpg",
        .qvec = {1.0, 0.0, 0.0, 0.0},
        .tvec = {0.0, 0.0, 0.0},
        .source_x = 4U,
        .source_y = 4U,
        .source_width = 24U,
        .source_height = 24U,
    });
    std::uint64_t id = 1U;
    for (int y = -3; y <= 3; ++y) {
        for (int x = -3; x <= 3; ++x) {
            scene.points.push_back({
                .id = id++,
                .xyz = {
                    static_cast<double>(x) * 0.15,
                    static_cast<double>(y) * 0.15,
                    2.0,
                },
                .rgb = {128U, 180U, 128U},
            });
        }
    }
    return scene;
}

dronegs::Scene make_corner_supported_scene() {
    auto scene = make_scene();
    scene.points.clear();
    std::uint64_t id = 1U;
    for (int y = 0; y < 3; ++y) {
        for (int x = 0; x < 3; ++x) {
            scene.points.push_back({
                .id = id++,
                .xyz = {
                    -0.45 + static_cast<double>(x) * 0.03,
                    -0.45 + static_cast<double>(y) * 0.03,
                    2.0,
                },
                .rgb = {128U, 180U, 128U},
            });
        }
    }
    return scene;
}

}  // namespace

int main() {
    const auto suffix =
        std::chrono::steady_clock::now().time_since_epoch().count();
    const auto root = std::filesystem::temp_directory_path() /
        ("dronegs-tile-training-test-" + std::to_string(suffix));
    try {
        std::filesystem::create_directories(root / "images");
        write_fixture_image(root / "images" / "tile.jpg");
        auto gaussians = dronegs::initialize_fixed_topology(make_scene());
        dronegs::Options options;
        options.data_path = root;
        options.output_path = root / "output";
        options.run_manifest = root / "output" / "trainer_run.json";
        options.iterations = 2U;
        options.strategy = "mrnf";
        options.sh_degree = 1U;
        options.sh_degree_interval = 1U;
        options.max_cap = 100U;
        options.resize_factor = 1U;
        options.max_width = 32U;
        options.tile_mode = 4U;
        options.seed = 17U;
        options.checkpoint_every = 1U;
        options.checkpoint_path = root / "training.ckpt";
        if (options.tile_mode != 4U) {
            throw std::runtime_error(
                "tile training fixture options were not initialized");
        }
        const auto metrics = dronegs::train_ordered_mrnf(
            options, make_scene(), gaussians);
        if (metrics.completed_iterations != 2U ||
            metrics.training_image_count != 1U ||
            metrics.held_out_image_count != 0U ||
            metrics.frame_descriptor_count != 4U ||
            metrics.training_frame_count != 4U ||
            metrics.held_out_frame_count != 0U ||
            metrics.image_cache_misses < 2U ||
            metrics.periodic_checkpoints != 2U ||
            !std::filesystem::is_regular_file(
                options.checkpoint_path) ||
            std::filesystem::exists(
                options.checkpoint_path.string() + ".tmp")) {
            throw std::runtime_error(
                "four-tile MRNF training metrics mismatch: completed=" +
                std::to_string(metrics.completed_iterations) +
                " training_images=" +
                std::to_string(metrics.training_image_count) +
                " held_out_images=" +
                std::to_string(metrics.held_out_image_count) +
                " frame_descriptors=" +
                std::to_string(metrics.frame_descriptor_count) +
                " training_frames=" +
                std::to_string(metrics.training_frame_count) +
                " cache_misses=" +
                std::to_string(metrics.image_cache_misses));
        }
        auto corner_gaussians = dronegs::initialize_fixed_topology(
            make_corner_supported_scene());
        auto corner_options = options;
        corner_options.output_path = root / "corner-output";
        corner_options.run_manifest =
            corner_options.output_path / "trainer_run.json";
        corner_options.iterations = 1U;
        const auto corner_metrics = dronegs::train_ordered_mrnf(
            corner_options, make_corner_supported_scene(),
            corner_gaussians);
        if (corner_metrics.completed_iterations != 1U ||
            corner_metrics.image_cache_misses != 1U) {
            throw std::runtime_error(
                "unsupported training tiles were not removed: misses=" +
                std::to_string(corner_metrics.image_cache_misses));
        }
        bool learned_directional_opacity = false;
        for (const auto& gaussian : gaussians) {
            for (std::size_t coefficient = 0U;
                 coefficient < 3U; ++coefficient) {
                learned_directional_opacity =
                    learned_directional_opacity ||
                    std::abs(gaussian.opacity_sh[coefficient]) > 1.0e-9F;
            }
        }
        if (!learned_directional_opacity) {
            throw std::runtime_error(
                "opacity-SH coefficients were not trained");
        }

        const auto checkpoint = root / "opacity-sh-v4.ckpt";
        dronegs::OrderedAlphaTrainingContext checkpoint_source(
            gaussians, 16U * 16U, 2U, 100U,
            dronegs::MrnfOptimizerProfile::dronegs_dev16,
            1U, 1U, 17U);
        const dronegs::TrainingCheckpointProgress saved_progress{};
        checkpoint_source.save_checkpoint(
            checkpoint, saved_progress, "tile-dataset", "tile-config");
        dronegs::OrderedAlphaTrainingContext checkpoint_restored(
            gaussians, 16U * 16U, 2U, 100U,
            dronegs::MrnfOptimizerProfile::dronegs_dev16,
            1U, 1U, 17U);
        const auto loaded_progress = checkpoint_restored.load_checkpoint(
            checkpoint, "tile-dataset", "tile-config");
        if (loaded_progress.completed_iteration != 0U ||
            checkpoint_restored.size() != gaussians.size()) {
            throw std::runtime_error(
                "opacity-SH checkpoint v4 round-trip mismatch");
        }
        std::filesystem::remove_all(root);
        std::cout << "DroneGS tile training test passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "DroneGS tile training test failed: "
                  << error.what() << '\n';
        std::filesystem::remove_all(root);
        return 1;
    }
}
