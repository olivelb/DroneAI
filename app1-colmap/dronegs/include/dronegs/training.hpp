// SPDX-License-Identifier: MIT
#pragma once

#include <cstdint>
#include <vector>

#include "dronegs/types.hpp"

namespace dronegs {

struct TrainingMetrics {
    float initial_loss = 0.0F;
    float final_loss = 0.0F;
    std::uint64_t iterations = 0;
    double image_loading_seconds = 0.0;
    double image_decode_seconds = 0.0;
    double setup_seconds = 0.0;
    double training_seconds = 0.0;
    std::uint64_t image_cache_hits = 0;
    std::uint64_t image_cache_misses = 0;
    std::uint64_t image_cache_evictions = 0;
    std::uint64_t image_cache_capacity_bytes = 0;
    std::uint64_t peak_image_cache_bytes = 0;
    std::uint64_t image_prefetch_started = 0;
    std::uint64_t image_prefetch_consumed = 0;
    std::uint64_t image_prefetch_ready = 0;
};

TrainingMetrics train_fixed_topology(const Options& options, const Scene& scene,
                                     std::vector<Gaussian>& gaussians);

}  // namespace dronegs
