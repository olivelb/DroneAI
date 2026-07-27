// SPDX-License-Identifier: MIT
#pragma once

#include "dronegs/types.hpp"

namespace dronegs {

Options parse_options(int argc, char** argv);
void validate_options(const Options& options);
const char* help_text();

}  // namespace dronegs
