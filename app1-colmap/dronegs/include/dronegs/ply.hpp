// SPDX-License-Identifier: MIT
#pragma once

#include <cstdint>
#include <filesystem>
#include <vector>

#include "dronegs/types.hpp"

namespace dronegs {

void write_gaussian_ply(
    const std::filesystem::path& path,
    const std::vector<Gaussian>& gaussians,
    std::uint32_t sh_degree);

}  // namespace dronegs
