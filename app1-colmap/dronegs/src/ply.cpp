// SPDX-License-Identifier: MIT
#include "dronegs/ply.hpp"

#include <array>
#include <bit>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace dronegs {
namespace {

void write_float(std::ostream& stream, float value) {
    stream.write(reinterpret_cast<const char*>(&value), sizeof(value));
    if (!stream) {
        throw std::runtime_error("failed while writing Gaussian PLY payload");
    }
}

}  // namespace

GaussianPly read_gaussian_ply(const std::filesystem::path& path) {
    static_assert(std::endian::native == std::endian::little,
                  "binary PLY reader currently requires a little-endian host");
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        throw std::runtime_error(
            "cannot open Gaussian PLY: " + path.string());
    }
    std::string line;
    if (!std::getline(stream, line) || line != "ply") {
        throw std::runtime_error("Gaussian PLY magic is invalid");
    }
    std::size_t vertex_count = 0U;
    bool binary_little_endian = false;
    bool in_vertex_element = false;
    std::vector<std::string> properties;
    while (std::getline(stream, line)) {
        if (!line.empty() && line.back() == '\r') {
            line.pop_back();
        }
        if (line == "format binary_little_endian 1.0") {
            binary_little_endian = true;
        } else if (line.rfind("element ", 0U) == 0U) {
            std::istringstream parser(line);
            std::string keyword;
            std::string name;
            std::size_t count = 0U;
            parser >> keyword >> name >> count;
            in_vertex_element = name == "vertex";
            if (in_vertex_element) {
                vertex_count = count;
            }
        } else if (line.rfind("property ", 0U) == 0U &&
                   in_vertex_element) {
            std::istringstream parser(line);
            std::string keyword;
            std::string type;
            std::string name;
            parser >> keyword >> type >> name;
            if (type != "float" || name.empty()) {
                throw std::runtime_error(
                    "Gaussian PLY vertex properties must be float");
            }
            properties.push_back(std::move(name));
        } else if (line == "end_header") {
            break;
        }
    }
    if (!binary_little_endian || vertex_count == 0U ||
        properties.empty()) {
        throw std::runtime_error(
            "Gaussian PLY header is incomplete or unsupported");
    }
    const std::array required{
        "x", "y", "z",
        "f_dc_0", "f_dc_1", "f_dc_2",
        "opacity",
        "scale_0", "scale_1", "scale_2",
        "rot_0", "rot_1", "rot_2", "rot_3",
    };
    std::unordered_map<std::string, std::size_t> property_indices;
    for (std::size_t index = 0U; index < properties.size(); ++index) {
        property_indices.emplace(properties[index], index);
    }
    for (const auto* name : required) {
        if (!property_indices.contains(name)) {
            throw std::runtime_error(
                "Gaussian PLY is missing property " +
                std::string(name));
        }
    }
    std::uint32_t rest_count = 0U;
    while (property_indices.contains(
        "f_rest_" + std::to_string(rest_count))) {
        ++rest_count;
    }
    if (rest_count > maximum_sh_rest_values ||
        rest_count % 3U != 0U) {
        throw std::runtime_error(
            "Gaussian PLY SH-rest property count is unsupported");
    }
    const std::uint32_t coefficient_count = rest_count / 3U + 1U;
    std::uint32_t sh_degree = 0U;
    while ((sh_degree + 1U) * (sh_degree + 1U) <
           coefficient_count) {
        ++sh_degree;
    }
    if ((sh_degree + 1U) * (sh_degree + 1U) !=
            coefficient_count ||
        sh_degree > maximum_sh_degree) {
        throw std::runtime_error(
            "Gaussian PLY SH degree is unsupported");
    }

    GaussianPly output{
        .gaussians = std::vector<Gaussian>(vertex_count),
        .sh_degree = sh_degree,
    };
    std::vector<float> row(properties.size());
    const auto value = [&row, &property_indices](
                           const std::string& name) {
        return row[property_indices.at(name)];
    };
    for (auto& gaussian : output.gaussians) {
        stream.read(
            reinterpret_cast<char*>(row.data()),
            static_cast<std::streamsize>(
                row.size() * sizeof(float)));
        if (!stream) {
            throw std::runtime_error(
                "Gaussian PLY payload is truncated");
        }
        gaussian.xyz = {
            value("x"), value("y"), value("z")};
        gaussian.dc = {
            value("f_dc_0"),
            value("f_dc_1"),
            value("f_dc_2")};
        for (std::uint32_t index = 0U;
             index < rest_count; ++index) {
            gaussian.sh_rest[index] =
                value("f_rest_" + std::to_string(index));
        }
        gaussian.opacity_logit = value("opacity");
        gaussian.log_scale = {
            value("scale_0"),
            value("scale_1"),
            value("scale_2")};
        gaussian.rotation = {
            value("rot_0"),
            value("rot_1"),
            value("rot_2"),
            value("rot_3")};
        const auto finite = [](float item) {
            return std::isfinite(item);
        };
        for (const float item : gaussian.xyz) {
            if (!finite(item)) {
                throw std::runtime_error(
                    "Gaussian PLY contains non-finite geometry");
            }
        }
        for (const float item : gaussian.log_scale) {
            if (!finite(item)) {
                throw std::runtime_error(
                    "Gaussian PLY contains non-finite scale");
            }
        }
    }
    return output;
}

