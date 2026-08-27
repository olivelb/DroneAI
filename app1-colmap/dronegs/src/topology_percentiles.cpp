// SPDX-License-Identifier: MIT
#include "dronegs/topology_percentiles.hpp"
#include "dronegs/training.hpp"

#include <array>
#include <future>
#include <thread>
#include <utility>

namespace dronegs {

PruningPercentiles compute_pruning_percentiles(
    std::array<std::vector<float>, 3U> coordinates,
    const std::vector<float>& maximum_scales) {
    PruningPercentiles result;
    const bool parallel = maximum_scales.size() >= parallel_percentile_minimum_values &&
                          std::thread::hardware_concurrency() >= 4U;
    // Futures are destroyed before the captured coordinates, including on
    // exceptions. Each task owns one disjoint vector; no reductions are shared.
    std::array<std::future<std::pair<float, float>>, 3U> pending;
    for (std::size_t axis = 0U; axis < coordinates.size(); ++axis) {
        const auto calculate = [&coordinates, axis]() {
            return exact_floor_percentile_pair(std::move(coordinates[axis]), 0.1F, 0.9F);
        };
        if (parallel && coordinates[axis].size() >= parallel_percentile_minimum_values) {
            // Permit a sequential fallback if the runtime cannot start a thread.
            pending[axis] = std::async(std::launch::async | std::launch::deferred, calculate);
        } else {
            result.coordinates[axis] = calculate();
        }
    }
    result.scale80 = exact_floor_percentile(maximum_scales, 0.8F);
    for (std::size_t axis = 0U; axis < pending.size(); ++axis) {
        if (pending[axis].valid()) result.coordinates[axis] = pending[axis].get();
    }
    return result;
}

}  // namespace dronegs
