/* SPDX-FileCopyrightText: 2026 DroneAI authors
 * SPDX-FileCopyrightText: 2025 LichtFeld Studio Authors
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * The dev.15 refine cadence, threshold, growth fraction, and growth window,
 * plus the dev.16 weighted-Gumbel seed protocol and dev.17 optimizer
 * schedules, are adapted from the pinned LichtFeld MRNF strategy. Dev.18
 * profile selection and telemetry emission, dev.19 family-isolated ablations,
 * the dev.20 DC-plus-opacity combination, and the dev.21 intermediate-DC sweep
 * are DroneAI additions. The
 * pre-existing DroneGS orchestration in this file was original MIT code; this
 * combined translation unit is conservatively distributed under
 * GPL-3.0-or-later from dev.15 onward.
 */
#include "dronegs/training.hpp"

#include <cuda_runtime.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "dronegs/image.hpp"
#include "dronegs/colmap.hpp"
#include "dronegs/ordered_training.hpp"
#include "dronegs/profile_registry.hpp"

namespace dronegs {
namespace {

constexpr float sh_c0 = 0.28209479177387814F;
constexpr float minimum_depth = 1.0e-4F;
constexpr float minimum_weight = 1.0e-8F;

void require_cuda(cudaError_t status, const char* operation) {
    if (status != cudaSuccess) {
        throw std::runtime_error(std::string(operation) + ": " + cudaGetErrorString(status));
    }
}

template <typename T>
class DeviceBuffer {
public:
    DeviceBuffer() = default;
    explicit DeviceBuffer(std::size_t count) {
        allocate(count);
    }
    ~DeviceBuffer() {
        if (data_ != nullptr) {
            static_cast<void>(cudaFree(data_));
        }
    }
    DeviceBuffer(const DeviceBuffer&) = delete;
    DeviceBuffer& operator=(const DeviceBuffer&) = delete;
    DeviceBuffer(DeviceBuffer&&) = delete;
    DeviceBuffer& operator=(DeviceBuffer&&) = delete;

    void allocate(std::size_t count) {
        if (data_ != nullptr) {
            throw std::logic_error("device buffer already allocated");
        }
        if (count == 0U || count > std::numeric_limits<std::size_t>::max() / sizeof(T)) {
            throw std::invalid_argument("invalid device buffer size");
        }
        require_cuda(cudaMalloc(reinterpret_cast<void**>(&data_), count * sizeof(T)),
                     "cudaMalloc training buffer");
        count_ = count;
    }

    T* data() { return data_; }
    const T* data() const { return data_; }
    std::size_t size() const { return count_; }

    void zero(std::size_t count) {
        if (count > count_) {
            throw std::out_of_range("device memset exceeds buffer");
        }
        require_cuda(cudaMemset(data_, 0, count * sizeof(T)), "cudaMemset training buffer");
    }

    void copy_from_host(const T* source, std::size_t count) {
        if (count > count_) {
            throw std::out_of_range("host-to-device copy exceeds buffer");
        }
        require_cuda(cudaMemcpy(data_, source, count * sizeof(T), cudaMemcpyHostToDevice),
                     "copy training buffer to device");
    }

