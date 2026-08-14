// SPDX-License-Identifier: MIT
#include <chrono>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string_view>

#include "dronegs/cli.hpp"
#include "dronegs/colmap.hpp"
#include "dronegs/manifest.hpp"
#include "dronegs/model.hpp"
#include "dronegs/ply.hpp"
#include "dronegs/training.hpp"

int main(int argc, char** argv) {
    using clock = std::chrono::steady_clock;
    if (argc == 2 && std::string_view(argv[1]) == "--help") {
        std::cout << dronegs::help_text();
        return 0;
    }
    if (argc == 2 && std::string_view(argv[1]) == "--version") {
        std::cout
            << "DroneGS 0.5.0-dev.50 deferred-metrics "
               "crop-aware-scale portable-CUDA "
               "shared-backward MRNF prototype\n";
        return 0;
    }

    const auto wall_start = clock::now();
    try {
        const auto options = dronegs::parse_options(argc, argv);
        std::filesystem::create_directories(options.output_path);
        const dronegs::RunMeasurements initial{
            .started_at = dronegs::utc_timestamp(),
        };
        std::cerr << "DroneGS 0.5.0-dev.50 uses an independent "
                     "bounded/FastGS raster profile plus compensated-antialias "
                     "AbsGrad-guided "
                     "extended-color crop-aware local/projected-KNN "
                     "initialized "
                     "experimental anisotropic "
                     "ordered-alpha training with reproducible weighted-"
                     "Gumbel MRNF growth, Sobel edge guidance, and MRNF "
                     "optimizer profiles validated on two drone scenes; "
                     "the objective is 0.8 L1 + 0.2 DSSIM and held-out "
                     "PSNR/SSIM and exact-pair external LPIPS are available, "
                     "with progressive SH and a complete deterministic MRNF "
                     "prune/reuse/noise/decay lifecycle; dev40 decouples "
                     "the rasterizer from optimizer rates and recycles "
                     "pruned slots in place on GPU at capacity; dev41 "
                     "reuses forward transmittance and active-range state "
                     "during backward; dev42 ports scanned buckets, packed "
                     "checkpoints, warp-cooperative backward, and fused "
                     "L1/SSIM while retaining DroneGS orchestration; dev43 "
                     "auto-sizes the decoded RGB8 cache under a configurable "
                     "ceiling with a 2 GiB default, so thousand-view scenes "
                     "can become resident without unbounded host RAM growth; "
                     "dev44 can reserve "
                     "an explicit fixed-topology cooldown at the end of the "
                     "same iteration budget for convergence ablations; dev45 "
                     "can linearly blend the final objective toward active-"
                     "pixel MSE to trade excess perceptual margin for PSNR "
                     "without adding iterations; dev49 derives projected "
                     "initial scales from the actual crop cameras and "
                     "scales exact capacity growth to every operator-"
                     "selected iteration budget.\n";
        std::cout << "{\"event\":\"progress\",\"iteration\":0,"
                     "\"iterations\":" << options.iterations
                  << ",\"loss\":0.0,\"gaussians\":0}\n" << std::flush;

        const auto loading_start = clock::now();
        const auto scene = dronegs::load_colmap_scene(options.data_path);
        const auto fingerprint =
            options.dataset_fingerprint.empty()
                ? dronegs::dataset_fingerprint(scene, options.data_path)
                : options.dataset_fingerprint;
        const auto loading_end = clock::now();
        if (scene.points.size() > options.max_cap &&
            options.initial_ply.empty()) {
            throw std::runtime_error("sparse point count exceeds --max-cap");
        }
        auto gaussians = [&]() {
            if (options.initial_ply.empty()) {
                const auto policy = options.initial_scale_policy ==
                        "projected-knn"
                    ? dronegs::InitialScalePolicy::projected_knn
                    : dronegs::InitialScalePolicy::local_knn;
                auto initialization = dronegs::initialize_fixed_topology(
                    scene,
                    {
                        .policy = policy,
                        .maximum_projected_sigma_pixels =
                            options.initial_max_projected_sigma_pixels,
                        .resize_factor = options.resize_factor,
                        .maximum_image_width = options.max_width,
                        .tile_mode = options.tile_mode,
                        .adaptive_native_crop_tiles =
                            options.adaptive_native_crop_tiles != 0U,
                    });
                const auto& statistics = initialization.statistics;
                std::cout
                    << "{\"event\":\"gaussian_initialization\","
                    << "\"policy\":\"" << options.initial_scale_policy
                    << "\",\"gaussians\":" << statistics.gaussian_count
                    << ",\"projection_supported\":"
                    << statistics.projection_supported_count
                    << ",\"projected_scale_clamped\":"
                    << statistics.projected_scale_clamped_count
                    << ",\"projected_sigma_before_p50\":"
                    << statistics.projected_sigma_before_p50
                    << ",\"projected_sigma_before_p95\":"
                    << statistics.projected_sigma_before_p95
                    << ",\"projected_sigma_before_maximum\":"
                    << statistics.projected_sigma_before_maximum
                    << ",\"projected_sigma_after_p50\":"
                    << statistics.projected_sigma_after_p50
                    << ",\"projected_sigma_after_p95\":"
                    << statistics.projected_sigma_after_p95
                    << ",\"projected_sigma_after_maximum\":"
                    << statistics.projected_sigma_after_maximum
                    << "}\n" << std::flush;
                return std::move(initialization.gaussians);
            }
            auto loaded =
                dronegs::read_gaussian_ply(options.initial_ply);
            if (loaded.sh_degree < options.sh_degree) {
                throw std::runtime_error(
                    "initial PLY does not contain the requested SH degree");
            }
            return std::move(loaded.gaussians);
        }();
        if (gaussians.size() > options.max_cap) {
            throw std::runtime_error(
                "initial PLY Gaussian count exceeds --max-cap");
        }
        const auto training = dronegs::train_ordered_mrnf(
            options, scene, gaussians);
        if (!training.completed) {
            std::cout
                << "{\"event\":\"training_paused\",\"iteration\":"
                << training.completed_iterations
                << ",\"iterations\":" << options.iterations
                << ",\"checkpoint\":\""
                << options.checkpoint_path.string() << "\"}\n"
                << std::flush;
            return 75;
        }

        const auto ply_path = options.output_path / "point_cloud.ply";
        const auto export_start = clock::now();
        dronegs::write_gaussian_ply(ply_path, gaussians, options.sh_degree);
        const auto export_end = clock::now();

        dronegs::RunMeasurements measurements = initial;
        measurements.finished_at = dronegs::utc_timestamp();
        measurements.loading_seconds =
            std::chrono::duration<double>(loading_end - loading_start).count() +
            training.image_loading_seconds;
        measurements.image_decode_seconds = training.image_decode_seconds;
        measurements.image_wait_seconds = training.image_loading_seconds;
        measurements.training_seconds = training.training_seconds;
        measurements.topology_refinement_seconds =
            training.topology_refinement_seconds;
        measurements.periodic_checkpoint_seconds =
            training.periodic_checkpoint_seconds;
        measurements.evaluation_seconds = training.evaluation_seconds;
        measurements.initial_loss = training.initial_loss;
        measurements.startup_seconds = training.setup_seconds;
        measurements.final_loss = training.final_loss;
        measurements.image_cache_hits = training.image_cache_hits;
        measurements.image_cache_misses = training.image_cache_misses;
        measurements.image_cache_evictions = training.image_cache_evictions;
        measurements.image_cache_capacity_bytes = training.image_cache_capacity_bytes;
        measurements.image_cache_working_set_bytes =
            training.image_cache_working_set_bytes;
        measurements.peak_image_cache_bytes = training.peak_image_cache_bytes;
        measurements.image_prefetch_started = training.image_prefetch_started;
        measurements.image_prefetch_consumed = training.image_prefetch_consumed;
        measurements.image_prefetch_ready = training.image_prefetch_ready;
        measurements.training_image_count =
            training.training_image_count;
        measurements.held_out_image_count =
            training.held_out_image_count;
        measurements.ignored_image_count =
            training.ignored_image_count;
        measurements.frame_descriptor_count =
            training.frame_descriptor_count;
        measurements.training_frame_count =
            training.training_frame_count;
        measurements.held_out_frame_count =
            training.held_out_frame_count;
        measurements.ignored_frame_count =
            training.ignored_frame_count;
        measurements.topology_refinements =
            training.topology_refinements;
        measurements.gaussians_added =
            training.gaussians_added;
        measurements.gaussians_pruned =
            training.gaussians_pruned;
        measurements.gaussian_slots_reused =
            training.gaussian_slots_reused;
        measurements.topology_compactions =
            training.topology_compactions;
        measurements.final_active_sh_degree =
            training.final_active_sh_degree;
        measurements.initial_held_out_psnr =
            training.initial_held_out_psnr;
        measurements.initial_held_out_ssim =
            training.initial_held_out_ssim;
        measurements.initial_pixel_weighted_psnr =
            training.initial_pixel_weighted_psnr;
        measurements.initial_pixel_weighted_ssim =
            training.initial_pixel_weighted_ssim;
        measurements.final_held_out_psnr =
            training.final_held_out_psnr;
        measurements.final_held_out_ssim =
            training.final_held_out_ssim;
        measurements.final_pixel_weighted_psnr =
            training.final_pixel_weighted_psnr;
        measurements.final_pixel_weighted_ssim =
            training.final_pixel_weighted_ssim;
        measurements.export_seconds =
            std::chrono::duration<double>(export_end - export_start).count();
        measurements.wall_seconds =
            std::chrono::duration<double>(export_end - wall_start).count();
        dronegs::write_completed_manifest(
            options, scene, fingerprint, measurements,
            ply_path, gaussians.size());

        std::cout << "{\"event\":\"progress\",\"iteration\":" << options.iterations
                  << ",\"iterations\":" << options.iterations
                  << ",\"loss\":" << training.final_loss
                  << ",\"gaussians\":" << gaussians.size() << "}\n";
        return 0;
    } catch (const std::invalid_argument& error) {
        std::cerr << "invalid arguments: " << error.what() << "\n";
        std::cerr << dronegs::help_text();
        return 2;
    } catch (const std::exception& error) {
        std::cerr << "DroneGS native slice failed: " << error.what() << "\n";
        return 10;
    }
}
