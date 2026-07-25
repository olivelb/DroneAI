// SPDX-License-Identifier: MIT
#include <cuda_runtime.h>

#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <vector>

#include <jpeglib.h>

#include "dronegs/model.hpp"
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

}  // namespace

int main() {
    const auto suffix = std::chrono::steady_clock::now().time_since_epoch().count();
    const auto root = std::filesystem::temp_directory_path() /
                      ("dronegs-training-test-" + std::to_string(suffix));
    try {
        std::filesystem::create_directories(root / "images");
        write_solid_jpeg(root / "images" / "frame.jpg");
        const auto scene = make_scene();
        const auto initialized =
            dronegs::initialize_fixed_topology(scene);
        auto additive_gaussians = initialized;
        auto ordered_gaussians = initialized;
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
