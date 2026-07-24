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
        std::cout << "DroneGS 0.5.0-dev.1 fixed-topology additive prototype\n";
        return 0;
    }

    const auto wall_start = clock::now();
    try {
        const auto options = dronegs::parse_options(argc, argv);
        std::filesystem::create_directories(options.output_path);
        const dronegs::RunMeasurements initial{
            .started_at = dronegs::utc_timestamp(),
        };
        std::cerr << "DroneGS 0.5.0-dev.1 uses experimental additive splatting; "
                     "ordered alpha compositing and parity are not implemented yet.\n";
        std::cout << "{\"event\":\"progress\",\"iteration\":0,"
                     "\"iterations\":" << options.iterations
                  << ",\"loss\":0.0,\"gaussians\":0}\n" << std::flush;

        const auto loading_start = clock::now();
        const auto scene = dronegs::load_colmap_scene(options.data_path);
        const auto loading_end = clock::now();
        if (scene.points.size() > options.max_cap) {
            throw std::runtime_error("sparse point count exceeds --max-cap");
        }
        auto gaussians = dronegs::initialize_fixed_topology(scene);
        const auto training = dronegs::train_fixed_topology(
            options, scene, gaussians);

        const auto ply_path = options.output_path / "point_cloud.ply";
        const auto export_start = clock::now();
        dronegs::write_gaussian_ply(ply_path, gaussians, options.sh_degree);
        const auto export_end = clock::now();

        dronegs::RunMeasurements measurements = initial;
        measurements.finished_at = dronegs::utc_timestamp();
        measurements.loading_seconds =
            std::chrono::duration<double>(loading_end - loading_start).count() +
            training.image_loading_seconds;
        measurements.training_seconds = training.training_seconds;
        measurements.initial_loss = training.initial_loss;
        measurements.startup_seconds = training.setup_seconds;
        measurements.final_loss = training.final_loss;
        measurements.export_seconds =
            std::chrono::duration<double>(export_end - export_start).count();
        measurements.wall_seconds =
            std::chrono::duration<double>(export_end - wall_start).count();
        dronegs::write_completed_manifest(
            options, scene, dronegs::dataset_fingerprint(scene), measurements,
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
