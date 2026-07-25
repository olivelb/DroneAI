// SPDX-License-Identifier: MIT
#include "dronegs/ply.hpp"

#include <bit>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>

namespace dronegs {
namespace {

void write_float(std::ostream& stream, float value) {
    stream.write(reinterpret_cast<const char*>(&value), sizeof(value));
    if (!stream) {
        throw std::runtime_error("failed while writing Gaussian PLY payload");
    }
}

}  // namespace

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
           << "comment DroneGS fixed-topology additive prototype 0.5.0-dev.4\n"
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
            write_float(stream, 0.0F);
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
