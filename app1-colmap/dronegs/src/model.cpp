// SPDX-License-Identifier: MIT
#include "dronegs/model.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <vector>

namespace dronegs {

std::vector<Gaussian> initialize_fixed_topology(const Scene& scene) {
    if (scene.points.empty()) {
        throw std::invalid_argument("cannot initialize Gaussians without sparse points");
    }
    std::array<double, 3> minimum{
        std::numeric_limits<double>::max(),
        std::numeric_limits<double>::max(),
        std::numeric_limits<double>::max(),
    };
    std::array<double, 3> maximum{
        std::numeric_limits<double>::lowest(),
        std::numeric_limits<double>::lowest(),
        std::numeric_limits<double>::lowest(),
    };
    for (const auto& point : scene.points) {
        for (std::size_t axis = 0; axis < 3; ++axis) {
            minimum[axis] = std::min(minimum[axis], point.xyz[axis]);
            maximum[axis] = std::max(maximum[axis], point.xyz[axis]);
        }
    }
    double diagonal_squared = 0.0;
    for (std::size_t axis = 0; axis < 3; ++axis) {
        const double extent = maximum[axis] - minimum[axis];
        diagonal_squared += extent * extent;
    }
    const double diagonal = std::max(std::sqrt(diagonal_squared), 1.0e-6);
    const double spacing = std::max(
        0.25 * diagonal / std::cbrt(static_cast<double>(scene.points.size())), 1.0e-7);
    const float log_scale = static_cast<float>(std::log(spacing));
    constexpr double sh_c0 = 0.28209479177387814;
    constexpr float opacity_logit = -2.197224577F;  // logit(0.1)

    std::vector<Gaussian> gaussians;
    gaussians.reserve(scene.points.size());
    for (const auto& point : scene.points) {
        Gaussian gaussian;
        for (std::size_t axis = 0; axis < 3; ++axis) {
            gaussian.xyz[axis] = static_cast<float>(point.xyz[axis]);
            const double color = static_cast<double>(point.rgb[axis]) / 255.0;
            gaussian.dc[axis] = static_cast<float>((color - 0.5) / sh_c0);
            gaussian.log_scale[axis] = log_scale;
        }
        gaussian.opacity_logit = opacity_logit;
        gaussians.push_back(gaussian);
    }
    return gaussians;
}

}  // namespace dronegs