    void copy_to_host(T* destination, std::size_t count) const {
        if (count > count_) {
            throw std::out_of_range("device-to-host copy exceeds buffer");
        }
        require_cuda(cudaMemcpy(destination, data_, count * sizeof(T), cudaMemcpyDeviceToHost),
                     "copy training buffer to host");
    }

private:
    T* data_ = nullptr;
    std::size_t count_ = 0;
};

struct DeviceCamera {
    float rotation[9]{};
    float translation[3]{};
    float fx = 0.0F;
    float fy = 0.0F;
    float cx = 0.0F;
    float cy = 0.0F;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
};

struct TrainingFrame {
    const ImageData* image = nullptr;
    DeviceCamera camera;
};

struct ProjectedGaussian {
    float x = 0.0F;
    float y = 0.0F;
    float sigma = 0.0F;
    bool visible = false;
};

__device__ float sigmoid(float value) {
    return 1.0F / (1.0F + expf(-value));
}

__host__ __device__ ProjectedGaussian project_gaussian(
    const Gaussian& gaussian, const DeviceCamera& camera) {
    const float world_x = gaussian.xyz[0];
    const float world_y = gaussian.xyz[1];
    const float world_z = gaussian.xyz[2];
    const float camera_x =
        camera.rotation[0] * world_x + camera.rotation[1] * world_y +
        camera.rotation[2] * world_z + camera.translation[0];
    const float camera_y =
        camera.rotation[3] * world_x + camera.rotation[4] * world_y +
        camera.rotation[5] * world_z + camera.translation[1];
    const float camera_z =
        camera.rotation[6] * world_x + camera.rotation[7] * world_y +
        camera.rotation[8] * world_z + camera.translation[2];
    if (camera_z <= minimum_depth) {
        return {};
    }
    const float x = camera.fx * camera_x / camera_z + camera.cx;
    const float y = camera.fy * camera_y / camera_z + camera.cy;
    const float world_sigma = expf(
        (gaussian.log_scale[0] + gaussian.log_scale[1] + gaussian.log_scale[2]) /
        3.0F);
    const float focal = 0.5F * (camera.fx + camera.fy);
    const float sigma = fminf(fmaxf(world_sigma * focal / camera_z, 0.75F), 8.0F);
    const float support = 2.5F * sigma;
    const bool visible =
        x + support >= 0.0F && y + support >= 0.0F &&
        x - support < static_cast<float>(camera.width) &&
        y - support < static_cast<float>(camera.height);
    return {.x = x, .y = y, .sigma = sigma, .visible = visible};
}

__global__ void render_additive_kernel(const Gaussian* gaussians,
                                       std::size_t gaussian_count,
                                       DeviceCamera camera,
                                       float* accumulated_rgb,
                                       float* accumulated_weight) {
    const std::size_t gaussian_index =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (gaussian_index >= gaussian_count) {
        return;
    }
    const auto& gaussian = gaussians[gaussian_index];
    const auto projected = project_gaussian(gaussian, camera);
    if (!projected.visible) {
        return;
    }
    const float opacity = sigmoid(gaussian.opacity_logit);
    const float color[3]{
        fminf(fmaxf(0.5F + sh_c0 * gaussian.dc[0], 0.0F), 1.0F),
        fminf(fmaxf(0.5F + sh_c0 * gaussian.dc[1], 0.0F), 1.0F),
        fminf(fmaxf(0.5F + sh_c0 * gaussian.dc[2], 0.0F), 1.0F),
    };
    const int support = static_cast<int>(ceilf(2.5F * projected.sigma));
    const int minimum_x = max(0, static_cast<int>(floorf(projected.x)) - support);
    const int maximum_x = min(
        static_cast<int>(camera.width) - 1,
        static_cast<int>(floorf(projected.x)) + support);
    const int minimum_y = max(0, static_cast<int>(floorf(projected.y)) - support);
    const int maximum_y = min(
        static_cast<int>(camera.height) - 1,
        static_cast<int>(floorf(projected.y)) + support);
    const float inverse_two_variance =
        0.5F / (projected.sigma * projected.sigma);
    for (int y = minimum_y; y <= maximum_y; ++y) {
        for (int x = minimum_x; x <= maximum_x; ++x) {
            const float delta_x = (static_cast<float>(x) + 0.5F) - projected.x;
            const float delta_y = (static_cast<float>(y) + 0.5F) - projected.y;
            const float gaussian_weight =
                expf(-(delta_x * delta_x + delta_y * delta_y) * inverse_two_variance);
            const float contribution = opacity * gaussian_weight;
            const auto pixel = static_cast<std::size_t>(y) * camera.width +
                               static_cast<std::size_t>(x);
            atomicAdd(&accumulated_weight[pixel], contribution);
            for (std::size_t channel = 0; channel < 3U; ++channel) {
                atomicAdd(&accumulated_rgb[pixel * 3U + channel],
                          contribution * color[channel]);
            }
        }
    }
}

__global__ void normalize_and_loss_kernel(const float* accumulated_rgb,
                                          const float* accumulated_weight,
                                          const std::uint8_t* target,
                                          float* prediction,
                                          float* loss_sum,
                                          unsigned int* active_pixels,
                                          std::size_t pixel_count) {
    const std::size_t pixel =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (pixel >= pixel_count) {
        return;
    }
    const float weight = accumulated_weight[pixel];
    if (weight <= minimum_weight) {
        prediction[pixel * 3U] = 0.0F;
        prediction[pixel * 3U + 1U] = 0.0F;
        prediction[pixel * 3U + 2U] = 0.0F;
        return;
    }
    float pixel_loss = 0.0F;
    for (std::size_t channel = 0; channel < 3U; ++channel) {
        const auto offset = pixel * 3U + channel;
        const float value = accumulated_rgb[offset] / weight;
        prediction[offset] = value;
        const float target_value = static_cast<float>(target[offset]) / 255.0F;
        pixel_loss += fabsf(value - target_value);
    }
    atomicAdd(loss_sum, pixel_loss);
    atomicAdd(active_pixels, 1U);
}

__global__ void image_gradient_kernel(const float* prediction,
                                      const std::uint8_t* target,
                                      const float* accumulated_weight,
                                      float* image_gradient,
                                      float normalizer,
                                      std::size_t pixel_count) {
    const std::size_t pixel =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (pixel >= pixel_count) {
        return;
    }
    const bool active = accumulated_weight[pixel] > minimum_weight;
    for (std::size_t channel = 0; channel < 3U; ++channel) {
        const auto offset = pixel * 3U + channel;
        const float target_value = static_cast<float>(target[offset]) / 255.0F;
        const float difference = prediction[offset] - target_value;
        image_gradient[offset] =
            active ? (difference > 0.0F ? normalizer :
                      (difference < 0.0F ? -normalizer : 0.0F)) : 0.0F;
    }
}

__global__ void backward_gaussians_kernel(const Gaussian* gaussians,
                                          std::size_t gaussian_count,
                                          DeviceCamera camera,
                                          const float* prediction,
                                          const float* accumulated_weight,
                                          const float* image_gradient,
                                          float* dc_gradient,
                                          float* opacity_gradient) {
    const std::size_t gaussian_index =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (gaussian_index >= gaussian_count) {
        return;
    }
    const auto& gaussian = gaussians[gaussian_index];
    const auto projected = project_gaussian(gaussian, camera);
    if (!projected.visible) {
        dc_gradient[gaussian_index * 3U] = 0.0F;
        dc_gradient[gaussian_index * 3U + 1U] = 0.0F;
        dc_gradient[gaussian_index * 3U + 2U] = 0.0F;
        opacity_gradient[gaussian_index] = 0.0F;
        return;
    }
    const float opacity = sigmoid(gaussian.opacity_logit);
    const float unclamped_color[3]{
        0.5F + sh_c0 * gaussian.dc[0],
        0.5F + sh_c0 * gaussian.dc[1],
        0.5F + sh_c0 * gaussian.dc[2],
    };
    const float color[3]{
        fminf(fmaxf(unclamped_color[0], 0.0F), 1.0F),
        fminf(fmaxf(unclamped_color[1], 0.0F), 1.0F),
        fminf(fmaxf(unclamped_color[2], 0.0F), 1.0F),
    };
    float color_gradient[3]{0.0F, 0.0F, 0.0F};
    float logit_gradient = 0.0F;
    const int support = static_cast<int>(ceilf(2.5F * projected.sigma));
    const int minimum_x = max(0, static_cast<int>(floorf(projected.x)) - support);
    const int maximum_x = min(
        static_cast<int>(camera.width) - 1,
        static_cast<int>(floorf(projected.x)) + support);
    const int minimum_y = max(0, static_cast<int>(floorf(projected.y)) - support);
    const int maximum_y = min(
        static_cast<int>(camera.height) - 1,
        static_cast<int>(floorf(projected.y)) + support);
    const float inverse_two_variance =
        0.5F / (projected.sigma * projected.sigma);
    for (int y = minimum_y; y <= maximum_y; ++y) {
        for (int x = minimum_x; x <= maximum_x; ++x) {
            const auto pixel = static_cast<std::size_t>(y) * camera.width +
                               static_cast<std::size_t>(x);
            const float total_weight = accumulated_weight[pixel];
            if (total_weight <= minimum_weight) {
                continue;
            }
            const float delta_x = (static_cast<float>(x) + 0.5F) - projected.x;
            const float delta_y = (static_cast<float>(y) + 0.5F) - projected.y;
            const float gaussian_weight =
                expf(-(delta_x * delta_x + delta_y * delta_y) * inverse_two_variance);
            const float contribution = opacity * gaussian_weight;
            float contribution_gradient = 0.0F;
            for (std::size_t channel = 0; channel < 3U; ++channel) {
                const auto offset = pixel * 3U + channel;
                const float upstream = image_gradient[offset];
                color_gradient[channel] +=
                    upstream * contribution / total_weight;
                contribution_gradient +=
                    upstream * (color[channel] - prediction[offset]) / total_weight;
            }
            logit_gradient += contribution_gradient * gaussian_weight *
                              opacity * (1.0F - opacity);
        }
    }
    for (std::size_t channel = 0; channel < 3U; ++channel) {
        const bool color_is_unclamped =
            unclamped_color[channel] > 0.0F && unclamped_color[channel] < 1.0F;
        dc_gradient[gaussian_index * 3U + channel] =
            color_is_unclamped ? sh_c0 * color_gradient[channel] : 0.0F;
    }
    opacity_gradient[gaussian_index] = logit_gradient;
}

__global__ void adam_update_kernel(Gaussian* gaussians,
                                   std::size_t gaussian_count,
                                   const float* dc_gradient,
                                   const float* opacity_gradient,
                                   float* first_dc,
                                   float* second_dc,
                                   float* first_opacity,
                                   float* second_opacity,
                                   float inverse_bias_first,
                                   float inverse_bias_second) {
    const std::size_t gaussian_index =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (gaussian_index >= gaussian_count) {
        return;
    }
    constexpr float beta_first = 0.9F;
    constexpr float beta_second = 0.999F;
    constexpr float color_learning_rate = 0.05F;
    constexpr float opacity_learning_rate = 0.01F;
    constexpr float epsilon = 1.0e-8F;
    for (std::size_t channel = 0; channel < 3U; ++channel) {
        const auto offset = gaussian_index * 3U + channel;
        const float gradient = dc_gradient[offset];
        first_dc[offset] =
            beta_first * first_dc[offset] + (1.0F - beta_first) * gradient;
        second_dc[offset] =
            beta_second * second_dc[offset] + (1.0F - beta_second) * gradient * gradient;
        const float corrected_first = first_dc[offset] * inverse_bias_first;
        const float corrected_second = second_dc[offset] * inverse_bias_second;
        gaussians[gaussian_index].dc[channel] -=
            color_learning_rate * corrected_first / (sqrtf(corrected_second) + epsilon);
    }
    const float opacity_grad = opacity_gradient[gaussian_index];
    first_opacity[gaussian_index] =
        beta_first * first_opacity[gaussian_index] +
        (1.0F - beta_first) * opacity_grad;
    second_opacity[gaussian_index] =
        beta_second * second_opacity[gaussian_index] +
        (1.0F - beta_second) * opacity_grad * opacity_grad;
    const float corrected_first =
        first_opacity[gaussian_index] * inverse_bias_first;
    const float corrected_second =
        second_opacity[gaussian_index] * inverse_bias_second;
    gaussians[gaussian_index].opacity_logit -=
        opacity_learning_rate * corrected_first / (sqrtf(corrected_second) + epsilon);
}

const Camera& find_camera(const Scene& scene, std::uint32_t id) {
    const auto found = std::find_if(
        scene.cameras.begin(), scene.cameras.end(),
        [id](const Camera& camera) { return camera.id == id; });
    if (found == scene.cameras.end()) {
        throw std::runtime_error("COLMAP image references an unknown camera");
    }
    return *found;
}

DeviceCamera make_device_camera(const Camera& camera, const Image& image,
                                const ImageData& decoded) {
    if (camera.model_id != 0 && camera.model_id != 1) {
        throw std::runtime_error(
            "Phase 4 prototype supports SIMPLE_PINHOLE and PINHOLE cameras only");
    }
    const auto expected_parameters = camera.model_id == 0 ? 3U : 4U;
    if (camera.parameters.size() != expected_parameters) {
        throw std::runtime_error("invalid pinhole camera parameter count");
    }
    const double norm = std::sqrt(
        image.qvec[0] * image.qvec[0] + image.qvec[1] * image.qvec[1] +
        image.qvec[2] * image.qvec[2] + image.qvec[3] * image.qvec[3]);
    if (norm <= 1.0e-12) {
        throw std::runtime_error("invalid zero COLMAP camera quaternion");
    }
    const double w = image.qvec[0] / norm;
    const double x = image.qvec[1] / norm;
    const double y = image.qvec[2] / norm;
    const double z = image.qvec[3] / norm;
    const std::array<double, 9> rotation{
        1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w),
        2.0 * (x * z + y * w), 2.0 * (x * y + z * w),
        1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w),
        2.0 * (x * z - y * w), 2.0 * (y * z + x * w),
        1.0 - 2.0 * (x * x + y * y),
    };

