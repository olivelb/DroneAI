// SPDX-License-Identifier: MIT
#include "dronegs/cli.hpp"
#include "dronegs/profile_registry.hpp"

#include <charconv>
#include <cstdint>
#include <filesystem>
#include <limits>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_map>
#include <unordered_set>

namespace dronegs {
namespace {

std::uint64_t parse_unsigned(std::string_view text, std::string_view option) {
    std::uint64_t value = 0;
    const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
    if (error != std::errc{} || end != text.data() + text.size()) {
        throw std::invalid_argument(std::string(option) + " must be an unsigned integer");
    }
    return value;
}

std::uint32_t parse_u32(std::string_view text, std::string_view option) {
    const auto value = parse_unsigned(text, option);
    if (value > std::numeric_limits<std::uint32_t>::max()) {
        throw std::invalid_argument(std::string(option) + " is out of range");
    }
    return static_cast<std::uint32_t>(value);
}

bool is_descendant_or_equal(const std::filesystem::path& path,
                            const std::filesystem::path& parent) {
    auto path_it = path.begin();
    for (auto parent_it = parent.begin(); parent_it != parent.end(); ++parent_it, ++path_it) {
        if (path_it == path.end() || *path_it != *parent_it) {
            return false;
        }
    }
    return true;
}

}  // namespace

const char* help_text() {
    return
        "DroneGS complete MRNF lifecycle "
        "ordered-alpha L1+DSSIM prototype 0.5.0-dev.48\n"
        "Usage: dronegs --data-path PATH --output-path PATH --iter N "
        "--strategy mrnf --sh-degree N --max-cap N --resize-factor N "
        "--max-width N --tile-mode N --seed N --run-manifest PATH "
        "[--initial-ply PATH] "
        "[--prefetch-depth N] [--decode-workers N] "
        "[--jpeg-idct-scale 0|1] [--test-every 0|N] "
        "[--test-split modulo|spatial-block] "
        "[--test-guard-percent 0..100] "
        "[--save-eval-images 0|1] "
        "[--checkpoint-every N] [--checkpoint-path PATH] "
        "[--resume-from PATH] [--stop-after N] "
        "[--topology-cooldown N] "
        "[--photometric-finish N] [--photometric-mse-percent 0..100] "
        "[--sh-degree-interval N] "
        "[--profile-id NAME] [--dataset-fingerprint VALUE] "
        "[--optimizer-profile dronegs-dev16|reference-absolute|"
        "reference-absolute-absgrad025|"
        "reference-absolute-absgrad050|"
        "reference-dc-only|reference-position-only|"
        "reference-opacity-only|reference-scale-only|"
        "reference-rotation-only|reference-dc-opacity|"
        "calibrated-dc-0.005-opacity|"
        "calibrated-dc-0.010-opacity|"
        "calibrated-dc-0.020-opacity|"
        "calibrated-dc-0.010-opacity-0.024|"
        "calibrated-dc-0.010-opacity-0.048|"
        "calibrated-dc-0.010-opacity-0.096|"
        "dev34-opacity096-reference-scale|"
        "dev34-opacity096-reference-rotation|"
        "dev34-opacity096-reference-scale-rotation|"
        "dev35-opacity096-reference-scale-staged-rotation004|"
        "dev35-opacity096-reference-scale-staged-rotation008|"
        "dev36-staged-rotation008-absgrad025|"
        "dev36-staged-rotation008-absgrad050|"
        "dev37-staged-rotation008-absgrad050-aa005|"
        "dev37-staged-rotation008-absgrad050-aa015|"
        "dev37-staged-rotation008-absgrad050-aa030|"
        "dev38-staged-rotation008-absgrad050-fastgs] "
        "[--pruning-policy original|spatial-bounds] "
        "[--raster-profile auto|bounded|fastgs]\n";
}

Options parse_options(int argc, char** argv) {
    if (argc < 2 || (argc - 1) % 2 != 0) {
        throw std::invalid_argument("contract-v1 options must be supplied as name/value pairs");
    }
    const std::unordered_set<std::string> known{
        "--data-path", "--output-path", "--iter", "--strategy", "--sh-degree",
        "--max-cap", "--resize-factor", "--max-width", "--tile-mode", "--seed",
        "--run-manifest", "--prefetch-depth", "--decode-workers",
        "--jpeg-idct-scale", "--test-every", "--test-split",
        "--test-guard-percent", "--save-eval-images",
        "--checkpoint-every", "--checkpoint-path", "--resume-from",
        "--stop-after",
        "--topology-cooldown", "--photometric-finish",
        "--photometric-mse-percent",
        "--optimizer-profile", "--sh-degree-interval",
        "--initial-ply", "--pruning-policy", "--raster-profile",
        "--profile-id", "--dataset-fingerprint",
    };
    const std::unordered_set<std::string> required{
        "--data-path", "--output-path", "--iter", "--strategy", "--sh-degree",
        "--max-cap", "--resize-factor", "--max-width", "--tile-mode", "--seed",
        "--run-manifest",
    };
    std::unordered_map<std::string, std::string> values;
    for (int index = 1; index < argc; index += 2) {
        const std::string option = argv[index];
        if (!known.contains(option)) {
            throw std::invalid_argument("unknown option: " + option);
        }
        if (!values.emplace(option, argv[index + 1]).second) {
            throw std::invalid_argument("duplicate option: " + option);
        }
    }
    for (const auto& option : required) {
        if (!values.contains(option)) {
            throw std::invalid_argument("missing required option: " + option);
        }
    }

    Options options;
    options.data_path = values.at("--data-path");
    options.output_path = values.at("--output-path");
    options.run_manifest = values.at("--run-manifest");
    if (values.contains("--initial-ply")) {
        options.initial_ply = values.at("--initial-ply");
    }
    if (values.contains("--checkpoint-path")) {
        options.checkpoint_path = values.at("--checkpoint-path");
    }
    if (values.contains("--resume-from")) {
        options.resume_from = values.at("--resume-from");
    }
    options.iterations = parse_unsigned(values.at("--iter"), "--iter");
    options.strategy = values.at("--strategy");
    options.sh_degree = parse_u32(values.at("--sh-degree"), "--sh-degree");
    options.max_cap = parse_unsigned(values.at("--max-cap"), "--max-cap");
    options.resize_factor = parse_u32(values.at("--resize-factor"), "--resize-factor");
    options.max_width = parse_u32(values.at("--max-width"), "--max-width");
    options.tile_mode = parse_u32(values.at("--tile-mode"), "--tile-mode");
    options.seed = parse_unsigned(values.at("--seed"), "--seed");
    if (values.contains("--prefetch-depth")) {
        options.prefetch_depth =
            parse_u32(values.at("--prefetch-depth"), "--prefetch-depth");
    }
    if (values.contains("--decode-workers")) {
        options.decode_workers =
            parse_u32(values.at("--decode-workers"), "--decode-workers");
    }
    if (values.contains("--jpeg-idct-scale")) {
        options.jpeg_idct_scale =
            parse_u32(values.at("--jpeg-idct-scale"), "--jpeg-idct-scale");
    }
    if (values.contains("--test-every")) {
        options.test_every =
            parse_u32(values.at("--test-every"), "--test-every");
    }
    if (values.contains("--test-split")) {
        options.test_split = values.at("--test-split");
    }
    if (values.contains("--test-guard-percent")) {
        options.test_guard_percent = parse_u32(
            values.at("--test-guard-percent"),
            "--test-guard-percent");
    }
    if (values.contains("--save-eval-images")) {
        options.save_eval_images = parse_u32(
            values.at("--save-eval-images"), "--save-eval-images");
    }
    if (values.contains("--topology-cooldown")) {
        options.topology_cooldown = parse_unsigned(
            values.at("--topology-cooldown"), "--topology-cooldown");
    }
    if (values.contains("--photometric-finish")) {
        options.photometric_finish = parse_unsigned(
            values.at("--photometric-finish"),
            "--photometric-finish");
    }
    if (values.contains("--photometric-mse-percent")) {
        options.photometric_mse_percent = parse_u32(
            values.at("--photometric-mse-percent"),
            "--photometric-mse-percent");
    }
    if (values.contains("--checkpoint-every")) {
        options.checkpoint_every = parse_unsigned(
            values.at("--checkpoint-every"), "--checkpoint-every");
    }
    if (values.contains("--stop-after")) {
        options.stop_after =
            parse_unsigned(values.at("--stop-after"), "--stop-after");
    }
    if (values.contains("--optimizer-profile")) {
        options.optimizer_profile =
            values.at("--optimizer-profile");
    }
    if (values.contains("--profile-id")) {
        options.profile_id = values.at("--profile-id");
    }
    if (values.contains("--dataset-fingerprint")) {
        options.dataset_fingerprint =
            values.at("--dataset-fingerprint");
    }
    if (values.contains("--sh-degree-interval")) {
        options.sh_degree_interval = parse_u32(
            values.at("--sh-degree-interval"), "--sh-degree-interval");
    }
    if (values.contains("--pruning-policy")) {
        options.pruning_policy = values.at("--pruning-policy");
    }
    if (values.contains("--raster-profile")) {
        options.raster_profile = values.at("--raster-profile");
    }
    validate_options(options);
    return options;
}

void validate_options(const Options& options) {
    if (options.iterations == 0) {
        throw std::invalid_argument("--iter must be positive");
    }
    if (options.strategy != "mrnf") {
        throw std::invalid_argument(
            "the native topology-growth prototype only accepts strategy mrnf");
    }
    if (options.sh_degree > maximum_sh_degree) {
        throw std::invalid_argument("--sh-degree must be between 0 and 3");
    }
    if (options.sh_degree_interval == 0U) {
        throw std::invalid_argument("--sh-degree-interval must be positive");
    }
    if (options.max_cap == 0) {
        throw std::invalid_argument("--max-cap must be positive");
    }
    if (options.resize_factor != 1 && options.resize_factor != 2 &&
        options.resize_factor != 4 && options.resize_factor != 8) {
        throw std::invalid_argument("--resize-factor must be 1, 2, 4, or 8");
    }
    if (options.max_width == 0 || options.max_width > 4096) {
        throw std::invalid_argument("--max-width must be between 1 and 4096");
    }
    if (options.tile_mode != 1 && options.tile_mode != 2 && options.tile_mode != 4) {
        throw std::invalid_argument("--tile-mode must be 1, 2, or 4");
    }
    if (options.prefetch_depth == 0U || options.prefetch_depth > 64U) {
        throw std::invalid_argument("--prefetch-depth must be between 1 and 64");
    }
    if (options.decode_workers == 0U || options.decode_workers > 16U) {
        throw std::invalid_argument("--decode-workers must be between 1 and 16");
    }
    if (options.decode_workers > options.prefetch_depth) {
        throw std::invalid_argument(
            "--decode-workers must not exceed --prefetch-depth");
    }
    if (options.jpeg_idct_scale > 1U) {
        throw std::invalid_argument("--jpeg-idct-scale must be 0 or 1");
    }
    if (options.test_every == 1U) {
        throw std::invalid_argument(
            "--test-every must be 0 (disabled) or at least 2");
    }
    if (options.test_split != "modulo" &&
        options.test_split != "spatial-block") {
        throw std::invalid_argument(
            "--test-split must be modulo or spatial-block");
    }
    if (options.test_guard_percent > 100U) {
        throw std::invalid_argument(
            "--test-guard-percent must be between 0 and 100");
    }
    if (options.test_split == "modulo" &&
        options.test_guard_percent != 0U) {
        throw std::invalid_argument(
            "--test-guard-percent requires --test-split spatial-block");
    }
    if (options.test_guard_percent != 0U &&
        options.test_every == 0U) {
        throw std::invalid_argument(
            "--test-guard-percent requires --test-every");
    }
    if (options.save_eval_images > 1U) {
        throw std::invalid_argument(
            "--save-eval-images must be 0 or 1");
    }
    if (options.save_eval_images != 0U &&
        options.test_every == 0U) {
        throw std::invalid_argument(
            "--save-eval-images requires --test-every");
    }
    if (options.topology_cooldown > options.iterations) {
        throw std::invalid_argument(
            "--topology-cooldown must not exceed --iter");
    }
    if (options.photometric_finish > options.iterations) {
        throw std::invalid_argument(
            "--photometric-finish must not exceed --iter");
    }
    if (options.photometric_mse_percent > 100U) {
        throw std::invalid_argument(
            "--photometric-mse-percent must be between 0 and 100");
    }
    if ((options.photometric_finish == 0U) !=
        (options.photometric_mse_percent == 0U)) {
        throw std::invalid_argument(
            "--photometric-finish and --photometric-mse-percent "
            "must both be zero or both be positive");
    }
    if (options.checkpoint_every != 0U &&
        options.checkpoint_path.empty()) {
        throw std::invalid_argument(
            "--checkpoint-every requires --checkpoint-path");
    }
    if (options.stop_after > options.iterations) {
        throw std::invalid_argument(
            "--stop-after must not exceed --iter");
    }
    if (options.stop_after != 0U &&
        options.checkpoint_path.empty()) {
        throw std::invalid_argument(
            "--stop-after requires --checkpoint-path");
    }
    if (!options.resume_from.empty() &&
        !std::filesystem::is_regular_file(options.resume_from)) {
        throw std::invalid_argument(
            "--resume-from must be an existing checkpoint file");
    }
    if (find_optimizer_profile(options.optimizer_profile) == nullptr) {
        throw std::invalid_argument(
            "--optimizer-profile is not present in the versioned registry");
    }
    if (options.pruning_policy != "original" &&
        options.pruning_policy != "spatial-bounds") {
        throw std::invalid_argument(
            "--pruning-policy must be original or spatial-bounds");
    }
    if (options.raster_profile != "auto" &&
        options.raster_profile != "bounded" &&
        options.raster_profile != "fastgs") {
        throw std::invalid_argument(
            "--raster-profile must be auto, bounded, or fastgs");
    }
    if (options.profile_id.empty()) {
        throw std::invalid_argument("--profile-id must not be empty");
    }
    if (options.dataset_fingerprint.size() > 512U) {
        throw std::invalid_argument(
            "--dataset-fingerprint exceeds the safety limit");
    }

    const auto data = std::filesystem::absolute(options.data_path).lexically_normal();
    const auto output = std::filesystem::absolute(options.output_path).lexically_normal();
    const auto manifest = std::filesystem::absolute(options.run_manifest).lexically_normal();
    if (!std::filesystem::is_directory(data)) {
        throw std::invalid_argument("--data-path must be an existing directory");
    }
    if (!options.initial_ply.empty() &&
        !std::filesystem::is_regular_file(options.initial_ply)) {
        throw std::invalid_argument(
            "--initial-ply must be an existing regular file");
    }
    if (is_descendant_or_equal(output, data) || is_descendant_or_equal(data, output)) {
        throw std::invalid_argument("output and source dataset must be separate trees");
    }
    if (options.resume_from.empty() &&
        std::filesystem::exists(output) &&
        !std::filesystem::is_empty(output)) {
        throw std::invalid_argument("--output-path must not contain existing artifacts");
    }
    if (manifest.parent_path() != output) {
        throw std::invalid_argument("--run-manifest must be directly inside --output-path");
    }
}

}  // namespace dronegs
