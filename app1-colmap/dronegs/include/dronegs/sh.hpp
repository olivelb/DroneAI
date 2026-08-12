// SPDX-License-Identifier: MIT
#pragma once

#include <array>
#include <cstdint>

namespace dronegs {

inline constexpr float sh_dc_basis = 0.28209479177387814F;

std::array<float, 16> evaluate_sh_basis(
    const std::array<float, 3>& direction,
    std::uint32_t active_degree);

}  // namespace dronegs
