// SPDX-License-Identifier: MIT
#include "dronegs/cli.hpp"

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
        "ordered-alpha L1+DSSIM prototype 0.5.0-dev.29\n"
        "Usage: dronegs --data-path PATH --output-path PATH --iter N "
        "--strategy mrnf --sh-degree N --max-cap N --resize-factor N "
        "--max-width N --tile-mode N --seed N --run-manifest PATH "
        "[--prefetch-depth N] [--decode-workers N] "
        "[--jpeg-idct-scale 0|1] [--test-every 0|N] "
        "[--save-eval-images 0|1] "
        "[--sh-degree-interval N] "
        "[--optimizer-profile dronegs-dev16|lichtfeld-absolute|"
        "lichtfeld-dc-only|lichtfeld-position-only|"
        "lichtfeld-opacity-only|lichtfeld-scale-only|"
        "lichtfeld-rotation-only|lichtfeld-dc-opacity|"
        "calibrated-dc-0.005-opacity|"
        "calibrated-dc-0.010-opacity|"
        "calibrated-dc-0.020-opacity]\n";
}

Options parse_options(int argc, char** argv) {
    if (argc < 2 || (argc - 1) % 2 != 0) {
        throw std::invalid_argument("contract-v1 options must be supplied as name/value pairs");
    }
    const std::unordered_set<std::string> known{
        "--data-path", "--output-path", "--iter", "--strategy", "--sh-degree",
        "--max-cap", "--resize-factor", "--max-width", "--tile-mode", "--seed",
        "--run-manifest", "--prefetch-depth", "--decode-workers",
        "--jpeg-idct-scale", "--test-every", "--save-eval-images",
        "--optimizer-profile", "--sh-degree-interval",
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
    if (values.contains("--save-eval-images")) {
        options.save_eval_images = parse_u32(
            values.at("--save-eval-images"), "--save-eval-images");
    }
    if (values.contains("--optimizer-profile")) {
        options.optimizer_profile =
            values.at("--optimizer-profile");
    }
    if (values.contains("--sh-degree-interval")) {
        options.sh_degree_interval = parse_u32(
            values.at("--sh-degree-interval"), "--sh-degree-interval");
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
    if (options.save_eval_images > 1U) {
        throw std::invalid_argument(
            "--save-eval-images must be 0 or 1");
    }
    if (options.save_eval_images != 0U &&
        options.test_every == 0U) {
        throw std::invalid_argument(
            "--save-eval-images requires --test-every");
    }
    if (options.optimizer_profile != "lichtfeld-absolute" &&
        options.optimizer_profile != "dronegs-dev16" &&
        options.optimizer_profile != "lichtfeld-dc-only" &&
        options.optimizer_profile != "lichtfeld-position-only" &&
        options.optimizer_profile != "lichtfeld-opacity-only" &&
        options.optimizer_profile != "lichtfeld-scale-only" &&
        options.optimizer_profile != "lichtfeld-rotation-only" &&
        options.optimizer_profile != "lichtfeld-dc-opacity" &&
        options.optimizer_profile != "calibrated-dc-0.005-opacity" &&
        options.optimizer_profile != "calibrated-dc-0.010-opacity" &&
        options.optimizer_profile != "calibrated-dc-0.020-opacity") {
        throw std::invalid_argument(
            "--optimizer-profile must be dronegs-dev16, "
            "lichtfeld-absolute, lichtfeld-dc-only, or "
            "a supported LichtFeld family ablation/combination");
    }

    const auto data = std::filesystem::absolute(options.data_path).lexically_normal();
    const auto output = std::filesystem::absolute(options.output_path).lexically_normal();
    const auto manifest = std::filesystem::absolute(options.run_manifest).lexically_normal();
    if (!std::filesystem::is_directory(data)) {
        throw std::invalid_argument("--data-path must be an existing directory");
    }
    if (is_descendant_or_equal(output, data) || is_descendant_or_equal(data, output)) {
        throw std::invalid_argument("output and source dataset must be separate trees");
    }
    if (std::filesystem::exists(output) && !std::filesystem::is_empty(output)) {
        throw std::invalid_argument("--output-path must not contain existing artifacts");
    }
    if (manifest.parent_path() != output) {
        throw std::invalid_argument("--run-manifest must be directly inside --output-path");
    }
}

}  // namespace dronegs
