// SPDX-License-Identifier: MIT
#include <fstream>
#include <iostream>
#include <functional>
#include "dronegs/checkpoint_evaluation.hpp"
#include "dronegs/cli.hpp"
namespace {
void check(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
void rejected(const std::function<void()>& call) {
    try { call(); } catch (const std::invalid_argument&) { return; }
    throw std::runtime_error("invalid request was accepted");
}
dronegs::CheckpointEvaluationCommand parse(std::vector<std::string> args) {
    std::vector<char*> pointers;
    for (auto& arg : args) pointers.push_back(arg.data());
    return dronegs::parse_checkpoint_evaluation_command(static_cast<int>(pointers.size()), pointers.data());
}
}
int main(int argc, char** argv) {
    try {
        if (argc != 2) throw std::invalid_argument("test requires a new scratch directory");
        const std::filesystem::path root = argv[1];
        if (!std::filesystem::create_directory(root)) throw std::invalid_argument("test scratch must be new");
        const std::string sha(64, 'a');
        auto args = std::vector<std::string>{"probe", "--expected-completed-iteration", "20512",
            "--binary-sha256", sha, "--repeats", "2", "--export-frames", "0,73", "--", "--iter", "30000"};
        const auto parsed = parse(args);
        check(parsed.native_arguments_begin == 10 && parsed.evaluation.expected_completed_iteration == 20512 &&
            parsed.evaluation.repeats == 2 && parsed.evaluation.export_frame_indices == std::vector<std::size_t>{0,73},
            "diagnostic/native boundary or indices changed");
        for (const auto& value : {"-1", "+1", "1.0", "", "18446744073709551616"}) {
            auto bad = args; bad[2] = value; rejected([&] { parse(bad); });
        }
        for (const auto& value : {"0", "11", "4294967297"}) {
            auto bad = args; bad[6] = value; rejected([&] { parse(bad); });
        }
        for (const auto& value : {"0,0", "1,", ",1", "1,,2", "-1"}) {
            auto bad = args; bad[8] = value; rejected([&] { parse(bad); });
        }
        for (const auto& value : {"all", "none"}) {
            auto valid = args; valid[8] = value; const auto p = parse(valid);
            check(p.evaluation.export_frame_indices.empty() && p.evaluation.export_all_predictions == (std::string(value)=="all"),
                "all/none mode is wrong");
        }
        { auto bad = args; bad[4] = "not-a-sha"; rejected([&] { parse(bad); }); }
        { auto bad = args; bad[1] = "--unknown"; rejected([&] { parse(bad); }); }
        { auto bad = args; bad[5] = "--binary-sha256"; rejected([&] { parse(bad); }); }
        { auto bad = args; bad.resize(9); rejected([&] { parse(bad); }); }
        { auto bad = args; bad.resize(10); rejected([&] { parse(bad); }); }
        std::filesystem::create_directory(root / "data");
        std::ofstream(root / "source.ckpt") << "test";
        dronegs::Options options;
        options.iterations=30000; options.data_path=root/"data";
        options.output_path=root/"output"; options.resume_from=root/"source.ckpt";
        dronegs::validate_checkpoint_evaluation_request(options, parsed.evaluation);
        check(!std::filesystem::exists(options.output_path), "validation wrote output");
        for (int mode=0; mode<5; ++mode) {
            auto bad=options;
            if(mode==0) bad.checkpoint_every=1;
            if(mode==1) bad.checkpoint_path=root/"write.ckpt";
            if(mode==2) bad.stop_after=20512;
            if(mode==3) bad.eval_every=1;
            if(mode==4) bad.save_eval_images=1;
            rejected([&] { dronegs::validate_checkpoint_evaluation_request(bad,parsed.evaluation); });
        }
        { auto bad=options; bad.iterations=20511; rejected([&]{dronegs::validate_checkpoint_evaluation_request(bad,parsed.evaluation);}); }
        { auto bad=options; bad.output_path=root/"data"/"new"; rejected([&]{dronegs::validate_checkpoint_evaluation_request(bad,parsed.evaluation);}); }
        std::filesystem::create_directory_symlink(root/"data",root/"alias");
        { auto bad=options; bad.output_path=root/"alias"/"new"; rejected([&]{dronegs::validate_checkpoint_evaluation_request(bad,parsed.evaluation);}); }
        std::filesystem::create_symlink(root/"missing",root/"dangling");
        { auto bad=options; bad.output_path=root/"dangling"; rejected([&]{dronegs::validate_checkpoint_evaluation_request(bad,parsed.evaluation);}); }
        std::filesystem::create_directory(options.output_path);
        rejected([&]{dronegs::validate_checkpoint_evaluation_request(options,parsed.evaluation);});
        // Exercise the actual native parser separately, retaining the original budget.
        std::vector<std::string> native{"probe", "--data-path", options.data_path.string(), "--output-path", (root/"native-output").string(),
            "--iter", "30000", "--strategy", "mrnf", "--sh-degree", "3", "--max-cap", "5600000", "--resize-factor", "1",
            "--max-width", "5280", "--tile-mode", "4", "--seed", "42", "--run-manifest", (root/"native-output"/"unused.json").string(),
            "--resume-from", options.resume_from.string()};
        std::vector<char*> pointers; for(auto& arg:native) pointers.push_back(arg.data());
        const auto actual=dronegs::parse_options(static_cast<int>(pointers.size()),pointers.data());
        check(actual.iterations==30000 && actual.seed==42, "native options changed");
        dronegs::validate_checkpoint_evaluation_request(actual,parsed.evaluation);
        std::cout << "checkpoint evaluation CPU parser/request tests passed\n";
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
