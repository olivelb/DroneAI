// SPDX-License-Identifier: MIT
#pragma once

#include <cstddef>

namespace dronegs {

float color_l1_loss_gradient(
    const float* device_prediction,
    const float* device_target,
    float* device_gradient,
    std::size_t count);

}  // namespace dronegs
