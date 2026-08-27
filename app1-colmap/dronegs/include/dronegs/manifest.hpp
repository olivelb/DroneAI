// SPDX-License-Identifier: MIT
#pragma once

#include <cstdint>
#include <cstddef>
#include <filesystem>
#include <optional>
#include <string>

#include "dronegs/types.hpp"
#include "dronegs/topology_telemetry.hpp"

namespace dronegs {

struct RunMeasurements {
    std::string started_at;
    std::string finished_at;
    double loading_seconds = 0.0;
    double image_decode_seconds = 0.0;
    double image_wait_seconds = 0.0;
    double startup_seconds = 0.0;
    double training_seconds = 0.0;
    double topology_refinement_seconds = 0.0;
    TopologyRefinementTelemetry topology_telemetry;
    double periodic_checkpoint_seconds = 0.0;
    double checkpoint_snapshot_seconds = 0.0;
    double checkpoint_wait_seconds = 0.0;
    double checkpoint_write_seconds = 0.0;
    std::uint64_t periodic_checkpoints = 0;
    double evaluation_seconds = 0.0;
    double export_seconds = 0.0;
    double wall_seconds = 0.0;
    float initial_loss = 0.0F;
    float final_loss = 0.0F;
    std::uint64_t image_cache_hits = 0;
    std::uint64_t image_cache_misses = 0;
    std::uint64_t image_cache_evictions = 0;
    std::uint64_t image_cache_capacity_bytes = 0;
    std::uint64_t image_cache_working_set_bytes = 0;
    std::uint64_t peak_image_cache_bytes = 0;
    std::uint64_t image_prefetch_started = 0;
    std::uint64_t image_prefetch_consumed = 0;
    std::uint64_t image_prefetch_ready = 0;
    std::uint64_t training_image_count = 0;
    std::uint64_t held_out_image_count = 0;
    std::uint64_t ignored_image_count = 0;
    std::uint64_t frame_descriptor_count = 0;
    std::uint64_t training_frame_count = 0;
    std::uint64_t held_out_frame_count = 0;
    std::uint64_t ignored_frame_count = 0;
    std::uint64_t topology_refinements = 0;
    std::uint64_t gaussians_added = 0;
    std::uint64_t gaussians_pruned = 0;
    std::uint64_t gaussian_slots_reused = 0;
    std::uint64_t topology_compactions = 0;
    std::uint32_t final_active_sh_degree = 0U;
    std::optional<float> initial_held_out_psnr;
    std::optional<float> initial_held_out_ssim;
    std::optional<float> initial_pixel_weighted_psnr;
    std::optional<float> initial_pixel_weighted_ssim;
    std::optional<float> final_held_out_psnr;
    std::optional<float> final_held_out_ssim;
    std::optional<float> final_pixel_weighted_psnr;
    std::optional<float> final_pixel_weighted_ssim;
};

std::string utc_timestamp();
void write_completed_manifest(const Options& options, const Scene& scene,
                              const std::string& fingerprint,
                              const RunMeasurements& measurements,
                              const std::filesystem::path& ply_path,
                              std::size_t gaussian_count);

}  // namespace dronegs
