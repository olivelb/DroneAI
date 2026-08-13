// SPDX-License-Identifier: MIT
#pragma once

#include <cstddef>
#include <cstdint>
#include <condition_variable>
#include <deque>
#include <exception>
#include <filesystem>
#include <functional>
#include <list>
#include <mutex>
#include <optional>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace dronegs {

struct ImageRegion {
    std::uint32_t source_x = 0;
    std::uint32_t source_y = 0;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
};

struct ImageData {
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::uint32_t source_x = 0;
    std::uint32_t source_y = 0;
    float source_to_image_x = 1.0F;
    float source_to_image_y = 1.0F;
    std::vector<std::uint8_t> rgb;
};

std::vector<ImageRegion> make_training_tiles(
    std::uint32_t source_width, std::uint32_t source_height,
    std::uint32_t tile_mode);

std::vector<ImageRegion> make_training_tiles(
    const ImageRegion& source_region,
    std::uint32_t tile_mode);

std::vector<ImageRegion> make_adaptive_training_tiles(
    const ImageRegion& source_region,
    std::uint32_t full_width, std::uint32_t full_height,
    std::uint32_t maximum_tile_mode);

std::pair<std::uint32_t, std::uint32_t> training_image_dimensions(
    const ImageRegion& region, std::uint32_t resize_factor,
    std::uint32_t max_width);

std::vector<std::uint8_t> resample_rgb_area(
    const std::vector<std::uint8_t>& source,
    std::uint32_t source_width, std::uint32_t source_height,
    std::uint32_t target_width, std::uint32_t target_height);

ImageData load_training_image(const std::filesystem::path& path,
                              std::uint32_t resize_factor,
                              std::uint32_t max_width,
                              bool use_scaled_idct = false,
                              std::optional<ImageRegion> region = std::nullopt);

struct ImageCacheStats {
    std::uint64_t requests = 0;
    std::uint64_t hits = 0;
    std::uint64_t misses = 0;
    std::uint64_t evictions = 0;
    std::size_t resident_bytes = 0;
    std::size_t peak_resident_bytes = 0;
    double loading_seconds = 0.0;
    double wait_seconds = 0.0;
    std::uint64_t prefetch_started = 0;
    std::uint64_t prefetch_consumed = 0;
    std::uint64_t prefetch_ready = 0;
};

class ImageCache {
public:
    using Loader = std::function<ImageData(std::size_t)>;

    ImageCache(std::size_t item_count, std::size_t capacity_bytes, Loader loader,
               std::size_t prefetch_capacity = 1U,
               std::size_t worker_count = 1U);
    ~ImageCache();

    const ImageData& get(std::size_t index);
    void prefetch(std::size_t index);
    const ImageCacheStats& stats() const noexcept;
    std::size_t capacity_bytes() const noexcept;
    std::size_t prefetch_capacity() const noexcept;
    std::size_t worker_count() const noexcept;

private:
    struct Entry {
        ImageData image;
        std::list<std::size_t>::iterator recency;
        std::size_t bytes = 0;
    };

    struct LoadedImage {
        ImageData image;
        double loading_seconds = 0.0;
    };

    LoadedImage load(std::size_t index) const;
    const ImageData& insert(std::size_t index, LoadedImage loaded);
    void worker_loop();
    void touch(std::unordered_map<std::size_t, Entry>::iterator entry);
    void make_room(std::size_t bytes);

    std::size_t item_count_ = 0;
    std::size_t capacity_bytes_ = 0;
    std::size_t prefetch_capacity_ = 0;
    std::size_t worker_count_ = 0;
    Loader loader_;
    std::list<std::size_t> recency_;
    std::unordered_map<std::size_t, Entry> entries_;
    ImageCacheStats stats_;
    std::mutex pending_mutex_;
    std::condition_variable pending_condition_;
    std::condition_variable ready_condition_;
    std::deque<std::size_t> pending_indices_;
    std::unordered_set<std::size_t> outstanding_indices_;
    std::unordered_map<std::size_t, LoadedImage> ready_results_;
    std::unordered_map<std::size_t, std::exception_ptr> ready_errors_;
    bool stop_worker_ = false;
    std::vector<std::thread> workers_;
};

}  // namespace dronegs