void write_gaussian_ply(const std::filesystem::path& path,
                        const std::vector<Gaussian>& gaussians,
                        std::uint32_t sh_degree) {
    static_assert(std::endian::native == std::endian::little,
                  "binary PLY writer currently requires a little-endian host");
    if (sh_degree > 3) {
        throw std::invalid_argument("PLY SH degree must be between 0 and 3");
    }
    const std::uint32_t coefficient_count = (sh_degree + 1U) * (sh_degree + 1U);
    const std::uint32_t rest_count = 3U * (coefficient_count - 1U);
    const auto temporary = path.string() + ".tmp";
    std::ofstream stream(temporary, std::ios::binary | std::ios::trunc);
    if (!stream) {
        throw std::runtime_error("cannot create Gaussian PLY: " + temporary);
    }
    stream << "ply\n"
           << "format binary_little_endian 1.0\n"
           << "comment DroneGS anisotropic "
              "geometry-optimized MRNF-growth L1+DSSIM prototype "
              "0.5.0-dev.39\n"
           << "element vertex " << gaussians.size() << "\n"
           << "property float x\n"
           << "property float y\n"
           << "property float z\n"
           << "property float f_dc_0\n"
           << "property float f_dc_1\n"
           << "property float f_dc_2\n";
    for (std::uint32_t index = 0; index < rest_count; ++index) {
        stream << "property float f_rest_" << index << "\n";
    }
    stream << "property float scale_0\n"
           << "property float scale_1\n"
           << "property float scale_2\n"
           << "property float rot_0\n"
           << "property float rot_1\n"
           << "property float rot_2\n"
           << "property float rot_3\n"
           << "property float opacity\n"
           << "end_header\n";
    if (!stream) {
        throw std::runtime_error("failed while writing Gaussian PLY header");
    }

    for (const auto& gaussian : gaussians) {
        for (float value : gaussian.xyz) {
            write_float(stream, value);
        }
        for (float value : gaussian.dc) {
            write_float(stream, value);
        }
        for (std::uint32_t index = 0; index < rest_count; ++index) {
            write_float(stream, gaussian.sh_rest[index]);
        }
        for (float value : gaussian.log_scale) {
            write_float(stream, value);
        }
        for (float value : gaussian.rotation) {
            write_float(stream, value);
        }
        write_float(stream, gaussian.opacity_logit);
    }
    stream.close();
    if (!stream) {
        throw std::runtime_error("failed to finalize Gaussian PLY");
    }
    std::filesystem::rename(temporary, path);
}

}  // namespace dronegs
