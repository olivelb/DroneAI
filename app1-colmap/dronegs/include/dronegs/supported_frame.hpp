// SPDX-License-Identifier: MIT
#pragma once

#include <algorithm>
#include <cstddef>
#include <stdexcept>
#include <vector>
#include "dronegs/ordered_training.hpp"

namespace dronegs {
// Training indices are sorted by descriptor order. A retry changes neither the
// scheduled iteration nor RNG state. No rejection cache is needed in checkpoints.
template <typename Attempt, typename Rejected>
float with_supported_training_frame(
    const std::vector<std::size_t>& training, std::size_t scheduled,
    Attempt&& attempt, Rejected&& rejected) {
    const auto position = std::lower_bound(training.begin(), training.end(), scheduled);
    if (position == training.end() || *position != scheduled) {
        throw std::logic_error("scheduled frame is not in the training split");
    }
    const auto start = static_cast<std::size_t>(position - training.begin());
    for (std::size_t retry = 0U; retry < training.size(); ++retry) {
        const auto frame = training[(start + retry) % training.size()];
        try {
            return attempt(frame);
        } catch (const NoProjectedGaussians&) {
            rejected(frame);
        }
    }
    throw std::runtime_error("all training views lost GPU projection support");
}
}  // namespace dronegs
