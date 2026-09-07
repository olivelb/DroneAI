// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <vector>
#ifndef _WIN32
#include <fcntl.h>
#include <unistd.h>
#endif
#include "dronegs/types.hpp"

namespace dronegs::checkpoint_io {

// Explicit build-only pilot. No environment variable, CLI or checkpoint change.
#ifndef DRONEGS_EXPERIMENTAL_STREAMING_CHECKPOINT_CHECKSUM
#define DRONEGS_EXPERIMENTAL_STREAMING_CHECKPOINT_CHECKSUM 0
#endif
#ifndef DRONEGS_PROFILE_CHECKPOINT_WRITE_PHASES
#define DRONEGS_PROFILE_CHECKPOINT_WRITE_PHASES 0
#endif
static_assert(DRONEGS_EXPERIMENTAL_STREAMING_CHECKPOINT_CHECKSUM == 0 ||
              DRONEGS_EXPERIMENTAL_STREAMING_CHECKPOINT_CHECKSUM == 1);
static_assert(DRONEGS_PROFILE_CHECKPOINT_WRITE_PHASES == 0 ||
              DRONEGS_PROFILE_CHECKPOINT_WRITE_PHASES == 1);

enum class ChecksumMode { reread_file, streaming };
inline constexpr ChecksumMode default_checksum_mode =
    DRONEGS_EXPERIMENTAL_STREAMING_CHECKPOINT_CHECKSUM
        ? ChecksumMode::streaming : ChecksumMode::reread_file;

struct WriteTimings {
    double serialization_seconds = 0.0;
    // Streaming checksum time is INSIDE serialization_seconds, not additive.
    double checksum_seconds = 0.0;
    double payload_flush_close_seconds = 0.0;
    double trailer_seconds = 0.0;
    double file_sync_seconds = 0.0;
    double publication_seconds = 0.0;
    double directory_sync_seconds = 0.0;
    double total_seconds = 0.0;
    std::uint64_t payload_bytes = 0;
    bool checksum_inside_serialization = false;
};

class StreamingChecksum {
public:
    void update(const char* data, std::size_t size) {
        for (std::size_t i = 0; i < size; ++i) {
            hash_ ^= static_cast<unsigned char>(data[i]);
            hash_ *= 1099511628211ULL;
        }
        bytes_ += static_cast<std::uint64_t>(size);
    }
    std::uint64_t value() const noexcept { return hash_; }
    std::uint64_t bytes() const noexcept { return bytes_; }
private:
    std::uint64_t hash_ = 14695981039346656037ULL;
    std::uint64_t bytes_ = 0;
};

inline std::uint64_t checkpoint_checksum(
    const std::filesystem::path& path,
    std::uint64_t byte_count) {
    constexpr std::uint64_t offset_basis = 14695981039346656037ULL;
    constexpr std::uint64_t prime = 1099511628211ULL;
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        throw std::runtime_error(
            "cannot read checkpoint for checksum: " + path.string());
    }
    std::uint64_t hash = offset_basis;
    std::array<char, 64U * 1024U> buffer{};
    while (byte_count != 0U) {
        const auto requested = static_cast<std::streamsize>(
            std::min<std::uint64_t>(buffer.size(), byte_count));
        stream.read(buffer.data(), requested);
        const auto read = stream.gcount();
        if (read <= 0) {
            throw std::runtime_error(
                "checkpoint checksum source is truncated");
        }
        for (std::streamsize index = 0; index < read; ++index) {
            hash ^= static_cast<unsigned char>(
                buffer[static_cast<std::size_t>(index)]);
            hash *= prime;
        }
        byte_count -= static_cast<std::uint64_t>(read);
    }
    return hash;
}

inline void sync_checkpoint_file(const std::filesystem::path& path) {
#ifndef _WIN32
    const int descriptor = ::open(path.c_str(), O_RDONLY);
    if (descriptor < 0) {
        throw std::runtime_error(
            "cannot open checkpoint for fsync: " + path.string());
    }
    const int result = ::fsync(descriptor);
    ::close(descriptor);
    if (result != 0) {
        throw std::runtime_error(
            "cannot fsync checkpoint: " + path.string());
    }
#else
    static_cast<void>(path);
#endif
}

