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

std::string json_number(const std::optional<float>& value) {
    if (!value.has_value()) {
        return "null";
    }
    std::ostringstream stream;
    stream << std::setprecision(10) << *value;
    return stream.str();
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
    const bool lichtfeld_all =
        options.optimizer_profile == "lichtfeld-absolute";
    const bool lichtfeld_dc =
        lichtfeld_all ||
        options.optimizer_profile == "lichtfeld-dc-only";
    const bool lichtfeld_position =
        lichtfeld_all ||
        options.optimizer_profile == "lichtfeld-position-only";
    const bool lichtfeld_opacity =
        lichtfeld_all ||
        options.optimizer_profile == "lichtfeld-opacity-only";
    const bool lichtfeld_scale =
        lichtfeld_all ||
        options.optimizer_profile == "lichtfeld-scale-only";
    const bool lichtfeld_rotation =
        lichtfeld_all ||
        options.optimizer_profile == "lichtfeld-rotation-only";
    const bool mixed_epsilon =
        options.optimizer_profile != "dronegs-dev16" &&
        !lichtfeld_all;
    const auto temporary = options.run_manifest.string() + ".tmp";
    std::ofstream stream(temporary, std::ios::trunc);
    if (!stream) {
        throw std::runtime_error("cannot create run manifest: " + temporary);
    }
    stream << std::setprecision(10)
           << "{\n"
           << "  \"contract_version\": 1,\n"
           << "  \"backend\": \"dronegs-mrnf-optimizer-ablation-prototype\",\n"
           << "  \"trainer_version\": \"0.5.0-dev.19\",\n"
           << "  \"git_revision\": \"" << json_escape(DRONEGS_GIT_REVISION) << "\",\n"
           << "  \"status\": \"completed\",\n"
           << "  \"started_at\": \"" << json_escape(measurements.started_at) << "\",\n"
           << "  \"finished_at\": \"" << json_escape(measurements.finished_at) << "\",\n"
           << "  \"dataset\": {\n"
           << "    \"path\": \"" << json_escape(options.data_path.string()) << "\",\n"
           << "    \"fingerprint\": \"" << json_escape(fingerprint) << "\",\n"
           << "    \"image_count\": " << scene.images.size() << ",\n"
           << "    \"training_image_count\": "
           << measurements.training_image_count << ",\n"
           << "    \"held_out_image_count\": "
           << measurements.held_out_image_count << ",\n"
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
           << "    \"optimizer_profile\": \""
           << json_escape(options.optimizer_profile) << "\",\n"
           << "    \"prefetch_depth\": " << options.prefetch_depth << ",\n"
           << "    \"decode_workers\": " << options.decode_workers << ",\n"
           << "    \"jpeg_idct_scale\": " << options.jpeg_idct_scale << ",\n"
           << "    \"test_every\": " << options.test_every << ",\n"
           << "    \"save_eval_images\": "
           << options.save_eval_images << ",\n"
           << "    \"held_out_rule\": "
              "\"scene_index_modulo_test_every_equals_zero\",\n"
           << "    \"quality_data_range\": 1.0,\n"
           << "    \"ssim_window\": 11,\n"
           << "    \"ssim_sigma\": 1.5,\n"
           << "    \"ssim_padding\": \"valid\",\n"
           << "    \"loss\": \"0.8_active_pixel_l1_plus_0.2_dssim\",\n"
           << "    \"lambda_dssim\": 0.2,\n"
           << "    \"topology_growth\": "
              "\"deterministic_weighted_gumbel_long_axis_split\",\n"
           << "    \"growth_score\": "
              "\"max_normalized_ssim_error_weighted_alpha_contribution\",\n"
           << "    \"growth_gradient_threshold\": 0.003,\n"
           << "    \"grow_fraction\": 0.07,\n"
           << "    \"refine_every\": 200,\n"
           << "    \"grow_until_iteration\": 15000,\n"
           << "    \"growth_selection\": "
              "\"log_guided_weight_plus_splitmix64_gumbel_top_k\",\n"
           << "    \"growth_seed_protocol\": "
              "\"cli_seed_xor_iteration_times_golden_ratio_64_then_source_splitmix64\",\n"
           << "    \"edge_guidance\": "
              "\"training_view_sobel_luminance_alpha_contribution_positive_median\",\n"
           << "    \"edge_score_weight\": 0.25,\n"
           << "    \"edge_extra_render_passes\": 0,\n"
           << "    \"adam_epsilon\": "
           << (mixed_epsilon
                   ? "null"
                   : (lichtfeld_all ? "1e-15" : "1e-8")) << ",\n"
           << "    \"position_adam_epsilon\": "
           << (lichtfeld_position ? "1e-15" : "1e-8") << ",\n"
           << "    \"dc_adam_epsilon\": "
           << (lichtfeld_dc ? "1e-15" : "1e-8") << ",\n"
           << "    \"opacity_adam_epsilon\": "
           << (lichtfeld_opacity ? "1e-15" : "1e-8") << ",\n"
           << "    \"scale_adam_epsilon\": "
           << (lichtfeld_scale ? "1e-15" : "1e-8") << ",\n"
           << "    \"rotation_adam_epsilon\": "
           << (lichtfeld_rotation ? "1e-15" : "1e-8") << ",\n"
           << "    \"dc_lr\": "
           << (lichtfeld_dc ? "0.002" : "0.05") << ",\n"
           << "    \"opacity_lr\": "
           << (lichtfeld_opacity ? "0.012" : "0.01") << ",\n"
           << "    \"position_lr_initial_factor\": "
           << (lichtfeld_position ? "0.00002" : "0.00016") << ",\n"
           << "    \"position_lr_final_factor\": "
           << (lichtfeld_position ? "0.0000002" : "0.0000016")
           << ",\n"
           << "    \"position_lr_scale\": "
           << (lichtfeld_position
                   ? "\"initial_gaussian_axis_10_90_percentile_median_width\""
                   : "\"initial_gaussian_bbox_diagonal\"")
           << ",\n"
           << "    \"position_lr_schedule\": "
           << (lichtfeld_position
                   ? "\"exponential_step_minus_one_over_iterations\""
                   : "\"exponential_step_minus_one_over_iterations_minus_one\"")
           << ",\n"
           << "    \"scale_lr_initial\": "
           << (lichtfeld_scale ? "0.007" : "0.005") << ",\n"
           << "    \"scale_lr_final\": 0.005,\n"
           << "    \"scale_lr_schedule\": "
           << (lichtfeld_scale
                   ? "\"exponential_step_minus_one_over_iterations\""
                   : "\"constant\"")
           << ",\n"
           << "    \"rotation_lr\": "
           << (lichtfeld_rotation ? "0.002" : "0.001") << ",\n"
           << "    \"optimizer_telemetry\": "
              "\"deterministic_approximately_4096_gaussians_steps_1_and_fifths\",\n"
           << "    \"log_scale_limit_delta\": 4.0,\n"
           << "    \"host_image_storage\": \"rgb8\",\n"
           << "    \"host_image_cache_bytes\": " << measurements.image_cache_capacity_bytes << ",\n"
           << "    \"mode\": "
              "\"mrnf-optimizer-ablation-anisotropic-dssim-held-out-prototype\"\n"
           << "  },\n"
           << "  \"timings\": {\n"
           << "    \"startup_seconds\": " << measurements.startup_seconds << ",\n"
           << "    \"data_loading_seconds\": " << measurements.loading_seconds << ",\n"
           << "    \"image_decode_seconds\": " << measurements.image_decode_seconds << ",\n"
           << "    \"image_wait_seconds\": " << measurements.image_wait_seconds << ",\n"
           << "    \"training_seconds\": " << measurements.training_seconds << ",\n"
           << "    \"evaluation_seconds\": "
           << measurements.evaluation_seconds << ",\n"
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
           << "    \"topology_refinements\": "
           << measurements.topology_refinements << ",\n"
           << "    \"gaussians_added\": "
           << measurements.gaussians_added << ",\n"
           << "    \"initial_held_out_psnr\": "
           << json_number(measurements.initial_held_out_psnr) << ",\n"
           << "    \"initial_held_out_ssim\": "
           << json_number(measurements.initial_held_out_ssim) << ",\n"
           << "    \"psnr\": "
           << json_number(measurements.final_held_out_psnr) << ",\n"
           << "    \"ssim\": "
           << json_number(measurements.final_held_out_ssim)
           << ", \"lpips\": null\n"
           << "  },\n"
           << "  \"artifacts\": {\n"
           << "    \"point_cloud.ply\": {\n"
           << "      \"path\": \"" << json_escape(ply_path.string()) << "\",\n"
           << "      \"sha256\": null, \"bytes\": " << std::filesystem::file_size(ply_path) << "\n"
           << "    }";
    const auto evaluation_csv =
        options.output_path / "evaluation" / "metrics.csv";
    if (std::filesystem::is_regular_file(evaluation_csv)) {
        stream
            << ",\n"
            << "    \"evaluation/metrics.csv\": {\n"
            << "      \"path\": \""
            << json_escape(evaluation_csv.string()) << "\",\n"
            << "      \"sha256\": null, \"bytes\": "
            << std::filesystem::file_size(evaluation_csv) << "\n"
            << "    }";
    }
    stream
           << "\n  },\n"
           << "  \"error\": null\n"
           << "}\n";
    stream.close();
    if (!stream) {
        throw std::runtime_error("failed to finalize run manifest");
    }
    std::filesystem::rename(temporary, options.run_manifest);
}

}  // namespace dronegs
