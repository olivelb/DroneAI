// SPDX-License-Identifier: MIT
#include "dronegs/ordered_training.hpp"

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {
std::vector<char> read_bytes(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) throw std::runtime_error("cannot open workspace parity checkpoint");
    std::vector<char> bytes{std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>()};
    if (stream.bad() || bytes.size() != std::filesystem::file_size(path)) {
        throw std::runtime_error("incomplete workspace parity checkpoint read");
    }
    return bytes;
}
}  // namespace

void test_refinement_workspace_reuse(const std::filesystem::path& root) {
    for (const auto [opacity_sh, fastgs] : {
             std::pair{false, false}, std::pair{false, true},
             std::pair{true, false}, std::pair{true, true}}) {
        const auto profile = dronegs::MrnfOptimizerProfile::reference_absolute;
        const std::vector<dronegs::Gaussian> placeholder(1U);
        dronegs::OrderedAlphaTrainingContext cached(
            placeholder, 1U, 4U, 1025U, profile, 3U, 1U, 42U, fastgs, 54.59815F, opacity_sh);
        for (const std::size_t count : {1U, 17U, 257U, 9U, 513U, 3U}) {
            std::vector<dronegs::Gaussian> input(count);
            for (std::size_t i = 0U; i < count; ++i) {
                input[i].xyz = {static_cast<float>(i) * 0.001F, 0.0F, 2.0F};
                input[i].log_scale = {-3.0F, -3.0F, -3.0F};
                input[i].opacity_logit = i % 5U == 0U ? -20.0F : 0.0F;
            }
            const auto prefix = "workspace-" + std::to_string(opacity_sh) + "-" +
                                std::to_string(fastgs) + "-" + std::to_string(count);
            const auto before = root / (prefix + "-before.ckpt");
            {
                dronegs::OrderedAlphaTrainingContext source(
                    input, 1U, 4U, 1025U, profile, 3U, 1U, 42U, fastgs, 54.59815F, opacity_sh);
                source.save_checkpoint(before, {}, "workspace-dataset", "workspace-config");
            }
            dronegs::OrderedAlphaTrainingContext fresh(
                placeholder, 1U, 4U, 1025U, profile, 3U, 1U, 42U, fastgs, 54.59815F, opacity_sh);
            const auto progress = cached.load_checkpoint(before, "workspace-dataset", "workspace-config");
            static_cast<void>(fresh.load_checkpoint(before, "workspace-dataset", "workspace-config"));
            const auto reused = cached.refine_topology(0.003F, 0.0F, 42U, false);
            const auto cold = fresh.refine_topology(0.003F, 0.0F, 42U, false);
            if (reused.pruned != cold.pruned || reused.added != cold.added ||
                reused.candidates != cold.candidates || reused.compacted != cold.compacted) {
                throw std::runtime_error("workspace reuse changed topology decisions");
            }
            const auto actual = root / (prefix + "-reused.ckpt");
            const auto expected = root / (prefix + "-fresh.ckpt");
            cached.save_checkpoint(actual, progress, "workspace-dataset", "workspace-config");
            fresh.save_checkpoint(expected, progress, "workspace-dataset", "workspace-config");
            if (read_bytes(actual) != read_bytes(expected)) {
                throw std::runtime_error("workspace reuse changed full checkpoint bytes");
            }
        }
    }
}
