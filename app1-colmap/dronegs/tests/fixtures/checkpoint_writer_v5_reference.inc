// SPDX-License-Identifier: GPL-3.0-or-later
// Frozen original CPU writer extracted from rasterization.cu SHA
// 95fed9a03d3893ade4a20a7aa605563ab52f5d63b972251656f737ad6af31164.
// Only signature/host-fixture class names change; serializer and publication do not.
namespace writer_reference {
using namespace dronegs;
struct float2 { float x, y; };
struct CheckpointSnapshotFixture {
    TrainingCheckpointProgress progress;
    std::string dataset_fingerprint;
    std::string configuration_fingerprint;
    std::uint64_t optimizer_steps = 0U;
    std::uint64_t maximum_steps = 0U;
    std::uint64_t noise_seed = 0U;
    std::size_t gaussian_count = 0U;
    std::uint32_t maximum_active_sh_degree = 0U;
    std::uint32_t sh_degree_interval = 0U;
    std::uint32_t active_sh_degree = 0U;
    MrnfOptimizerProfile optimizer_profile =
        MrnfOptimizerProfile::reference_absolute;
    bool fastgs_compatibility = false;
    float position_learning_rate_scale = 1.0F;
    float minimum_log_scale = -16.0F;
    float maximum_log_scale = 16.0F;
    float beta_first_power = 1.0F;
    float beta_second_power = 1.0F;
    std::vector<Gaussian> gaussians;
    std::vector<float> first_dc;
    std::vector<float> second_dc;
    std::vector<float2> sh_rest_moments;
    std::vector<float> first_opacity;
    std::vector<float> second_opacity;
    std::vector<float2> opacity_sh_moments;
    std::vector<float> first_xyz;
    std::vector<float> second_xyz;
    std::vector<float> first_log_scale;
    std::vector<float> second_log_scale;
    std::vector<float> first_rotation;
    std::vector<float> second_rotation;
    std::vector<float> refine_weight_max;
    std::vector<float> visibility_count;
    std::vector<float> edge_weight_sum;
    std::vector<float> absgrad_sum;
    std::vector<float> absgrad_observation_count;
};
std::uint64_t checkpoint_checksum(
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

void sync_checkpoint_file(const std::filesystem::path& path) {
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

void sync_checkpoint_directory(const std::filesystem::path& path) {
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
void write_original(const CheckpointSnapshotFixture& snapshot,
                    const std::filesystem::path& path) {
    const auto temporary = path.string() + ".tmp";
    std::ofstream stream(
        temporary, std::ios::binary | std::ios::trunc);
    if (!stream) {
        throw std::runtime_error(
            "cannot create checkpoint: " + temporary);
    }
    const auto write_value = [&stream](const auto& value) {
        stream.write(
            reinterpret_cast<const char*>(&value),
            static_cast<std::streamsize>(sizeof(value)));
    };
    const auto write_string =
        [&stream, &write_value](const std::string& value) {
            const auto size =
                static_cast<std::uint64_t>(value.size());
            write_value(size);
            stream.write(
                value.data(),
                static_cast<std::streamsize>(value.size()));
        };
    const auto write_device =
        [&stream](const auto& allocation, std::size_t count) {
            using value_type = std::remove_cv_t<
                std::remove_pointer_t<
                    decltype(allocation.data())>>;
            if (count != 0U) {
                stream.write(
                    reinterpret_cast<const char*>(allocation.data()),
                    static_cast<std::streamsize>(
                        count * sizeof(value_type)));
            }
        };
    const auto write_moment_pairs =
        [&stream](const std::vector<float2>& moments,
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
                    stream.write(
                        reinterpret_cast<const char*>(chunk.data()),
                        static_cast<std::streamsize>(
                            size * sizeof(float)));
                }
            }
        };
    constexpr std::array<char, 16> magic{
        'D', 'R', 'O', 'N', 'E', 'G', 'S', '-', 'C', 'K', 'P', 'T',
        '-', 'V', '1', '\0'};
    stream.write(magic.data(), magic.size());
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
    stream.flush();
    if (!stream) {
        throw std::runtime_error(
            "failed to write checkpoint: " + temporary);
    }
    stream.close();
    const auto payload_bytes =
        static_cast<std::uint64_t>(
            std::filesystem::file_size(temporary));
    const auto checksum =
        checkpoint_checksum(temporary, payload_bytes);
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
    sync_checkpoint_file(temporary);
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
    sync_checkpoint_directory(path);
}

} // namespace writer_reference