    DeviceCamera result{};
    for (std::size_t index = 0; index < rotation.size(); ++index) {
        result.rotation[index] = static_cast<float>(rotation[index]);
    }
    for (std::size_t index = 0; index < image.tvec.size(); ++index) {
        result.translation[index] = static_cast<float>(image.tvec[index]);
    }
    if (camera.model_id == 0) {
        result.fx = static_cast<float>(camera.parameters[0]) * decoded.source_to_image_x;
        result.fy = static_cast<float>(camera.parameters[0]) * decoded.source_to_image_y;
        result.cx = static_cast<float>(
            camera.parameters[1] - static_cast<double>(decoded.source_x)) *
            decoded.source_to_image_x;
        result.cy = static_cast<float>(
            camera.parameters[2] - static_cast<double>(decoded.source_y)) *
            decoded.source_to_image_y;
    } else {
        result.fx = static_cast<float>(camera.parameters[0]) * decoded.source_to_image_x;
        result.fy = static_cast<float>(camera.parameters[1]) * decoded.source_to_image_y;
        result.cx = static_cast<float>(
            camera.parameters[2] - static_cast<double>(decoded.source_x)) *
            decoded.source_to_image_x;
        result.cy = static_cast<float>(
            camera.parameters[3] - static_cast<double>(decoded.source_y)) *
            decoded.source_to_image_y;
    }
    result.width = decoded.width;
    result.height = decoded.height;
    return result;
}

RasterCamera make_raster_camera(const DeviceCamera& camera) {
    RasterCamera result{
        .fx = camera.fx,
        .fy = camera.fy,
        .cx = camera.cx,
        .cy = camera.cy,
        .width = camera.width,
        .height = camera.height,
    };
    for (std::size_t index = 0U; index < result.rotation.size(); ++index) {
        result.rotation[index] = camera.rotation[index];
    }
    for (std::size_t index = 0U; index < result.translation.size(); ++index) {
        result.translation[index] = camera.translation[index];
    }
    return result;
}

struct FrameDescriptor {
    const Image* image = nullptr;
    const Camera* camera = nullptr;
    ImageRegion region;
    std::size_t scene_index = 0U;
    std::uint32_t tile_index = 0U;
};

std::pair<std::uint32_t, std::uint32_t> training_dimensions(
    const FrameDescriptor& descriptor, const Options& options) {
    return training_image_dimensions(
        descriptor.region, options.resize_factor, options.max_width);
}

std::vector<FrameDescriptor> make_frame_descriptors(
    const Options& options, const Scene& scene,
    const std::vector<Gaussian>& gaussians,
    std::size_t& maximum_pixels) {
    std::vector<FrameDescriptor> descriptors;
    descriptors.reserve(
        scene.images.size() * static_cast<std::size_t>(options.tile_mode));
    maximum_pixels = 0U;
    for (std::size_t scene_index = 0U;
         scene_index < scene.images.size(); ++scene_index) {
        const auto& image = scene.images[scene_index];
        const auto& camera = find_camera(scene, image.camera_id);
        if (camera.width > std::numeric_limits<std::uint32_t>::max() ||
            camera.height > std::numeric_limits<std::uint32_t>::max()) {
            throw std::runtime_error(
                "training camera dimensions exceed uint32");
        }
        const ImageRegion source_region = image.source_width > 0U
            ? ImageRegion{
                  .source_x = image.source_x,
                  .source_y = image.source_y,
                  .width = image.source_width,
                  .height = image.source_height,
              }
            : ImageRegion{
                  .source_x = 0U,
                  .source_y = 0U,
                  .width = static_cast<std::uint32_t>(camera.width),
                  .height = static_cast<std::uint32_t>(camera.height),
              };
        const auto regions = make_training_tiles(
            source_region,
            options.tile_mode);
        for (std::size_t tile_index = 0U;
             tile_index < regions.size(); ++tile_index) {
            FrameDescriptor descriptor{
                .image = &image,
                .camera = &camera,
                .region = regions[tile_index],
                .scene_index = scene_index,
                .tile_index = static_cast<std::uint32_t>(tile_index),
            };
            const auto [width, height] =
                training_dimensions(descriptor, options);
            const ImageData geometry{
                .width = width,
                .height = height,
                .source_x = descriptor.region.source_x,
                .source_y = descriptor.region.source_y,
                .source_to_image_x =
                    static_cast<float>(width) / descriptor.region.width,
                .source_to_image_y =
                    static_cast<float>(height) / descriptor.region.height,
            };
            const auto device_camera = make_device_camera(
                camera, image, geometry);
            const bool supported = std::any_of(
                gaussians.begin(), gaussians.end(),
                [&device_camera](const Gaussian& gaussian) {
                    return project_gaussian(gaussian, device_camera).visible;
                });
            if (!supported) {
                continue;
            }
            const std::size_t pixels =
                static_cast<std::size_t>(width) * height;
            maximum_pixels = std::max(maximum_pixels, pixels);
            descriptors.push_back(descriptor);
        }
    }
    if (descriptors.empty()) {
        throw std::runtime_error(
            "no training tile has sparse Gaussian support");
    }
    return descriptors;
}

DatasetSplit expand_frame_split(
    const DatasetSplit& image_split,
    const std::vector<FrameDescriptor>& descriptors) {
    std::size_t image_count = 0U;
    for (const auto& descriptor : descriptors) {
        image_count = std::max(image_count, descriptor.scene_index + 1U);
    }
    std::vector<std::uint8_t> membership(image_count, 0U);
    const auto assign = [&membership](
                            const std::vector<std::size_t>& indices,
                            std::uint8_t value) {
        for (const auto index : indices) {
            if (index >= membership.size() || membership[index] != 0U) {
                throw std::logic_error(
                    "dataset split contains an invalid or duplicate image");
            }
            membership[index] = value;
        }
    };
    assign(image_split.training, 1U);
    assign(image_split.held_out, 2U);
    assign(image_split.ignored, 3U);

    DatasetSplit frame_split;
    for (std::size_t frame_index = 0U;
         frame_index < descriptors.size(); ++frame_index) {
        switch (membership.at(descriptors[frame_index].scene_index)) {
            case 1U:
                frame_split.training.push_back(frame_index);
                break;
            case 2U:
                frame_split.held_out.push_back(frame_index);
                break;
            case 3U:
                frame_split.ignored.push_back(frame_index);
                break;
            default:
                throw std::logic_error(
                    "dataset split does not classify every source image");
        }
    }
    return frame_split;
}

struct HostImageCachePlan {
    std::size_t working_set_bytes = 0U;
    std::size_t capacity_bytes = 0U;
};

HostImageCachePlan host_image_cache_plan(
    const std::vector<FrameDescriptor>& descriptors,
    const Options& options) {
    constexpr std::size_t minimum_capacity = 256U * 1024U * 1024U;
    const std::size_t maximum_capacity =
        static_cast<std::size_t>(options.host_image_cache_mib) *
        1024U * 1024U;
    std::size_t working_set_bytes = 0U;
    for (const auto& descriptor : descriptors) {
        const auto [width, height] =
            training_dimensions(descriptor, options);
        const std::size_t pixels =
            static_cast<std::size_t>(width) * height;
        if (pixels > std::numeric_limits<std::size_t>::max() / 3U ||
            working_set_bytes >
                std::numeric_limits<std::size_t>::max() - pixels * 3U) {
            working_set_bytes =
                std::numeric_limits<std::size_t>::max();
            break;
        }
        working_set_bytes += pixels * 3U;
    }
    return {
        .working_set_bytes = working_set_bytes,
        .capacity_bytes = std::clamp(
            working_set_bytes,
            minimum_capacity,
            maximum_capacity),
    };
}

std::vector<std::size_t> make_training_schedule(
    const std::vector<std::size_t>& training_indices,
    std::uint64_t iterations, std::uint64_t seed) {
    if (iterations > std::numeric_limits<std::size_t>::max()) {
        throw std::runtime_error("iteration count exceeds host address space");
    }
    if (training_indices.empty()) {
        throw std::invalid_argument(
            "training schedule requires at least one training image");
    }
    std::vector<std::size_t> schedule;
    schedule.reserve(static_cast<std::size_t>(iterations));
    std::vector<std::size_t> camera_order = training_indices;
    std::mt19937_64 random(seed);
    while (schedule.size() < static_cast<std::size_t>(iterations)) {
        std::shuffle(camera_order.begin(), camera_order.end(), random);
        const auto remaining =
            static_cast<std::size_t>(iterations) - schedule.size();
        const auto count = std::min(remaining, camera_order.size());
        schedule.insert(
            schedule.end(), camera_order.begin(), camera_order.begin() + count);
    }
    return schedule;
}

void prefetch_schedule_window(
    ImageCache& cache, const std::vector<std::size_t>& schedule,
    std::size_t next_index, std::size_t depth) {
    if (next_index >= schedule.size()) {
        return;
    }
    const auto count =
        std::min(depth, schedule.size() - next_index);
    for (std::size_t offset = 0U; offset < count; ++offset) {
        cache.prefetch(schedule[next_index + offset]);
    }
}

TrainingFrame frame_from_cache(ImageCache& cache,
                               const std::vector<FrameDescriptor>& descriptors,
                               std::size_t index, const Options& options) {
    const auto& descriptor = descriptors.at(index);
    const auto& decoded = cache.get(index);
    const auto [expected_width, expected_height] =
        training_dimensions(descriptor, options);
    if (decoded.width != expected_width || decoded.height != expected_height) {
        throw std::runtime_error(
            "decoded training image dimensions do not match COLMAP camera");
    }
    return {
        .image = &decoded,
        .camera = make_device_camera(*descriptor.camera, *descriptor.image, decoded),
    };
}

