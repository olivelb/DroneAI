// SPDX-License-Identifier: MIT
#include "dronegs/colmap.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <type_traits>

namespace dronegs {
namespace {

template <typename T>
T read_value(std::istream& stream, const char* label) {
    static_assert(std::is_trivially_copyable_v<T>);
    T value{};
    stream.read(reinterpret_cast<char*>(&value), sizeof(T));
    if (!stream) {
        throw std::runtime_error(std::string("truncated COLMAP field: ") + label);
    }
    return value;
}

void skip_bytes(std::istream& stream, std::uint64_t bytes, const char* label) {
    if (bytes > static_cast<std::uint64_t>(std::numeric_limits<std::streamoff>::max())) {
        throw std::runtime_error(std::string("COLMAP field too large: ") + label);
    }
    stream.seekg(static_cast<std::streamoff>(bytes), std::ios::cur);
    if (!stream) {
        throw std::runtime_error(std::string("truncated COLMAP field: ") + label);
    }
}

std::size_t camera_parameter_count(std::int32_t model_id) {
    switch (model_id) {
        case 0: return 3;
        case 1: return 4;
        case 2: return 4;
        case 3: return 5;
        case 4: return 8;
        case 5: return 8;
        case 6: return 12;
        case 7: return 5;
        case 8: return 4;
        case 9: return 5;
        case 10: return 12;
        case 11: return 16;
        default: throw std::runtime_error("unsupported COLMAP camera model id");
    }
}

std::ifstream open_binary(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        throw std::runtime_error("cannot open COLMAP file: " + path.string());
    }
    return stream;
}

std::vector<Camera> load_cameras(const std::filesystem::path& path) {
    auto stream = open_binary(path);
    const auto count = read_value<std::uint64_t>(stream, "camera count");
    std::vector<Camera> cameras;
    cameras.reserve(static_cast<std::size_t>(count));
    for (std::uint64_t index = 0; index < count; ++index) {
        Camera camera;
        camera.id = read_value<std::uint32_t>(stream, "camera id");
        camera.model_id = read_value<std::int32_t>(stream, "camera model");
        camera.width = read_value<std::uint64_t>(stream, "camera width");
        camera.height = read_value<std::uint64_t>(stream, "camera height");
        const auto parameter_count = camera_parameter_count(camera.model_id);
        camera.parameters.reserve(parameter_count);
        for (std::size_t parameter = 0; parameter < parameter_count; ++parameter) {
            camera.parameters.push_back(read_value<double>(stream, "camera parameter"));
        }
        cameras.push_back(std::move(camera));
    }
    return cameras;
}

std::string read_c_string(std::istream& stream) {
    std::string value;
    for (std::size_t length = 0; length < 1'000'000; ++length) {
        const auto byte = read_value<char>(stream, "image name");
        if (byte == '\0') {
            return value;
        }
        value.push_back(byte);
    }
    throw std::runtime_error("COLMAP image name exceeds safety limit");
}

std::vector<Image> load_images(const std::filesystem::path& path) {
    auto stream = open_binary(path);
    const auto count = read_value<std::uint64_t>(stream, "image count");
    std::vector<Image> images;
    images.reserve(static_cast<std::size_t>(count));
    for (std::uint64_t index = 0; index < count; ++index) {
        Image image;
        image.id = read_value<std::uint32_t>(stream, "image id");
        skip_bytes(stream, 7U * sizeof(double), "image pose");
        image.camera_id = read_value<std::uint32_t>(stream, "image camera id");
        image.name = read_c_string(stream);
        const auto observation_count = read_value<std::uint64_t>(stream, "observation count");
        if (observation_count > std::numeric_limits<std::uint64_t>::max() / 24U) {
            throw std::runtime_error("COLMAP observation count overflows");
        }
        skip_bytes(stream, observation_count * 24U, "image observations");
        images.push_back(std::move(image));
    }
    return images;
}

std::vector<SparsePoint> load_points(const std::filesystem::path& path) {
    auto stream = open_binary(path);
    const auto count = read_value<std::uint64_t>(stream, "point count");
    std::vector<SparsePoint> points;
    points.reserve(static_cast<std::size_t>(count));
    for (std::uint64_t index = 0; index < count; ++index) {
        SparsePoint point;
        point.id = read_value<std::uint64_t>(stream, "point id");
        for (double& coordinate : point.xyz) {
            coordinate = read_value<double>(stream, "point coordinate");
        }
        for (std::uint8_t& color : point.rgb) {
            color = read_value<std::uint8_t>(stream, "point color");
        }
        static_cast<void>(read_value<double>(stream, "point error"));
        const auto track_count = read_value<std::uint64_t>(stream, "track count");
        if (track_count > std::numeric_limits<std::uint64_t>::max() / 8U) {
            throw std::runtime_error("COLMAP track count overflows");
        }
        skip_bytes(stream, track_count * 8U, "point track");
        points.push_back(point);
    }
    return points;
}

void hash_bytes(std::uint64_t& hash, const void* data, std::size_t size) {
    constexpr std::uint64_t prime = 1099511628211ULL;
    const auto* bytes = static_cast<const unsigned char*>(data);
    for (std::size_t index = 0; index < size; ++index) {
        hash ^= bytes[index];
        hash *= prime;
    }
}

}  // namespace

std::filesystem::path find_sparse_model(const std::filesystem::path& data_path) {
    const auto nested = data_path / "sparse" / "0";
    const auto flat = data_path / "sparse";
    const auto complete = [](const std::filesystem::path& candidate) {
        return std::filesystem::is_regular_file(candidate / "cameras.bin") &&
               std::filesystem::is_regular_file(candidate / "images.bin") &&
               std::filesystem::is_regular_file(candidate / "points3D.bin");
    };
    if (complete(nested)) {
        return nested;
    }
    if (complete(flat)) {
        return flat;
    }
    throw std::runtime_error("COLMAP sparse binary model not found under " + data_path.string());
}

Scene load_colmap_scene(const std::filesystem::path& data_path) {
    if (!std::filesystem::is_directory(data_path / "images")) {
        throw std::runtime_error("COLMAP dataset has no images directory");
    }
    const auto sparse = find_sparse_model(data_path);
    Scene scene{
        .cameras = load_cameras(sparse / "cameras.bin"),
        .images = load_images(sparse / "images.bin"),
        .points = load_points(sparse / "points3D.bin"),
    };
    if (scene.cameras.empty() || scene.images.empty() || scene.points.empty()) {
        throw std::runtime_error("COLMAP scene must contain cameras, images, and sparse points");
    }
    return scene;
}

std::string dataset_fingerprint(const Scene& scene) {
    std::uint64_t hash = 14695981039346656037ULL;
    for (const auto& image : scene.images) {
        hash_bytes(hash, image.name.data(), image.name.size());
        hash_bytes(hash, &image.camera_id, sizeof(image.camera_id));
    }
    for (const auto& point : scene.points) {
        hash_bytes(hash, &point.id, sizeof(point.id));
        hash_bytes(hash, point.xyz.data(), sizeof(double) * point.xyz.size());
        hash_bytes(hash, point.rgb.data(), sizeof(std::uint8_t) * point.rgb.size());
    }
    std::ostringstream result;
    result << "fnv1a64:" << std::hex << std::setw(16) << std::setfill('0') << hash;
    return result.str();
}

}  // namespace dronegs
