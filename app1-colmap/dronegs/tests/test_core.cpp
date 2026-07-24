// SPDX-License-Identifier: MIT
#include <array>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <vector>

#include "dronegs/cli.hpp"
#include "dronegs/colmap.hpp"
#include "dronegs/manifest.hpp"
#include "dronegs/model.hpp"
#include "dronegs/ply.hpp"

namespace {

void check(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
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
    check(scene.cameras.front().parameters.size() == 4, "camera parameter count mismatch");
    check(dronegs::dataset_fingerprint(scene).starts_with("fnv1a64:"),
          "fingerprint kind mismatch");

    const auto gaussians = dronegs::initialize_fixed_topology(scene);
    check(gaussians.size() == 2, "Gaussian count mismatch");
    check(gaussians.front().rotation[0] == 1.0F, "identity quaternion missing");
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
    check(header.find("property float rot_3\n") != std::string::npos,
          "PLY rotation layout mismatch");

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
    };
    const dronegs::RunMeasurements measurements{
        .started_at = "2026-07-24T10:00:00Z",
        .finished_at = "2026-07-24T10:00:01Z",
        .loading_seconds = 0.1,
        .export_seconds = 0.2,
        .wall_seconds = 0.3,
    };
    dronegs::write_completed_manifest(
        options, scene, dronegs::dataset_fingerprint(scene), measurements,
        ply, gaussians.size());
    std::ifstream manifest(options.run_manifest);
    const std::string manifest_text(
        (std::istreambuf_iterator<char>(manifest)), std::istreambuf_iterator<char>());
    check(manifest_text.find("\"contract_version\": 1") != std::string::npos,
          "manifest contract version missing");
    check(manifest_text.find("\"git_revision\": null") == std::string::npos,
          "manifest Git revision missing");
}

void test_cli(const std::filesystem::path& data, const std::filesystem::path& output) {
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

}  // namespace

int main() {
    const auto suffix = std::chrono::steady_clock::now().time_since_epoch().count();
    const auto base = std::filesystem::temp_directory_path() /
                      ("dronegs-core-test-" + std::to_string(suffix));
    const auto data = base / "dataset";
    const auto output = base / "output";
    try {
        test_scene_and_ply(data);
        test_cli(data, output);
        std::filesystem::remove_all(base);
        std::cout << "DroneGS core tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "DroneGS core test failed: " << error.what() << "\n";
        std::filesystem::remove_all(base);
        return 1;
    }
}
