// SPDX-License-Identifier: MIT
#pragma once

#include <array>
#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

namespace dronegs {

struct Options {
    std::filesystem::path data_path;
    std::filesystem::path output_path;
    std::filesystem::path run_manifest;
    std::uint64_t iterations = 0;
    std::string strategy;
    std::uint32_t sh_degree = 0;
    std::uint64_t max_cap = 0;
    std::uint32_t resize_factor = 0;
    std::uint32_t max_width = 0;
    std::uint32_t tile_mode = 0;
    std::uint64_t seed = 0;
    std::uint32_t prefetch_depth = 1;
    std::uint32_t decode_workers = 1;
    std::uint32_t jpeg_idct_scale = 0;
    std::uint32_t test_every = 0;
    std::uint32_t save_eval_images = 0;
};

struct Camera {
    std::uint32_t id = 0;
    std::int32_t model_id = 0;
    std::uint64_t width = 0;
    std::uint64_t height = 0;
    std::vector<double> parameters;
};

struct Image {
    std::uint32_t id = 0;
    std::uint32_t camera_id = 0;
    std::string name;
    std::array<double, 4> qvec{};
    std::array<double, 3> tvec{};
};

struct SparsePoint {
    std::uint64_t id = 0;
    std::array<double, 3> xyz{};
    std::array<std::uint8_t, 3> rgb{};
};

struct Scene {
    std::vector<Camera> cameras;
    std::vector<Image> images;
    std::vector<SparsePoint> points;
};

struct Gaussian {
    std::array<float, 3> xyz{};
    std::array<float, 3> dc{};
    std::array<float, 3> log_scale{};
    std::array<float, 4> rotation{1.0F, 0.0F, 0.0F, 0.0F};
    float opacity_logit = 0.0F;
};

}  // namespace dronegs
