// SPDX-License-Identifier: MIT
#pragma once

#include <cstdint>
#include <filesystem>
#include <vector>

#include "dronegs/types.hpp"

namespace dronegs {

struct GaussianPly {
    std::vector<Gaussian> gaussians;
    std::uint32_t sh_degree = 0U;
};

GaussianPly read_gaussian_ply(const std::filesystem::path& path);

void write_gaussian_ply(
    const std::filesystem::path& path,
    const std::vector<Gaussian>& gaussians,
    std::uint32_t sh_degree);

}  // namespace dronegs
