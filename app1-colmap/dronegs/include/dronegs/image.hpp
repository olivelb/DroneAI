// SPDX-License-Identifier: MIT
#pragma once

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <list>
#include <unordered_map>
#include <vector>

namespace dronegs {

struct ImageData {
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    float source_to_image_x = 1.0F;
    float source_to_image_y = 1.0F;
    std::vector<std::uint8_t> rgb;
};

ImageData load_training_image(const std::filesystem::path& path,
                              std::uint32_t resize_factor,
                              std::uint32_t max_width);

struct ImageCacheStats {
    std::uint64_t requests = 0;
    std::uint64_t hits = 0;
    std::uint64_t misses = 0;
    std::uint64_t evictions = 0;
    std::size_t resident_bytes = 0;
    std::size_t peak_resident_bytes = 0;
    double loading_seconds = 0.0;
};

class ImageCache {
public:
    using Loader = std::function<ImageData(std::size_t)>;

    ImageCache(std::size_t item_count, std::size_t capacity_bytes, Loader loader);

    const ImageData& get(std::size_t index);
    const ImageCacheStats& stats() const noexcept;
    std::size_t capacity_bytes() const noexcept;

private:
    struct Entry {
        ImageData image;
        std::list<std::size_t>::iterator recency;
        std::size_t bytes = 0;
    };

    void touch(std::unordered_map<std::size_t, Entry>::iterator entry);
    void make_room(std::size_t bytes);

    std::size_t item_count_ = 0;
    std::size_t capacity_bytes_ = 0;
    Loader loader_;
    std::list<std::size_t> recency_;
    std::unordered_map<std::size_t, Entry> entries_;
    ImageCacheStats stats_;
};

}  // namespace dronegs
