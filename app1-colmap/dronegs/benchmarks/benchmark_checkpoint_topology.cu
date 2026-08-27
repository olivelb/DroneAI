// SPDX-License-Identifier: MIT
#include "dronegs/ordered_training.hpp"

#include <cuda_runtime.h>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
void check(cudaError_t result) {
    if (result != cudaSuccess) throw std::runtime_error(cudaGetErrorString(result));
}

// Benchmark-only bootstrap: read bounded metadata to create a compatible
// context. The production loader still verifies the checksum, identities,
// complete payload and runtime state before any measured operation.
struct Metadata {
    std::string dataset;
    std::string configuration;
    std::uint64_t maximum_steps;
    std::uint64_t seed;
    std::uint64_t count;
    std::uint32_t maximum_sh;
    std::uint32_t sh_interval;
    std::uint32_t profile;
    bool fastgs;
};

template <typename T> T read(std::istream& stream) {
    T value{};
    if (!stream.read(reinterpret_cast<char*>(&value), sizeof(value))) {
        throw std::runtime_error("truncated checkpoint metadata");
    }
    return value;
}

std::string read_string(std::istream& stream) {
    const auto size = read<std::uint64_t>(stream);
    if (size > 1'048'576U) throw std::runtime_error("oversized checkpoint identity");
    std::string value(static_cast<std::size_t>(size), '\0');
    if (!stream.read(value.data(), static_cast<std::streamsize>(size))) {
        throw std::runtime_error("truncated checkpoint identity");
    }
    return value;
}

