// SPDX-License-Identifier: MIT
#pragma once
#include <cstdint>
#include "dronegs/types.hpp"

namespace dronegs {
struct TrainingStepBenchmarkOptions {
    std::uint32_t warmups = 1;
    std::uint32_t repeats = 5;
    std::uint32_t views = 3;
};
// Removes benchmark-only arguments; the remaining arguments use the normal CLI contract.
TrainingStepBenchmarkOptions parse_training_step_benchmark_options(int& argc, char** argv);
void benchmark_training_steps(const Options& options, const Scene& scene,
    std::vector<Gaussian>& initial_gaussians, const TrainingStepBenchmarkOptions& benchmark);
} // namespace dronegs
