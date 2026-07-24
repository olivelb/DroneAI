// SPDX-License-Identifier: MIT
#pragma once

#include <cstdint>
#include <cstddef>
#include <filesystem>
#include <string>

#include "dronegs/types.hpp"

namespace dronegs {

struct RunMeasurements {
    std::string started_at;
    std::string finished_at;
    double loading_seconds = 0.0;
    double startup_seconds = 0.0;
    double training_seconds = 0.0;
    double export_seconds = 0.0;
    double wall_seconds = 0.0;
    float initial_loss = 0.0F;
    float final_loss = 0.0F;
    std::uint64_t image_cache_hits = 0;
    std::uint64_t image_cache_misses = 0;
    std::uint64_t image_cache_evictions = 0;
    std::uint64_t image_cache_capacity_bytes = 0;
    std::uint64_t peak_image_cache_bytes = 0;
};

std::string utc_timestamp();
void write_completed_manifest(const Options& options, const Scene& scene,
                              const std::string& fingerprint,
                              const RunMeasurements& measurements,
                              const std::filesystem::path& ply_path,
                              std::size_t gaussian_count);

}  // namespace dronegs
