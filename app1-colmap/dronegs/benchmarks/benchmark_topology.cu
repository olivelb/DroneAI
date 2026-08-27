// SPDX-License-Identifier: MIT
#include "dronegs/ordered_training.hpp"

#include <cuda_runtime.h>
#include <algorithm>
#include <array>
#include <bit>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
void synchronize() {
    if (cudaDeviceSynchronize() != cudaSuccess) {
        throw std::runtime_error("topology benchmark CUDA synchronization failed");
    }
}

struct Observation {
    double wall_seconds;
    dronegs::TopologyRefinementResult result;
    dronegs::TopologyRefinementTelemetry telemetry;
    std::vector<dronegs::Gaussian> model;
};

const dronegs::RasterCamera camera{
    .fx = 120.0F, .fy = 120.0F, .cx = 64.0F, .cy = 64.0F,
    .width = 128U, .height = 128U,
};

std::vector<dronegs::Gaussian> make_scene(std::size_t count, const std::string& mode) {
    std::vector<dronegs::Gaussian> initial(count);
    for (std::size_t i = 0U; i < count; ++i) {
        auto& g = initial[i];
        const float z = 2.0F + static_cast<float>(i / 16384U) * 0.05F;
        g.xyz = {(static_cast<float>(i % 128U) + 0.5F - camera.cx) * z / camera.fx,
                 (static_cast<float>((i / 128U) % 128U) + 0.5F - camera.cy) * z / camera.fy, z};
        g.dc = {0.1F, -0.1F, 0.2F};
        const float scale = std::log(0.6F * z / camera.fx);
        g.log_scale = {scale, scale, scale};
        g.rotation = {1.0F, 0.0F, 0.0F, 0.0F};
        const bool prune = (mode == "compact" && i % 16U == 0U) ||
                           (mode == "recycle" && i % 256U == 0U);
        g.opacity_logit = prune ? -20.0F : -1.0F;
    }
    return initial;
}

auto make_context(const std::vector<dronegs::Gaussian>& initial, const std::string& mode) {
    const auto count = initial.size();
    const auto capacity = mode == "recycle" ? count : count + count / 2U;
    return std::make_unique<dronegs::OrderedAlphaTrainingContext>(
        initial, 128U * 128U, 2U, capacity,
        dronegs::MrnfOptimizerProfile::reference_absolute, 3U, 1U, 42U, true);
}

Observation measure(const std::vector<dronegs::Gaussian>& initial,
                    const std::string& mode, const std::filesystem::path& checkpoint,
                    bool instrumented) {
    auto context = make_context(initial, mode);
    static_cast<void>(context->load_checkpoint(checkpoint, "topology-synthetic-v1", mode));
    // Benchmark fences are outside production instrumentation. Wall time here
    // includes actual completion of split/decay, unlike device_submit_seconds.
    synchronize();
    Observation observation{};
    const auto start = std::chrono::steady_clock::now();
    observation.result = context->refine_topology(
        0.0F, mode == "no-growth" ? 0.0F : 1.0F, 42U, true,
        instrumented ? &observation.telemetry : nullptr);
    synchronize();
    observation.wall_seconds = std::chrono::duration<double>(
        std::chrono::steady_clock::now() - start).count();
    context->download(observation.model);
    if ((mode == "compact" && !observation.result.compacted) ||
        (mode == "recycle" && !observation.result.in_place_recycled) ||
        (mode == "no-growth" && observation.result.added != 0U)) {
        throw std::runtime_error("synthetic fixture did not exercise the requested topology path");
    }
    return observation;
}

float compare(const Observation& a, const Observation& b) {
    if (a.model.size() != b.model.size() || a.result.candidates != b.result.candidates ||
        a.result.pruned != b.result.pruned || a.result.added != b.result.added ||
        a.result.reused != b.result.reused || a.result.compacted != b.result.compacted ||
        a.result.in_place_recycled != b.result.in_place_recycled) {
        throw std::runtime_error("instrumentation changed topology decisions");
    }
    static_assert(sizeof(dronegs::Gaussian) == 74U * sizeof(float));
    float maximum_delta = 0.0F;
    for (std::size_t i = 0U; i < a.model.size(); ++i) {
        const auto left = std::bit_cast<std::array<float, 74U>>(a.model[i]);
        const auto right = std::bit_cast<std::array<float, 74U>>(b.model[i]);
        for (std::size_t j = 0U; j < left.size(); ++j) {
            if (!std::isfinite(left[j]) || !std::isfinite(right[j])) {
                throw std::runtime_error("non-finite topology benchmark model");
            }
            maximum_delta = std::max(maximum_delta, std::abs(left[j] - right[j]));
        }
    }
    if (maximum_delta > 2.0e-6F) {
        throw std::runtime_error("instrumented/uninstrumented model exceeds 2e-6 parity tolerance");
    }
    return maximum_delta;
}
}  // namespace

int main(int argc, char** argv) {
    try {
        const std::size_t count = argc > 1 ? std::stoull(argv[1]) : 32768U;
        const std::size_t repeats = argc > 2 ? std::stoull(argv[2]) : 5U;
        if (count < 256U || count > 262144U || repeats < 1U || repeats > 100U) {
            throw std::invalid_argument("population must be 256..262144 and repeats 1..100");
        }
        if (argc != 4) throw std::invalid_argument("usage: benchmark population repeats NEW_OUTPUT_DIRECTORY");
        const std::filesystem::path output = argv[3];
        if (!std::filesystem::create_directory(output)) {
            throw std::invalid_argument("benchmark output directory already exists");
        }
        std::cout << std::setprecision(10);
        for (const std::string mode : {"no-growth", "compact", "recycle"}) {
            const auto initial = make_scene(count, mode);
            const auto checkpoint = output / (mode + ".ckpt");
            {
                auto source = make_context(initial, mode);
                const std::vector<std::uint8_t> target(128U * 128U * 3U, 180U);
                static_cast<void>(source->train_step(camera, target.data(), target.size()));
                source->save_checkpoint(checkpoint,
                    dronegs::TrainingCheckpointProgress{.completed_iteration = 1U},
                    "topology-synthetic-v1", mode);
            }
            for (std::size_t repeat = 0U; repeat <= repeats; ++repeat) {
                const bool first_enabled = repeat % 2U == 0U;
                const auto first = measure(initial, mode, checkpoint, first_enabled);
                const auto second = measure(initial, mode, checkpoint, !first_enabled);
                const auto& enabled = first_enabled ? first : second;
                const auto& disabled = first_enabled ? second : first;
                const float delta = compare(enabled, disabled);
                std::cout << "{\"event\":\"topology_benchmark\",\"mode\":\"" << mode
                          << "\",\"population\":" << count << ",\"repeat\":" << repeat
                          << ",\"warmup\":" << (repeat == 0U ? "true" : "false")
                          << ",\"instrumented_first\":" << (first_enabled ? "true" : "false")
                          << ",\"enabled_wall_seconds\":" << enabled.wall_seconds
                          << ",\"disabled_wall_seconds\":" << disabled.wall_seconds
                          << ",\"maximum_parameter_delta\":" << delta
                          << ",\"candidates\":" << enabled.result.candidates
                          << ",\"added\":" << enabled.result.added
                          << ",\"pruned\":" << enabled.result.pruned
                          << ",\"telemetry\":";
                dronegs::write_topology_telemetry(std::cout, enabled.telemetry);
                std::cout << "}\n" << std::flush;
            }
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
