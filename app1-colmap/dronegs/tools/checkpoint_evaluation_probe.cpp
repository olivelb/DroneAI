// SPDX-License-Identifier: MIT
#include <iostream>
#include <stdexcept>
#include <string_view>
#include <vector>
#include "dronegs/checkpoint_evaluation.hpp"
#include "dronegs/cli.hpp"
#include "dronegs/colmap.hpp"
#include "dronegs/model.hpp"
#include "dronegs/ply.hpp"

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string_view(argv[1]) == "--help") {
            std::cout << "Read-only native checkpoint evaluation. No training or checkpoint/PLY writes.\n"
                "--expected-completed-iteration N --binary-sha256 LOWERCASE_SHA256 "
                "[--expected-held-out-frames N] [--repeats 1..10] "
                "[--export-frames none|all|FRAME_INDEX,...] -- NATIVE_NAME_VALUE_OPTIONS\n"
                "Keep the original --iter budget and fingerprints. Use --resume-from and a NEW output; "
                "omit checkpoint-path/stop-after and disable checkpoint/eval/save-eval-images.\n";
            return 0;
        }
        const auto command = dronegs::parse_checkpoint_evaluation_command(argc, argv);
        std::vector<char*> native{argv[0]};
        for (int i = command.native_arguments_begin; i < argc; ++i) native.push_back(argv[i]);
        const auto options = dronegs::parse_options(static_cast<int>(native.size()), native.data());
        dronegs::validate_checkpoint_evaluation_request(options, command.evaluation);
        const auto scene = dronegs::load_colmap_scene(options.data_path);
        if (scene.points.size() > options.max_cap && options.initial_ply.empty())
            throw std::runtime_error("sparse point count exceeds --max-cap");
        // Identical initializer/API parameters to main.cpp. Frame support must be
        // established from this initial model, before restoring trained Gaussians.
        auto initial_gaussians = [&]() {
            if (!options.initial_ply.empty()) {
                auto model = dronegs::read_gaussian_ply(options.initial_ply);
                if (model.sh_degree < options.sh_degree)
                    throw std::runtime_error("initial PLY does not contain the requested SH degree");
                return std::move(model.gaussians);
            }
            auto initial = dronegs::initialize_fixed_topology(scene, {
                .policy = options.initial_scale_policy == "projected-knn"
                    ? dronegs::InitialScalePolicy::projected_knn : dronegs::InitialScalePolicy::local_knn,
                .maximum_projected_sigma_pixels = options.initial_max_projected_sigma_pixels,
                .resize_factor = options.resize_factor,
                .maximum_image_width = options.max_width,
                .tile_mode = options.tile_mode,
                .adaptive_native_crop_tiles = options.adaptive_native_crop_tiles != 0U,
            });
            return std::move(initial.gaussians);
        }();
        if (initial_gaussians.size() > options.max_cap)
            throw std::runtime_error("initial Gaussian count exceeds --max-cap");
        dronegs::evaluate_checkpoint_read_only(options, scene, initial_gaussians, command.evaluation);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "checkpoint evaluation diagnostic: " << error.what() << '\n';
        return 1;
    }
}
