// SPDX-License-Identifier: MIT
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <exception>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include "dronegs/rasterization.hpp"

namespace {

std::vector<dronegs::Gaussian> make_scene(
    std::size_t gaussian_count, const dronegs::RasterCamera& camera) {
    std::vector<dronegs::Gaussian> gaussians(gaussian_count);
    const float focal = 0.5F * (camera.fx + camera.fy);
    for (std::size_t index = 0; index < gaussian_count; ++index) {
        const auto pixel_x =
            static_cast<std::uint32_t>(index % camera.width);
        const auto pixel_y = static_cast<std::uint32_t>(
            (index / camera.width) % camera.height);
        const float depth =
            1.2F + static_cast<float>(index % 1024U) * 0.001F;
        const float projected_sigma =
            1.57F + static_cast<float>(index % 5U) * 0.23F;
        const float world_sigma = projected_sigma * depth / focal;
        auto& gaussian = gaussians[index];
        gaussian.xyz = {
            ((static_cast<float>(pixel_x) + 0.5F) - camera.cx) *
                depth / camera.fx,
            ((static_cast<float>(pixel_y) + 0.5F) - camera.cy) *
                depth / camera.fy,
            depth,
        };
        gaussian.dc = {
            static_cast<float>(index % 17U) / 17.0F,
            static_cast<float>(index % 23U) / 23.0F,
            static_cast<float>(index % 29U) / 29.0F,
        };
        gaussian.log_scale = {
            std::log(world_sigma),
            std::log(world_sigma),
            std::log(world_sigma),
        };
        gaussian.opacity_logit = -1.0F;
    }
    return gaussians;
}

double render_milliseconds(
    const std::vector<dronegs::Gaussian>& gaussians,
    const dronegs::RasterCamera& camera,
    dronegs::AlphaRenderStats& stats) {
    const auto start = std::chrono::steady_clock::now();
    const auto output =
        dronegs::render_alpha_tiled_cuda(gaussians, camera);
    const auto end = std::chrono::steady_clock::now();
    stats = output.stats;
    return std::chrono::duration<double, std::milli>(end - start).count();
}

}  // namespace

int main(int argc, char** argv) {
    try {
        const std::size_t gaussian_count =
            argc > 1 ? std::stoull(argv[1]) : 1'025'093U;
        const std::size_t repeat_count =
            argc > 2 ? std::stoull(argv[2]) : 5U;
        if (gaussian_count == 0U || repeat_count == 0U) {
            throw std::invalid_argument(
                "Gaussian and repeat counts must be positive");
        }
        const dronegs::RasterCamera camera{
            .fx = 620.0F,
            .fy = 615.0F,
            .cx = 400.0F,
            .cy = 290.0F,
            .width = 800U,
            .height = 580U,
        };
        const auto gaussians = make_scene(gaussian_count, camera);
        dronegs::AlphaRenderStats stats{};
        const double warmup_ms =
            render_milliseconds(gaussians, camera, stats);
        std::vector<double> runs;
        runs.reserve(repeat_count);
        for (std::size_t repeat = 0; repeat < repeat_count; ++repeat) {
            runs.push_back(
                render_milliseconds(gaussians, camera, stats));
        }
        auto sorted = runs;
        std::sort(sorted.begin(), sorted.end());
        const double median_ms =
            sorted[sorted.size() / 2U];

        std::cout << std::fixed << std::setprecision(3)
                  << "{\"gaussians\":" << gaussian_count
                  << ",\"width\":" << camera.width
                  << ",\"height\":" << camera.height
                  << ",\"warmup_ms\":" << warmup_ms
                  << ",\"runs_ms\":[";
        for (std::size_t index = 0; index < runs.size(); ++index) {
            if (index != 0U) {
                std::cout << ',';
            }
            std::cout << runs[index];
        }
        std::cout << "],\"median_ms\":" << median_ms
                  << ",\"visible_splats\":" << stats.visible_splats
                  << ",\"evaluated_pairs\":" << stats.evaluated_pairs
                  << ",\"contributing_pairs\":"
                  << stats.contributing_pairs << "}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "DroneGS raster benchmark failed: "
                  << error.what() << '\n';
        return 1;
    }
}
