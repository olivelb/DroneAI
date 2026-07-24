// SPDX-License-Identifier: MIT
#pragma once

#include <cstddef>
#include <filesystem>
#include <string>

#include "dronegs/types.hpp"

namespace dronegs {

struct RunMeasurements {
    std::string started_at;
    std::string finished_at;
    double loading_seconds = 0.0;
    double export_seconds = 0.0;
    double wall_seconds = 0.0;
};

std::string utc_timestamp();
void write_completed_manifest(const Options& options, const Scene& scene,
                              const std::string& fingerprint,
                              const RunMeasurements& measurements,
                              const std::filesystem::path& ply_path,
                              std::size_t gaussian_count);

}  // namespace dronegs