std::string csv_escape(const std::string& value) {
    std::string result{"\""};
    for (const char character : value) {
        if (character == '"') {
            result += "\"\"";
        } else {
            result += character;
        }
    }
    result += '"';
    return result;
}

void write_prediction_ppm(
    const std::filesystem::path& path,
    const std::vector<float>& prediction,
    std::uint32_t width, std::uint32_t height) {
    const std::size_t sample_count =
        static_cast<std::size_t>(width) * height * 3U;
    if (prediction.size() != sample_count) {
        throw std::invalid_argument(
            "prediction size does not match PPM dimensions");
    }
    std::ofstream stream(path, std::ios::binary | std::ios::trunc);
    if (!stream) {
        throw std::runtime_error(
            "cannot create held-out prediction: " + path.string());
    }
    stream << "P6\n" << width << ' ' << height << "\n255\n";
    std::vector<std::uint8_t> quantized(sample_count);
    for (std::size_t sample = 0U; sample < sample_count; ++sample) {
        quantized[sample] = static_cast<std::uint8_t>(
            std::lround(
                std::clamp(prediction[sample], 0.0F, 1.0F) * 255.0F));
    }
    stream.write(
        reinterpret_cast<const char*>(quantized.data()),
        static_cast<std::streamsize>(quantized.size()));
    if (!stream) {
        throw std::runtime_error(
            "failed to write held-out prediction: " + path.string());
    }
}

void write_target_ppm(
    const std::filesystem::path& path,
    const std::vector<std::uint8_t>& target,
    std::uint32_t width, std::uint32_t height) {
    const std::size_t sample_count =
        static_cast<std::size_t>(width) * height * 3U;
    if (target.size() != sample_count) {
        throw std::invalid_argument(
            "target size does not match PPM dimensions");
    }
    std::ofstream stream(path, std::ios::binary | std::ios::trunc);
    if (!stream) {
        throw std::runtime_error(
            "cannot create held-out target: " + path.string());
    }
    stream << "P6\n" << width << ' ' << height << "\n255\n";
    stream.write(
        reinterpret_cast<const char*>(target.data()),
        static_cast<std::streamsize>(target.size()));
    if (!stream) {
        throw std::runtime_error(
            "failed to write held-out target: " + path.string());
    }
}

struct HeldOutAggregate {
    float psnr = 0.0F;
    float ssim = 0.0F;
    double seconds = 0.0;
};

HeldOutAggregate evaluate_held_out(
    const Options& options, ImageCache& cache,
    const std::vector<FrameDescriptor>& descriptors,
    const std::vector<std::size_t>& held_out_indices,
    OrderedAlphaTrainingContext& workspace,
    std::string_view stage, bool save_predictions) {
    if (held_out_indices.empty()) {
        throw std::invalid_argument(
            "held-out evaluation requires held-out images");
    }
    const auto evaluation_directory =
        options.output_path / "evaluation";
    std::filesystem::create_directories(evaluation_directory);
    const auto csv_path = evaluation_directory / "metrics.csv";
    std::ofstream csv(
        csv_path,
        stage == "initial"
            ? std::ios::trunc
            : std::ios::app);
    if (!csv) {
        throw std::runtime_error(
            "cannot create held-out metrics CSV: " + csv_path.string());
    }
    if (stage == "initial") {
        csv << "stage,held_out_index,frame_index,scene_index,tile_index,image_name,"
               "psnr,ssim,active_pixel_fraction\n";
    }
    const auto prediction_directory = evaluation_directory / "predictions";
    const auto target_directory = evaluation_directory / "targets";
    if (save_predictions) {
        std::filesystem::create_directories(prediction_directory);
        std::filesystem::create_directories(target_directory);
    }

    const auto start = std::chrono::steady_clock::now();
    double psnr_sum = 0.0;
    double ssim_sum = 0.0;
    const std::size_t progress_interval =
        std::max<std::size_t>(1U, held_out_indices.size() / 10U);
    for (std::size_t held_out_index = 0U;
         held_out_index < held_out_indices.size(); ++held_out_index) {
        const auto frame_index = held_out_indices[held_out_index];
        const auto frame = frame_from_cache(
            cache, descriptors, frame_index, options);
        if (held_out_index + 1U < held_out_indices.size()) {
            cache.prefetch(held_out_indices[held_out_index + 1U]);
        }
        const auto raster_camera =
            make_raster_camera(frame.camera);
        std::vector<float> prediction;
        const auto quality = workspace.evaluate_quality(
            raster_camera, frame.image->rgb.data(),
            frame.image->rgb.size(),
            save_predictions ? &prediction : nullptr);
        psnr_sum += quality.psnr;
        ssim_sum += quality.ssim;
        csv << stage << ','
            << held_out_index << ','
            << frame_index << ','
            << descriptors[frame_index].scene_index << ','
            << descriptors[frame_index].tile_index << ','
            << csv_escape(descriptors[frame_index].image->name) << ','
            << std::setprecision(9) << quality.psnr << ','
            << quality.ssim << ','
            << quality.active_pixel_fraction << '\n';
        if (save_predictions) {
            std::ostringstream filename;
            filename << std::setw(6) << std::setfill('0')
                     << held_out_index << ".ppm";
            write_prediction_ppm(
                prediction_directory / filename.str(),
                prediction, raster_camera.width, raster_camera.height);
            write_target_ppm(
                target_directory / filename.str(), frame.image->rgb,
                raster_camera.width, raster_camera.height);
        }
        if (held_out_index == 0U ||
            held_out_index + 1U == held_out_indices.size() ||
            (held_out_index + 1U) % progress_interval == 0U) {
            std::cout
                << "{\"event\":\"evaluation\",\"stage\":\""
                << stage << "\",\"view\":"
                << (held_out_index + 1U)
                << ",\"views\":" << held_out_indices.size()
                << ",\"psnr\":" << quality.psnr
                << ",\"ssim\":" << quality.ssim << "}\n"
                << std::flush;
        }
    }
    csv.close();
    if (!csv) {
        throw std::runtime_error(
            "failed to finalize held-out metrics CSV");
    }
    const double count =
        static_cast<double>(held_out_indices.size());
    return {
        .psnr = static_cast<float>(psnr_sum / count),
        .ssim = static_cast<float>(ssim_sum / count),
        .seconds = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - start).count(),
    };
}

struct TrainingWorkspace {
    explicit TrainingWorkspace(std::size_t gaussian_count, std::size_t maximum_pixels)
        : gaussians(gaussian_count),
          target(maximum_pixels * 3U),
          accumulated_rgb(maximum_pixels * 3U),
          accumulated_weight(maximum_pixels),
          prediction(maximum_pixels * 3U),
          image_gradient(maximum_pixels * 3U),
          loss_sum(1U),
          active_pixels(1U),
          dc_gradient(gaussian_count * 3U),
          opacity_gradient(gaussian_count),
          first_dc(gaussian_count * 3U),
          second_dc(gaussian_count * 3U),
          first_opacity(gaussian_count),
          second_opacity(gaussian_count) {
        first_dc.zero(first_dc.size());
        second_dc.zero(second_dc.size());
        first_opacity.zero(first_opacity.size());
        second_opacity.zero(second_opacity.size());
    }

    DeviceBuffer<Gaussian> gaussians;
    DeviceBuffer<std::uint8_t> target;
    DeviceBuffer<float> accumulated_rgb;
    DeviceBuffer<float> accumulated_weight;
    DeviceBuffer<float> prediction;
    DeviceBuffer<float> image_gradient;
    DeviceBuffer<float> loss_sum;
    DeviceBuffer<unsigned int> active_pixels;
    DeviceBuffer<float> dc_gradient;
    DeviceBuffer<float> opacity_gradient;
    DeviceBuffer<float> first_dc;
    DeviceBuffer<float> second_dc;
    DeviceBuffer<float> first_opacity;
    DeviceBuffer<float> second_opacity;
};

