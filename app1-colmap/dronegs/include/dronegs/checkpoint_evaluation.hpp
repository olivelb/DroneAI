// SPDX-License-Identifier: MIT
#pragma once
#include <algorithm>
#include <charconv>
#include <cstdint>
#include <filesystem>
#include <limits>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>
#include "dronegs/types.hpp"

namespace dronegs {
struct CheckpointEvaluationOptions {
    std::uint64_t expected_completed_iteration = 0;
    std::uint32_t repeats = 1;
    std::uint64_t expected_held_out_frames = 0; // Zero disables this count assertion.
    bool export_all_predictions = false;
    std::vector<std::size_t> export_frame_indices;
    // Supplied by the launcher, NOT computed or verified by native code.
    std::string supplied_binary_sha256;
};
struct CheckpointEvaluationCommand {
    CheckpointEvaluationOptions evaluation;
    int native_arguments_begin = 0;
};
inline std::uint64_t checkpoint_evaluation_unsigned(std::string_view text) {
    std::uint64_t value = 0;
    const auto parsed = std::from_chars(text.data(), text.data() + text.size(), value);
    if (text.empty() || parsed.ec != std::errc{} || parsed.ptr != text.data() + text.size())
        throw std::invalid_argument("diagnostic arguments require unsigned decimal integers");
    return value;
}
inline void validate_checkpoint_evaluation_settings(const CheckpointEvaluationOptions& settings) {
    if (settings.repeats == 0 || settings.repeats > 10)
        throw std::invalid_argument("diagnostic repeats must be 1..10");
    if (settings.supplied_binary_sha256.size() != 64 ||
        settings.supplied_binary_sha256.find_first_not_of("0123456789abcdef") != std::string::npos)
        throw std::invalid_argument("diagnostic requires a supplied lowercase binary SHA256");
    if (settings.export_all_predictions && !settings.export_frame_indices.empty())
        throw std::invalid_argument("choose all predictions or explicit frame indices");
    if (std::set<std::size_t>(settings.export_frame_indices.begin(), settings.export_frame_indices.end()).size()
        != settings.export_frame_indices.size())
        throw std::invalid_argument("duplicate diagnostic frame index");
}
inline CheckpointEvaluationCommand parse_checkpoint_evaluation_command(int argc, char** argv) {
    CheckpointEvaluationCommand command;
    std::set<std::string> seen;
    int index = 1;
    for (; index < argc && std::string_view(argv[index]) != "--"; index += 2) {
        const std::string name = argv[index];
        if (index + 1 >= argc || std::string_view(argv[index + 1]) == "--")
            throw std::invalid_argument("diagnostic options require name/value pairs followed by --");
        if (!seen.insert(name).second) throw std::invalid_argument("duplicate diagnostic option");
        const std::string_view value = argv[index + 1];
        if (name == "--expected-completed-iteration")
            command.evaluation.expected_completed_iteration = checkpoint_evaluation_unsigned(value);
        else if (name == "--expected-held-out-frames")
            command.evaluation.expected_held_out_frames = checkpoint_evaluation_unsigned(value);
        else if (name == "--repeats") {
            const auto count = checkpoint_evaluation_unsigned(value);
            if (count == 0 || count > 10) throw std::invalid_argument("diagnostic repeats must be 1..10");
            command.evaluation.repeats = static_cast<std::uint32_t>(count);
        } else if (name == "--binary-sha256") command.evaluation.supplied_binary_sha256 = value;
        else if (name == "--export-frames") {
            if (value == "all") command.evaluation.export_all_predictions = true;
            else if (value != "none") {
                std::size_t start = 0;
                while (true) {
                    const auto comma = value.find(',', start);
                    const auto number = checkpoint_evaluation_unsigned(value.substr(start,
                        comma == std::string_view::npos ? comma : comma - start));
                    if (number > std::numeric_limits<std::size_t>::max())
                        throw std::invalid_argument("frame index exceeds size_t");
                    command.evaluation.export_frame_indices.push_back(static_cast<std::size_t>(number));
                    if (comma == std::string_view::npos) break;
                    start = comma + 1;
                }
            }
        } else throw std::invalid_argument("unknown diagnostic option: " + name);
    }
    if (index >= argc || index + 1 >= argc || !seen.contains("--expected-completed-iteration"))
        throw std::invalid_argument("diagnostic requires expected iteration and -- followed by native options");
    command.native_arguments_begin = index + 1;
    validate_checkpoint_evaluation_settings(command.evaluation);
    return command;
}
inline void validate_checkpoint_evaluation_request(const Options& options,
    const CheckpointEvaluationOptions& settings) {
    validate_checkpoint_evaluation_settings(settings);
    if (options.resume_from.empty() || !std::filesystem::is_regular_file(options.resume_from) ||
        options.checkpoint_every || !options.checkpoint_path.empty() || options.stop_after ||
        options.eval_every || options.save_eval_images)
        throw std::invalid_argument("read-only evaluation requires a checkpoint and disables training/output side modes");
    if (settings.expected_completed_iteration > options.iterations)
        throw std::invalid_argument("expected checkpoint iteration exceeds unchanged training budget");
    if (options.output_path.empty() || std::filesystem::exists(options.output_path) ||
        std::filesystem::is_symlink(std::filesystem::symlink_status(options.output_path)))
        throw std::invalid_argument("diagnostic output must be a new directory");
    const auto output = std::filesystem::weakly_canonical(options.output_path);
    const auto data = std::filesystem::weakly_canonical(options.data_path);
    const auto contained = [](const auto& child, const auto& parent) {
        const auto mismatch = std::mismatch(parent.begin(), parent.end(), child.begin(), child.end());
        return mismatch.first == parent.end();
    };
    if (contained(output, data) || contained(data, output))
        throw std::invalid_argument("diagnostic output and dataset must resolve to separate trees");
}
void evaluate_checkpoint_read_only(const Options&, const Scene&,
    std::vector<Gaussian>& initial_gaussians, const CheckpointEvaluationOptions&);
} // namespace dronegs
