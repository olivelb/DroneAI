// SPDX-License-Identifier: MIT
#include "dronegs/image.hpp"

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <csetjmp>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <cstdio>
#include <filesystem>
#include <limits>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

#include <jpeglib.h>

namespace dronegs {
namespace {

struct JpegError {
    jpeg_error_mgr manager{};
    std::jmp_buf jump_buffer{};
    char message[JMSG_LENGTH_MAX]{};
};

void jpeg_error_exit(j_common_ptr common) {
    auto* error = reinterpret_cast<JpegError*>(common->err);
    error->manager.format_message(common, error->message);
    std::longjmp(error->jump_buffer, 1);
}

struct DecodedImage {
    std::uint32_t source_width = 0;
    std::uint32_t source_height = 0;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::vector<std::uint8_t> rgb;
};

DecodedImage decode_jpeg(const std::filesystem::path& path,
                         std::uint32_t resize_factor,
                         std::uint32_t max_width,
                         bool use_scaled_idct) {
    auto* file = std::fopen(path.string().c_str(), "rb");
    if (file == nullptr) {
        throw std::runtime_error("cannot open training image: " + path.string());
    }

    jpeg_decompress_struct decoder{};
    JpegError error{};
    decoder.err = jpeg_std_error(&error.manager);
    error.manager.error_exit = jpeg_error_exit;
    if (setjmp(error.jump_buffer) != 0) {
        jpeg_destroy_decompress(&decoder);
        std::fclose(file);
        throw std::runtime_error(
            "cannot decode JPEG " + path.string() + ": " + error.message);
    }

    jpeg_create_decompress(&decoder);
    jpeg_stdio_src(&decoder, file);
    static_cast<void>(jpeg_read_header(&decoder, TRUE));
    if (decoder.image_width > std::numeric_limits<std::uint32_t>::max() ||
        decoder.image_height > std::numeric_limits<std::uint32_t>::max()) {
        jpeg_destroy_decompress(&decoder);
        std::fclose(file);
        throw std::runtime_error("unsupported JPEG dimensions: " + path.string());
    }
    const auto source_width =
        static_cast<std::uint32_t>(decoder.image_width);
    const auto source_height =
        static_cast<std::uint32_t>(decoder.image_height);
    if (use_scaled_idct) {
        double effective_factor =
            static_cast<double>(std::max(resize_factor, 1U));
        if (max_width > 0U && source_width > max_width) {
            effective_factor = std::max(
                effective_factor,
                static_cast<double>(source_width) /
                    static_cast<double>(max_width));
        }
        decoder.scale_num = 1U;
        if (effective_factor >= 8.0) {
            decoder.scale_denom = 8U;
        } else if (effective_factor >= 4.0) {
            decoder.scale_denom = 4U;
        } else if (effective_factor >= 2.0) {
            decoder.scale_denom = 2U;
        }
    }
    decoder.out_color_space = JCS_RGB;
    static_cast<void>(jpeg_start_decompress(&decoder));
    if (decoder.output_components != 3U ||
        decoder.output_width > std::numeric_limits<std::uint32_t>::max() ||
        decoder.output_height > std::numeric_limits<std::uint32_t>::max()) {
        jpeg_destroy_decompress(&decoder);
        std::fclose(file);
        throw std::runtime_error("unsupported JPEG dimensions or colorspace: " + path.string());
    }

    DecodedImage image{
        .source_width = source_width,
        .source_height = source_height,
        .width = static_cast<std::uint32_t>(decoder.output_width),
        .height = static_cast<std::uint32_t>(decoder.output_height),
        .rgb = {},
    };
    const auto pixel_count = static_cast<std::size_t>(image.width) * image.height;
    if (pixel_count > std::numeric_limits<std::size_t>::max() / 3U) {
        jpeg_destroy_decompress(&decoder);
        std::fclose(file);
        throw std::runtime_error("JPEG image is too large: " + path.string());
    }
    image.rgb.resize(pixel_count * 3U);
    const auto row_stride = static_cast<std::size_t>(image.width) * 3U;
    while (decoder.output_scanline < decoder.output_height) {
        auto* row = image.rgb.data() +
                    static_cast<std::size_t>(decoder.output_scanline) * row_stride;
        static_cast<void>(jpeg_read_scanlines(&decoder, &row, 1));
    }
    static_cast<void>(jpeg_finish_decompress(&decoder));
    jpeg_destroy_decompress(&decoder);
    std::fclose(file);
    return image;
}

}  // namespace

ImageData load_training_image(const std::filesystem::path& path,
                              std::uint32_t resize_factor,
                              std::uint32_t max_width,
                              bool use_scaled_idct) {
    auto source = decode_jpeg(
        path, resize_factor, max_width, use_scaled_idct);
    double effective_factor = static_cast<double>(std::max(resize_factor, 1U));
    if (max_width > 0U && source.source_width > max_width) {
        effective_factor = std::max(
            effective_factor,
            static_cast<double>(source.source_width) /
                static_cast<double>(max_width));
    }
    const auto target_width = std::max(
        1U, static_cast<std::uint32_t>(
                static_cast<double>(source.source_width) / effective_factor));
    const auto target_height = std::max(
        1U, static_cast<std::uint32_t>(
                static_cast<double>(source.source_height) / effective_factor));

    ImageData result{
        .width = target_width,
        .height = target_height,
        .source_to_image_x =
            static_cast<float>(target_width) /
                static_cast<float>(source.source_width),
        .source_to_image_y =
            static_cast<float>(target_height) /
                static_cast<float>(source.source_height),
        .rgb = {},
    };
    if (source.width == target_width && source.height == target_height) {
        result.rgb = std::move(source.rgb);
        return result;
    }
    result.rgb.resize(static_cast<std::size_t>(target_width) * target_height * 3U);
    for (std::uint32_t y = 0; y < target_height; ++y) {
        const auto source_y = std::min(
            source.height - 1U,
            static_cast<std::uint32_t>(
                static_cast<double>(y) * source.height / target_height));
        for (std::uint32_t x = 0; x < target_width; ++x) {
            const auto source_x = std::min(
                source.width - 1U,
                static_cast<std::uint32_t>(
                    static_cast<double>(x) * source.width / target_width));
            const auto source_offset =
                (static_cast<std::size_t>(source_y) * source.width + source_x) * 3U;
            const auto target_offset =
                (static_cast<std::size_t>(y) * target_width + x) * 3U;
            for (std::size_t channel = 0; channel < 3U; ++channel) {
                result.rgb[target_offset + channel] = source.rgb[source_offset + channel];
            }
        }
    }
    return result;
}

ImageCache::ImageCache(std::size_t item_count, std::size_t capacity_bytes,
                       Loader loader, std::size_t prefetch_capacity,
                       std::size_t worker_count)
    : item_count_(item_count),
      capacity_bytes_(capacity_bytes),
      prefetch_capacity_(prefetch_capacity),
      worker_count_(worker_count),
      loader_(std::move(loader)) {
    if (item_count_ == 0U) {
        throw std::invalid_argument("image cache requires at least one item");
    }
    if (capacity_bytes_ == 0U) {
        throw std::invalid_argument("image cache capacity must be positive");
    }
    if (!loader_) {
        throw std::invalid_argument("image cache loader is required");
    }
    if (prefetch_capacity_ == 0U) {
        throw std::invalid_argument("image cache prefetch capacity must be positive");
    }
    if (worker_count_ == 0U || worker_count_ > prefetch_capacity_) {
        throw std::invalid_argument(
            "image cache worker count must be between one and prefetch capacity");
    }
    workers_.reserve(worker_count_);
    try {
        for (std::size_t index = 0U; index < worker_count_; ++index) {
            workers_.emplace_back([this]() { worker_loop(); });
        }
    } catch (...) {
        {
            const std::lock_guard lock(pending_mutex_);
            stop_worker_ = true;
        }
        pending_condition_.notify_all();
        for (auto& worker : workers_) {
            if (worker.joinable()) {
                worker.join();
            }
        }
        throw;
    }
}

ImageCache::~ImageCache() {
    {
        const std::lock_guard lock(pending_mutex_);
        stop_worker_ = true;
    }
    pending_condition_.notify_all();
    for (auto& worker : workers_) {
        if (worker.joinable()) {
            worker.join();
        }
    }
}

const ImageData& ImageCache::get(std::size_t index) {
    if (index >= item_count_) {
        throw std::out_of_range("image cache index is out of range");
    }
    ++stats_.requests;
    auto found = entries_.find(index);
    if (found != entries_.end()) {
        ++stats_.hits;
        touch(found);
        return found->second.image;
    }

    ++stats_.misses;
    const auto wait_start = std::chrono::steady_clock::now();
    std::optional<LoadedImage> loaded;
    {
        std::unique_lock lock(pending_mutex_);
        if (outstanding_indices_.contains(index)) {
            ++stats_.prefetch_consumed;
            if (ready_results_.contains(index) || ready_errors_.contains(index)) {
                ++stats_.prefetch_ready;
            }
            ready_condition_.wait(lock, [this, index]() {
                return ready_results_.contains(index) ||
                       ready_errors_.contains(index);
            });
            const auto error = ready_errors_.find(index);
            if (error != ready_errors_.end()) {
                auto captured = error->second;
                ready_errors_.erase(error);
                outstanding_indices_.erase(index);
                lock.unlock();
                std::rethrow_exception(captured);
            }
            auto result = ready_results_.find(index);
            if (result == ready_results_.end()) {
                throw std::logic_error("image cache ready state is inconsistent");
            }
            loaded = std::move(result->second);
            ready_results_.erase(result);
            outstanding_indices_.erase(index);
        }
    }
    if (!loaded.has_value()) {
        loaded = load(index);
    }
    stats_.wait_seconds += std::chrono::duration<double>(
        std::chrono::steady_clock::now() - wait_start).count();
    return insert(index, std::move(*loaded));
}

void ImageCache::prefetch(std::size_t index) {
    if (index >= item_count_) {
        throw std::out_of_range("image cache prefetch index is out of range");
    }
    if (entries_.contains(index)) {
        return;
    }
    {
        const std::lock_guard lock(pending_mutex_);
        if (outstanding_indices_.contains(index)) {
            return;
        }
        if (outstanding_indices_.size() >= prefetch_capacity_) {
            throw std::logic_error(
                "image cache prefetch queue is at capacity");
        }
        ++stats_.prefetch_started;
        outstanding_indices_.insert(index);
        pending_indices_.push_back(index);
    }
    pending_condition_.notify_one();
}

void ImageCache::worker_loop() {
    for (;;) {
        std::size_t index = 0;
        {
            std::unique_lock lock(pending_mutex_);
            pending_condition_.wait(lock, [this]() {
                return stop_worker_ || !pending_indices_.empty();
            });
            if (stop_worker_) {
                return;
            }
            index = pending_indices_.front();
            pending_indices_.pop_front();
        }
        try {
            auto loaded = load(index);
            {
                const std::lock_guard lock(pending_mutex_);
                static_cast<void>(
                    ready_results_.emplace(index, std::move(loaded)));
            }
        } catch (...) {
            const std::lock_guard lock(pending_mutex_);
            ready_errors_.insert_or_assign(index, std::current_exception());
        }
        ready_condition_.notify_all();
    }
}

ImageCache::LoadedImage ImageCache::load(std::size_t index) const {
    const auto loading_start = std::chrono::steady_clock::now();
    auto image = loader_(index);
    return {
        .image = std::move(image),
        .loading_seconds = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - loading_start).count(),
    };
}