float render_loss(TrainingWorkspace& workspace, const TrainingFrame& frame,
                  std::size_t gaussian_count, bool compute_gradient) {
    if (frame.image == nullptr) {
        throw std::invalid_argument("training frame has no decoded image");
    }
    const std::size_t pixel_count =
        static_cast<std::size_t>(frame.image->width) * frame.image->height;
    workspace.target.copy_from_host(frame.image->rgb.data(), pixel_count * 3U);
    workspace.accumulated_rgb.zero(pixel_count * 3U);
    workspace.accumulated_weight.zero(pixel_count);
    workspace.loss_sum.zero(1U);
    workspace.active_pixels.zero(1U);
    constexpr unsigned int block_size = 256U;
    const auto gaussian_blocks = static_cast<unsigned int>(
        (gaussian_count + block_size - 1U) / block_size);
    render_additive_kernel<<<gaussian_blocks, block_size>>>(
        workspace.gaussians.data(), gaussian_count, frame.camera,
        workspace.accumulated_rgb.data(), workspace.accumulated_weight.data());
    require_cuda(cudaGetLastError(), "launch additive Gaussian render");
    const auto pixel_blocks = static_cast<unsigned int>(
        (pixel_count + block_size - 1U) / block_size);
    normalize_and_loss_kernel<<<pixel_blocks, block_size>>>(
        workspace.accumulated_rgb.data(), workspace.accumulated_weight.data(),
        workspace.target.data(), workspace.prediction.data(),
        workspace.loss_sum.data(), workspace.active_pixels.data(), pixel_count);
    require_cuda(cudaGetLastError(), "launch normalization and loss");
    float loss_sum = 0.0F;
    unsigned int active_pixels = 0U;
    workspace.loss_sum.copy_to_host(&loss_sum, 1U);
    workspace.active_pixels.copy_to_host(&active_pixels, 1U);
    if (active_pixels == 0U) {
        throw std::runtime_error("no sparse Gaussian projects into the selected training image");
    }
    const float normalizer = 1.0F / (3.0F * static_cast<float>(active_pixels));
    if (compute_gradient) {
        image_gradient_kernel<<<pixel_blocks, block_size>>>(
            workspace.prediction.data(), workspace.target.data(),
            workspace.accumulated_weight.data(), workspace.image_gradient.data(),
            normalizer, pixel_count);
        require_cuda(cudaGetLastError(), "launch image gradient");
        backward_gaussians_kernel<<<gaussian_blocks, block_size>>>(
            workspace.gaussians.data(), gaussian_count, frame.camera,
            workspace.prediction.data(), workspace.accumulated_weight.data(),
            workspace.image_gradient.data(), workspace.dc_gradient.data(),
            workspace.opacity_gradient.data());
        require_cuda(cudaGetLastError(), "launch Gaussian backward");
    }
    return loss_sum * normalizer;
}

}  // namespace

DatasetSplit make_dataset_split(
    std::size_t image_count, std::uint32_t test_every) {
    if (image_count == 0U) {
        throw std::invalid_argument(
            "dataset split requires at least one image");
    }
    DatasetSplit split;
    split.training.reserve(image_count);
    split.held_out.reserve(
        test_every == 0U
            ? 0U
            : (image_count + test_every - 1U) / test_every);
    for (std::size_t index = 0U; index < image_count; ++index) {
        if (test_every != 0U && index % test_every == 0U) {
            split.held_out.push_back(index);
        } else {
            split.training.push_back(index);
        }
    }
    if (split.training.empty()) {
        throw std::invalid_argument(
            "dataset split leaves no training images");
    }
    return split;
}

DatasetSplit make_dataset_split(
    const Scene& scene, std::uint32_t test_every,
    std::string_view test_split, std::uint32_t test_guard_percent) {
    if (test_split == "modulo" || test_every == 0U) {
        return make_dataset_split(scene.images.size(), test_every);
    }
    if (test_split != "spatial-block") {
        throw std::invalid_argument(
            "dataset split must be modulo or spatial-block");
    }
    if (scene.images.empty()) {
        throw std::invalid_argument(
            "dataset split requires at least one image");
    }

    std::vector<std::array<double, 3>> centers;
    centers.reserve(scene.images.size());
    for (const auto& image : scene.images) {
        const double qw = image.qvec[0];
        const double qx = image.qvec[1];
        const double qy = image.qvec[2];
        const double qz = image.qvec[3];
        const double norm_squared =
            qw * qw + qx * qx + qy * qy + qz * qz;
        if (!(norm_squared > 0.0) || !std::isfinite(norm_squared)) {
            throw std::invalid_argument(
                "spatial split requires finite camera rotations");
        }
        const double scale = 2.0 / norm_squared;
        const std::array<double, 9> rotation{
            1.0 - scale * (qy * qy + qz * qz),
            scale * (qx * qy - qz * qw),
            scale * (qx * qz + qy * qw),
            scale * (qx * qy + qz * qw),
            1.0 - scale * (qx * qx + qz * qz),
            scale * (qy * qz - qx * qw),
            scale * (qx * qz - qy * qw),
            scale * (qy * qz + qx * qw),
            1.0 - scale * (qx * qx + qy * qy),
        };
        centers.push_back({
            -(rotation[0] * image.tvec[0] +
              rotation[3] * image.tvec[1] +
              rotation[6] * image.tvec[2]),
            -(rotation[1] * image.tvec[0] +
              rotation[4] * image.tvec[1] +
              rotation[7] * image.tvec[2]),
            -(rotation[2] * image.tvec[0] +
              rotation[5] * image.tvec[1] +
              rotation[8] * image.tvec[2]),
        });
    }

    std::array<double, 3> minimum{
        std::numeric_limits<double>::infinity(),
        std::numeric_limits<double>::infinity(),
        std::numeric_limits<double>::infinity(),
    };
    std::array<double, 3> maximum{
        -std::numeric_limits<double>::infinity(),
        -std::numeric_limits<double>::infinity(),
        -std::numeric_limits<double>::infinity(),
    };
    std::array<double, 3> centroid{};
    for (const auto& center : centers) {
        for (std::size_t axis = 0U; axis < 3U; ++axis) {
            minimum[axis] = std::min(minimum[axis], center[axis]);
            maximum[axis] = std::max(maximum[axis], center[axis]);
            centroid[axis] += center[axis];
        }
    }
    for (double& coordinate : centroid) {
        coordinate /= static_cast<double>(centers.size());
    }
    std::array<std::pair<double, std::size_t>, 3> axes{{
        {maximum[0] - minimum[0], 0U},
        {maximum[1] - minimum[1], 1U},
        {maximum[2] - minimum[2], 2U},
    }};
    std::sort(
        axes.begin(), axes.end(),
        [](const auto& first, const auto& second) {
            if (first.first != second.first) {
                return first.first > second.first;
            }
            return first.second < second.second;
        });

    std::vector<std::pair<double, std::size_t>> radial_order;
    radial_order.reserve(centers.size());
    for (std::size_t index = 0U; index < centers.size(); ++index) {
        double radius_squared = 0.0;
        for (std::size_t planar_axis = 0U; planar_axis < 2U;
             ++planar_axis) {
            const auto [range, axis] = axes[planar_axis];
            const double normalized =
                range > 1.0e-12
                    ? (centers[index][axis] - centroid[axis]) / range
                    : 0.0;
            radius_squared += normalized * normalized;
        }
        radial_order.emplace_back(radius_squared, index);
    }
    std::sort(
        radial_order.begin(), radial_order.end(),
        [](const auto& first, const auto& second) {
            if (first.first != second.first) {
                return first.first < second.first;
            }
            return first.second < second.second;
        });

    const std::size_t held_out_count =
        (centers.size() + test_every - 1U) / test_every;
    const std::size_t requested_guard =
        (held_out_count * test_guard_percent + 99U) / 100U;
    const std::size_t maximum_guard =
        centers.size() > held_out_count
            ? centers.size() - held_out_count - 1U
            : 0U;
    const std::size_t guard_count =
        std::min(requested_guard, maximum_guard);

    DatasetSplit split;
    split.held_out.reserve(held_out_count);
    split.ignored.reserve(guard_count);
    split.training.reserve(
        centers.size() - held_out_count - guard_count);
    for (std::size_t rank = 0U; rank < radial_order.size(); ++rank) {
        const auto index = radial_order[rank].second;
        if (rank < held_out_count) {
            split.held_out.push_back(index);
        } else if (rank < held_out_count + guard_count) {
            split.ignored.push_back(index);
        } else {
            split.training.push_back(index);
        }
    }
    std::sort(split.held_out.begin(), split.held_out.end());
    std::sort(split.ignored.begin(), split.ignored.end());
    std::sort(split.training.begin(), split.training.end());
    if (split.training.empty()) {
        throw std::invalid_argument(
            "spatial dataset split leaves no training images");
    }
    return split;
}