inline void sync_checkpoint_directory(const std::filesystem::path& path) {
#ifndef _WIN32
    const int descriptor =
        ::open(path.parent_path().c_str(), O_RDONLY | O_DIRECTORY);
    if (descriptor >= 0) {
        static_cast<void>(::fsync(descriptor));
        ::close(descriptor);
    }
#else
    static_cast<void>(path);
#endif
}
template <typename Snapshot>
void write_checkpoint(const Snapshot& snapshot,
                      const std::filesystem::path& path,
                      ChecksumMode mode = default_checksum_mode,
                      WriteTimings* timings = nullptr) {
    if (mode != ChecksumMode::reread_file && mode != ChecksumMode::streaming) {
        throw std::invalid_argument("invalid checkpoint checksum mode");
    }
    using Clock = std::chrono::steady_clock;
    const auto now = [&]() { return timings ? Clock::now() : Clock::time_point{}; };
    const auto seconds = [](auto begin, auto end) {
        return std::chrono::duration<double>(end - begin).count();
    };
    if (timings) { *timings = {}; timings->checksum_inside_serialization = mode == ChecksumMode::streaming; }
    const auto started = now();
    auto phase_started = started;
    StreamingChecksum streaming_checksum;
    const auto temporary = path.string() + ".tmp";
    std::ofstream stream(
        temporary, std::ios::binary | std::ios::trunc);
    if (!stream) {
        throw std::runtime_error(
            "cannot create checkpoint: " + temporary);
    }
    const auto write_bytes = [&](const char* bytes, std::streamsize count) {
        stream.write(bytes, count);
        if (mode == ChecksumMode::streaming && stream) {
            const auto hash_started = now();
            streaming_checksum.update(bytes, static_cast<std::size_t>(count));
            if (timings) timings->checksum_seconds += seconds(hash_started, now());
        }
    };
    const auto write_value = [&write_bytes](const auto& value) {
        write_bytes(
            reinterpret_cast<const char*>(&value),
            static_cast<std::streamsize>(sizeof(value)));
    };
    const auto write_string =
        [&write_bytes, &write_value](const std::string& value) {
            const auto size =
                static_cast<std::uint64_t>(value.size());
            write_value(size);
            write_bytes(
                value.data(),
                static_cast<std::streamsize>(value.size()));
        };
    const auto write_device =
        [&write_bytes](const auto& allocation, std::size_t count) {
            using value_type = std::remove_cv_t<
                std::remove_pointer_t<
                    decltype(allocation.data())>>;
            if (count != 0U) {
                write_bytes(
                    reinterpret_cast<const char*>(allocation.data()),
                    static_cast<std::streamsize>(
                        count * sizeof(value_type)));
            }
        };
    const auto write_moment_pairs =
        [&write_bytes](const auto& moments,
                  std::size_t count) {
            if (count > moments.size()) {
                throw std::logic_error(
                    "checkpoint moment snapshot is truncated");
            }
            if (count == 0U) {
                return;
            }
            constexpr std::size_t chunk_size = 1U << 20U;
            std::vector<float> chunk(std::min(count, chunk_size));
            for (std::size_t component = 0U; component < 2U;
                 ++component) {
                for (std::size_t offset = 0U; offset < count;
                     offset += chunk_size) {
                    const auto size =
                        std::min(chunk_size, count - offset);
                    for (std::size_t index = 0U; index < size; ++index) {
                        const auto pair = moments[offset + index];
                        chunk[index] = component == 0U ? pair.x : pair.y;
                    }
                    write_bytes(
                        reinterpret_cast<const char*>(chunk.data()),
                        static_cast<std::streamsize>(
                            size * sizeof(float)));
                }
            }
        };
    constexpr std::array<char, 16> magic{
        'D', 'R', 'O', 'N', 'E', 'G', 'S', '-', 'C', 'K', 'P', 'T',
        '-', 'V', '1', '\0'};
    write_bytes(magic.data(), magic.size());
    constexpr std::uint32_t format_version = 5U;
    write_value(format_version);
    write_string(snapshot.dataset_fingerprint);
    write_string(snapshot.configuration_fingerprint);
    write_value(snapshot.progress.completed_iteration);
    write_value(snapshot.progress.topology_refinements);
    write_value(snapshot.progress.gaussians_added);
    write_value(snapshot.progress.gaussians_pruned);
    write_value(snapshot.progress.gaussian_slots_reused);
    write_value(snapshot.progress.topology_compactions);
    write_value(snapshot.progress.initial_loss);
    const std::uint8_t has_initial_held_out_psnr =
        snapshot.progress.initial_held_out_psnr.has_value() ? 1U : 0U;
    const std::uint8_t has_initial_held_out_ssim =
        snapshot.progress.initial_held_out_ssim.has_value() ? 1U : 0U;
    write_value(has_initial_held_out_psnr);
    if (has_initial_held_out_psnr) {
        write_value(*snapshot.progress.initial_held_out_psnr);
    }
    write_value(has_initial_held_out_ssim);
    if (has_initial_held_out_ssim) {
        write_value(*snapshot.progress.initial_held_out_ssim);
    }
    const std::uint8_t has_initial_pixel_weighted_psnr =
        snapshot.progress.initial_pixel_weighted_psnr.has_value()
            ? 1U
            : 0U;
    const std::uint8_t has_initial_pixel_weighted_ssim =
        snapshot.progress.initial_pixel_weighted_ssim.has_value()
            ? 1U
            : 0U;
    write_value(has_initial_pixel_weighted_psnr);
    if (has_initial_pixel_weighted_psnr) {
        write_value(*snapshot.progress.initial_pixel_weighted_psnr);
    }
    write_value(has_initial_pixel_weighted_ssim);
    if (has_initial_pixel_weighted_ssim) {
        write_value(*snapshot.progress.initial_pixel_weighted_ssim);
    }
    write_value(snapshot.optimizer_steps);
    write_value(snapshot.maximum_steps);
    write_value(snapshot.noise_seed);
    const auto count = snapshot.gaussian_count;
    const auto portable_count = static_cast<std::uint64_t>(count);
    write_value(portable_count);
    write_value(snapshot.maximum_active_sh_degree);
    write_value(snapshot.sh_degree_interval);
    write_value(snapshot.active_sh_degree);
    const auto profile =
        static_cast<std::uint32_t>(snapshot.optimizer_profile);
    write_value(profile);
    const std::uint8_t portable_fastgs =
        snapshot.fastgs_compatibility ? 1U : 0U;
    write_value(portable_fastgs);
    write_value(snapshot.position_learning_rate_scale);
    write_value(snapshot.minimum_log_scale);
    write_value(snapshot.maximum_log_scale);
    write_value(snapshot.beta_first_power);
    write_value(snapshot.beta_second_power);
    write_device(snapshot.gaussians, count);
    write_device(snapshot.first_dc, count * 3U);
    write_device(snapshot.second_dc, count * 3U);
    write_moment_pairs(
        snapshot.sh_rest_moments,
        count * maximum_sh_rest_values);
    write_device(snapshot.first_opacity, count);
    write_device(snapshot.second_opacity, count);
    write_moment_pairs(
        snapshot.opacity_sh_moments,
        count * maximum_opacity_sh_coefficients);
    write_device(snapshot.first_xyz, count * 3U);
    write_device(snapshot.second_xyz, count * 3U);
    write_device(snapshot.first_log_scale, count * 3U);
    write_device(snapshot.second_log_scale, count * 3U);
    write_device(snapshot.first_rotation, count * 4U);
    write_device(snapshot.second_rotation, count * 4U);
    write_device(snapshot.refine_weight_max, count);
    write_device(snapshot.visibility_count, count);
    write_device(snapshot.edge_weight_sum, count);
    write_device(snapshot.absgrad_sum, count);
    write_device(snapshot.absgrad_observation_count, count);
    if (timings) timings->serialization_seconds = seconds(started, now());
    phase_started = now();
    stream.flush();
    if (!stream) {
        throw std::runtime_error(
            "failed to write checkpoint: " + temporary);
    }
    stream.close();
    if (timings) timings->payload_flush_close_seconds = seconds(phase_started, now());
    const auto payload_bytes =
        static_cast<std::uint64_t>(
            std::filesystem::file_size(temporary));
    if (timings) timings->payload_bytes = payload_bytes;
    phase_started = now();
    std::uint64_t checksum;
    if (mode == ChecksumMode::streaming) {
        if (streaming_checksum.bytes() != payload_bytes) {
            throw std::runtime_error("streaming checkpoint payload byte count mismatch");
        }
        checksum = streaming_checksum.value();
    } else {
        checksum = checkpoint_checksum(temporary, payload_bytes);
        if (timings) timings->checksum_seconds = seconds(phase_started, now());
    }
    phase_started = now();
    {
        std::ofstream trailer(temporary, std::ios::binary | std::ios::app);
        trailer.write(
            reinterpret_cast<const char*>(&checksum),
            static_cast<std::streamsize>(sizeof(checksum)));
        trailer.flush();
        if (!trailer) {
            throw std::runtime_error(
                "failed to append checkpoint checksum");
        }
    }
    if (timings) timings->trailer_seconds = seconds(phase_started, now());
    phase_started = now();
    sync_checkpoint_file(temporary);
    if (timings) timings->file_sync_seconds = seconds(phase_started, now());
    phase_started = now();
    std::error_code error;
    std::filesystem::rename(temporary, path, error);
    if (error) {
        const auto backup = path.string() + ".previous";
        std::error_code backup_error;
        std::filesystem::remove(backup, backup_error);
        backup_error.clear();
        if (std::filesystem::exists(path)) {
            std::filesystem::rename(path, backup, backup_error);
        }
        if (backup_error) {
            throw std::runtime_error(
                "cannot preserve previous checkpoint: " +
                backup_error.message());
        }
        error.clear();
        std::filesystem::rename(temporary, path, error);
        if (error && std::filesystem::exists(backup)) {
            std::error_code restore_error;
            std::filesystem::rename(backup, path, restore_error);
        } else {
            std::filesystem::remove(backup, backup_error);
        }
    }
    if (error) {
        throw std::runtime_error(
            "cannot publish checkpoint: " + error.message());
    }
    if (timings) timings->publication_seconds = seconds(phase_started, now());
    phase_started = now();
    sync_checkpoint_directory(path);
    if (timings) {
        timings->directory_sync_seconds = seconds(phase_started, now());
        timings->total_seconds = seconds(started, now());
    }
}

} // namespace dronegs::checkpoint_io
