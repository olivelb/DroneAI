// SPDX-License-Identifier: MIT
#include <array>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <iostream>
#include <fstream>
#include <iterator>
#include <string>
#include "dronegs/training_benchmark.hpp"
#include <stdexcept>

#include <jpeglib.h>

#include "dronegs/model.hpp"
#include "dronegs/ordered_training.hpp"
#include "dronegs/supported_frame.hpp"
#include "dronegs/training.hpp"

namespace {

void write_fixture_image(const std::filesystem::path& path) {
    auto* file = std::fopen(path.string().c_str(), "wb");
    if (file == nullptr) {
        throw std::runtime_error("cannot create tile training fixture");
    }
    jpeg_compress_struct compressor{};
    jpeg_error_mgr error{};
    compressor.err = jpeg_std_error(&error);
    jpeg_create_compress(&compressor);
    jpeg_stdio_dest(&compressor, file);
    compressor.image_width = 32U;
    compressor.image_height = 32U;
    compressor.input_components = 3;
    compressor.in_color_space = JCS_RGB;
    jpeg_set_defaults(&compressor);
    jpeg_set_quality(&compressor, 95, TRUE);
    jpeg_start_compress(&compressor, TRUE);
    std::array<JSAMPLE, 32U * 3U> row{};
    for (std::size_t x = 0U; x < 32U; ++x) {
        row[x * 3U] = static_cast<JSAMPLE>(x * 7U);
        row[x * 3U + 1U] = 180U;
        row[x * 3U + 2U] = static_cast<JSAMPLE>(255U - x * 7U);
    }
    while (compressor.next_scanline < compressor.image_height) {
        auto* pointer = row.data();
        static_cast<void>(jpeg_write_scanlines(&compressor, &pointer, 1U));
    }
    jpeg_finish_compress(&compressor);
    jpeg_destroy_compress(&compressor);
    std::fclose(file);
}

dronegs::Scene make_scene() {
    dronegs::Scene scene;
    scene.cameras.push_back({
        .id = 1U,
        .model_id = 1,
        .width = 32U,
        .height = 32U,
        .parameters = {30.0, 30.0, 16.0, 16.0},
    });
    scene.images.push_back({
        .id = 1U,
        .camera_id = 1U,
        .name = "tile.jpg",
        .qvec = {1.0, 0.0, 0.0, 0.0},
        .tvec = {0.0, 0.0, 0.0},
        .source_x = 4U,
        .source_y = 4U,
        .source_width = 24U,
        .source_height = 24U,
    });
    std::uint64_t id = 1U;
    for (int y = -3; y <= 3; ++y) {
        for (int x = -3; x <= 3; ++x) {
            scene.points.push_back({
                .id = id++,
                .xyz = {
                    static_cast<double>(x) * 0.15,
                    static_cast<double>(y) * 0.15,
                    2.0,
                },
                .rgb = {128U, 180U, 128U},
            });
        }
    }
    return scene;
}

dronegs::Scene make_corner_supported_scene() {
    auto scene = make_scene();
    scene.points.clear();
    std::uint64_t id = 1U;
    for (int y = 0; y < 3; ++y) {
        for (int x = 0; x < 3; ++x) {
            scene.points.push_back({
                .id = id++,
                .xyz = {
                    -0.45 + static_cast<double>(x) * 0.03,
                    -0.45 + static_cast<double>(y) * 0.03,
                    2.0,
                },
                .rgb = {128U, 180U, 128U},
            });
        }
    }
    return scene;
}

std::string read_bytes(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) throw std::runtime_error("cannot read test artifact");
    return {std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>()};
}