TrainingMetrics train_fixed_topology(const Options& options, const Scene& scene,
                                     std::vector<Gaussian>& gaussians) {
    if (gaussians.empty() || scene.images.empty()) {
        throw std::invalid_argument("training requires images and initialized Gaussians");
    }
    std::size_t maximum_pixels = 0U;
    const auto descriptors = make_frame_descriptors(
        options, scene, gaussians, maximum_pixels);
    const auto host_cache_plan =
        host_image_cache_plan(descriptors, options);
    const auto image_split = make_dataset_split(
        scene, options.test_every, options.test_split,
        options.test_guard_percent);
    const auto frame_split = expand_frame_split(image_split, descriptors);
    ImageCache cache(
        descriptors.size(), host_cache_plan.capacity_bytes,
        [&descriptors, &options](std::size_t index) {
            const auto* image = descriptors.at(index).image;
            return load_training_image(
                options.data_path / "images" / image->name,
                options.resize_factor, options.max_width,
                options.jpeg_idct_scale != 0U,
                descriptors.at(index).region);
        },
        options.prefetch_depth, options.decode_workers);

    const auto setup_start = std::chrono::steady_clock::now();
    TrainingWorkspace workspace(gaussians.size(), maximum_pixels);
    workspace.gaussians.copy_from_host(gaussians.data(), gaussians.size());

    TrainingMetrics metrics;
    metrics.iterations = options.iterations;
    metrics.training_image_count =
        static_cast<std::uint64_t>(image_split.training.size());
    metrics.held_out_image_count =
        static_cast<std::uint64_t>(image_split.held_out.size());
    metrics.ignored_image_count =
        static_cast<std::uint64_t>(image_split.ignored.size());
    const auto schedule = make_training_schedule(
        frame_split.training, options.iterations, options.seed);
    const auto initial_frame = frame_from_cache(
        cache, descriptors, frame_split.training.front(), options);
    prefetch_schedule_window(
        cache, schedule, 0U, options.prefetch_depth);
    metrics.initial_loss =
        render_loss(workspace, initial_frame, gaussians.size(), false);
    const auto training_start = std::chrono::steady_clock::now();
    const double setup_wall_seconds =
        std::chrono::duration<double>(training_start - setup_start).count();
    metrics.setup_seconds = std::max(
        0.0, setup_wall_seconds - cache.stats().wait_seconds);
    const double wait_seconds_before_training = cache.stats().wait_seconds;

    constexpr float beta_first = 0.9F;
    constexpr float beta_second = 0.999F;
    float beta_first_power = 1.0F;
    float beta_second_power = 1.0F;
    const std::uint64_t progress_interval = std::max<std::uint64_t>(
        1U, options.iterations / 20U);
    for (std::uint64_t iteration = 1; iteration <= options.iterations; ++iteration) {
        const auto schedule_index = static_cast<std::size_t>(iteration - 1U);
        const auto frame = frame_from_cache(
            cache, descriptors, schedule[schedule_index], options);
        prefetch_schedule_window(
            cache, schedule, schedule_index + 1U,
            options.prefetch_depth);
        const float loss = render_loss(workspace, frame, gaussians.size(), true);
        beta_first_power *= beta_first;
        beta_second_power *= beta_second;
        const float inverse_bias_first = 1.0F / (1.0F - beta_first_power);
        const float inverse_bias_second = 1.0F / (1.0F - beta_second_power);
        constexpr unsigned int block_size = 256U;
        const auto gaussian_blocks = static_cast<unsigned int>(
            (gaussians.size() + block_size - 1U) / block_size);
        adam_update_kernel<<<gaussian_blocks, block_size>>>(
            workspace.gaussians.data(), gaussians.size(),
            workspace.dc_gradient.data(), workspace.opacity_gradient.data(),
            workspace.first_dc.data(), workspace.second_dc.data(),
            workspace.first_opacity.data(), workspace.second_opacity.data(),
            inverse_bias_first, inverse_bias_second);
        require_cuda(cudaGetLastError(), "launch Adam update");
        if (iteration == 1U || iteration == options.iterations ||
            iteration % progress_interval == 0U) {
            std::cout << "{\"event\":\"progress\",\"iteration\":" << iteration
                      << ",\"iterations\":" << options.iterations
                      << ",\"loss\":" << loss
                      << ",\"gaussians\":" << gaussians.size() << "}\n"
                      << std::flush;
        }
    }
    require_cuda(cudaDeviceSynchronize(), "synchronize fixed-topology training");
    const auto training_end = std::chrono::steady_clock::now();
    const double training_wall_seconds =
        std::chrono::duration<double>(training_end - training_start).count();
    const double training_wait_seconds =
        cache.stats().wait_seconds - wait_seconds_before_training;
    metrics.training_seconds = std::max(
        0.0, training_wall_seconds - training_wait_seconds);

    const auto final_evaluation_start = std::chrono::steady_clock::now();
    const double wait_seconds_before_final = cache.stats().wait_seconds;
    const auto final_frame = frame_from_cache(
        cache, descriptors, frame_split.training.front(), options);
    metrics.final_loss =
        render_loss(workspace, final_frame, gaussians.size(), false);
    const double final_evaluation_wall_seconds = std::chrono::duration<double>(
        std::chrono::steady_clock::now() - final_evaluation_start).count();
    const double final_wait_seconds =
        cache.stats().wait_seconds - wait_seconds_before_final;
    metrics.setup_seconds += std::max(
        0.0, final_evaluation_wall_seconds - final_wait_seconds);

    metrics.image_loading_seconds = cache.stats().wait_seconds;
    metrics.image_decode_seconds = cache.stats().loading_seconds;
    metrics.image_cache_hits = cache.stats().hits;
    metrics.image_cache_misses = cache.stats().misses;
    metrics.image_cache_evictions = cache.stats().evictions;
    metrics.image_cache_capacity_bytes =
        static_cast<std::uint64_t>(cache.capacity_bytes());
    metrics.image_cache_working_set_bytes =
        static_cast<std::uint64_t>(host_cache_plan.working_set_bytes);
    metrics.peak_image_cache_bytes =
        static_cast<std::uint64_t>(cache.stats().peak_resident_bytes);
    metrics.image_prefetch_started = cache.stats().prefetch_started;
    metrics.image_prefetch_consumed = cache.stats().prefetch_consumed;
    metrics.image_prefetch_ready = cache.stats().prefetch_ready;
    workspace.gaussians.copy_to_host(gaussians.data(), gaussians.size());
    return metrics;
}