Metadata inspect(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary);
    const auto magic = read<std::array<char, 16U>>(stream);
    constexpr std::array<char, 16U> expected{
        'D','R','O','N','E','G','S','-','C','K','P','T','-','V','1','\0'};
    const auto version = read<std::uint32_t>(stream);
    if (magic != expected || (version != 4U && version != 5U)) {
        throw std::runtime_error("benchmark requires a V4/V5 checkpoint");
    }
    Metadata metadata{};
    metadata.dataset = read_string(stream);
    metadata.configuration = read_string(stream);
    for (int i = 0; i < 6; ++i) static_cast<void>(read<std::uint64_t>(stream));
    static_cast<void>(read<float>(stream));
    for (int i = 0; i < (version == 5U ? 4 : 2); ++i) {
        const auto present = read<std::uint8_t>(stream);
        if (present > 1U) throw std::runtime_error("invalid optional metric flag");
        if (present != 0U) static_cast<void>(read<float>(stream));
    }
    static_cast<void>(read<std::uint64_t>(stream));  // optimizer steps
    metadata.maximum_steps = read<std::uint64_t>(stream);
    metadata.seed = read<std::uint64_t>(stream);
    metadata.count = read<std::uint64_t>(stream);
    metadata.maximum_sh = read<std::uint32_t>(stream);
    metadata.sh_interval = read<std::uint32_t>(stream);
    static_cast<void>(read<std::uint32_t>(stream));  // active SH restored by loader
    metadata.profile = read<std::uint32_t>(stream);
    const auto fastgs = read<std::uint8_t>(stream);
    if (metadata.count == 0U || metadata.count >= static_cast<std::uint64_t>(std::numeric_limits<int>::max()) ||
        metadata.maximum_steps == 0U || metadata.maximum_sh > 3U || metadata.sh_interval == 0U ||
        metadata.profile > static_cast<std::uint32_t>(
            dronegs::MrnfOptimizerProfile::dev38_staged_rotation008_absgrad050_fastgs) || fastgs > 1U) {
        throw std::runtime_error("unsupported checkpoint runtime metadata");
    }
    metadata.fastgs = fastgs != 0U;
    return metadata;
}
}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc != 7 && argc != 8) {
            throw std::invalid_argument(
                "usage: benchmark CHECKPOINT OPACITY_SH(0|1) CAPACITY(0=count) GROW_FRACTION REPEATS NEW_OUTPUT_DIRECTORY [fresh|reuse]");
        }
        const std::string context_mode = argc == 8 ? argv[7] : "fresh";
        if (context_mode != "fresh" && context_mode != "reuse") {
            throw std::invalid_argument("context mode must be fresh or reuse");
        }
        const std::filesystem::path checkpoint = argv[1];
        const std::string opacity_arg = argv[2];
        if (opacity_arg != "0" && opacity_arg != "1") throw std::invalid_argument("invalid opacity mode");
        const auto metadata = inspect(checkpoint);
        auto capacity = std::stoull(argv[3]);
        if (capacity == 0U) capacity = metadata.count;
        const float growth = std::stof(argv[4]);
        const auto repeats = std::stoull(argv[5]);
        if (capacity < metadata.count || capacity >= static_cast<std::uint64_t>(std::numeric_limits<int>::max()) ||
            !std::isfinite(growth) || growth < 0.0F || growth > 1.0F || repeats < 1U || repeats > 100U) {
            throw std::invalid_argument("invalid capacity, growth or repeats");
        }
        const std::filesystem::path output = argv[6];
        if (!std::filesystem::create_directory(output)) throw std::invalid_argument("output directory exists");
        // Initial placeholder is fully replaced by the verified checkpoint.
        const std::vector<dronegs::Gaussian> initial(1U);
        std::unique_ptr<dronegs::OrderedAlphaTrainingContext> resident;
        std::cout << std::setprecision(10);
        for (std::uint64_t repeat = 0; repeat <= repeats; ++repeat) {
            if (!resident) {
                resident = std::make_unique<dronegs::OrderedAlphaTrainingContext>(
                    initial, 1U, metadata.maximum_steps, static_cast<std::size_t>(capacity),
                    static_cast<dronegs::MrnfOptimizerProfile>(metadata.profile),
                    metadata.maximum_sh, metadata.sh_interval, metadata.seed,
                    metadata.fastgs, 54.59815F, opacity_arg == "1");
            }
            auto& context = *resident;
            // Restore identical complete state even when retaining the context.
            const auto progress = context.load_checkpoint(checkpoint, metadata.dataset, metadata.configuration);
            check(cudaDeviceSynchronize());
            std::size_t free_before = 0, free_after = 0, total = 0;
            check(cudaMemGetInfo(&free_before, &total));
            dronegs::TopologyRefinementTelemetry telemetry;
            const auto start = std::chrono::steady_clock::now();
            const auto result = context.refine_topology(0.003F, growth, 42U, true, &telemetry);
            check(cudaDeviceSynchronize());  // Benchmark-only completion fence.
            const auto wall = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
            check(cudaMemGetInfo(&free_after, &total));
            std::cout << "{\"event\":\"checkpoint_topology_benchmark\",\"repeat\":" << repeat
                      << ",\"context_mode\":\"" << context_mode << "\""
                      << ",\"warmup\":" << (repeat == 0U ? "true" : "false")
                      << ",\"population\":" << metadata.count << ",\"capacity\":" << capacity
                      << ",\"growth_fraction\":" << growth << ",\"wall_seconds\":" << wall
                      << ",\"pruned\":" << result.pruned << ",\"added\":" << result.added
                      << ",\"candidates\":" << result.candidates
                      << ",\"compacted\":" << (result.compacted ? "true" : "false")
                      << ",\"in_place_recycled\":" << (result.in_place_recycled ? "true" : "false")
                      << ",\"device_free_before\":" << free_before << ",\"device_free_after\":" << free_after
                      << ",\"telemetry\":";
            dronegs::write_topology_telemetry(std::cout, telemetry);
            std::cout << "}\n" << std::flush;
            if (repeat == 0U) {
                // Complete model, moments and statistics for bytewise A/B comparison.
                context.save_checkpoint(output / "after-refinement.ckpt", progress,
                                        metadata.dataset, metadata.configuration);
            }
            if (context_mode == "reuse" && repeat == repeats) {
                context.save_checkpoint(output / "after-reused-refinement.ckpt", progress,
                                        metadata.dataset, metadata.configuration);
            }
            if (context_mode == "fresh") resident.reset();
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
