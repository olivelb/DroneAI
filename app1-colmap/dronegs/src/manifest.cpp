// SPDX-License-Identifier: MIT
#include "dronegs/manifest.hpp"

#include <chrono>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <string>

#ifndef DRONEGS_GIT_REVISION
#define DRONEGS_GIT_REVISION "unknown"
#endif

namespace dronegs {
namespace {

std::string json_escape(const std::string& value) {
    std::ostringstream escaped;
    for (const unsigned char character : value) {
        switch (character) {
            case '"': escaped << "\\\""; break;
            case '\\': escaped << "\\\\"; break;
            case '\b': escaped << "\\b"; break;
            case '\f': escaped << "\\f"; break;
            case '\n': escaped << "\\n"; break;
            case '\r': escaped << "\\r"; break;
            case '\t': escaped << "\\t"; break;
            default:
                if (character < 0x20U) {
                    escaped << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                            << static_cast<unsigned int>(character) << std::dec;
                } else {
                    escaped << static_cast<char>(character);
                }
        }
    }
    return escaped.str();
}

}  // namespace

std::string utc_timestamp() {
    const auto now = std::chrono::system_clock::now();
    const std::time_t time = std::chrono::system_clock::to_time_t(now);
    std::tm utc{};
#ifdef _WIN32
    gmtime_s(&utc, &time);
#else
    gmtime_r(&time, &utc);
#endif
    std::ostringstream result;
    result << std::put_time(&utc, "%Y-%m-%dT%H:%M:%SZ");
    return result.str();
}

void write_completed_manifest(const Options& options, const Scene& scene,
                              const std::string& fingerprint,
                              const RunMeasurements& measurements,
                              const std::filesystem::path& ply_path,
                              std::size_t gaussian_count) {
    const auto temporary = options.run_manifest.string() + ".tmp";
    std::ofstream stream(temporary, std::ios::trunc);
    if (!stream) {
        throw std::runtime_error("cannot create run manifest: " + temporary);
    }
    stream << std::setprecision(10)
           << "{\n"
           << "  \"contract_version\": 1,\n"
           << "  \"backend\": \"dronegs-fixed-topology-additive-prototype\",\n"
           << "  \"trainer_version\": \"0.5.0-dev.4\",\n"
           << "  \"git_revision\": \"" << json_escape(DRONEGS_GIT_REVISION) << "\",\n"
           << "  \"status\": \"completed\",\n"
           << "  \"started_at\": \"" << json_escape(measurements.started_at) << "\",\n"
           << "  \"finished_at\": \"" << json_escape(measurements.finished_at) << "\",\n"
           << "  \"dataset\": {\n"
           << "    \"path\": \"" << json_escape(options.data_path.string()) << "\",\n"
           << "    \"fingerprint\": \"" << json_escape(fingerprint) << "\",\n"
           << "    \"image_count\": " << scene.images.size() << ",\n"
           << "    \"source_pixels\": null\n"
           << "  },\n"
           << "  \"hardware\": {\n"
           << "    \"gpu\": null, \"driver\": null, \"cuda_runtime\": \"12.8\",\n"
           << "    \"peak_vram_mib\": null\n"
           << "  },\n"
           << "  \"parameters\": {\n"
           << "    \"iterations\": " << options.iterations << ",\n"
           << "    \"strategy\": \"" << json_escape(options.strategy) << "\",\n"
           << "    \"sh_degree\": " << options.sh_degree << ",\n"
           << "    \"max_cap\": " << options.max_cap << ",\n"
           << "    \"resize_factor\": " << options.resize_factor << ",\n"
           << "    \"max_width\": " << options.max_width << ",\n"
           << "    \"tile_mode\": " << options.tile_mode << ",\n"
           << "    \"seed\": " << options.seed << ",\n"
           << "    \"host_image_storage\": \"rgb8\",\n"
           << "    \"host_image_cache_bytes\": " << measurements.image_cache_capacity_bytes << ",\n"
           << "    \"mode\": \"fixed-topology-additive-prototype\"\n"
           << "  },\n"
           << "  \"timings\": {\n"
           << "    \"startup_seconds\": " << measurements.startup_seconds << ",\n"
           << "    \"data_loading_seconds\": " << measurements.loading_seconds << ",\n"
           << "    \"image_decode_seconds\": " << measurements.image_decode_seconds << ",\n"
           << "    \"image_wait_seconds\": " << measurements.image_wait_seconds << ",\n"
           << "    \"training_seconds\": " << measurements.training_seconds << ",\n"
           << "    \"checkpoint_seconds\": " << measurements.export_seconds << ",\n"
           << "    \"wall_seconds\": " << measurements.wall_seconds << "\n"
           << "  },\n"
           << "  \"metrics\": {\n"
           << "    \"initial_loss\": " << measurements.initial_loss << ",\n"
           << "    \"final_loss\": " << measurements.final_loss
           << ", \"final_gaussians\": " << gaussian_count << ",\n"
           << "    \"image_cache_hits\": " << measurements.image_cache_hits << ",\n"
           << "    \"image_cache_misses\": " << measurements.image_cache_misses << ",\n"
           << "    \"image_cache_evictions\": " << measurements.image_cache_evictions << ",\n"
           << "    \"peak_image_cache_bytes\": "
           << measurements.peak_image_cache_bytes << ",\n"
           << "    \"image_prefetch_started\": "
           << measurements.image_prefetch_started << ",\n"
           << "    \"image_prefetch_consumed\": "
           << measurements.image_prefetch_consumed << ",\n"
           << "    \"image_prefetch_ready\": "
           << measurements.image_prefetch_ready << ",\n"
           << "    \"psnr\": null, \"ssim\": null, \"lpips\": null\n"
           << "  },\n"
           << "  \"artifacts\": {\n"
           << "    \"point_cloud.ply\": {\n"
           << "      \"path\": \"" << json_escape(ply_path.string()) << "\",\n"
           << "      \"sha256\": null, \"bytes\": " << std::filesystem::file_size(ply_path) << "\n"
           << "    }\n"
           << "  },\n"
           << "  \"error\": null\n"
           << "}\n";
    stream.close();
    if (!stream) {
        throw std::runtime_error("failed to finalize run manifest");
    }
    std::filesystem::rename(temporary, options.run_manifest);
}

}  // namespace dronegs
