// SPDX-License-Identifier: MIT
#include "dronegs/topology_percentiles.hpp"
#include "dronegs/training.hpp"

#include <array>
#include <bit>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

int main(int argc, char** argv) {
    try {
        if (argc != 3) throw std::invalid_argument("usage: benchmark COUNT REPEATS");
        const auto count = std::stoull(argv[1]);
        const auto repeats = std::stoull(argv[2]);
        if (count == 0U || count > 10'000'000U || repeats == 0U || repeats > 100U) {
            throw std::invalid_argument("count/repeats outside benchmark bounds");
        }
        std::array<std::vector<float>, 3U> original;
        std::vector<float> scales;
        std::uint32_t random = 42U;
        for (std::size_t i = 0U; i < count; ++i) {
            for (auto& axis : original) {
                random = random * 1664525U + 1013904223U;
                axis.push_back(static_cast<float>(random % 1'000'003U));
            }
            scales.push_back(original[0].back() * 0.001F);
        }
        std::cout << std::setprecision(10);
        for (std::uint64_t repeat = 0U; repeat <= repeats; ++repeat) {
            std::array<dronegs::PruningPercentiles, 2U> results;
            std::array<double, 2U> seconds{};
            for (std::size_t order = 0U; order < 2U; ++order) {
                const auto arm = (order + repeat) % 2U;
                auto coordinates = original;  // Untimed identical input preparation.
                const auto start = std::chrono::steady_clock::now();
                if (arm == 1U) {
                    results[arm] = dronegs::compute_pruning_percentiles(std::move(coordinates), scales);
                } else {
                    for (std::size_t axis = 0U; axis < coordinates.size(); ++axis) {
                        results[arm].coordinates[axis] = dronegs::exact_floor_percentile_pair(
                            std::move(coordinates[axis]), 0.1F, 0.9F);
                    }
                    results[arm].scale80 = dronegs::exact_floor_percentile(scales, 0.8F);
                }
                seconds[arm] = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
            }
            const auto same = [](float left, float right) {
                return std::bit_cast<std::uint32_t>(left) == std::bit_cast<std::uint32_t>(right);
            };
            for (std::size_t axis = 0U; axis < original.size(); ++axis) {
                if (!same(results[0].coordinates[axis].first, results[1].coordinates[axis].first) ||
                    !same(results[0].coordinates[axis].second, results[1].coordinates[axis].second)) {
                    throw std::runtime_error("percentile benchmark bitwise parity failure");
                }
            }
            if (!same(results[0].scale80, results[1].scale80)) throw std::runtime_error("scale percentile parity failure");
            std::cout << "{\"event\":\"topology_percentile_benchmark\",\"count\":" << count
                      << ",\"repeat\":" << repeat << ",\"warmup\":" << (repeat == 0U ? "true" : "false")
                      << ",\"hardware_threads\":" << std::thread::hardware_concurrency()
                      << ",\"serial_seconds\":" << seconds[0] << ",\"bounded_seconds\":" << seconds[1]
                      << ",\"bitwise_equal\":true}\n";
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
