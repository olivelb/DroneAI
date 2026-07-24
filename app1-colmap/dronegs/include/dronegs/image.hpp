// SPDX-License-Identifier: MIT
#pragma once

#include <cstdint>
#include <filesystem>
#include <vector>

namespace dronegs {

struct ImageData {
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    float source_to_image_x = 1.0F;
    float source_to_image_y = 1.0F;
    std::vector<float> rgb;
};

ImageData load_training_image(const std::filesystem::path& path,
                              std::uint32_t resize_factor,
                              std::uint32_t max_width);

}  // namespace dronegs