void test_periodic_evaluation(const std::filesystem::path& root, dronegs::Options options) {
    auto scene = make_scene();
    auto invalid = options;
    invalid.test_every = 2U;
    invalid.eval_every = 1U;
    auto invalid_gaussians = dronegs::initialize_fixed_topology(scene);
    bool missing_held_out_rejected = false;
    try { static_cast<void>(dronegs::train_ordered_mrnf(invalid, scene, invalid_gaussians)); }
    catch (const std::invalid_argument&) { missing_held_out_rejected = true; }
    if (!missing_held_out_rejected) throw std::runtime_error("periodic evaluation accepted no supported held-out frames");
    auto held_out = scene.images.front();
    held_out.id = 2U;
    held_out.name = "held-out.jpg";
    scene.images.push_back(held_out);
    write_fixture_image(root / "images" / held_out.name);
    options.iterations = 600U;
    options.sh_degree_interval = 100U;
    options.test_every = 2U;
    options.prefetch_depth = 2U;
    options.decode_workers = 2U;
    options.background_mode = "random";
    options.loss_pixel_mask = "all";
    options.checkpoint_every = 600U;
    options.dataset_fingerprint = "periodic-evaluation-fixture";
    // Use one initial checkpoint: initial loss uses GPU atomic reductions and can
    // differ by one ULP between independent processes, outside optimizer state.
    options.output_path = root / "seed";
    options.checkpoint_path = options.output_path / "training.ckpt";
    options.stop_after = 1U;
    auto seed = dronegs::initialize_fixed_topology(scene);
    static_cast<void>(dronegs::train_ordered_mrnf(options, scene, seed));
    options.resume_from = options.checkpoint_path;
    options.stop_after = 0U;
    options.output_path = root / "baseline";
    options.checkpoint_path = options.output_path / "training.ckpt";
    std::filesystem::create_directories(options.output_path);
    auto baseline = dronegs::initialize_fixed_topology(scene);
    const auto baseline_metrics = dronegs::train_ordered_mrnf(options, scene, baseline);
    const auto expected = read_bytes(options.checkpoint_path);
    if (baseline_metrics.gaussians_added == 0U) throw std::runtime_error("evaluation fixture did not exercise Gaussian growth");
    if (baseline_metrics.periodic_evaluation_seconds != 0.0) {
        throw std::runtime_error("disabled evaluations consumed time");
    }
    options.output_path = root / "observed";
    options.checkpoint_path = options.output_path / "training.ckpt";
    // Evaluate immediately before refinement at step 200 and near completion.
    options.eval_every = 199U;
    options.eval_start = 199U;
    auto observed = dronegs::initialize_fixed_topology(scene);
    const auto observed_metrics = dronegs::train_ordered_mrnf(options, scene, observed);
    if (read_bytes(options.checkpoint_path) != expected ||
        std::abs(*observed_metrics.final_held_out_psnr - *baseline_metrics.final_held_out_psnr) > 1.0e-5F ||
        observed_metrics.periodic_evaluation_seconds <= 0.0 ||
        observed_metrics.evaluation_seconds < observed_metrics.periodic_evaluation_seconds ||
        observed_metrics.training_seconds <= 0.0) {
        throw std::runtime_error("periodic evaluations changed optimizer/topology state or timings");
    }
    const auto curve = read_bytes(options.output_path / "evaluation" / "curve.csv");
    if (curve.find("iteration_199,199,1,") == std::string::npos ||
        curve.find("iteration_398,398,1,") == std::string::npos ||
        curve.find("final,600,1,") == std::string::npos ||
        curve.find("iteration_600") != std::string::npos ||
        std::filesystem::exists(options.output_path / "evaluation" / "predictions")) {
        throw std::runtime_error("periodic evaluation curve stages/images mismatch");
    }
    options.output_path = root / "paused";
    options.checkpoint_path = options.output_path / "training.ckpt";
    options.stop_after = 199U;
    std::filesystem::create_directories(options.output_path);
    auto paused = dronegs::initialize_fixed_topology(scene);
    const auto pause_metrics = dronegs::train_ordered_mrnf(options, scene, paused);
    if (pause_metrics.completed || read_bytes(options.output_path / "evaluation" / "curve.csv").find(
            "iteration_199,199,1,") == std::string::npos) {
        throw std::runtime_error("paused training missed scheduled evaluation");
    }
    options.resume_from = options.checkpoint_path;
    options.output_path = root / "resumed";
    options.checkpoint_path = options.output_path / "training.ckpt";
    options.stop_after = 0U;
    auto resumed = dronegs::initialize_fixed_topology(scene);
    static_cast<void>(dronegs::train_ordered_mrnf(options, scene, resumed));
    if (read_bytes(options.checkpoint_path) != expected ||
        read_bytes(options.output_path / "evaluation" / "metrics.csv").find("stage,held_out_index") != 0U ||
        read_bytes(options.output_path / "evaluation" / "curve.csv").find("iteration_398,398,199,") == std::string::npos) {
        throw std::runtime_error("resumed evaluation changed checkpoint or lost CSV headers/session identity");
    }
    // Benchmark must load the final checkpoint repeatedly without changing it or exporting a model.
    options.resume_from = options.checkpoint_path;
    options.output_path = root / "benchmark";
    std::filesystem::create_directories(options.output_path);
    options.checkpoint_path.clear();
    options.checkpoint_every = 0U;
    options.eval_every = 0U;
    options.eval_start = 0U;
    auto initial = dronegs::initialize_fixed_topology(scene);
    dronegs::benchmark_training_steps(options, scene, initial, {.warmups=1U, .repeats=2U, .views=2U});
    const auto csv = read_bytes(options.output_path / "step_benchmark.csv");
    if (read_bytes(options.resume_from) != expected || std::count(csv.begin(), csv.end(), '\n') != 7 ||
        csv.find(",600,601,") == std::string::npos ||
        std::filesystem::exists(options.output_path / "point_cloud.ply")) {
        throw std::runtime_error("checkpoint benchmark modified its input or emitted invalid samples");
    }
}

}  // namespace

