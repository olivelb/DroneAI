// SPDX-License-Identifier: MIT
#include "dronegs/topology_percentiles.hpp"
#include "dronegs/training.hpp"

#include <algorithm>
#include <array>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace {
void same_bits(float actual, float expected) {
    if (std::bit_cast<std::uint32_t>(actual) != std::bit_cast<std::uint32_t>(expected)) {
        throw std::runtime_error("bounded CPU percentiles changed exact legacy bits");
    }
}

void compare(std::size_t count, unsigned pattern) {
    std::array<std::vector<float>, 3U> coordinates;
    std::vector<float> scales;
    std::uint32_t random = 42U;
    for (std::size_t i = 0U; i < count; ++i) {
        random = random * 1664525U + 1013904223U;
        float value = static_cast<float>(random % 100003U);
        if (pattern == 1U) value = static_cast<float>(i);
        if (pattern == 2U) value = static_cast<float>(count - i);
        if (pattern == 3U) value = i % 2U == 0U ? 0.0F : -0.0F;
        if (pattern == 4U) value = 7.0F;
        coordinates[0].push_back(value);
        if (pattern != 5U) coordinates[1].push_back(-value);
        if (pattern != 5U || i % 3U == 0U) coordinates[2].push_back(value * 0.5F);
        scales.push_back(value);
    }
    const auto original_scales = scales;
    const auto actual = dronegs::compute_pruning_percentiles(coordinates, scales);
    for (std::size_t axis = 0U; axis < coordinates.size(); ++axis) {
        const auto expected = dronegs::exact_floor_percentile_pair(coordinates[axis], 0.1F, 0.9F);
        same_bits(actual.coordinates[axis].first, expected.first);
        same_bits(actual.coordinates[axis].second, expected.second);
        // Independent sorted oracle checks numerical floor-rank semantics.
        auto sorted = coordinates[axis];
        std::sort(sorted.begin(), sorted.end());
        if (!sorted.empty()) {
            const auto lower = static_cast<std::size_t>(static_cast<float>(sorted.size() - 1U) * 0.1F);
            const auto upper = static_cast<std::size_t>(static_cast<float>(sorted.size() - 1U) * 0.9F);
            if (actual.coordinates[axis].first != sorted[lower] || actual.coordinates[axis].second != sorted[upper]) {
                throw std::runtime_error("CPU pruning percentiles differ from sorted ranks");
            }
        }
    }
    same_bits(actual.scale80, dronegs::exact_floor_percentile(scales, 0.8F));
    for (std::size_t i = 0U; i < scales.size(); ++i) same_bits(scales[i], original_scales[i]);
}
}  // namespace

void test_topology_percentiles() {
    constexpr auto threshold = dronegs::parallel_percentile_minimum_values;
    for (const auto count : {0U, 1U, 2U, 7U, 8U, 17U, 257U}) {
        for (unsigned pattern = 0U; pattern < 6U; ++pattern) compare(count, pattern);
    }
    for (const auto count : {threshold - 1U, threshold, threshold + 1U}) {
        for (unsigned pattern = 0U; pattern < 6U; ++pattern) compare(count, pattern);
    }
}
