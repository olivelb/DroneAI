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
    double setup_seconds = 0.0;
    double training_seconds = 0.0;
};

TrainingMetrics train_fixed_topology(const Options& options, const Scene& scene,
                                     std::vector<Gaussian>& gaussians);

}  // namespace dronegs
