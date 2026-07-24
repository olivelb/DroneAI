// SPDX-License-Identifier: MIT
#include "dronegs/image.hpp"

#include <algorithm>
#include <csetjmp>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

#include <jpeglib.h>

namespace dronegs {
namespace {

struct JpegError {
    jpeg_error_mgr manager{};
    std::jmp_buf jump_buffer{};
    char message[JMSG_LENGTH_MAX]{};
};

void jpeg_error_exit(j_common_ptr common) {
    auto* error = reinterpret_cast<JpegError*>(common->err);
    error->manager.format_message(common, error->message);
    std::longjmp(error->jump_buffer, 1);
}

struct DecodedImage {
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::vector<std::uint8_t> rgb;
};

DecodedImage decode_jpeg(const std::filesystem::path& path) {
    auto* file = std::fopen(path.string().c_str(), "rb");
    if (file == nullptr) {
        throw std::runtime_error("cannot open training image: " + path.string());
    }

    jpeg_decompress_struct decoder{};
    JpegError error{};
    decoder.err = jpeg_std_error(&error.manager);
    error.manager.error_exit = jpeg_error_exit;
    if (setjmp(error.jump_buffer) != 0) {
        jpeg_destroy_decompress(&decoder);
        std::fclose(file);
        throw std::runtime_error(
            "cannot decode JPEG " + path.string() + ": " + error.message);
    }

    jpeg_create_decompress(&decoder);
    jpeg_stdio_src(&decoder, file);
    static_cast<void>(jpeg_read_header(&decoder, TRUE));
    decoder.out_color_space = JCS_RGB;
    static_cast<void>(jpeg_start_decompress(&decoder));
    if (decoder.output_components != 3U ||
        decoder.output_width > std::numeric_limits<std::uint32_t>::max() ||
        decoder.output_height > std::numeric_limits<std::uint32_t>::max()) {
        jpeg_destroy_decompress(&decoder);
        std::fclose(file);
        throw std::runtime_error("unsupported JPEG dimensions or colorspace: " + path.string());
    }

    DecodedImage image{
        .width = static_cast<std::uint32_t>(decoder.output_width),
        .height = static_cast<std::uint32_t>(decoder.output_height),
        .rgb = {},
    };
    const auto pixel_count = static_cast<std::size_t>(image.width) * image.height;
    if (pixel_count > std::numeric_limits<std::size_t>::max() / 3U) {
        jpeg_destroy_decompress(&decoder);
        std::fclose(file);
        throw std::runtime_error("JPEG image is too large: " + path.string());
    }
    image.rgb.resize(pixel_count * 3U);
    const auto row_stride = static_cast<std::size_t>(image.width) * 3U;
    while (decoder.output_scanline < decoder.output_height) {
        auto* row = image.rgb.data() +
                    static_cast<std::size_t>(decoder.output_scanline) * row_stride;
        static_cast<void>(jpeg_read_scanlines(&decoder, &row, 1));
    }
    static_cast<void>(jpeg_finish_decompress(&decoder));
    jpeg_destroy_decompress(&decoder);
    std::fclose(file);
    return image;
}

}  // namespace

ImageData load_training_image(const std::filesystem::path& path,
                              std::uint32_t resize_factor,
                              std::uint32_t max_width) {
    const auto source = decode_jpeg(path);
    double effective_factor = static_cast<double>(std::max(resize_factor, 1U));
    if (max_width > 0U && source.width > max_width) {
        effective_factor = std::max(
            effective_factor,
            static_cast<double>(source.width) / static_cast<double>(max_width));
    }
    const auto target_width = std::max(
        1U, static_cast<std::uint32_t>(
                static_cast<double>(source.width) / effective_factor));
    const auto target_height = std::max(
        1U, static_cast<std::uint32_t>(
                static_cast<double>(source.height) / effective_factor));

    ImageData result{
        .width = target_width,
        .height = target_height,
        .source_to_image_x =
            static_cast<float>(target_width) / static_cast<float>(source.width),
        .source_to_image_y =
            static_cast<float>(target_height) / static_cast<float>(source.height),
        .rgb = {},
    };
    result.rgb.resize(static_cast<std::size_t>(target_width) * target_height * 3U);
    for (std::uint32_t y = 0; y < target_height; ++y) {
        const auto source_y = std::min(
            source.height - 1U,
            static_cast<std::uint32_t>(
                static_cast<double>(y) * source.height / target_height));
        for (std::uint32_t x = 0; x < target_width; ++x) {
            const auto source_x = std::min(
                source.width - 1U,
                static_cast<std::uint32_t>(
                    static_cast<double>(x) * source.width / target_width));
            const auto source_offset =
                (static_cast<std::size_t>(source_y) * source.width + source_x) * 3U;
            const auto target_offset =
                (static_cast<std::size_t>(y) * target_width + x) * 3U;
            for (std::size_t channel = 0; channel < 3U; ++channel) {
                result.rgb[target_offset + channel] =
                    static_cast<float>(source.rgb[source_offset + channel]) / 255.0F;
            }
        }
    }
    return result;
}

}  // namespace dronegs
