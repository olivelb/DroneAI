// SPDX-License-Identifier: MIT
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <future>
#include <iostream>
#include <iterator>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

#include "dronegs/cli.hpp"
#include "dronegs/colmap.hpp"
#include "dronegs/manifest.hpp"
#include "dronegs/model.hpp"
#include "dronegs/image.hpp"
#include "dronegs/ply.hpp"
#include "dronegs/profile_registry.hpp"
#include "dronegs/training.hpp"

namespace {

void check(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

std::size_t occurrence_count(
    const std::string& text,
    const std::string& needle) {
    std::size_t count = 0U;
    for (std::size_t position = 0U;
         (position = text.find(needle, position)) != std::string::npos;
         position += needle.size()) {
        ++count;
    }
    return count;
}

template <typename T>
void write_value(std::ostream& stream, const T& value) {
    stream.write(reinterpret_cast<const char*>(&value), sizeof(T));
}

void write_fixture(const std::filesystem::path& root) {
    const auto sparse = root / "sparse" / "0";
    std::filesystem::create_directories(sparse);
    std::filesystem::create_directories(root / "images");
    std::ofstream(root / "images" / "frame.jpg", std::ios::binary).put('\0');
    {
        std::ofstream regions(root / "image_regions.tsv");
        regions << "# dronegs-image-regions-v1\n"
                << "frame.jpg\t16\t24\t320\t240\n";
    }

    {
        std::ofstream stream(sparse / "cameras.bin", std::ios::binary);
        write_value(stream, std::uint64_t{1});
        write_value(stream, std::uint32_t{7});
        write_value(stream, std::int32_t{1});
        write_value(stream, std::uint64_t{640});
        write_value(stream, std::uint64_t{480});
        for (double parameter : std::array<double, 4>{500.0, 500.0, 320.0, 240.0}) {
            write_value(stream, parameter);
        }
    }
    {
        std::ofstream stream(sparse / "images.bin", std::ios::binary);
        write_value(stream, std::uint64_t{1});
        write_value(stream, std::uint32_t{3});
        for (double pose : std::array<double, 7>{1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0}) {
            write_value(stream, pose);
        }
        write_value(stream, std::uint32_t{7});
        stream.write("frame.jpg", 9);
        stream.put('\0');
        write_value(stream, std::uint64_t{0});
    }
    {
        std::ofstream stream(sparse / "points3D.bin", std::ios::binary);
        write_value(stream, std::uint64_t{2});
        for (std::uint64_t id = 1; id <= 2; ++id) {
            write_value(stream, id);
            write_value(stream, static_cast<double>(id));
            write_value(stream, static_cast<double>(id + 1));
            write_value(stream, static_cast<double>(id + 2));
            write_value(stream, static_cast<std::uint8_t>(10 * id));
            write_value(stream, static_cast<std::uint8_t>(20 * id));
            write_value(stream, static_cast<std::uint8_t>(30 * id));
            write_value(stream, 0.1);
            write_value(stream, std::uint64_t{0});
        }
    }
}

std::vector<char*> mutable_arguments(std::vector<std::string>& values) {
    std::vector<char*> pointers;
    pointers.reserve(values.size());
    for (auto& value : values) {
        pointers.push_back(value.data());
    }
    return pointers;
}

void test_scene_and_ply(const std::filesystem::path& root) {
    write_fixture(root);
    const auto scene = dronegs::load_colmap_scene(root);
    check(scene.cameras.size() == 1, "camera count mismatch");
    check(scene.images.size() == 1, "image count mismatch");
    check(scene.points.size() == 2, "point count mismatch");
    check(scene.images.front().name == "frame.jpg", "image name mismatch");
    check(scene.images.front().qvec[0] == 1.0, "image quaternion mismatch");
    check(scene.images.front().tvec[2] == 0.0, "image translation mismatch");
    check(
        scene.images.front().source_x == 16U &&
            scene.images.front().source_y == 24U &&
            scene.images.front().source_width == 320U &&
            scene.images.front().source_height == 240U,
        "native image region mismatch");
    check(scene.cameras.front().parameters.size() == 4, "camera parameter count mismatch");
    const auto original_fingerprint =
        dronegs::dataset_fingerprint(scene, root);
    check(original_fingerprint.starts_with("fnv1a64:v3:"),
          "fingerprint kind mismatch");
    auto calibration_changed = scene;
    calibration_changed.cameras.front().parameters.front() += 1.0;
    check(
        dronegs::dataset_fingerprint(calibration_changed, root) !=
            original_fingerprint,
        "camera intrinsics must invalidate the dataset fingerprint");
    auto pose_changed = scene;
    pose_changed.images.front().tvec.front() += 1.0;
    check(
        dronegs::dataset_fingerprint(pose_changed, root) !=
            original_fingerprint,
        "camera poses must invalidate the dataset fingerprint");
    auto region_changed = scene;
    region_changed.images.front().source_x += 1U;
    check(
        dronegs::dataset_fingerprint(region_changed, root) !=
            original_fingerprint,
        "native image regions must invalidate the dataset fingerprint");

    auto gaussians = dronegs::initialize_fixed_topology(scene);
    check(gaussians.size() == 2, "Gaussian count mismatch");
    check(gaussians.front().rotation[0] == 1.0F, "identity quaternion missing");
    check(gaussians.front().log_scale[0] == 0.0F,
          "MRNF small-cloud scale fallback mismatch");
    for (std::size_t index = 0U; index < 9U; ++index) {
        gaussians.front().sh_rest[index] =
            0.01F * static_cast<float>(index + 1U);
    }
    for (std::size_t index = 0U; index < 3U; ++index) {
        gaussians.front().opacity_sh[index] =
            -0.02F * static_cast<float>(index + 1U);
    }
    const auto output = root.parent_path() / "native-output";
    std::filesystem::create_directories(output);
    const auto ply = output / "point_cloud.ply";
    dronegs::write_gaussian_ply(ply, gaussians, 1);

    std::ifstream stream(ply, std::ios::binary);
    std::string line;
    std::string header;
    while (std::getline(stream, line)) {
        header += line + "\n";
        if (line == "end_header") {
            break;
        }
    }
    check(header.starts_with("ply\nformat binary_little_endian 1.0\n"),
          "PLY format mismatch");
    check(header.find("element vertex 2\n") != std::string::npos,
          "PLY vertex count mismatch");
    check(header.find("property float f_rest_8\n") != std::string::npos,
          "PLY degree-1 SH layout mismatch");
    check(header.find("property float opacity_sh_2\n") != std::string::npos,
          "PLY degree-1 opacity-SH layout mismatch");
    check(header.find("property float rot_3\n") != std::string::npos,
          "PLY rotation layout mismatch");
    const auto loaded = dronegs::read_gaussian_ply(ply);
    check(loaded.sh_degree == 1U, "PLY reader SH degree mismatch");
    check(loaded.gaussians.size() == gaussians.size(),
          "PLY reader Gaussian count mismatch");
    check(loaded.gaussians.front().xyz == gaussians.front().xyz,
          "PLY reader position mismatch");
    check(loaded.gaussians.front().dc == gaussians.front().dc,
          "PLY reader DC mismatch");
    check(loaded.gaussians.front().log_scale ==
              gaussians.front().log_scale,
          "PLY reader scale mismatch");
    check(loaded.gaussians.front().rotation ==
              gaussians.front().rotation,
          "PLY reader rotation mismatch");
    check(loaded.gaussians.front().sh_rest[8] ==
              gaussians.front().sh_rest[8],
          "PLY reader SH-rest mismatch");
    check(loaded.gaussians.front().opacity_sh[2] ==
              gaussians.front().opacity_sh[2],
          "PLY reader opacity-SH mismatch");

    dronegs::Options options{
        .data_path = root,
        .output_path = output,
        .run_manifest = output / "trainer_run.json",
        .iterations = 1,
        .strategy = "mrnf",
        .sh_degree = 1,
        .max_cap = 100,
        .resize_factor = 4,
        .max_width = 1600,
        .tile_mode = 4,
        .seed = 42,
        .adaptive_growth_target = 1,
    };
    const dronegs::RunMeasurements measurements{
        .started_at = "2026-07-24T10:00:00Z",
        .finished_at = "2026-07-24T10:00:01Z",
        .loading_seconds = 0.1,
        .image_decode_seconds = 0.08,
        .image_wait_seconds = 0.02,
        .startup_seconds = 0.05,
        .training_seconds = 0.15,
        .export_seconds = 0.2,
        .wall_seconds = 0.3,
        .initial_loss = 0.4F,
        .final_loss = 0.2F,
        .image_cache_hits = 3U,
        .image_cache_misses = 2U,
        .image_cache_evictions = 1U,
        .image_cache_capacity_bytes = 1024U,
        .peak_image_cache_bytes = 512U,
        .image_prefetch_started = 4U,
        .image_prefetch_consumed = 3U,
        .image_prefetch_ready = 2U,
        .training_image_count = 7U,
        .held_out_image_count = 1U,
        .frame_descriptor_count = 25U,
        .training_frame_count = 21U,
        .held_out_frame_count = 4U,
        .topology_refinements = 2U,
        .gaussians_added = 15U,
        .initial_held_out_psnr = 10.0F,
        .initial_held_out_ssim = 0.2F,
        .initial_pixel_weighted_psnr = 10.5F,
        .initial_pixel_weighted_ssim = 0.25F,
        .final_held_out_psnr = 12.0F,
        .final_held_out_ssim = 0.3F,
        .final_pixel_weighted_psnr = 12.5F,
        .final_pixel_weighted_ssim = 0.35F,
    };
    dronegs::write_completed_manifest(
        options, scene, dronegs::dataset_fingerprint(scene), measurements,
        ply, gaussians.size());
    std::ifstream manifest(options.run_manifest);
    const std::string manifest_text(
        (std::istreambuf_iterator<char>(manifest)), std::istreambuf_iterator<char>());
    check(manifest_text.find("\"contract_version\": 1") != std::string::npos,
          "manifest contract version missing");
    check(manifest_text.find("\"initial_ply\": null") != std::string::npos,
          "manifest initial PLY provenance missing");
    check(manifest_text.find("\"git_revision\": null") == std::string::npos,
          "manifest Git revision missing");
    check(manifest_text.find("\"image_cache_hits\": 3") != std::string::npos,
          "manifest image cache metrics missing");
    check(
        manifest_text.find("\"pixel_weighted_psnr\": 12.5") !=
            std::string::npos,
        "manifest pixel-weighted quality metrics missing");
    check(manifest_text.find("\"image_decode_seconds\": 0.08") != std::string::npos,
          "manifest image decode timing missing");
    check(manifest_text.find("\"image_prefetch_consumed\": 3") != std::string::npos,
          "manifest image prefetch metrics missing");
    check(manifest_text.find("\"prefetch_depth\": 1") != std::string::npos,
          "manifest prefetch depth missing");
    check(manifest_text.find("\"decode_workers\": 1") != std::string::npos,
          "manifest decode worker count missing");
    check(
        manifest_text.find("\"topology_refinement_seconds\": 0") !=
            std::string::npos,
        "manifest topology timing missing");
    check(
        manifest_text.find("\"periodic_checkpoint_seconds\": 0") !=
            std::string::npos,
        "manifest checkpoint timing missing");
    check(
        manifest_text.find("\"final_ply_export_seconds\": 0") !=
            std::string::npos,
        "manifest final PLY export timing missing");
    check(
        manifest_text.find("\"image_cache_working_set_bytes\": 0") !=
            std::string::npos,
        "manifest image-cache working set missing");
    check(manifest_text.find("\"jpeg_idct_scale\": 0") != std::string::npos,
          "manifest JPEG IDCT mode missing");
    check(manifest_text.find("\"checkpoint_every\": 0") !=
              std::string::npos,
          "manifest checkpoint interval missing");
    check(manifest_text.find("\"resumed_from_checkpoint\": false") !=
              std::string::npos,
          "manifest resume provenance missing");
    check(manifest_text.find("\"adaptive_growth_target\": true") !=
              std::string::npos,
          "manifest adaptive growth policy missing");
    check(manifest_text.find(
              "\"growth_fraction_policy\": "
              "\"capacity_targeted_0.07_to_0.25\"") !=
              std::string::npos,
          "manifest adaptive growth schedule missing");
    check(manifest_text.find("\"training_image_count\": 7") != std::string::npos,
          "manifest training split count missing");
    check(manifest_text.find("\"held_out_image_count\": 1") != std::string::npos,
          "manifest held-out split count missing");
    check(manifest_text.find("\"frame_descriptor_count\": 25") !=
              std::string::npos,
          "manifest frame descriptor count missing");
    check(manifest_text.find("\"training_frame_count\": 21") !=
              std::string::npos,
          "manifest training frame count missing");
    check(manifest_text.find("\"held_out_frame_count\": 4") !=
              std::string::npos,
          "manifest held-out frame count missing");
    check(manifest_text.find("\"topology_refinements\": 2") !=
              std::string::npos,
          "manifest topology refinement count missing");
    check(manifest_text.find("\"gaussians_added\": 15") !=
              std::string::npos,
          "manifest Gaussian growth count missing");
    check(manifest_text.find("\"growth_gradient_threshold\": 0.003") !=
              std::string::npos,
          "manifest topology growth protocol missing");
    check(manifest_text.find(
              "\"growth_score\": "
              "\"max_normalized_ssim_error_weighted_alpha_contribution\"") !=
              std::string::npos,
          "manifest topology score definition missing");
    check(manifest_text.find("\"psnr\": 12") != std::string::npos,
          "manifest held-out PSNR missing");
    check(manifest_text.find("\"ssim\": 0.300000") != std::string::npos,
          "manifest held-out SSIM missing");
    check(
        occurrence_count(manifest_text, "\"raster_profile\"") == 1U,
        "manifest must contain exactly one requested raster profile");
    check(
        occurrence_count(manifest_text, "\"effective_raster_profile\"") ==
            1U,
        "manifest must contain exactly one effective raster profile");
    check(
        occurrence_count(manifest_text, "\"refine_every\"") == 1U,
        "manifest must not duplicate refine_every");
    check(
        occurrence_count(manifest_text, "\"grow_until_iteration\"") == 1U,
        "manifest must not duplicate grow_until_iteration");

    options.optimizer_profile =
        "reference-absolute-absgrad025";
    options.run_manifest = output / "trainer_run_absgrad025.json";
    dronegs::write_completed_manifest(
        options, scene, dronegs::dataset_fingerprint(scene), measurements,
        ply, gaussians.size());
    std::ifstream absgrad_manifest(options.run_manifest);
    const std::string absgrad_manifest_text(
        (std::istreambuf_iterator<char>(absgrad_manifest)),
        std::istreambuf_iterator<char>());
    check(
        absgrad_manifest_text.find("\"absgrad_score_weight\": 0.25") !=
            std::string::npos,
        "neutral AbsGrad manifest weight missing");
    check(
        absgrad_manifest_text.find(
            "\"growth_score\": "
            "\"mrnf_error_edge_times_robust_abs_projected_gradient\"") !=
            std::string::npos,
        "neutral AbsGrad manifest growth score missing");
}

void test_optimizer_profile_registry() {
    const auto* production =
        dronegs::find_optimizer_profile("reference-absolute");
    check(production != nullptr, "production optimizer profile missing");
    check(
        production->status ==
            dronegs::OptimizerProfileStatus::validated,
        "production optimizer must be marked validated");
    for (const auto* name : {
             "reference-absolute-absgrad025",
             "reference-absolute-absgrad050",
         }) {
        const auto* candidate = dronegs::find_optimizer_profile(name);
        check(candidate != nullptr, "neutral AbsGrad candidate missing");
        check(
            candidate->status ==
                dronegs::OptimizerProfileStatus::experimental,
            "neutral AbsGrad candidate must remain experimental");
    }
    const auto* dev38 = dronegs::find_optimizer_profile(
        "dev38-staged-rotation008-absgrad050-fastgs");
    check(dev38 != nullptr, "dev38 profile missing");
    check(
        dev38->status ==
            dronegs::OptimizerProfileStatus::experimental,
        "dev38 must remain explicitly experimental");

    const std::string help = dronegs::help_text();
    for (std::size_t index = 0U;
         index < dronegs::optimizer_profile_registry.size(); ++index) {
        const auto& profile = dronegs::optimizer_profile_registry[index];
        const std::string token = std::string(profile.name) +
            (index + 1U == dronegs::optimizer_profile_registry.size()
                 ? "]"
                 : "|");
        check(
            occurrence_count(help, token) == 1U,
            "optimizer profile must occur exactly once in CLI help: " +
                std::string(profile.name));
    }
}

void test_local_scale_initialization() {
    dronegs::Scene scene;
    const std::array<std::array<double, 3>, 8> positions{{
        {0.0, 0.0, 0.0},
        {0.002, 0.0, 0.0},
        {0.0, 0.004, 0.0},
        {0.0, 0.0, 0.008},
        {1.0, 1.0, 1.0},
        {2.0, 2.0, 2.0},
        {4.0, 4.0, 4.0},
        {8.0, 8.0, 8.0},
    }};
    for (std::size_t index = 0U; index < positions.size(); ++index) {
        scene.points.push_back(dronegs::SparsePoint{
            .id = static_cast<std::uint64_t>(index + 1U),
            .xyz = positions[index],
            .rgb = {64U, 128U, 192U},
        });
    }

    const auto gaussians = dronegs::initialize_fixed_topology(scene);
    check(gaussians.size() == positions.size(),
          "local-scale Gaussian count mismatch");
    for (const auto& gaussian : gaussians) {
        check(
            gaussian.log_scale[0] == gaussian.log_scale[1] &&
                gaussian.log_scale[1] == gaussian.log_scale[2],
            "MRNF local initialization must remain isotropic");
        check(std::isfinite(gaussian.log_scale[0]),
              "MRNF local initialization produced a non-finite scale");
    }
    check(std::exp(gaussians.front().log_scale[0]) < 0.01F,
          "dense neighborhood did not receive a compact scale");
    check(std::exp(gaussians.back().log_scale[0]) > 0.1F,
          "sparse neighborhood did not receive a larger scale");
    check(gaussians.front().log_scale[0] != gaussians.back().log_scale[0],
          "local initialization collapsed to one scene-wide scale");

    dronegs::Scene duplicate_scene;
    duplicate_scene.points.resize(3U);
    const auto duplicate_gaussians =
        dronegs::initialize_fixed_topology(duplicate_scene);
    const float duplicate_scale =
        std::exp(duplicate_gaussians.front().log_scale[0]);
    check(std::abs(duplicate_scale - 1.0e-3F) < 1.0e-7F,
          "duplicate-point scale floor mismatch");
}

void test_cli(const std::filesystem::path& data, const std::filesystem::path& output) {
    std::filesystem::create_directories(output);
    std::vector<std::string> values{
        "dronegs",
        "--data-path", data.string(),
        "--output-path", output.string(),
        "--iter", "1",
        "--strategy", "mrnf",
        "--sh-degree", "1",
        "--max-cap", "100",
        "--resize-factor", "4",
        "--max-width", "1600",
        "--tile-mode", "4",
        "--seed", "42",
        "--run-manifest", (output / "trainer_run.json").string(),
    };
    auto arguments = mutable_arguments(values);
    const auto parsed = dronegs::parse_options(
        static_cast<int>(arguments.size()), arguments.data());
    check(parsed.seed == 42, "CLI seed mismatch");
    check(parsed.sh_degree == 1, "CLI SH degree mismatch");
    check(
        parsed.adaptive_native_crop_tiles == 0U,
        "CLI adaptive native crop tile default mismatch");
    check(
        parsed.sh_degree_interval == 1000U,
        "CLI SH interval default mismatch");
    check(parsed.prefetch_depth == 1U, "CLI prefetch default mismatch");
    check(parsed.decode_workers == 1U, "CLI decode worker default mismatch");
    check(
        parsed.host_image_cache_mib == 2048U,
        "CLI host image cache default mismatch");
    check(parsed.jpeg_idct_scale == 0U, "CLI JPEG IDCT default mismatch");
    check(parsed.test_every == 0U, "CLI held-out split default mismatch");
    check(parsed.test_split == "modulo", "CLI split policy default mismatch");
    check(
        parsed.test_guard_percent == 0U,
        "CLI spatial guard default mismatch");
    check(parsed.save_eval_images == 0U, "CLI eval export default mismatch");
    check(
        parsed.topology_cooldown == 0U,
        "CLI topology cooldown default mismatch");
    check(
        parsed.photometric_finish == 0U &&
            parsed.photometric_mse_percent == 0U,
        "CLI photometric finish default mismatch");
    check(
        parsed.adaptive_growth_target == 0U,
        "CLI adaptive growth default mismatch");
    check(
        parsed.optimizer_profile == "dronegs-dev16",
        "CLI optimizer profile default mismatch");
    check(
        parsed.pruning_policy == "original",
        "CLI pruning policy default mismatch");
    check(
        parsed.raster_profile == "auto",
        "CLI raster profile default mismatch");

    const auto checkpoint = output / "training.ckpt";
    std::ofstream(checkpoint, std::ios::binary).put('\0');
    values.insert(values.end(), {
        "--prefetch-depth", "12",
        "--decode-workers", "3",
        "--host-image-cache-mib", "4096",
        "--jpeg-idct-scale", "0",
        "--test-every", "8",
        "--test-split", "spatial-block",
        "--test-guard-percent", "25",
        "--save-eval-images", "1",
        "--topology-cooldown", "1",
        "--photometric-finish", "1",
        "--photometric-mse-percent", "50",
        "--adaptive-growth-target", "1",
        "--adaptive-native-crop-tiles", "1",
        "--sh-degree-interval", "250",
        "--initial-ply",
        (data.parent_path() / "native-output" / "point_cloud.ply").string(),
        "--optimizer-profile",
        "dev38-staged-rotation008-absgrad050-fastgs",
        "--pruning-policy", "spatial-bounds",
        "--raster-profile", "fastgs",
        "--checkpoint-every", "1",
        "--checkpoint-path", checkpoint.string(),
        "--resume-from", checkpoint.string(),
        "--stop-after", "1",
    });
    arguments = mutable_arguments(values);
    const auto tuned = dronegs::parse_options(
        static_cast<int>(arguments.size()), arguments.data());
    check(tuned.prefetch_depth == 12U, "CLI prefetch depth mismatch");
    check(tuned.decode_workers == 3U, "CLI decode worker count mismatch");
    check(
        tuned.host_image_cache_mib == 4096U,
        "CLI host image cache limit mismatch");
    check(tuned.jpeg_idct_scale == 0U, "CLI JPEG IDCT mode mismatch");
    check(tuned.test_every == 8U, "CLI held-out stride mismatch");
    check(
        tuned.test_split == "spatial-block",
        "CLI spatial split mismatch");
    check(
        tuned.test_guard_percent == 25U,
        "CLI spatial guard mismatch");
    check(tuned.save_eval_images == 1U, "CLI eval export mismatch");
    check(
        tuned.topology_cooldown == 1U,
        "CLI topology cooldown mismatch");
    check(
        tuned.photometric_finish == 1U &&
            tuned.photometric_mse_percent == 50U,
        "CLI photometric finish mismatch");
    check(
        tuned.adaptive_growth_target == 1U,
        "CLI adaptive growth mismatch");
    check(
        tuned.adaptive_native_crop_tiles == 1U,
        "CLI adaptive native crop tiles mismatch");
    check(
        tuned.sh_degree_interval == 250U,
        "CLI SH interval mismatch");
    check(
        tuned.optimizer_profile ==
            "dev38-staged-rotation008-absgrad050-fastgs",
        "CLI optimizer profile mismatch");
    check(
        tuned.pruning_policy == "spatial-bounds",
        "CLI pruning policy mismatch");
    check(
        tuned.raster_profile == "fastgs",
        "CLI raster profile mismatch");
    check(tuned.checkpoint_every == 1U, "CLI checkpoint interval mismatch");
    check(tuned.checkpoint_path == checkpoint, "CLI checkpoint path mismatch");
    check(tuned.resume_from == checkpoint, "CLI resume path mismatch");
    check(tuned.stop_after == 1U, "CLI stop-after mismatch");
    check(tuned.initial_ply ==
              data.parent_path() / "native-output" / "point_cloud.ply",
          "CLI initial PLY mismatch");
    values.resize(values.size() - 38U);

    values[values.size() - 7] = "4097";  // --max-width value
    arguments = mutable_arguments(values);
    bool rejected = false;
    try {
        static_cast<void>(dronegs::parse_options(
            static_cast<int>(arguments.size()), arguments.data()));
    } catch (const std::invalid_argument&) {
        rejected = true;
    }
    check(rejected, "CLI accepted max-width above contract limit");
}

void test_adaptive_capacity_growth() {
    check(
        dronegs::topology_refinement_end_iteration(
            30'000U, 1'000U, false) == 29'000U,
        "legacy topology cooldown changed");
    check(
        dronegs::topology_refinement_end_iteration(
            30'000U, 1'000U, true) == 14'800U,
        "adaptive topology was not frozen after growth");
    const auto initial = dronegs::adaptive_capacity_growth_fraction(
        22'547U, 5'700'000U, 200U);
    check(
        initial > 0.10F && initial < 0.13F,
        "adaptive initial growth fraction mismatch");
    check(
        dronegs::adaptive_capacity_growth_fraction(
            5'700'000U, 5'700'000U, 14'600U) == 0.0F,
        "adaptive growth continued at capacity before the final window");
    check(
        dronegs::adaptive_capacity_growth_fraction(
            5'700'000U, 5'700'000U, 14'800U) == 0.07F,
        "adaptive final window did not reserve pruning replacement");
    check(
        dronegs::adaptive_capacity_growth_fraction(
            1U, 5'700'000U, 14'800U) == 0.25F,
        "adaptive growth upper bound mismatch");
    bool empty_model_rejected = false;
    try {
        static_cast<void>(dronegs::adaptive_capacity_growth_fraction(
            0U, 100U, 200U));
    } catch (const std::invalid_argument&) {
        empty_model_rejected = true;
    }
    check(empty_model_rejected, "adaptive growth accepted an empty model");
}

void test_exact_floor_percentile() {
    const std::vector<float> unsorted{
        8.0F, 1.0F, 5.0F, 3.0F, 9.0F, 2.0F,
        7.0F, 4.0F, 6.0F, 0.0F, 5.0F};
    check(
        dronegs::exact_floor_percentile({}, 0.5F) == 0.0F,
        "empty percentile fallback mismatch");
    check(
        dronegs::exact_floor_percentile(unsorted, 0.0F) == 0.0F &&
            dronegs::exact_floor_percentile(unsorted, 0.1F) == 1.0F &&
            dronegs::exact_floor_percentile(unsorted, 0.5F) == 5.0F &&
            dronegs::exact_floor_percentile(unsorted, 0.9F) == 8.0F &&
            dronegs::exact_floor_percentile(unsorted, 1.0F) == 9.0F,
        "exact floor percentile differs from sorted order statistics");
    const auto [q10, q90] = dronegs::exact_floor_percentile_pair(
        unsorted, 0.1F, 0.9F);
    check(
        q10 == 1.0F && q90 == 8.0F,
        "paired percentile differs from sorted order statistics");
    const auto [same_lower, same_upper] =
        dronegs::exact_floor_percentile_pair(unsorted, 0.5F, 0.5F);
    check(
        same_lower == 5.0F && same_upper == 5.0F,
        "paired percentile equal-index result mismatch");
    bool invalid_fraction_rejected = false;
    try {
        static_cast<void>(
            dronegs::exact_floor_percentile(unsorted, 1.01F));
    } catch (const std::invalid_argument&) {
        invalid_fraction_rejected = true;
    }
    check(invalid_fraction_rejected,
          "invalid percentile fraction was accepted");
}

void test_image_cache() {
    std::uint64_t loads = 0U;
    dronegs::ImageCache cache(
        3U, 12U,
        [&loads](std::size_t index) {
            ++loads;
            dronegs::ImageData image{
                .width = 2U,
                .height = 1U,
                .source_to_image_x = 1.0F,
                .source_to_image_y = 1.0F,
                .rgb = std::vector<std::uint8_t>(6U, static_cast<std::uint8_t>(index)),
            };
            return image;
        });
    static_cast<void>(cache.get(0U));
    static_cast<void>(cache.get(1U));
    static_cast<void>(cache.get(0U));
    static_cast<void>(cache.get(2U));
    check(loads == 3U, "image cache loader count mismatch");
    check(cache.stats().requests == 4U, "image cache request count mismatch");
    check(cache.stats().hits == 1U, "image cache hit count mismatch");
    check(cache.stats().misses == 3U, "image cache miss count mismatch");
    check(cache.stats().evictions == 1U, "image cache eviction count mismatch");
    check(cache.stats().resident_bytes == 12U, "image cache resident bytes mismatch");
    check(cache.stats().peak_resident_bytes <= cache.capacity_bytes(),
          "image cache exceeded its byte capacity");

    bool oversized_rejected = false;
    try {
        dronegs::ImageCache oversized(
            1U, 5U, [](std::size_t) {
                return dronegs::ImageData{.width = 2U, .height = 1U, .rgb = {0, 0, 0, 0, 0, 0}};
            });
        static_cast<void>(oversized.get(0U));
    } catch (const std::runtime_error&) {
        oversized_rejected = true;
    }
    check(oversized_rejected, "image cache accepted an oversized decoded image");
    constexpr std::size_t large_item_count = 2048U;
    constexpr std::size_t large_item_bytes = 1024U;
    constexpr std::size_t large_capacity = 8U * large_item_bytes;
    dronegs::ImageCache large_cache(
        large_item_count, large_capacity,
        [](std::size_t index) {
            return dronegs::ImageData{
                .width = 1U,
                .height = 1U,
                .rgb = std::vector<std::uint8_t>(
                    large_item_bytes, static_cast<std::uint8_t>(index % 256U)),
            };
        });
    for (std::size_t pass = 0; pass < 2U; ++pass) {
        for (std::size_t index = 0; index < large_item_count; ++index) {
            static_cast<void>(large_cache.get(index));
        }
    }
    check(large_cache.stats().peak_resident_bytes <= large_capacity,
          "large-cardinality image cache exceeded its byte capacity");

    std::promise<void> loader_started;
    auto loader_started_future = loader_started.get_future();
    std::promise<void> release_loader;
    auto release_loader_future = release_loader.get_future().share();
    std::atomic<std::uint64_t> async_loads{0U};
    dronegs::ImageCache async_cache(
        3U, 12U,
        [&loader_started, release_loader_future, &async_loads](std::size_t index) {
            ++async_loads;
            loader_started.set_value();
            release_loader_future.wait();
            return dronegs::ImageData{
                .width = 2U,
                .height = 1U,
                .rgb = std::vector<std::uint8_t>(
                    6U, static_cast<std::uint8_t>(index)),
            };
        });
    async_cache.prefetch(1U);
    loader_started_future.wait();
    check(async_cache.stats().prefetch_started == 1U,
          "image cache did not start asynchronous prefetch");
    bool second_prefetch_rejected = false;
    try {
        async_cache.prefetch(2U);
    } catch (const std::logic_error&) {
        second_prefetch_rejected = true;
    }
    check(second_prefetch_rejected,
          "image cache accepted more than one outstanding prefetch");
    release_loader.set_value();
    const auto& prefetched = async_cache.get(1U);
    check(prefetched.rgb.front() == 1U, "prefetched image contents mismatch");
    check(async_loads == 1U, "prefetched image was decoded more than once");
    check(async_cache.stats().prefetch_consumed == 1U,
          "image cache did not consume prefetched image");
    check(async_cache.stats().misses == 1U,
          "prefetched demand should remain a cache miss");
    static_cast<void>(async_cache.get(1U));
    check(async_cache.stats().hits == 1U,
          "prefetched image was not retained by the LRU");
    async_cache.prefetch(1U);
    check(async_cache.stats().prefetch_started == 1U,
          "prefetch unexpectedly decoded an already resident image");

    std::mutex parallel_mutex;
    std::condition_variable parallel_started;
    std::promise<void> release_parallel_loaders;
    const auto release_parallel_future =
        release_parallel_loaders.get_future().share();
    std::atomic<std::uint64_t> parallel_loads{0U};
    std::atomic<std::uint64_t> active_loads{0U};
    std::atomic<std::uint64_t> peak_active_loads{0U};
    dronegs::ImageCache parallel_cache(
        4U, 24U,
        [&parallel_mutex, &parallel_started, release_parallel_future,
         &parallel_loads, &active_loads,
         &peak_active_loads](std::size_t index) {
            ++parallel_loads;
            const auto active = ++active_loads;
            auto peak = peak_active_loads.load();
            while (active > peak &&
                   !peak_active_loads.compare_exchange_weak(
                       peak, active)) {
            }
            parallel_started.notify_all();
            release_parallel_future.wait();
            --active_loads;
            return dronegs::ImageData{
                .width = 2U,
                .height = 1U,
                .rgb = std::vector<std::uint8_t>(
                    6U, static_cast<std::uint8_t>(index)),
            };
        },
        3U, 2U);
    check(parallel_cache.prefetch_capacity() == 3U,
          "parallel cache prefetch capacity mismatch");
    check(parallel_cache.worker_count() == 2U,
          "parallel cache worker count mismatch");
    parallel_cache.prefetch(0U);
    parallel_cache.prefetch(1U);
    parallel_cache.prefetch(2U);
    {
        std::unique_lock lock(parallel_mutex);
        const bool started = parallel_started.wait_for(
            lock, std::chrono::seconds(2), [&parallel_loads]() {
                return parallel_loads.load() >= 2U;
            });
        check(started, "parallel image cache workers did not start");
    }
    check(peak_active_loads.load() >= 2U,
          "image cache did not decode concurrently");
    bool bounded_queue_rejected = false;
    try {
        parallel_cache.prefetch(3U);
    } catch (const std::logic_error&) {
        bounded_queue_rejected = true;
    }
    check(bounded_queue_rejected,
          "parallel image cache exceeded its prefetch capacity");
    release_parallel_loaders.set_value();
    for (std::size_t index = 0U; index < 3U; ++index) {
        const auto& image = parallel_cache.get(index);
        check(image.rgb.front() == index,
              "parallel prefetch image contents mismatch");
    }
    parallel_cache.prefetch(3U);
    check(parallel_cache.get(3U).rgb.front() == 3U,
          "parallel cache failed after refilling the bounded queue");
}

void test_training_tiles() {
    const auto landscape = dronegs::make_training_tiles(6001U, 4001U, 4U);
    check(landscape.size() == 4U, "four-tile mode count mismatch");
    check(
        landscape[0].source_x == 0U &&
            landscape[0].source_y == 0U &&
            landscape[0].width == 3000U &&
            landscape[0].height == 2000U,
        "four-tile mode first region mismatch");
    check(
        landscape[3].source_x == 3000U &&
            landscape[3].source_y == 2000U &&
            landscape[3].width == 3001U &&
            landscape[3].height == 2001U,
        "four-tile mode did not preserve odd image borders");

    const auto wide = dronegs::make_training_tiles(6000U, 4000U, 2U);
    check(
        wide.size() == 2U && wide[0].width == 3000U &&
            wide[1].source_x == 3000U && wide[1].height == 4000U,
        "two-tile landscape split mismatch");
    const auto portrait = dronegs::make_training_tiles(4000U, 6000U, 2U);
    check(
        portrait.size() == 2U && portrait[0].height == 3000U &&
            portrait[1].source_y == 3000U && portrait[1].width == 4000U,
        "two-tile portrait split mismatch");

    const dronegs::ImageRegion small_crop{
        .source_x = 1986U,
        .source_y = 2211U,
        .width = 2210U,
        .height = 832U,
    };
    const auto adaptive_small = dronegs::make_adaptive_training_tiles(
        small_crop, 4200U, 3043U, 4U);
    check(
        adaptive_small.size() == 1U &&
            adaptive_small.front().source_x == small_crop.source_x &&
            adaptive_small.front().source_y == small_crop.source_y,
        "small native crop should retain one contextual tile");
    const dronegs::ImageRegion half_crop{
        .source_x = 0U,
        .source_y = 0U,
        .width = 4200U,
        .height = 1603U,
    };
    check(
        dronegs::make_adaptive_training_tiles(
            half_crop, 4200U, 3043U, 4U).size() == 4U,
        "crop above half-sensor budget should retain four tiles");
    const dronegs::ImageRegion narrow_half_crop{
        .source_x = 0U,
        .source_y = 0U,
        .width = 4200U,
        .height = 1400U,
    };
    check(
        dronegs::make_adaptive_training_tiles(
            narrow_half_crop, 4200U, 3043U, 4U).size() == 2U,
        "crop within half-sensor budget should use two tiles");

    bool out_of_bounds_crop_rejected = false;
    try {
        static_cast<void>(dronegs::make_adaptive_training_tiles(
            dronegs::ImageRegion{
                .source_x = 4190U,
                .source_y = 0U,
                .width = 20U,
                .height = 100U,
            },
            4200U, 3043U, 4U));
    } catch (const std::invalid_argument&) {
        out_of_bounds_crop_rejected = true;
    }
    check(
        out_of_bounds_crop_rejected,
        "adaptive crop outside the source image was accepted");

    const auto [native_width, native_height] =
        dronegs::training_image_dimensions(landscape[0], 1U, 4096U);
    check(
        native_width == 3000U && native_height == 2000U,
        "tile-local max-width unexpectedly downsampled a native tile");
    const auto [scaled_width, scaled_height] =
        dronegs::training_image_dimensions(landscape[3], 2U, 4096U);
    check(
        scaled_width == 1500U && scaled_height == 1000U,
        "tile resize dimensions mismatch");

    bool invalid_mode_rejected = false;
    try {
        static_cast<void>(dronegs::make_training_tiles(32U, 32U, 3U));
    } catch (const std::invalid_argument&) {
        invalid_mode_rejected = true;
    }
    check(invalid_mode_rejected, "invalid tile mode was accepted");
}

void test_area_image_resampling() {
    std::vector<std::uint8_t> checkerboard;
    checkerboard.reserve(4U * 4U * 3U);
    for (std::uint32_t y = 0U; y < 4U; ++y) {
        for (std::uint32_t x = 0U; x < 4U; ++x) {
            const auto value = static_cast<std::uint8_t>(
                ((x + y) % 2U == 0U) ? 0U : 255U);
            checkerboard.insert(checkerboard.end(), 3U, value);
        }
    }
    const auto reduced = dronegs::resample_rgb_area(
        checkerboard, 4U, 4U, 2U, 2U);
    check(reduced.size() == 12U, "area resampling size mismatch");
    check(
        std::all_of(
            reduced.begin(), reduced.end(),
            [](std::uint8_t value) { return value == 128U; }),
        "area resampling did not preserve checkerboard energy");

    const std::vector<std::uint8_t> weighted{
        0U, 0U, 0U, 0U, 0U, 0U, 255U, 255U, 255U};
    const auto uneven = dronegs::resample_rgb_area(
        weighted, 3U, 1U, 2U, 1U);
    check(
        uneven[0] == 0U && uneven[3] == 170U,
        "area resampling fractional overlap mismatch");
}


}  // namespace

int main() {
    const auto suffix = std::chrono::steady_clock::now().time_since_epoch().count();
    const auto base = std::filesystem::temp_directory_path() /
                      ("dronegs-core-test-" + std::to_string(suffix));
    const auto data = base / "dataset";
    const auto output = base / "output";
    try {
        test_scene_and_ply(data);
        test_optimizer_profile_registry();
        test_local_scale_initialization();
        test_cli(data, output);
        test_adaptive_capacity_growth();
        test_exact_floor_percentile();
        test_image_cache();
        test_training_tiles();
        test_area_image_resampling();
        std::filesystem::remove_all(base);
        std::cout << "DroneGS core tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "DroneGS core test failed: " << error.what() << "\n";
        std::filesystem::remove_all(base);
        return 1;
    }
}
