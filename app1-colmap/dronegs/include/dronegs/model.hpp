// SPDX-License-Identifier: MIT
#pragma once

#include <vector>

#include "dronegs/types.hpp"

namespace dronegs {

std::vector<Gaussian> initialize_fixed_topology(const Scene& scene);

}  // namespace dronegs
