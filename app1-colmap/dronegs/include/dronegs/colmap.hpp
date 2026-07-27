// SPDX-License-Identifier: MIT
#pragma once

#include <filesystem>

#include "dronegs/types.hpp"

namespace dronegs {

std::filesystem::path find_sparse_model(const std::filesystem::path& data_path);
Scene load_colmap_scene(const std::filesystem::path& data_path);
std::string dataset_fingerprint(const Scene& scene);

}  // namespace dronegs
