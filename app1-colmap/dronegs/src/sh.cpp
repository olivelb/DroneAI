// SPDX-License-Identifier: MIT
#include "dronegs/sh.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <stdexcept>

namespace dronegs {
namespace {

constexpr float sh_c1 = 0.4886025119029199F;
constexpr std::array<float, 5> sh_c2{
    1.0925484305920792F, -1.0925484305920792F,
    0.31539156525252005F, -1.0925484305920792F,
    0.5462742152960396F};
constexpr std::array<float, 7> sh_c3{
    -0.5900435899266435F, 2.890611442640554F,
    -0.4570457994644658F, 0.3731763325901154F,
    -0.4570457994644658F, 1.445305721320277F,
    -0.5900435899266435F};

}  // namespace

std::array<float, 16> evaluate_sh_basis(
    const std::array<float, 3>& direction,
    std::uint32_t active_degree) {
    if (active_degree > 3U) {
        throw std::invalid_argument(
            "spherical harmonic degree must be between zero and three");
    }
    float x = direction[0];
    float y = direction[1];
    float z = direction[2];
    const float norm_squared = x * x + y * y + z * z;
    if (!std::isfinite(norm_squared) || norm_squared <= 1.0e-20F) {
        throw std::invalid_argument(
            "spherical harmonic direction must be finite and non-zero");
    }
    const float inverse_norm = 1.0F / std::sqrt(norm_squared);
    x *= inverse_norm;
    y *= inverse_norm;
    z *= inverse_norm;

    std::array<float, 16> basis{};
    basis[0] = sh_dc_basis;
    if (active_degree == 0U) {
        return basis;
    }
    basis[1] = -sh_c1 * y;
    basis[2] = sh_c1 * z;
    basis[3] = -sh_c1 * x;
    if (active_degree == 1U) {
        return basis;
    }
    const float xx = x * x;
    const float yy = y * y;
    const float zz = z * z;
    basis[4] = sh_c2[0] * x * y;
    basis[5] = sh_c2[1] * y * z;
    basis[6] = sh_c2[2] * (2.0F * zz - xx - yy);
    basis[7] = sh_c2[3] * x * z;
    basis[8] = sh_c2[4] * (xx - yy);
    if (active_degree == 2U) {
        return basis;
    }
    basis[9] = sh_c3[0] * y * (3.0F * xx - yy);
    basis[10] = sh_c3[1] * x * y * z;
    basis[11] = sh_c3[2] * y * (4.0F * zz - xx - yy);
    basis[12] = sh_c3[3] * z * (2.0F * zz - 3.0F * xx - 3.0F * yy);
    basis[13] = sh_c3[4] * x * (4.0F * zz - xx - yy);
    basis[14] = sh_c3[5] * z * (xx - yy);
    basis[15] = sh_c3[6] * x * (xx - 3.0F * yy);
    return basis;
}

}  // namespace dronegs
