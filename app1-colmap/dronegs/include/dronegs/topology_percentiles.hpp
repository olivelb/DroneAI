// SPDX-License-Identifier: MIT
#pragma once

#include <array>
#include <cstddef>
#include <utility>
#include <vector>

namespace dronegs {

struct PruningPercentiles {
    std::array<std::pair<float, float>, 3U> coordinates{};
    float scale80 = 0.0F;
};

inline constexpr std::size_t parallel_percentile_minimum_values = 262144U;

// Consume the per-axis scratch vectors, but preserve scales in source order
// for the subsequent per-Gaussian pruning pass. Uses the unchanged exact
// floor-index helpers, at most three extra CPU threads, and no CUDA calls.
PruningPercentiles compute_pruning_percentiles(
    std::array<std::vector<float>, 3U> coordinates,
    const std::vector<float>& maximum_scales);

}  // namespace dronegs