int main() {
    const auto suffix =
        std::chrono::steady_clock::now().time_since_epoch().count();
    const auto root = std::filesystem::temp_directory_path() /
        ("dronegs-tile-training-test-" + std::to_string(suffix));
    try {
        std::filesystem::create_directories(root / "images");
        write_fixture_image(root / "images" / "tile.jpg");
        auto gaussians = dronegs::initialize_fixed_topology(make_scene());
        dronegs::Options options;
        options.data_path = root;
        options.output_path = root / "output";
        options.run_manifest = root / "output" / "trainer_run.json";
        options.iterations = 2U;
        options.strategy = "mrnf";
        options.sh_degree = 1U;
        options.sh_degree_interval = 1U;
        options.max_cap = 100U;
        options.resize_factor = 1U;
        options.max_width = 32U;
        options.tile_mode = 4U;
        options.seed = 17U;
        options.checkpoint_every = 1U;
        options.checkpoint_path = root / "training.ckpt";
        if (options.tile_mode != 4U) {
            throw std::runtime_error(
                "tile training fixture options were not initialized");
        }
        const auto metrics = dronegs::train_ordered_mrnf(
            options, make_scene(), gaussians);
        if (metrics.completed_iterations != 2U ||
            metrics.training_image_count != 1U ||
            metrics.held_out_image_count != 0U ||
            metrics.frame_descriptor_count != 4U ||
            metrics.training_frame_count != 4U ||
            metrics.held_out_frame_count != 0U ||
            metrics.image_cache_misses < 2U ||
            metrics.periodic_checkpoints != 2U ||
            !std::filesystem::is_regular_file(
                options.checkpoint_path) ||
            std::filesystem::exists(
                options.checkpoint_path.string() + ".tmp")) {
            throw std::runtime_error(
                "four-tile MRNF training metrics mismatch: completed=" +
                std::to_string(metrics.completed_iterations) +
                " training_images=" +
                std::to_string(metrics.training_image_count) +
                " held_out_images=" +
                std::to_string(metrics.held_out_image_count) +
                " frame_descriptors=" +
                std::to_string(metrics.frame_descriptor_count) +
                " training_frames=" +
                std::to_string(metrics.training_frame_count) +
                " cache_misses=" +
                std::to_string(metrics.image_cache_misses));
        }
        {
            // A fully transparent seed still passes the historical spherical geometry
            // test. Exact FastGS admission must remove it before decoding its photo.
            auto transparent = gaussians;
            for (auto& gaussian : transparent) gaussian.opacity_logit = -30.0F;
            auto empty_options = options;
            empty_options.data_path = root / "missing-images";
            bool empty_rejected = false;
            try {
                static_cast<void>(dronegs::train_ordered_mrnf(empty_options, make_scene(), transparent));
            } catch (const std::runtime_error& error) {
                empty_rejected = std::string(error.what()) ==
                    "no training view has GPU projection support";
            }
            if (!empty_rejected) throw std::runtime_error("empty cell was not rejected before image decode");

            // The same photo can lose GPU support after admission. Both synchronous
            // and deferred steps must reject it before changing checkpoint state.
            dronegs::RasterCamera supported_camera{.fx=30, .fy=30, .cx=16, .cy=16, .width=32, .height=32};
            auto empty_camera = supported_camera;
            empty_camera.translation[2] = -100.0F;
            auto seed = dronegs::initialize_fixed_topology(make_scene());
            dronegs::OrderedAlphaTrainingContext context(seed, 1024U, 3U, 100U,
                dronegs::MrnfOptimizerProfile::reference_absolute, 0U, 1000U, 17U, true);
            std::vector<std::uint8_t> target(32U * 32U * 3U, 128U);
            context.save_checkpoint(root / "before-empty.ckpt", {}, "fixture", "fixture");
            if (!context.has_projection(supported_camera) || context.has_projection(empty_camera))
                throw std::runtime_error("GPU support does not match visible and empty fixture");
            for (bool deferred : {false, true}) {
                bool rejected = false;
                try {
                    if (deferred) context.train_step_deferred(empty_camera, target.data(), target.size());
                    else static_cast<void>(context.train_step(empty_camera, target.data(), target.size()));
                } catch (const dronegs::NoProjectedGaussians&) { rejected = true; }
                if (!rejected) throw std::runtime_error("empty GPU step was not rejected");
            }
            context.save_checkpoint(root / "after-empty.ckpt", {}, "fixture", "fixture");
            if (read_bytes(root / "before-empty.ckpt") != read_bytes(root / "after-empty.ckpt"))
                throw std::runtime_error("rejected step or support probe changed checkpoint state");
            // Fixed held-out population: a lost view contributes a black prediction,
            // finite full-image error and zero coverage instead of disappearing.
            std::vector<float> prediction;
            const auto quality = context.evaluate_quality(empty_camera, target.data(), target.size(), &prediction);
            const float expected_mse = std::pow(128.0F / 255.0F, 2.0F);
            if (std::abs(quality.mse - expected_mse) > 1e-5F ||
                quality.active_pixel_fraction != 0.0F || !std::isfinite(quality.ssim) ||
                prediction.size() != target.size() ||
                !std::all_of(prediction.begin(), prediction.end(), [](float value) { return value == 0.0F; }))
                throw std::runtime_error("empty held-out view was not scored as background");

            const std::vector<std::size_t> training{2U, 5U, 9U};
            std::vector<std::size_t> attempts, rejected;
            const auto attempt = [&](std::size_t frame) {
                attempts.push_back(frame);
                if (frame != 5U) throw dronegs::NoProjectedGaussians();
                return context.train_step(supported_camera, target.data(), target.size());
            };
            static_cast<void>(dronegs::with_supported_training_frame(training, 9U, attempt,
                [&](std::size_t frame) { rejected.push_back(frame); }));
            if (attempts != std::vector<std::size_t>{9U, 2U, 5U} ||
                rejected != std::vector<std::size_t>{9U, 2U})
                throw std::runtime_error("lost-view fallback is not deterministic and split-local");
            std::size_t empty_attempts = 0U;
            bool all_empty_rejected = false;
            try {
                dronegs::with_supported_training_frame(training, 5U,
                    [&](std::size_t) -> float { ++empty_attempts; throw dronegs::NoProjectedGaussians(); },
                    [](std::size_t) {});
            } catch (const std::runtime_error& error) {
                all_empty_rejected = std::string(error.what()) == "all training views lost GPU projection support";
            }
            if (!all_empty_rejected || empty_attempts != training.size())
                throw std::runtime_error("all-empty recovery is not bounded");
            bool unexpected_propagated = false;
            try {
                dronegs::with_supported_training_frame(training, 2U,
                    [](std::size_t) -> float { throw std::runtime_error("CUDA failure fixture"); },
                    [](std::size_t) { throw std::runtime_error("unexpected retry"); });
            } catch (const std::runtime_error& error) {
                unexpected_propagated = std::string(error.what()) == "CUDA failure fixture";
            }
            if (!unexpected_propagated) throw std::runtime_error("non-support failure was hidden");

        }
        auto corner_gaussians = dronegs::initialize_fixed_topology(
            make_corner_supported_scene());
        auto corner_options = options;
        corner_options.output_path = root / "corner-output";
        corner_options.run_manifest =
            corner_options.output_path / "trainer_run.json";
        corner_options.iterations = 1U;
        const auto corner_metrics = dronegs::train_ordered_mrnf(
            corner_options, make_corner_supported_scene(),
            corner_gaussians);
        if (corner_metrics.completed_iterations != 1U ||
            corner_metrics.image_cache_misses != 1U) {
            throw std::runtime_error(
                "unsupported training tiles were not removed: misses=" +
                std::to_string(corner_metrics.image_cache_misses));
        }
        bool learned_directional_opacity = false;
        for (const auto& gaussian : gaussians) {
            for (std::size_t coefficient = 0U;
                 coefficient < 3U; ++coefficient) {
                learned_directional_opacity =
                    learned_directional_opacity ||
                    std::abs(gaussian.opacity_sh[coefficient]) > 1.0e-9F;
            }
        }
        if (learned_directional_opacity) {
            throw std::runtime_error(
                "opacity-SH coefficients changed without opt-in");
        }

        auto directional_gaussians =
            dronegs::initialize_fixed_topology(make_scene());
        auto directional_options = options;
        directional_options.output_path = root / "opacity-sh-output";
        directional_options.run_manifest =
            directional_options.output_path / "trainer_run.json";
        directional_options.checkpoint_path =
            root / "opacity-sh-training.ckpt";
        directional_options.opacity_sh_enabled = true;
        static_cast<void>(dronegs::train_ordered_mrnf(
            directional_options, make_scene(), directional_gaussians));
        learned_directional_opacity = false;
        for (const auto& gaussian : directional_gaussians) {
            for (std::size_t coefficient = 0U;
                 coefficient < 3U; ++coefficient) {
                learned_directional_opacity =
                    learned_directional_opacity ||
                    std::abs(gaussian.opacity_sh[coefficient]) > 1.0e-9F;
            }
        }
        if (!learned_directional_opacity) {
            throw std::runtime_error(
                "opacity-SH coefficients were not trained after opt-in");
        }

        const auto checkpoint = root / "opacity-sh-v4.ckpt";
        dronegs::OrderedAlphaTrainingContext checkpoint_source(
            gaussians, 16U * 16U, 2U, 100U,
            dronegs::MrnfOptimizerProfile::reference_absolute,
            1U, 1U, 17U);
        const dronegs::TrainingCheckpointProgress saved_progress{};
        checkpoint_source.save_checkpoint(
            checkpoint, saved_progress, "tile-dataset", "tile-config");
        const auto before_evaluation = read_bytes(checkpoint);
        const dronegs::RasterCamera evaluation_camera{
            .fx=30.0F, .fy=30.0F, .cx=8.0F, .cy=8.0F, .width=16U, .height=16U};
        const std::vector<std::uint8_t> target(16U * 16U * 3U, 128U);
        static_cast<void>(checkpoint_source.evaluate_quality(evaluation_camera, target.data(), target.size()));
        const auto after_evaluation = root / "after-evaluation.ckpt";
        checkpoint_source.save_checkpoint(after_evaluation, saved_progress, "tile-dataset", "tile-config");
        if (read_bytes(after_evaluation) != before_evaluation) {
            throw std::runtime_error("forward evaluation changed serialized optimizer/model state");
        }
        dronegs::OrderedAlphaTrainingContext checkpoint_restored(
            gaussians, 16U * 16U, 2U, 100U,
            dronegs::MrnfOptimizerProfile::reference_absolute,
            1U, 1U, 17U);
        const auto loaded_progress = checkpoint_restored.load_checkpoint(
            checkpoint, "tile-dataset", "tile-config");
        if (loaded_progress.completed_iteration != 0U ||
            checkpoint_restored.size() != gaussians.size()) {
            throw std::runtime_error(
                "opacity-SH checkpoint v4 round-trip mismatch");
        }
        test_periodic_evaluation(root, options);
        std::filesystem::remove_all(root);
        std::cout << "DroneGS tile training test passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "DroneGS tile training test failed: "
                  << error.what() << '\n';
        std::cerr << "Artifacts retained at " << root << std::endl;
        return 1;
    }
}