const ImageData& ImageCache::insert(std::size_t index, LoadedImage loaded) {
    stats_.loading_seconds += loaded.loading_seconds;
    const std::size_t bytes = loaded.image.rgb.size() * sizeof(std::uint8_t);
    if (bytes == 0U) {
        throw std::runtime_error("image cache loader returned an empty image");
    }
    if (bytes > capacity_bytes_) {
        throw std::runtime_error("decoded training image exceeds host cache capacity");
    }
    make_room(bytes);
    recency_.push_front(index);
    auto [inserted, was_inserted] = entries_.emplace(
        index, Entry{
            .image = std::move(loaded.image),
            .recency = recency_.begin(),
            .bytes = bytes,
        });
    if (!was_inserted) {
        throw std::logic_error("image cache insertion failed");
    }
    stats_.resident_bytes += bytes;
    stats_.peak_resident_bytes =
        std::max(stats_.peak_resident_bytes, stats_.resident_bytes);
    return inserted->second.image;
}

const ImageCacheStats& ImageCache::stats() const noexcept {
    return stats_;
}

std::size_t ImageCache::capacity_bytes() const noexcept {
    return capacity_bytes_;
}

std::size_t ImageCache::prefetch_capacity() const noexcept {
    return prefetch_capacity_;
}

std::size_t ImageCache::worker_count() const noexcept {
    return worker_count_;
}

void ImageCache::touch(std::unordered_map<std::size_t, Entry>::iterator entry) {
    recency_.erase(entry->second.recency);
    recency_.push_front(entry->first);
    entry->second.recency = recency_.begin();
}

void ImageCache::make_room(std::size_t bytes) {
    while (!recency_.empty() && stats_.resident_bytes + bytes > capacity_bytes_) {
        const std::size_t victim = recency_.back();
        const auto found = entries_.find(victim);
        if (found == entries_.end()) {
            throw std::logic_error("image cache LRU state is inconsistent");
        }
        stats_.resident_bytes -= found->second.bytes;
        entries_.erase(found);
        recency_.pop_back();
        ++stats_.evictions;
    }
}

}  // namespace dronegs
