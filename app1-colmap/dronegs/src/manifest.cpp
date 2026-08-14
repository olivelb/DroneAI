// SPDX-License-Identifier: MIT
#include "dronegs/manifest.hpp"
#include "dronegs/training.hpp"

#include <algorithm>
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

#ifndef DRONEGS_CUDA_RUNTIME_VERSION
#define DRONEGS_CUDA_RUNTIME_VERSION "unknown"
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
    const bool reference_absgrad025 =
        options.optimizer_profile ==
            "reference-absolute-absgrad025";
    const bool reference_absgrad050 =
        options.optimizer_profile ==
            "reference-absolute-absgrad050";
    const bool reference_all =
        options.optimizer_profile == "reference-absolute" ||
        reference_absgrad025 || reference_absgrad050;
    const bool dev34_geometry =
        options.optimizer_profile == "dev34-opacity096-reference-scale" ||
        options.optimizer_profile == "dev34-opacity096-reference-rotation" ||
        options.optimizer_profile ==
            "dev34-opacity096-reference-scale-rotation";
    const bool dev35_staged_rotation =
        options.optimizer_profile ==
            "dev35-opacity096-reference-scale-staged-rotation004" ||
        options.optimizer_profile ==
            "dev35-opacity096-reference-scale-staged-rotation008";
    const bool dev36_absgrad =
        options.optimizer_profile ==
            "dev36-staged-rotation008-absgrad025" ||
        options.optimizer_profile ==
            "dev36-staged-rotation008-absgrad050";
    const bool dev37_antialias =
        options.optimizer_profile ==
            "dev37-staged-rotation008-absgrad050-aa005" ||
        options.optimizer_profile ==
            "dev37-staged-rotation008-absgrad050-aa015" ||
        options.optimizer_profile ==
            "dev37-staged-rotation008-absgrad050-aa030";
    const bool dev38_fastgs =
        options.optimizer_profile ==
            "dev38-staged-rotation008-absgrad050-fastgs";
    const bool effective_fastgs =
        options.raster_profile == "fastgs" ||
        (options.raster_profile == "auto" && dev38_fastgs);
    const bool absgrad_enabled =
        reference_absgrad025 || reference_absgrad050 ||
        dev36_absgrad || dev37_antialias || dev38_fastgs;
    const bool staged_rotation =
        dev35_staged_rotation || dev36_absgrad ||
        dev37_antialias || dev38_fastgs;
    const bool calibrated_dc_opacity =
        options.optimizer_profile == "calibrated-dc-0.005-opacity" ||
        options.optimizer_profile == "calibrated-dc-0.010-opacity" ||
        options.optimizer_profile == "calibrated-dc-0.020-opacity" ||
        options.optimizer_profile ==
            "calibrated-dc-0.010-opacity-0.024" ||
        options.optimizer_profile ==
            "calibrated-dc-0.010-opacity-0.048" ||
        options.optimizer_profile ==
            "calibrated-dc-0.010-opacity-0.096" ||
        dev34_geometry ||
        staged_rotation;
    const bool reference_dc =
        reference_all ||
        options.optimizer_profile == "reference-dc-only" ||
        options.optimizer_profile == "reference-dc-opacity" ||
        calibrated_dc_opacity;
    const bool reference_position =
        reference_all ||
        options.optimizer_profile == "reference-position-only";
    const bool reference_opacity =
        reference_all ||
        options.optimizer_profile == "reference-opacity-only" ||
        options.optimizer_profile == "reference-dc-opacity" ||
        calibrated_dc_opacity;
    const bool reference_scale =
        reference_all ||
        options.optimizer_profile == "reference-scale-only" ||
        options.optimizer_profile == "dev34-opacity096-reference-scale" ||
        options.optimizer_profile ==
            "dev34-opacity096-reference-scale-rotation" ||
        staged_rotation;
    const bool reference_rotation =
        reference_all ||
        options.optimizer_profile == "reference-rotation-only" ||
        options.optimizer_profile == "dev34-opacity096-reference-rotation" ||
        options.optimizer_profile ==
            "dev34-opacity096-reference-scale-rotation" ||
        staged_rotation;
    const bool mixed_epsilon =
        options.optimizer_profile != "dronegs-dev16" &&
        !reference_all;
    const char* dc_learning_rate = reference_dc ? "0.002" : "0.05";
    if (options.optimizer_profile == "calibrated-dc-0.005-opacity") {
        dc_learning_rate = "0.005";
    } else if (
        options.optimizer_profile == "calibrated-dc-0.010-opacity" ||
        options.optimizer_profile ==
            "calibrated-dc-0.010-opacity-0.024" ||
        options.optimizer_profile ==
            "calibrated-dc-0.010-opacity-0.048" ||
        options.optimizer_profile ==
            "calibrated-dc-0.010-opacity-0.096" ||
        dev34_geometry ||
        staged_rotation) {
        dc_learning_rate = "0.01";
    } else if (
        options.optimizer_profile == "calibrated-dc-0.020-opacity") {
        dc_learning_rate = "0.02";
    }
    const char* opacity_learning_rate =
        reference_opacity ? "0.012" : "0.01";
    if (options.optimizer_profile ==
        "calibrated-dc-0.010-opacity-0.024") {
        opacity_learning_rate = "0.024";
    } else if (
        options.optimizer_profile ==
            "calibrated-dc-0.010-opacity-0.048") {
        opacity_learning_rate = "0.048";
    } else if (
        options.optimizer_profile ==
            "calibrated-dc-0.010-opacity-0.096" ||
        dev34_geometry ||
        staged_rotation) {
        opacity_learning_rate = "0.096";
    }
    const auto temporary = options.run_manifest.string() + ".tmp";
    std::ofstream stream(temporary, std::ios::trunc);
    if (!stream) {
        throw std::runtime_error("cannot create run manifest: " + temporary);
    }
    stream << std::setprecision(10)
           << "{\n"
           << "  \"contract_version\": 1,\n"
           << "  \"backend\": \"dronegs-native-mrnf-fastgs\",\n"
           << "  \"trainer_version\": \"0.5.0-dev.59\",\n"
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
           << "    \"ignored_image_count\": "
           << measurements.ignored_image_count << ",\n"
           << "    \"source_pixels\": null\n"
           << "  },\n"
           << "  \"hardware\": {\n"
           << "    \"gpu\": null, \"driver\": null, \"cuda_runtime\": \""
           << DRONEGS_CUDA_RUNTIME_VERSION << "\",\n"
           << "    \"peak_vram_mib\": null\n"
           << "  },\n"
           << "  \"parameters\": {\n"
           << "    \"iterations\": " << options.iterations << ",\n"
           << "    \"strategy\": \"" << json_escape(options.strategy) << "\",\n"
           << "    \"sh_degree\": " << options.sh_degree << ",\n"
           << "    \"sh_degree_interval\": "
           << options.sh_degree_interval << ",\n"
           << "    \"sh_schedule\": \"progressive_from_zero\",\n"
           << "    \"appearance_model\": \"color-sh-plus-opacity-sh-v1\",\n"
           << "    \"opacity_sh_learning_rate_ratio\": 0.05,\n"
           << "    \"max_cap\": " << options.max_cap << ",\n"
           << "    \"resize_factor\": " << options.resize_factor << ",\n"
           << "    \"max_width\": " << options.max_width << ",\n"
           << "    \"tile_mode\": " << options.tile_mode << ",\n"
           << "    \"adaptive_native_crop_tiles\": "
           << options.adaptive_native_crop_tiles << ",\n"
           << "    \"native_crop_tile_policy\": \""
           << (options.adaptive_native_crop_tiles != 0U
                   ? "sensor-pixel-budget-up-to-tile-mode-v1"
                   : "fixed-tile-mode-v1")
           << "\",\n"
           << "    \"initial_scale_policy\": \""
           << json_escape(options.initial_scale_policy) << "\",\n"
           << "    \"initial_max_projected_sigma_pixels\": "
           << options.initial_max_projected_sigma_pixels << ",\n"
           << "    \"maximum_scale_growth_factor\": "
           << options.maximum_scale_growth_factor << ",\n"
           << "    \"seed\": " << options.seed << ",\n"
           << "    \"profile_id\": \""
           << json_escape(options.profile_id) << "\",\n"
           << "    \"optimizer_profile\": \""
           << json_escape(options.optimizer_profile) << "\",\n"
           << "    \"pruning_policy\": \""
           << json_escape(options.pruning_policy) << "\",\n"
           << "    \"raster_profile\": \""
           << json_escape(options.raster_profile) << "\",\n"
           << "    \"initial_ply\": "
           << (options.initial_ply.empty()
                   ? "null"
                   : ("\"" +
                      json_escape(options.initial_ply.string()) + "\""))
           << ",\n"
           << "    \"prefetch_depth\": " << options.prefetch_depth << ",\n"
           << "    \"decode_workers\": " << options.decode_workers << ",\n"
           << "    \"jpeg_idct_scale\": " << options.jpeg_idct_scale << ",\n"
           << "    \"test_every\": " << options.test_every << ",\n"
           << "    \"test_split\": \""
           << json_escape(options.test_split) << "\",\n"
           << "    \"test_guard_percent\": "
           << options.test_guard_percent << ",\n"
           << "    \"save_eval_images\": "
           << options.save_eval_images << ",\n"
           << "    \"checkpoint_every\": "
           << options.checkpoint_every << ",\n"
           << "    \"checkpoint_path\": "
           << (options.checkpoint_path.empty()
                   ? "null"
                   : "\"" + json_escape(options.checkpoint_path.string()) + "\"")
           << ",\n"
           << "    \"resumed_from_checkpoint\": "
           << (options.resume_from.empty() ? "false" : "true") << ",\n"
           << "    \"topology_cooldown_iterations\": "
           << options.topology_cooldown << ",\n"
           << "    \"topology_refine_through_iteration\": "
           << topology_refinement_end_iteration(
                  options.iterations, options.topology_cooldown,
                  options.adaptive_growth_target != 0U)
           << ",\n"
           << "    \"photometric_finish_iterations\": "
           << options.photometric_finish << ",\n"
           << "    \"photometric_final_mse_percent\": "
           << options.photometric_mse_percent << ",\n"
           << "    \"adaptive_growth_target\": "
           << (options.adaptive_growth_target != 0U ? "true" : "false")
           << ",\n"
           << "    \"photometric_finish_start_after_iteration\": "
           << (options.iterations - options.photometric_finish) << ",\n"
           << "    \"training_loss_telemetry\": "
              "\"baseline_l1_dssim_for_cross_run_comparability\",\n"
           << "    \"held_out_rule\": "
           << (options.test_split == "spatial-block"
                   ? "\"central_planar_camera_block_with_guard_ring\""
                   : "\"scene_index_modulo_test_every_equals_zero\"")
           << ",\n"
           << "    \"quality_data_range\": 1.0,\n"
           << "    \"ssim_window\": 11,\n"
           << "    \"ssim_sigma\": 1.5,\n"
           << "    \"ssim_padding\": \"valid\",\n"
           << "    \"loss\": "
              "\"linear_blend_of_0.8_active_pixel_l1_plus_0.2_dssim"
              "_and_active_pixel_mse\",\n"
           << "    \"lambda_dssim\": 0.2,\n"
           << "    \"topology_growth\": "
              "\"deterministic_weighted_gumbel_long_axis_split\",\n"
           << "    \"refine_every\": 200,\n"
           << "    \"grow_until_iteration\": "
           << std::min(
                  topology_refinement_end_iteration(
                      options.iterations, options.topology_cooldown,
                      options.adaptive_growth_target != 0U),
                  topology_growth_end_iteration(options.iterations))
           << ",\n"
           << "    \"prune_until_iteration\": "
           << topology_refinement_end_iteration(
                  options.iterations, options.topology_cooldown,
                  options.adaptive_growth_target != 0U)
           << ",\n"
           << "    \"growth_threshold\": 0.003,\n"
           << "    \"growth_fraction\": "
           << (options.adaptive_growth_target != 0U ? "null" : "0.07")
           << ",\n"
           << "    \"growth_fraction_policy\": \""
           << (options.adaptive_growth_target != 0U
                   ? "capacity_targeted_0.07_to_0.50"
                   : "fixed_0.07")
           << "\",\n"
           << "    \"opacity_prune_threshold\": 0.003921568627,\n"
           << "    \"minimum_scale\": 1e-10,\n"
           << "    \"means_noise_weight\": 50.0,\n"
           << "    \"means_noise_until_iteration\": "
           << topology_growth_end_iteration(options.iterations) << ",\n"
           << "    \"means_noise_opacity_exponent\": 150.0,\n"
           << "    \"opacity_decay\": 0.004,\n"
           << "    \"scale_decay\": 0.002,\n"
           << "    \"topology_compaction\": \"hard_dense_preserve_adam\",\n"
           << "    \"growth_score\": "
           << (absgrad_enabled
                   ? "\"mrnf_error_edge_times_robust_abs_projected_gradient\""
                   : "\"max_normalized_ssim_error_weighted_alpha_contribution\"")
           << ",\n"
           << "    \"growth_gradient_threshold\": 0.003,\n"
           << "    \"growth_selection\": "
              "\"log_guided_weight_plus_splitmix64_gumbel_top_k\",\n"
           << "    \"growth_seed_protocol\": "
              "\"cli_seed_xor_iteration_times_golden_ratio_64_then_source_splitmix64\",\n"
           << "    \"edge_guidance\": "
              "\"training_view_sobel_luminance_alpha_contribution_positive_median\",\n"
           << "    \"edge_score_weight\": 0.25,\n"
           << "    \"edge_extra_render_passes\": 0,\n"
           << "    \"absgrad_guidance\": "
           << (absgrad_enabled
                   ? "\"homodirectional_per_pixel_projected_center_gradient\""
                   : "null")
           << ",\n"
           << "    \"absgrad_normalization\": "
           << (absgrad_enabled
                   ? "\"per_visible_view_positive_median_clamped_4\""
                   : "null")
           << ",\n"
           << "    \"absgrad_score_weight\": "
           << (reference_absgrad025 ||
                       options.optimizer_profile ==
                           "dev36-staged-rotation008-absgrad025"
                   ? "0.25"
                   : (absgrad_enabled
                          ? "0.50"
                          : "0.0"))
           << ",\n"
           << "    \"antialias_filter_variance\": "
           << (options.optimizer_profile ==
                       "dev37-staged-rotation008-absgrad050-aa005"
                   ? "0.05"
                   : (options.optimizer_profile ==
                              "dev37-staged-rotation008-absgrad050-aa015"
                          ? "0.15"
                          : (options.optimizer_profile ==
                                     "dev37-staged-rotation008-absgrad050-aa030"
                                 ? "0.30"
                                 : "0.0")))
           << ",\n"
           << "    \"antialias_compensation\": "
           << (dev37_antialias
                   ? "\"sqrt_det_original_over_det_filtered_with_exact_vjp\""
                   : "null")
           << ",\n"
           << "    \"effective_raster_profile\": "
           << (effective_fastgs ? "\"fastgs\"" : "\"bounded\"")
           << ",\n"
           << "    \"projected_covariance_regularization\": "
           << (effective_fastgs
                   ? "\"additive_0.3_identity_no_spectral_clamp\""
                   : "\"spectral_clamp_0.5625_to_64\"")
           << ",\n"
           << "    \"splat_support\": "
           << (effective_fastgs
                   ? "\"opacity_dependent_alpha_1_over_255\""
                   : "\"fixed_2.5_sigma\"")
           << ",\n"
           << "    \"maximum_fragment_alpha\": "
           << (effective_fastgs ? "0.999" : "0.99")
           << ",\n"
           << "    \"adam_epsilon\": "
           << (mixed_epsilon
                   ? "null"
                   : (reference_all ? "1e-15" : "1e-8")) << ",\n"
           << "    \"position_adam_epsilon\": "
           << (reference_position ? "1e-15" : "1e-8") << ",\n"
           << "    \"dc_adam_epsilon\": "
           << (reference_dc ? "1e-15" : "1e-8") << ",\n"
           << "    \"opacity_adam_epsilon\": "
           << (reference_opacity ? "1e-15" : "1e-8") << ",\n"
           << "    \"scale_adam_epsilon\": "
           << (reference_scale ? "1e-15" : "1e-8") << ",\n"
           << "    \"rotation_adam_epsilon\": "
           << (reference_rotation ? "1e-15" : "1e-8") << ",\n"
           << "    \"dc_lr\": "
           << dc_learning_rate << ",\n"
           << "    \"opacity_lr\": "
           << opacity_learning_rate << ",\n"
           << "    \"position_lr_initial_factor\": "
           << (reference_position ? "0.00002" : "0.00016") << ",\n"
           << "    \"position_lr_final_factor\": "
           << (reference_position ? "0.0000002" : "0.0000016")
           << ",\n"
           << "    \"position_lr_scale\": "
           << (reference_position
                   ? "\"initial_gaussian_axis_10_90_percentile_median_width\""
                   : "\"initial_gaussian_bbox_diagonal\"")
           << ",\n"
           << "    \"position_lr_schedule\": "
           << (reference_position
                   ? "\"exponential_step_minus_one_over_iterations\""
                   : "\"exponential_step_minus_one_over_iterations_minus_one\"")
           << ",\n"
           << "    \"scale_lr_initial\": "
           << (reference_scale ? "0.007" : "0.005") << ",\n"
           << "    \"scale_lr_final\": 0.005,\n"
           << "    \"scale_lr_schedule\": "
           << (reference_scale
                   ? "\"exponential_step_minus_one_over_iterations\""
                   : "\"constant\"")
           << ",\n"
           << "    \"rotation_lr\": "
           << (staged_rotation
                   ? "0.001"
                   : (reference_rotation ? "0.002" : "0.001"))
           << ",\n"
           << "    \"rotation_lr_final\": "
           << (options.optimizer_profile ==
                       "dev35-opacity096-reference-scale-staged-rotation004"
                   ? "0.004"
                   : (options.optimizer_profile ==
                                  "dev35-opacity096-reference-scale-staged-rotation008" ||
                              absgrad_enabled
                          ? "0.008"
                          : (reference_rotation ? "0.002" : "0.001")))
           << ",\n"
           << "    \"rotation_lr_schedule\": "
           << (staged_rotation
                   ? "\"piecewise_constant_0.4\""
                   : "\"constant\"")
           << ",\n"
           << "    \"rotation_lr_switch_fraction\": "
           << (staged_rotation ? "0.4" : "null") << ",\n"
           << "    \"optimizer_telemetry\": "
              "\"deterministic_approximately_4096_gaussians_steps_1_and_fifths\",\n"
           << "    \"log_scale_limit_delta\": 4.0,\n"
           << "    \"host_image_storage\": \"rgb8\",\n"
           << "    \"host_image_cache_limit_mib\": "
           << options.host_image_cache_mib << ",\n"
           << "    \"host_image_cache_bytes\": " << measurements.image_cache_capacity_bytes << ",\n"
           << "    \"mode\": "
              "\"mrnf-intermediate-dc-calibration-anisotropic-dssim-held-out-prototype\"\n"
           << "  },\n"
           << "  \"timings\": {\n"
           << "    \"startup_seconds\": " << measurements.startup_seconds << ",\n"
           << "    \"data_loading_seconds\": " << measurements.loading_seconds << ",\n"
           << "    \"image_decode_seconds\": " << measurements.image_decode_seconds << ",\n"
           << "    \"image_wait_seconds\": " << measurements.image_wait_seconds << ",\n"
           << "    \"training_seconds\": " << measurements.training_seconds << ",\n"
           << "    \"topology_refinement_seconds\": "
           << measurements.topology_refinement_seconds << ",\n"
           << "    \"periodic_checkpoint_seconds\": "
           << measurements.periodic_checkpoint_seconds << ",\n"
           << "    \"checkpoint_snapshot_seconds\": "
           << measurements.checkpoint_snapshot_seconds << ",\n"
           << "    \"checkpoint_wait_seconds\": "
           << measurements.checkpoint_wait_seconds << ",\n"
           << "    \"checkpoint_write_seconds\": "
           << measurements.checkpoint_write_seconds << ",\n"
           << "    \"evaluation_seconds\": "
           << measurements.evaluation_seconds << ",\n"
           << "    \"final_ply_export_seconds\": "
           << measurements.export_seconds << ",\n"
           << "    \"checkpoint_seconds\": "
           << measurements.export_seconds << ",\n"
           << "    \"wall_seconds\": " << measurements.wall_seconds << "\n"
           << "  },\n"
           << "  \"metrics\": {\n"
           << "    \"initial_loss\": " << measurements.initial_loss << ",\n"
           << "    \"final_loss\": " << measurements.final_loss
           << ", \"final_gaussians\": " << gaussian_count << ",\n"
           << "    \"periodic_checkpoints\": "
           << measurements.periodic_checkpoints << ",\n"
           << "    \"final_active_sh_degree\": "
           << measurements.final_active_sh_degree << ",\n"
           << "    \"image_cache_hits\": " << measurements.image_cache_hits << ",\n"
           << "    \"image_cache_misses\": " << measurements.image_cache_misses << ",\n"
           << "    \"image_cache_evictions\": " << measurements.image_cache_evictions << ",\n"
           << "    \"image_cache_working_set_bytes\": "
           << measurements.image_cache_working_set_bytes << ",\n"
           << "    \"peak_image_cache_bytes\": "
           << measurements.peak_image_cache_bytes << ",\n"
           << "    \"image_prefetch_started\": "
           << measurements.image_prefetch_started << ",\n"
           << "    \"image_prefetch_consumed\": "
           << measurements.image_prefetch_consumed << ",\n"
           << "    \"image_prefetch_ready\": "
           << measurements.image_prefetch_ready << ",\n"
           << "    \"frame_descriptor_count\": "
           << measurements.frame_descriptor_count << ",\n"
           << "    \"training_frame_count\": "
           << measurements.training_frame_count << ",\n"
           << "    \"held_out_frame_count\": "
           << measurements.held_out_frame_count << ",\n"
           << "    \"ignored_frame_count\": "
           << measurements.ignored_frame_count << ",\n"
           << "    \"topology_refinements\": "
           << measurements.topology_refinements << ",\n"
           << "    \"gaussians_added\": "
           << measurements.gaussians_added << ",\n"
           << "    \"gaussians_pruned\": "
           << measurements.gaussians_pruned << ",\n"
           << "    \"gaussian_slots_reused\": "
           << measurements.gaussian_slots_reused << ",\n"
           << "    \"topology_compactions\": "
           << measurements.topology_compactions << ",\n"
           << "    \"initial_held_out_psnr\": "
           << json_number(measurements.initial_held_out_psnr) << ",\n"
           << "    \"initial_held_out_ssim\": "
           << json_number(measurements.initial_held_out_ssim) << ",\n"
           << "    \"initial_pixel_weighted_psnr\": "
           << json_number(measurements.initial_pixel_weighted_psnr) << ",\n"
           << "    \"initial_pixel_weighted_ssim\": "
           << json_number(measurements.initial_pixel_weighted_ssim) << ",\n"
           << "    \"psnr\": "
           << json_number(measurements.final_held_out_psnr) << ",\n"
           << "    \"ssim\": "
           << json_number(measurements.final_held_out_ssim)
           << ",\n"
           << "    \"pixel_weighted_psnr\": "
           << json_number(measurements.final_pixel_weighted_psnr) << ",\n"
           << "    \"pixel_weighted_ssim\": "
           << json_number(measurements.final_pixel_weighted_ssim)
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
