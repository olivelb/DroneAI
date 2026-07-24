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

int main(int argc, char** argv) {
    using clock = std::chrono::steady_clock;
    if (argc == 2 && std::string_view(argv[1]) == "--help") {
        std::cout << dronegs::help_text();
        return 0;
    }
    if (argc == 2 && std::string_view(argv[1]) == "--version") {
        std::cout << "DroneGS 0.4.0 fixed-topology\n";
        return 0;
    }

    const auto wall_start = clock::now();
    try {
        const auto options = dronegs::parse_options(argc, argv);
        std::filesystem::create_directories(options.output_path);
        const dronegs::RunMeasurements initial{
            .started_at = dronegs::utc_timestamp(),
        };
        std::cerr << "DroneGS 0.4.0 is a fixed-topology initialization slice; "
                     "photometric optimization is not implemented yet.\n";
        std::cout << "{\"event\":\"progress\",\"iteration\":0,"
                     "\"iterations\":" << options.iterations
                  << ",\"loss\":0.0,\"gaussians\":0}\n" << std::flush;

        const auto loading_start = clock::now();
        const auto scene = dronegs::load_colmap_scene(options.data_path);
        const auto loading_end = clock::now();
        if (scene.points.size() > options.max_cap) {
            throw std::runtime_error("sparse point count exceeds --max-cap");
        }
        const auto gaussians = dronegs::initialize_fixed_topology(scene);
        const auto ply_path = options.output_path / "point_cloud.ply";
        const auto export_start = clock::now();
        dronegs::write_gaussian_ply(ply_path, gaussians, options.sh_degree);
        const auto export_end = clock::now();

        dronegs::RunMeasurements measurements = initial;
        measurements.finished_at = dronegs::utc_timestamp();
        measurements.loading_seconds =
            std::chrono::duration<double>(loading_end - loading_start).count();
        measurements.export_seconds =
            std::chrono::duration<double>(export_end - export_start).count();
        measurements.wall_seconds =
            std::chrono::duration<double>(export_end - wall_start).count();
        dronegs::write_completed_manifest(
            options, scene, dronegs::dataset_fingerprint(scene), measurements,
            ply_path, gaussians.size());

        std::cout << "{\"event\":\"progress\",\"iteration\":" << options.iterations
                  << ",\"iterations\":" << options.iterations
                  << ",\"loss\":0.0,\"gaussians\":" << gaussians.size() << "}\n";
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