TrainingMetrics train_ordered_mrnf(
    const Options& options, const Scene& scene,
    std::vector<Gaussian>& gaussians) {
    if (gaussians.empty() || scene.images.empty()) {
        throw std::invalid_argument(
            "ordered training requires images and initialized Gaussians");
    }
    std::size_t maximum_pixels = 0U;
    const auto descriptors = make_frame_descriptors(
        options, scene, gaussians, maximum_pixels);
    const auto requested_views =
        scene.images.size() * static_cast<std::size_t>(options.tile_mode);
    std::cout
        << "{\"event\":\"training_view_expansion\",\"source_images\":"
        << scene.images.size() << ",\"tile_mode\":"
        << options.tile_mode << ",\"training_views\":"
        << descriptors.size() << ",\"unsupported_views\":"
        << requested_views - descriptors.size()
        << "}\n"
        << std::flush;
    const auto host_cache_plan =
        host_image_cache_plan(descriptors, options);
    const auto image_split = make_dataset_split(
        scene, options.test_every, options.test_split,
        options.test_guard_percent);
    const auto frame_split = expand_frame_split(image_split, descriptors);
    ImageCache cache(
        descriptors.size(), host_cache_plan.capacity_bytes,
        [&descriptors, &options](std::size_t index) {
            const auto* image = descriptors.at(index).image;
            return load_training_image(
                options.data_path / "images" / image->name,
                options.resize_factor, options.max_width,
                options.jpeg_idct_scale != 0U,
                descriptors.at(index).region);
        },
        options.prefetch_depth, options.decode_workers);

    const auto setup_start = std::chrono::steady_clock::now();
    const auto optimizer_profile = [&options]() {
        const auto profile =
            optimizer_profile_from_name(options.optimizer_profile);
        if (!profile.has_value()) {
            throw std::invalid_argument(
                "optimizer profile is not present in the registry");
        }
        return *profile;
    }();
    const std::optional<bool> raster_override =
        options.raster_profile == "fastgs"
            ? std::optional<bool>(true)
            : (options.raster_profile == "bounded"
                   ? std::optional<bool>(false)
                   : std::nullopt);
    OrderedAlphaTrainingContext workspace(
        gaussians, maximum_pixels, options.iterations,
        static_cast<std::size_t>(options.max_cap),
        optimizer_profile, options.sh_degree,
        options.sh_degree_interval, options.seed,
        raster_override);
    const auto checkpoint_dataset_fingerprint =
        options.dataset_fingerprint.empty()
            ? dataset_fingerprint(scene, options.data_path)
            : options.dataset_fingerprint;
    std::ostringstream checkpoint_configuration;
    checkpoint_configuration
        << "contract=3"
        << ";iterations=" << options.iterations
        << ";strategy=" << options.strategy
        << ";sh=" << options.sh_degree
        << ";sh_interval=" << options.sh_degree_interval
        << ";max_cap=" << options.max_cap
        << ";resize=" << options.resize_factor
        << ";max_width=" << options.max_width
        << ";tile=" << options.tile_mode
        << ";seed=" << options.seed
        << ";test_every=" << options.test_every
        << ";test_split=" << options.test_split
        << ";test_guard_percent=" << options.test_guard_percent
        << ";topology_cooldown=" << options.topology_cooldown
        << ";photometric_finish=" << options.photometric_finish
        << ";photometric_mse=" << options.photometric_mse_percent
        << ";adaptive_growth=2:"
        << options.adaptive_growth_target
        << ";profile_id=" << options.profile_id
        << ";optimizer=" << options.optimizer_profile
        << ";pruning=" << options.pruning_policy
        << ";raster=" << options.raster_profile;
    const auto checkpoint_configuration_fingerprint =
        checkpoint_configuration.str();
    const bool imported_model = !options.initial_ply.empty();
    if (imported_model) {
        workspace.set_active_sh_degree(options.sh_degree);
    }
    const auto initial_learning_rates =
        workspace.current_learning_rates();
    std::cout
        << "{\"event\":\"optimizer_schedule\",\"stage\":\"initial\","
        << "\"profile\":\"" << options.optimizer_profile << "\","
        << "\"position_lr\":" << initial_learning_rates.position
        << ",\"dc_lr\":" << initial_learning_rates.dc
        << ",\"opacity_lr\":" << initial_learning_rates.opacity
        << ",\"scale_lr\":" << initial_learning_rates.scale
        << ",\"rotation_lr\":" << initial_learning_rates.rotation
        << ",\"position_epsilon\":"
        << initial_learning_rates.position_epsilon
        << ",\"dc_epsilon\":" << initial_learning_rates.dc_epsilon
        << ",\"opacity_epsilon\":"
        << initial_learning_rates.opacity_epsilon
        << ",\"scale_epsilon\":" << initial_learning_rates.scale_epsilon
        << ",\"rotation_epsilon\":"
        << initial_learning_rates.rotation_epsilon
        << "}\n"
        << std::flush;
    const auto emit_optimizer_telemetry =
        [](const std::optional<MrnfOptimizerTelemetry>& telemetry) {
        if (!telemetry.has_value()) {
            return;
        }
        const auto emit_family = [&telemetry](
            const char* family,
            const MrnfParameterTelemetry& values) {
            std::cout
                << "{\"event\":\"optimizer_telemetry\",\"step\":"
                << telemetry->step
                << ",\"family\":\"" << family << "\""
                << ",\"gradient_rms\":" << values.gradient_rms
                << ",\"update_rms\":" << values.update_rms
                << ",\"parameter_rms\":" << values.parameter_rms
                << ",\"samples\":" << values.samples
                << "}\n";
        };
        emit_family("dc", telemetry->dc);
        emit_family("opacity", telemetry->opacity);
        emit_family("position", telemetry->position);
        emit_family("scale", telemetry->scale);
        emit_family("rotation", telemetry->rotation);
        std::cout << std::flush;
    };
    TrainingMetrics metrics;
    metrics.iterations = options.iterations;
    metrics.completed_iterations = 0U;
    metrics.training_image_count =
        static_cast<std::uint64_t>(image_split.training.size());
    metrics.held_out_image_count =
        static_cast<std::uint64_t>(image_split.held_out.size());
    metrics.ignored_image_count =
        static_cast<std::uint64_t>(image_split.ignored.size());
    TrainingCheckpointProgress checkpoint_progress;
    if (!options.resume_from.empty()) {
        checkpoint_progress = workspace.load_checkpoint(
            options.resume_from,
            checkpoint_dataset_fingerprint,
            checkpoint_configuration_fingerprint);
        if (checkpoint_progress.completed_iteration >=
            options.iterations) {
            throw std::runtime_error(
                "checkpoint already reached the requested iteration count");
        }
        metrics.completed_iterations =
            checkpoint_progress.completed_iteration;
        metrics.topology_refinements =
            checkpoint_progress.topology_refinements;
        metrics.gaussians_added =
            checkpoint_progress.gaussians_added;
        metrics.gaussians_pruned =
            checkpoint_progress.gaussians_pruned;
        metrics.gaussian_slots_reused =
            checkpoint_progress.gaussian_slots_reused;
        metrics.topology_compactions =
            checkpoint_progress.topology_compactions;
        metrics.initial_loss =
            checkpoint_progress.initial_loss;
        metrics.initial_held_out_psnr =
            checkpoint_progress.initial_held_out_psnr;
        metrics.initial_held_out_ssim =
            checkpoint_progress.initial_held_out_ssim;
        std::cout
            << "{\"event\":\"checkpoint_resumed\",\"iteration\":"
            << checkpoint_progress.completed_iteration
            << ",\"path\":\"" << options.resume_from.string()
            << "\",\"gaussians\":" << workspace.size() << "}\n"
            << std::flush;
    }
    const auto schedule = make_training_schedule(
        frame_split.training, options.iterations, options.seed);
    if (options.resume_from.empty()) {
        const auto initial_frame =
            frame_from_cache(
                cache, descriptors, frame_split.training.front(), options);
        const auto initial_camera =
            make_raster_camera(initial_frame.camera);
        metrics.initial_loss = workspace.evaluate(
            initial_camera, initial_frame.image->rgb.data(),
            initial_frame.image->rgb.size());
    }
    const auto setup_end = std::chrono::steady_clock::now();
    const double setup_wall_seconds =
        std::chrono::duration<double>(
            setup_end - setup_start).count();
    metrics.setup_seconds = std::max(
        0.0, setup_wall_seconds - cache.stats().wait_seconds);
    if (!frame_split.held_out.empty() && options.resume_from.empty()) {
        const auto held_out = evaluate_held_out(
            options, cache, descriptors, frame_split.held_out,
            workspace, "initial",
            imported_model && options.save_eval_images != 0U);
        metrics.initial_held_out_psnr = held_out.psnr;
        metrics.initial_held_out_ssim = held_out.ssim;
        metrics.evaluation_seconds += held_out.seconds;
    }
    checkpoint_progress.initial_loss = metrics.initial_loss;
    checkpoint_progress.initial_held_out_psnr =
        metrics.initial_held_out_psnr;
    checkpoint_progress.initial_held_out_ssim =
        metrics.initial_held_out_ssim;
    prefetch_schedule_window(
        cache, schedule,
        static_cast<std::size_t>(
            checkpoint_progress.completed_iteration),
        options.prefetch_depth);

    const auto training_start = std::chrono::steady_clock::now();
    const double wait_seconds_before_training =
        cache.stats().wait_seconds;
    const std::uint64_t progress_interval =
        std::max<std::uint64_t>(
            1U, options.iterations / 20U);
    const std::uint64_t topology_refine_end =
        topology_refinement_end_iteration(
            options.iterations, options.topology_cooldown,
            options.adaptive_growth_target != 0U);
    if (topology_refine_end < options.iterations) {
        std::cout
            << "{\"event\":\"topology_cooldown\",\"refine_through_iteration\":"
            << topology_refine_end
            << ",\"fixed_topology_iterations\":"
            << (options.iterations - topology_refine_end) << "}\n"
            << std::flush;
    }
    const std::uint64_t photometric_finish_start =
        options.iterations - options.photometric_finish;
    if (options.photometric_finish != 0U) {
        std::cout
            << "{\"event\":\"photometric_finish\","
            << "\"start_after_iteration\":"
            << photometric_finish_start
            << ",\"finish_iterations\":"
            << options.photometric_finish
            << ",\"final_mse_percent\":"
            << options.photometric_mse_percent << "}\n"
            << std::flush;
    }
    for (std::uint64_t iteration =
             checkpoint_progress.completed_iteration + 1U;
         iteration <= options.iterations; ++iteration) {
        const auto schedule_index =
            static_cast<std::size_t>(iteration - 1U);
        const auto frame = frame_from_cache(
            cache, descriptors, schedule[schedule_index], options);
        prefetch_schedule_window(
            cache, schedule, schedule_index + 1U,
            options.prefetch_depth);
        const auto raster_camera =
            make_raster_camera(frame.camera);
        const auto degree_before = workspace.active_sh_degree();
        float mse_blend = 0.0F;
        if (options.photometric_finish != 0U &&
            iteration > photometric_finish_start) {
            const float progress = static_cast<float>(
                iteration - photometric_finish_start) /
                static_cast<float>(options.photometric_finish);
            mse_blend =
                progress *
                (static_cast<float>(
                     options.photometric_mse_percent) /
                 100.0F);
        }
        const float loss = workspace.train_step(
            raster_camera, frame.image->rgb.data(),
            frame.image->rgb.size(), mse_blend);
        if (workspace.active_sh_degree() != degree_before) {
            std::cout
                << "{\"event\":\"sh_degree_activation\",\"iteration\":"
                << iteration << ",\"active_sh_degree\":"
                << workspace.active_sh_degree() << "}\n"
                << std::flush;
        }
        emit_optimizer_telemetry(
            workspace.latest_optimizer_telemetry());
        if (iteration % 200U == 0U && iteration < 28'500U &&
            iteration <= topology_refine_end) {
            float growth_fraction = iteration < 15'000U ? 0.07F : 0.0F;
            if (growth_fraction > 0.0F &&
                options.adaptive_growth_target != 0U) {
                growth_fraction = adaptive_capacity_growth_fraction(
                    workspace.size(),
                    static_cast<std::size_t>(options.max_cap), iteration);
            }
            const auto refinement_seed =
                static_cast<std::uint64_t>(options.seed) ^
                (iteration * 0x9E3779B97F4A7C15ULL);
            const auto topology_start =
                std::chrono::steady_clock::now();
            const auto refinement =
                workspace.refine_topology(
                    0.003F,
                    growth_fraction,
                    refinement_seed,
                    options.pruning_policy == "spatial-bounds");
            const double topology_seconds =
                std::chrono::duration<double>(
                    std::chrono::steady_clock::now() - topology_start)
                    .count();
            metrics.topology_refinement_seconds += topology_seconds;
            ++metrics.topology_refinements;
            metrics.gaussians_added += refinement.added;
            metrics.gaussians_pruned += refinement.pruned;
            metrics.gaussian_slots_reused += refinement.reused;
            metrics.topology_compactions += refinement.compacted ? 1U : 0U;
            std::cout
                << "{\"event\":\"topology_refinement\",\"iteration\":"
                << iteration
                << ",\"growth_fraction\":" << growth_fraction
                << ",\"capacity_target\":"
                << (options.adaptive_growth_target != 0U
                        ? options.max_cap
                        : 0U)
                << ",\"candidates\":" << refinement.candidates
                << ",\"pruned\":" << refinement.pruned
                << ",\"pruned_non_finite\":"
                << refinement.pruned_non_finite
                << ",\"pruned_opacity\":"
                << refinement.pruned_opacity
                << ",\"pruned_scale_small\":"
                << refinement.pruned_scale_small
                << ",\"pruned_scale_large\":"
                << refinement.pruned_scale_large
                << ",\"pruned_spatial\":"
                << refinement.pruned_spatial
                << ",\"added\":" << refinement.added
                << ",\"reused\":" << refinement.reused
                << ",\"appended\":" << refinement.appended
                << ",\"compacted\":"
                << (refinement.compacted ? "true" : "false")
                << ",\"in_place_recycled\":"
                << (refinement.in_place_recycled
                        ? "true"
                        : "false")
                << ",\"gaussians\":" << refinement.gaussian_count
                << ",\"selection_seed\":" << refinement_seed
                << ",\"seconds\":" << topology_seconds
                << "}\n"
                << std::flush;
        }
        metrics.completed_iterations = iteration;
        checkpoint_progress.completed_iteration = iteration;
        checkpoint_progress.topology_refinements =
            metrics.topology_refinements;
        checkpoint_progress.gaussians_added =
            metrics.gaussians_added;
        checkpoint_progress.gaussians_pruned =
            metrics.gaussians_pruned;
        checkpoint_progress.gaussian_slots_reused =
            metrics.gaussian_slots_reused;
        checkpoint_progress.topology_compactions =
            metrics.topology_compactions;
        const bool periodic_checkpoint =
            options.checkpoint_every != 0U &&
            iteration % options.checkpoint_every == 0U;
        const bool requested_stop =
            options.stop_after != 0U &&
            iteration == options.stop_after;
        if (periodic_checkpoint || requested_stop) {
            const auto checkpoint_start =
                std::chrono::steady_clock::now();
            workspace.save_checkpoint(
                options.checkpoint_path, checkpoint_progress,
                checkpoint_dataset_fingerprint,
                checkpoint_configuration_fingerprint);
            const double checkpoint_seconds =
                std::chrono::duration<double>(
                    std::chrono::steady_clock::now() - checkpoint_start)
                    .count();
            metrics.periodic_checkpoint_seconds += checkpoint_seconds;
            std::cout
                << "{\"event\":\"checkpoint_saved\",\"iteration\":"
                << iteration << ",\"path\":\""
                << options.checkpoint_path.string()
                << "\",\"gaussians\":" << workspace.size()
                << ",\"seconds\":" << checkpoint_seconds << "}\n"
                << std::flush;
        }
        if (iteration == 1U ||
            iteration == options.iterations ||
            iteration % progress_interval == 0U) {
            std::cout
                << "{\"event\":\"progress\",\"iteration\":"
                << iteration
                << ",\"iterations\":" << options.iterations
                << ",\"loss\":" << loss
                << ",\"gaussians\":" << workspace.size()
                << "}\n"
                << std::flush;
        }
        if (requested_stop) {
            metrics.completed = false;
            break;
        }
    }
    const auto final_learning_rates =
        workspace.current_learning_rates();
    metrics.final_active_sh_degree = workspace.active_sh_degree();
    std::cout
        << "{\"event\":\"optimizer_schedule\",\"stage\":\"final\","
        << "\"profile\":\"" << options.optimizer_profile << "\","
        << "\"position_lr\":" << final_learning_rates.position
        << ",\"dc_lr\":" << final_learning_rates.dc
        << ",\"opacity_lr\":" << final_learning_rates.opacity
        << ",\"scale_lr\":" << final_learning_rates.scale
        << ",\"rotation_lr\":" << final_learning_rates.rotation
        << ",\"position_epsilon\":"
        << final_learning_rates.position_epsilon
        << ",\"dc_epsilon\":" << final_learning_rates.dc_epsilon
        << ",\"opacity_epsilon\":"
        << final_learning_rates.opacity_epsilon
        << ",\"scale_epsilon\":" << final_learning_rates.scale_epsilon
        << ",\"rotation_epsilon\":"
        << final_learning_rates.rotation_epsilon
        << "}\n"
        << std::flush;
    require_cuda(
        cudaDeviceSynchronize(),
        "synchronize ordered fixed-topology training");
    const auto training_end = std::chrono::steady_clock::now();
    const double training_wall_seconds =
        std::chrono::duration<double>(
            training_end - training_start)
            .count();
    const double training_wait_seconds =
        cache.stats().wait_seconds -
        wait_seconds_before_training;
    metrics.training_seconds = std::max(
        0.0, training_wall_seconds - training_wait_seconds);

    if (!metrics.completed) {
        metrics.final_active_sh_degree =
            workspace.active_sh_degree();
        metrics.image_loading_seconds =
            cache.stats().wait_seconds;
        metrics.image_decode_seconds =
            cache.stats().loading_seconds;
        metrics.image_cache_hits = cache.stats().hits;
        metrics.image_cache_misses = cache.stats().misses;
        metrics.image_cache_evictions = cache.stats().evictions;
        metrics.image_cache_capacity_bytes =
            static_cast<std::uint64_t>(cache.capacity_bytes());
        metrics.image_cache_working_set_bytes =
            static_cast<std::uint64_t>(
                host_cache_plan.working_set_bytes);
        metrics.peak_image_cache_bytes =
            static_cast<std::uint64_t>(
                cache.stats().peak_resident_bytes);
        metrics.image_prefetch_started =
            cache.stats().prefetch_started;
        metrics.image_prefetch_consumed =
            cache.stats().prefetch_consumed;
        metrics.image_prefetch_ready =
            cache.stats().prefetch_ready;
        workspace.download(gaussians);
        return metrics;
    }

    const auto final_evaluation_start =
        std::chrono::steady_clock::now();
    const double wait_seconds_before_final =
        cache.stats().wait_seconds;
    const auto final_frame =
        frame_from_cache(
            cache, descriptors, frame_split.training.front(), options);
    const auto final_camera =
        make_raster_camera(final_frame.camera);
    metrics.final_loss = workspace.evaluate(
        final_camera, final_frame.image->rgb.data(),
        final_frame.image->rgb.size());
    const auto final_anchor_end =
        std::chrono::steady_clock::now();
    const double final_anchor_wall_seconds =
        std::chrono::duration<double>(
            final_anchor_end - final_evaluation_start).count();
    const double final_anchor_wait_seconds =
        cache.stats().wait_seconds - wait_seconds_before_final;
    metrics.setup_seconds += std::max(
        0.0,
        final_anchor_wall_seconds - final_anchor_wait_seconds);
    if (!frame_split.held_out.empty()) {
        const auto held_out = evaluate_held_out(
            options, cache, descriptors, frame_split.held_out,
            workspace, "final",
            !imported_model && options.save_eval_images != 0U);
        metrics.final_held_out_psnr = held_out.psnr;
        metrics.final_held_out_ssim = held_out.ssim;
        metrics.evaluation_seconds += held_out.seconds;
    }

    metrics.image_loading_seconds = cache.stats().wait_seconds;
    metrics.image_decode_seconds = cache.stats().loading_seconds;
    metrics.image_cache_hits = cache.stats().hits;
    metrics.image_cache_misses = cache.stats().misses;
    metrics.image_cache_evictions = cache.stats().evictions;
    metrics.image_cache_capacity_bytes =
        static_cast<std::uint64_t>(cache.capacity_bytes());
    metrics.image_cache_working_set_bytes =
        static_cast<std::uint64_t>(host_cache_plan.working_set_bytes);
    metrics.peak_image_cache_bytes =
        static_cast<std::uint64_t>(
            cache.stats().peak_resident_bytes);
    metrics.image_prefetch_started =
        cache.stats().prefetch_started;
    metrics.image_prefetch_consumed =
        cache.stats().prefetch_consumed;
    metrics.image_prefetch_ready =
        cache.stats().prefetch_ready;
    workspace.download(gaussians);
    return metrics;
}

}  // namespace dronegs
