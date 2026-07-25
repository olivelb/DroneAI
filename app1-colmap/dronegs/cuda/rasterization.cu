/* SPDX-FileCopyrightText: 2026 DroneAI authors
 * SPDX-FileCopyrightText: 2025 LichtFeld Studio Authors
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * The dev.15 MRNF error-weighted contribution and long-axis split behavior
 * are adapted from the pinned LichtFeld implementation. The pre-existing
 * DroneGS rasterizer, loss, gradient, and optimizer code in this file was
 * original MIT code; this combined translation unit is conservatively
 * distributed under GPL-3.0-or-later from dev.15 onward.
 */
#include "dronegs/rasterization.hpp"
#include "dronegs/ordered_training.hpp"

#include <cub/cub.cuh>
#include <cuda_runtime.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

namespace dronegs {
namespace {

constexpr float sh_c0 = 0.28209479177387814F;
constexpr float minimum_depth = 1.0e-4F;
constexpr float minimum_projected_variance = 0.75F * 0.75F;
constexpr float maximum_projected_variance = 8.0F * 8.0F;
constexpr float gaussian_support = 2.5F;
constexpr std::uint32_t threads_per_block = 256U;
constexpr std::uint32_t ssim_window_radius = 5U;
constexpr float l1_objective_weight = 0.8F;
constexpr float dssim_objective_weight = 0.2F;

__device__ __constant__ float ssim_gaussian_weights[11]{
    0.0010283801F,
    0.0075987581F,
    0.0360007721F,
    0.1093606895F,
    0.2130055377F,
    0.2660117249F,
    0.2130055377F,
    0.1093606895F,
    0.0360007721F,
    0.0075987581F,
    0.0010283801F,
};

static_assert(std::is_trivially_copyable_v<Gaussian>);
static_assert(std::is_trivially_copyable_v<ProjectedAlphaSplat>);
static_assert(
    sizeof(std::array<float, 3>) == 3U * sizeof(float));

void require_cuda(cudaError_t status, const char* operation) {
    if (status != cudaSuccess) {
        throw std::runtime_error(
            std::string(operation) + ": " + cudaGetErrorString(status));
    }
}

template <typename T>
class DeviceAllocation {
public:
    explicit DeviceAllocation(std::size_t count) : count_(count) {
        if (count_ == 0U ||
            count_ > std::numeric_limits<std::size_t>::max() / sizeof(T)) {
            throw std::invalid_argument("invalid CUDA raster allocation size");
        }
        require_cuda(
            cudaMalloc(reinterpret_cast<void**>(&data_), count_ * sizeof(T)),
            "cudaMalloc tiled alpha buffer");
    }
    ~DeviceAllocation() {
        if (data_ != nullptr) {
            static_cast<void>(cudaFree(data_));
        }
    }
    DeviceAllocation(const DeviceAllocation&) = delete;
    DeviceAllocation& operator=(const DeviceAllocation&) = delete;

    T* data() { return data_; }
    const T* data() const { return data_; }

    void copy_from_host(const T* source) {
        require_cuda(
            cudaMemcpy(
                data_, source, count_ * sizeof(T), cudaMemcpyHostToDevice),
            "copy tiled alpha buffer to device");
    }

    void copy_to_host(T* destination) const {
        require_cuda(
            cudaMemcpy(
                destination, data_, count_ * sizeof(T), cudaMemcpyDeviceToHost),
            "copy tiled alpha buffer to host");
    }

    void zero() {
        require_cuda(
            cudaMemset(data_, 0, count_ * sizeof(T)),
            "zero tiled alpha buffer");
    }

private:
    T* data_ = nullptr;
    std::size_t count_ = 0;
};

template <typename T>
class ReusableDeviceAllocation {
public:
    ReusableDeviceAllocation() = default;
    ~ReusableDeviceAllocation() {
        if (data_ != nullptr) {
            static_cast<void>(cudaFree(data_));
        }
    }
    ReusableDeviceAllocation(const ReusableDeviceAllocation&) = delete;
    ReusableDeviceAllocation& operator=(
        const ReusableDeviceAllocation&) = delete;

    void ensure(std::size_t count) {
        if (count == 0U) {
            throw std::invalid_argument(
                "invalid reusable CUDA raster allocation size");
        }
        if (count <= capacity_) {
            return;
        }
        if (count >
            std::numeric_limits<std::size_t>::max() / sizeof(T)) {
            throw std::invalid_argument(
                "reusable CUDA raster allocation size overflows");
        }
        T* replacement = nullptr;
        require_cuda(
            cudaMalloc(
                reinterpret_cast<void**>(&replacement),
                count * sizeof(T)),
            "cudaMalloc reusable alpha buffer");
        if (data_ != nullptr) {
            require_cuda(
                cudaFree(data_), "cudaFree replaced alpha buffer");
        }
        data_ = replacement;
        capacity_ = count;
    }

    T* data() { return data_; }
    const T* data() const { return data_; }
    std::size_t capacity() const { return capacity_; }

    void zero(std::size_t count) {
        if (count > capacity_) {
            throw std::out_of_range(
                "reusable CUDA memset exceeds capacity");
        }
        require_cuda(
            cudaMemset(data_, 0, count * sizeof(T)),
            "zero reusable alpha buffer");
    }

    void copy_from_host(const T* source, std::size_t count) {
        if (count > capacity_) {
            throw std::out_of_range(
                "reusable host-to-device copy exceeds capacity");
        }
        require_cuda(
            cudaMemcpy(
                data_, source, count * sizeof(T),
                cudaMemcpyHostToDevice),
            "copy reusable alpha buffer to device");
    }

    void copy_to_host(T* destination, std::size_t count) const {
        if (count > capacity_) {
            throw std::out_of_range(
                "reusable device-to-host copy exceeds capacity");
        }
        require_cuda(
            cudaMemcpy(
                destination, data_, count * sizeof(T),
                cudaMemcpyDeviceToHost),
            "copy reusable alpha buffer to host");
    }

private:
    T* data_ = nullptr;
    std::size_t capacity_ = 0U;
};

struct DeviceRasterCamera {
    float rotation[9]{};
    float translation[3]{};
    float fx = 0.0F;
    float fy = 0.0F;
    float cx = 0.0F;
    float cy = 0.0F;
    std::uint32_t width = 0U;
    std::uint32_t height = 0U;
    std::uint32_t tiles_x = 0U;
    std::uint32_t tiles_y = 0U;
};

struct DeviceProjectedRecord {
    ProjectedAlphaSplat splat{};
    std::uint32_t minimum_tile_x = 0U;
    std::uint32_t maximum_tile_x = 0U;
    std::uint32_t minimum_tile_y = 0U;
    std::uint32_t maximum_tile_y = 0U;
    std::uint64_t pair_count = 0U;
};

static_assert(std::is_trivially_copyable_v<DeviceProjectedRecord>);

struct DeviceRenderStats {
    unsigned long long evaluated_pairs = 0U;
    unsigned long long contributing_pairs = 0U;
};

DeviceRasterCamera make_device_camera(const RasterCamera& camera) {
    DeviceRasterCamera result{};
    for (std::size_t index = 0; index < camera.rotation.size(); ++index) {
        result.rotation[index] = camera.rotation[index];
    }
    for (std::size_t index = 0; index < camera.translation.size(); ++index) {
        result.translation[index] = camera.translation[index];
    }
    result.fx = camera.fx;
    result.fy = camera.fy;
    result.cx = camera.cx;
    result.cy = camera.cy;
    result.width = camera.width;
    result.height = camera.height;
    result.tiles_x =
        camera.width / alpha_tile_width +
        (camera.width % alpha_tile_width != 0U ? 1U : 0U);
    result.tiles_y =
        camera.height / alpha_tile_height +
        (camera.height % alpha_tile_height != 0U ? 1U : 0U);
    return result;
}

void validate_inputs(
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera,
    const std::array<float, 3>& background) {
    if (camera.width == 0U || camera.height == 0U ||
        !std::isfinite(camera.fx) || !std::isfinite(camera.fy) ||
        camera.fx <= 0.0F || camera.fy <= 0.0F) {
        throw std::invalid_argument(
            "tiled alpha renderer requires a valid pinhole camera");
    }
    const auto pixel_count =
        static_cast<std::uint64_t>(camera.width) * camera.height;
    if (pixel_count > std::numeric_limits<std::size_t>::max() / 3U) {
        throw std::invalid_argument(
            "tiled alpha image dimensions are too large");
    }
    const auto tiles_x =
        (static_cast<std::uint64_t>(camera.width) +
         alpha_tile_width - 1U) /
        alpha_tile_width;
    const auto tiles_y =
        (static_cast<std::uint64_t>(camera.height) +
         alpha_tile_height - 1U) /
        alpha_tile_height;
    if (tiles_x * tiles_y > std::numeric_limits<std::uint32_t>::max()) {
        throw std::invalid_argument("tiled alpha tile count exceeds uint32");
    }
    if (gaussians.size() >
        static_cast<std::size_t>(std::numeric_limits<int>::max() - 1)) {
        throw std::invalid_argument(
            "tiled alpha Gaussian count exceeds CUB item limit");
    }
    for (const float channel : background) {
        if (!std::isfinite(channel) || channel < 0.0F || channel > 1.0F) {
            throw std::invalid_argument("tiled alpha background is invalid");
        }
    }
}

__device__ bool project_covariance_device(
    const Gaussian& gaussian, DeviceRasterCamera camera,
    float camera_x, float camera_y, float camera_z,
    float& radius_x, float& radius_y,
    float& conic_xx, float& conic_xy, float& conic_yy) {
    const float quaternion_norm = sqrtf(
        gaussian.rotation[0] * gaussian.rotation[0] +
        gaussian.rotation[1] * gaussian.rotation[1] +
        gaussian.rotation[2] * gaussian.rotation[2] +
        gaussian.rotation[3] * gaussian.rotation[3]);
    if (!isfinite(quaternion_norm) ||
        quaternion_norm <= 1.0e-12F) {
        return false;
    }
    const float w = gaussian.rotation[0] / quaternion_norm;
    const float x = gaussian.rotation[1] / quaternion_norm;
    const float y = gaussian.rotation[2] / quaternion_norm;
    const float z = gaussian.rotation[3] / quaternion_norm;
    const float gaussian_rotation[9]{
        1.0F - 2.0F * (y * y + z * z),
        2.0F * (x * y - z * w),
        2.0F * (x * z + y * w),
        2.0F * (x * y + z * w),
        1.0F - 2.0F * (x * x + z * z),
        2.0F * (y * z - x * w),
        2.0F * (x * z - y * w),
        2.0F * (y * z + x * w),
        1.0F - 2.0F * (x * x + y * y),
    };
    float camera_gaussian_rotation[9]{};
    for (std::uint32_t row = 0U; row < 3U; ++row) {
        for (std::uint32_t column = 0U; column < 3U; ++column) {
            for (std::uint32_t inner = 0U; inner < 3U; ++inner) {
                camera_gaussian_rotation[row * 3U + column] +=
                    camera.rotation[row * 3U + inner] *
                    gaussian_rotation[inner * 3U + column];
            }
        }
    }
    float scale[3]{};
    for (std::uint32_t axis = 0U; axis < 3U; ++axis) {
        scale[axis] = expf(gaussian.log_scale[axis]);
        if (!isfinite(scale[axis]) || scale[axis] <= 0.0F) {
            return false;
        }
    }
    const float inverse_depth = 1.0F / camera_z;
    const float inverse_depth_squared =
        inverse_depth * inverse_depth;
    const float jacobian_xx = camera.fx * inverse_depth;
    const float jacobian_xz =
        -camera.fx * camera_x * inverse_depth_squared;
    const float jacobian_yy = camera.fy * inverse_depth;
    const float jacobian_yz =
        -camera.fy * camera_y * inverse_depth_squared;
    float projected_row_x[3]{};
    float projected_row_y[3]{};
    for (std::uint32_t column = 0U; column < 3U; ++column) {
        projected_row_x[column] =
            (jacobian_xx * camera_gaussian_rotation[column] +
             jacobian_xz *
                 camera_gaussian_rotation[2U * 3U + column]) *
            scale[column];
        projected_row_y[column] =
            (jacobian_yy *
                 camera_gaussian_rotation[1U * 3U + column] +
             jacobian_yz *
                 camera_gaussian_rotation[2U * 3U + column]) *
            scale[column];
    }
    float covariance_xx = 0.0F;
    float covariance_xy = 0.0F;
    float covariance_yy = 0.0F;
    for (std::uint32_t column = 0U; column < 3U; ++column) {
        covariance_xx +=
            projected_row_x[column] * projected_row_x[column];
        covariance_xy +=
            projected_row_x[column] * projected_row_y[column];
        covariance_yy +=
            projected_row_y[column] * projected_row_y[column];
    }
    if (!isfinite(covariance_xx) ||
        !isfinite(covariance_xy) ||
        !isfinite(covariance_yy)) {
        return false;
    }
    const float trace = covariance_xx + covariance_yy;
    const float difference = covariance_xx - covariance_yy;
    const float spectral_gap = sqrtf(fmaxf(
        0.0F,
        difference * difference +
            4.0F * covariance_xy * covariance_xy));
    const float eigenvalue_maximum =
        0.5F * (trace + spectral_gap);
    const float eigenvalue_minimum =
        0.5F * (trace - spectral_gap);
    const float clamped_maximum = fminf(
        maximum_projected_variance,
        fmaxf(minimum_projected_variance, eigenvalue_maximum));
    const float clamped_minimum = fminf(
        maximum_projected_variance,
        fmaxf(minimum_projected_variance, eigenvalue_minimum));
    if (spectral_gap > 1.0e-8F) {
        const float projector_xx =
            (covariance_xx - eigenvalue_minimum) / spectral_gap;
        const float projector_xy =
            covariance_xy / spectral_gap;
        const float projector_yy =
            (covariance_yy - eigenvalue_minimum) / spectral_gap;
        const float clamped_gap =
            clamped_maximum - clamped_minimum;
        covariance_xx =
            clamped_minimum + clamped_gap * projector_xx;
        covariance_xy = clamped_gap * projector_xy;
        covariance_yy =
            clamped_minimum + clamped_gap * projector_yy;
    } else {
        const float variance = fminf(
            maximum_projected_variance,
            fmaxf(minimum_projected_variance, 0.5F * trace));
        covariance_xx = variance;
        covariance_xy = 0.0F;
        covariance_yy = variance;
    }
    const float determinant =
        covariance_xx * covariance_yy -
        covariance_xy * covariance_xy;
    if (!isfinite(determinant) || determinant <= 1.0e-12F) {
        return false;
    }
    radius_x = gaussian_support * sqrtf(covariance_xx);
    radius_y = gaussian_support * sqrtf(covariance_yy);
    conic_xx = covariance_yy / determinant;
    conic_xy = -covariance_xy / determinant;
    conic_yy = covariance_xx / determinant;
    return isfinite(radius_x) && isfinite(radius_y) &&
           isfinite(conic_xx) && isfinite(conic_xy) &&
           isfinite(conic_yy);
}

__device__ float gaussian_weight_device(
    const ProjectedAlphaSplat& splat, float delta_x, float delta_y) {
    const float squared_distance = fmaxf(
        0.0F,
        splat.conic_xx * delta_x * delta_x +
            2.0F * splat.conic_xy * delta_x * delta_y +
            splat.conic_yy * delta_y * delta_y);
    return expf(-0.5F * squared_distance);
}

__global__ void project_alpha_splats_kernel(
    const Gaussian* gaussians, std::uint32_t gaussian_count,
    DeviceRasterCamera camera, DeviceProjectedRecord* records,
    std::uint64_t* depth_keys, unsigned long long* visible_splats) {
    const std::uint32_t index =
        blockIdx.x * blockDim.x + threadIdx.x;
    if (index >= gaussian_count) {
        return;
    }

    DeviceProjectedRecord record{};
    std::uint64_t depth_key = std::numeric_limits<std::uint64_t>::max();
    const auto& gaussian = gaussians[index];
    const float camera_x =
        camera.rotation[0] * gaussian.xyz[0] +
        camera.rotation[1] * gaussian.xyz[1] +
        camera.rotation[2] * gaussian.xyz[2] + camera.translation[0];
    const float camera_y =
        camera.rotation[3] * gaussian.xyz[0] +
        camera.rotation[4] * gaussian.xyz[1] +
        camera.rotation[5] * gaussian.xyz[2] + camera.translation[1];
    const float camera_z =
        camera.rotation[6] * gaussian.xyz[0] +
        camera.rotation[7] * gaussian.xyz[1] +
        camera.rotation[8] * gaussian.xyz[2] + camera.translation[2];

    if (camera_z > minimum_depth && isfinite(camera_z)) {
        const float x = camera.fx * camera_x / camera_z + camera.cx;
        const float y = camera.fy * camera_y / camera_z + camera.cy;
        float radius_x = 0.0F;
        float radius_y = 0.0F;
        float conic_xx = 0.0F;
        float conic_xy = 0.0F;
        float conic_yy = 0.0F;
        const bool valid_covariance =
            project_covariance_device(
                gaussian, camera, camera_x, camera_y, camera_z,
                radius_x, radius_y, conic_xx, conic_xy, conic_yy);
        const bool visible =
            isfinite(x) && isfinite(y) && valid_covariance &&
            x + radius_x >= 0.0F && y + radius_y >= 0.0F &&
            x - radius_x < static_cast<float>(camera.width) &&
            y - radius_y < static_cast<float>(camera.height);
        if (visible) {
            const int pixel_support_x =
                static_cast<int>(ceilf(radius_x));
            const int pixel_support_y =
                static_cast<int>(ceilf(radius_y));
            const int center_x = static_cast<int>(floorf(x));
            const int center_y = static_cast<int>(floorf(y));
            const int minimum_x =
                max(0, center_x - pixel_support_x);
            const int maximum_x = min(
                static_cast<int>(camera.width) - 1,
                center_x + pixel_support_x);
            const int minimum_y =
                max(0, center_y - pixel_support_y);
            const int maximum_y = min(
                static_cast<int>(camera.height) - 1,
                center_y + pixel_support_y);
            record.minimum_tile_x =
                static_cast<std::uint32_t>(minimum_x) / alpha_tile_width;
            record.maximum_tile_x =
                static_cast<std::uint32_t>(maximum_x) / alpha_tile_width;
            record.minimum_tile_y =
                static_cast<std::uint32_t>(minimum_y) / alpha_tile_height;
            record.maximum_tile_y =
                static_cast<std::uint32_t>(maximum_y) / alpha_tile_height;
            record.pair_count =
                static_cast<std::uint64_t>(
                    record.maximum_tile_x - record.minimum_tile_x + 1U) *
                (record.maximum_tile_y - record.minimum_tile_y + 1U);
            record.splat = {
                .source_index = static_cast<std::size_t>(index),
                .depth = camera_z,
                .x = x,
                .y = y,
                .radius_x = radius_x,
                .radius_y = radius_y,
                .conic_xx = conic_xx,
                .conic_xy = conic_xy,
                .conic_yy = conic_yy,
                .opacity =
                    1.0F / (1.0F + expf(-gaussian.opacity_logit)),
                .color = {
                    fminf(
                        1.0F,
                        fmaxf(0.0F, 0.5F + sh_c0 * gaussian.dc[0])),
                    fminf(
                        1.0F,
                        fmaxf(0.0F, 0.5F + sh_c0 * gaussian.dc[1])),
                    fminf(
                        1.0F,
                        fmaxf(0.0F, 0.5F + sh_c0 * gaussian.dc[2])),
                },
            };
            depth_key =
                (static_cast<std::uint64_t>(__float_as_uint(camera_z))
                 << 32U) |
                index;
            atomicAdd(visible_splats, 1ULL);
        }
    }
    records[index] = record;
    depth_keys[index] = depth_key;
}

__global__ void extract_pair_counts_kernel(
    const DeviceProjectedRecord* records, std::uint32_t record_count,
    std::uint64_t* counts) {
    const std::uint32_t index =
        blockIdx.x * blockDim.x + threadIdx.x;
    if (index < record_count) {
        counts[index] = records[index].pair_count;
    } else if (index == record_count) {
        counts[index] = 0U;
    }
}

__global__ void duplicate_tile_pairs_kernel(
    const DeviceProjectedRecord* records, std::uint32_t record_count,
    const std::uint64_t* pair_offsets, std::uint32_t tiles_x,
    std::uint64_t* tile_depth_keys, std::uint32_t* record_indices) {
    const std::uint32_t record_index =
        blockIdx.x * blockDim.x + threadIdx.x;
    if (record_index >= record_count) {
        return;
    }
    const auto& record = records[record_index];
    std::uint64_t pair_index = pair_offsets[record_index];
    const std::uint64_t depth_bits =
        static_cast<std::uint64_t>(__float_as_uint(record.splat.depth));
    for (std::uint32_t tile_y = record.minimum_tile_y;
         tile_y <= record.maximum_tile_y && record.pair_count != 0U;
         ++tile_y) {
        for (std::uint32_t tile_x = record.minimum_tile_x;
             tile_x <= record.maximum_tile_x; ++tile_x) {
            const std::uint32_t tile = tile_y * tiles_x + tile_x;
            tile_depth_keys[pair_index] =
                (static_cast<std::uint64_t>(tile) << 32U) | depth_bits;
            record_indices[pair_index] = record_index;
            ++pair_index;
        }
    }
}

__global__ void build_tile_ranges_kernel(
    const std::uint64_t* tile_depth_keys, std::uint32_t pair_count,
    std::uint64_t* tile_starts, std::uint64_t* tile_ends) {
    const std::uint32_t index =
        blockIdx.x * blockDim.x + threadIdx.x;
    if (index >= pair_count) {
        return;
    }
    const std::uint32_t tile =
        static_cast<std::uint32_t>(tile_depth_keys[index] >> 32U);
    if (index == 0U ||
        static_cast<std::uint32_t>(tile_depth_keys[index - 1U] >> 32U) !=
            tile) {
        tile_starts[tile] = index;
    }
    if (index + 1U == pair_count ||
        static_cast<std::uint32_t>(tile_depth_keys[index + 1U] >> 32U) !=
            tile) {
        tile_ends[tile] = static_cast<std::uint64_t>(index) + 1U;
    }
}

__global__ void render_alpha_tiles_kernel(
    const DeviceProjectedRecord* records,
    const std::uint32_t* record_indices,
    const std::uint64_t* tile_starts, const std::uint64_t* tile_ends,
    std::uint32_t width, std::uint32_t height,
    float background_r, float background_g, float background_b,
    float* rgb, float* transmittance, DeviceRenderStats* stats) {
    constexpr std::uint32_t threads_per_tile =
        alpha_tile_width * alpha_tile_height;
    __shared__ ProjectedAlphaSplat batch[threads_per_tile];

    const std::uint32_t thread_index =
        threadIdx.y * blockDim.x + threadIdx.x;
    const std::uint32_t x =
        blockIdx.x * alpha_tile_width + threadIdx.x;
    const std::uint32_t y =
        blockIdx.y * alpha_tile_height + threadIdx.y;
    const bool valid_pixel = x < width && y < height;
    const std::uint32_t tiles_x =
        width / alpha_tile_width +
        (width % alpha_tile_width != 0U ? 1U : 0U);
    const std::size_t tile =
        static_cast<std::size_t>(blockIdx.y) * tiles_x + blockIdx.x;
    const std::uint64_t start = tile_starts[tile];
    const std::uint64_t end = tile_ends[tile];

    float red = 0.0F;
    float green = 0.0F;
    float blue = 0.0F;
    float remaining = 1.0F;
    unsigned long long evaluated = 0U;
    unsigned long long contributing = 0U;
    for (std::uint64_t base = start; base < end;
         base += threads_per_tile) {
        const std::uint64_t load_index = base + thread_index;
        if (load_index < end) {
            batch[thread_index] =
                records[record_indices[load_index]].splat;
        }
        __syncthreads();
        const auto batch_count = static_cast<std::uint32_t>(
            min(
                static_cast<std::uint64_t>(threads_per_tile),
                end - base));
        if (valid_pixel && remaining > alpha_minimum_transmittance) {
            for (std::uint32_t index = 0; index < batch_count; ++index) {
                const auto& splat = batch[index];
                const int support_x =
                    static_cast<int>(ceilf(splat.radius_x));
                const int support_y =
                    static_cast<int>(ceilf(splat.radius_y));
                const int center_x = static_cast<int>(floorf(splat.x));
                const int center_y = static_cast<int>(floorf(splat.y));
                if (static_cast<int>(x) < center_x - support_x ||
                    static_cast<int>(x) > center_x + support_x ||
                    static_cast<int>(y) < center_y - support_y ||
                    static_cast<int>(y) > center_y + support_y) {
                    continue;
                }
                ++evaluated;
                const float delta_x =
                    (static_cast<float>(x) + 0.5F) - splat.x;
                const float delta_y =
                    (static_cast<float>(y) + 0.5F) - splat.y;
                const float gaussian_weight =
                    gaussian_weight_device(splat, delta_x, delta_y);
                const float alpha = fminf(
                    alpha_maximum, splat.opacity * gaussian_weight);
                if (alpha < alpha_minimum_contribution) {
                    continue;
                }
                ++contributing;
                const float weight = remaining * alpha;
                red += weight * splat.color[0];
                green += weight * splat.color[1];
                blue += weight * splat.color[2];
                remaining *= 1.0F - alpha;
                if (remaining <= alpha_minimum_transmittance) {
                    break;
                }
            }
        }
        __syncthreads();
    }
    if (!valid_pixel) {
        return;
    }
    const auto pixel =
        static_cast<std::size_t>(y) * width + x;
    rgb[pixel * 3U] = red + remaining * background_r;
    rgb[pixel * 3U + 1U] = green + remaining * background_g;
    rgb[pixel * 3U + 2U] = blue + remaining * background_b;
    transmittance[pixel] = remaining;
    if (stats != nullptr) {
        atomicAdd(&stats->evaluated_pairs, evaluated);
        atomicAdd(&stats->contributing_pairs, contributing);
    }
}

__global__ void backward_alpha_tiles_kernel(
    const DeviceProjectedRecord* records,
    const std::uint32_t* record_indices,
    const std::uint64_t* tile_starts, const std::uint64_t* tile_ends,
    std::uint32_t width, std::uint32_t height,
    float background_r, float background_g, float background_b,
    const float* image_gradient, float* dc_gradient,
    float* opacity_logit_gradient,
    float* projected_geometry_gradient,
    const float* densification_error_map,
    float* frame_refinement_weight,
    float* frame_visibility_weight) {
    const std::uint32_t x =
        blockIdx.x * alpha_tile_width + threadIdx.x;
    const std::uint32_t y =
        blockIdx.y * alpha_tile_height + threadIdx.y;
    if (x >= width || y >= height) {
        return;
    }
    const std::uint32_t tiles_x =
        width / alpha_tile_width +
        (width % alpha_tile_width != 0U ? 1U : 0U);
    const std::size_t tile =
        static_cast<std::size_t>(blockIdx.y) * tiles_x + blockIdx.x;
    const std::uint64_t start = tile_starts[tile];
    const std::uint64_t end = tile_ends[tile];

    float remaining = 1.0F;
    std::uint64_t active_end = end;
    for (std::uint64_t pair = start; pair < end; ++pair) {
        if (remaining <= alpha_minimum_transmittance) {
            active_end = pair;
            break;
        }
        const auto& splat = records[record_indices[pair]].splat;
        const int support_x =
            static_cast<int>(ceilf(splat.radius_x));
        const int support_y =
            static_cast<int>(ceilf(splat.radius_y));
        const int center_x = static_cast<int>(floorf(splat.x));
        const int center_y = static_cast<int>(floorf(splat.y));
        if (static_cast<int>(x) < center_x - support_x ||
            static_cast<int>(x) > center_x + support_x ||
            static_cast<int>(y) < center_y - support_y ||
            static_cast<int>(y) > center_y + support_y) {
            continue;
        }
        const float delta_x =
            (static_cast<float>(x) + 0.5F) - splat.x;
        const float delta_y =
            (static_cast<float>(y) + 0.5F) - splat.y;
        const float gaussian_weight =
            gaussian_weight_device(splat, delta_x, delta_y);
        const float alpha = fminf(
            alpha_maximum, splat.opacity * gaussian_weight);
        if (alpha < alpha_minimum_contribution) {
            continue;
        }
        remaining *= 1.0F - alpha;
        if (remaining <= alpha_minimum_transmittance) {
            active_end = pair + 1U;
            break;
        }
    }

    const auto pixel =
        static_cast<std::size_t>(y) * width + x;
    const float upstream[3]{
        image_gradient[pixel * 3U],
        image_gradient[pixel * 3U + 1U],
        image_gradient[pixel * 3U + 2U],
    };
    float tail[3]{background_r, background_g, background_b};
    float transmittance_after = remaining;
    for (std::uint64_t cursor = active_end; cursor > start; --cursor) {
        const auto& splat =
            records[record_indices[cursor - 1U]].splat;
        const int support_x =
            static_cast<int>(ceilf(splat.radius_x));
        const int support_y =
            static_cast<int>(ceilf(splat.radius_y));
        const int center_x = static_cast<int>(floorf(splat.x));
        const int center_y = static_cast<int>(floorf(splat.y));
        if (static_cast<int>(x) < center_x - support_x ||
            static_cast<int>(x) > center_x + support_x ||
            static_cast<int>(y) < center_y - support_y ||
            static_cast<int>(y) > center_y + support_y) {
            continue;
        }
        const float delta_x =
            (static_cast<float>(x) + 0.5F) - splat.x;
        const float delta_y =
            (static_cast<float>(y) + 0.5F) - splat.y;
        const float gaussian_weight =
            gaussian_weight_device(splat, delta_x, delta_y);
        const float raw_alpha = splat.opacity * gaussian_weight;
        const float alpha = fminf(alpha_maximum, raw_alpha);
        if (alpha < alpha_minimum_contribution) {
            continue;
        }
        const float transmittance_before =
            transmittance_after / (1.0F - alpha);
        if (densification_error_map != nullptr &&
            frame_refinement_weight != nullptr &&
            frame_visibility_weight != nullptr) {
            const float blending_weight =
                transmittance_before * alpha;
            atomicAdd(
                &frame_visibility_weight[splat.source_index],
                blending_weight);
            atomicAdd(
                &frame_refinement_weight[splat.source_index],
                blending_weight *
                    densification_error_map[pixel]);
        }
        float alpha_gradient = 0.0F;
        const auto source = splat.source_index;
        for (std::size_t channel = 0U; channel < 3U; ++channel) {
            if (splat.color[channel] > 0.0F &&
                splat.color[channel] < 1.0F) {
                atomicAdd(
                    &dc_gradient[source * 3U + channel],
                    sh_c0 * upstream[channel] *
                        transmittance_before * alpha);
            }
            alpha_gradient +=
                upstream[channel] * transmittance_before *
                (splat.color[channel] - tail[channel]);
        }
        if (raw_alpha < alpha_maximum) {
            atomicAdd(
                &opacity_logit_gradient[source],
                alpha_gradient * gaussian_weight * splat.opacity *
                    (1.0F - splat.opacity));
            if (projected_geometry_gradient != nullptr) {
                const float squared_distance_gradient =
                    -0.5F * alpha_gradient * splat.opacity *
                    gaussian_weight;
                atomicAdd(
                    &projected_geometry_gradient[source * 5U],
                    squared_distance_gradient *
                        -2.0F *
                        (splat.conic_xx * delta_x +
                         splat.conic_xy * delta_y));
                atomicAdd(
                    &projected_geometry_gradient[source * 5U + 1U],
                    squared_distance_gradient *
                        -2.0F *
                        (splat.conic_xy * delta_x +
                         splat.conic_yy * delta_y));
                atomicAdd(
                    &projected_geometry_gradient[source * 5U + 2U],
                    squared_distance_gradient * delta_x * delta_x);
                atomicAdd(
                    &projected_geometry_gradient[source * 5U + 3U],
                    squared_distance_gradient *
                        2.0F * delta_x * delta_y);
                atomicAdd(
                    &projected_geometry_gradient[source * 5U + 4U],
                    squared_distance_gradient * delta_y * delta_y);
            }
        }
        for (std::size_t channel = 0U; channel < 3U; ++channel) {
            tail[channel] =
                alpha * splat.color[channel] +
                (1.0F - alpha) * tail[channel];
        }
        transmittance_after = transmittance_before;
    }
}

__global__ void backward_projected_geometry_kernel(
    const Gaussian* gaussians, std::uint32_t gaussian_count,
    DeviceRasterCamera camera,
    const float* projected_geometry_gradient,
    float* xyz_gradient, float* log_scale_gradient,
    float* rotation_gradient) {
    const std::uint32_t index =
        blockIdx.x * blockDim.x + threadIdx.x;
    if (index >= gaussian_count) {
        return;
    }
    const float* projected_gradient =
        projected_geometry_gradient + index * 5U;
    const float screen_x_gradient = projected_gradient[0];
    const float screen_y_gradient = projected_gradient[1];
    const float conic_xx_gradient = projected_gradient[2];
    const float conic_xy_gradient = projected_gradient[3];
    const float conic_yy_gradient = projected_gradient[4];
    if (screen_x_gradient == 0.0F &&
        screen_y_gradient == 0.0F &&
        conic_xx_gradient == 0.0F &&
        conic_xy_gradient == 0.0F &&
        conic_yy_gradient == 0.0F) {
        return;
    }

    const auto& gaussian = gaussians[index];
    const float camera_x =
        camera.rotation[0] * gaussian.xyz[0] +
        camera.rotation[1] * gaussian.xyz[1] +
        camera.rotation[2] * gaussian.xyz[2] +
        camera.translation[0];
    const float camera_y =
        camera.rotation[3] * gaussian.xyz[0] +
        camera.rotation[4] * gaussian.xyz[1] +
        camera.rotation[5] * gaussian.xyz[2] +
        camera.translation[1];
    const float camera_z =
        camera.rotation[6] * gaussian.xyz[0] +
        camera.rotation[7] * gaussian.xyz[1] +
        camera.rotation[8] * gaussian.xyz[2] +
        camera.translation[2];
    if (camera_z <= minimum_depth || !isfinite(camera_z)) {
        return;
    }

    const float quaternion_norm = sqrtf(
        gaussian.rotation[0] * gaussian.rotation[0] +
        gaussian.rotation[1] * gaussian.rotation[1] +
        gaussian.rotation[2] * gaussian.rotation[2] +
        gaussian.rotation[3] * gaussian.rotation[3]);
    if (!isfinite(quaternion_norm) ||
        quaternion_norm <= 1.0e-12F) {
        return;
    }
    const float quaternion[4]{
        gaussian.rotation[0] / quaternion_norm,
        gaussian.rotation[1] / quaternion_norm,
        gaussian.rotation[2] / quaternion_norm,
        gaussian.rotation[3] / quaternion_norm,
    };
    const float w = quaternion[0];
    const float x = quaternion[1];
    const float y = quaternion[2];
    const float z = quaternion[3];
    const float gaussian_rotation[9]{
        1.0F - 2.0F * (y * y + z * z),
        2.0F * (x * y - z * w),
        2.0F * (x * z + y * w),
        2.0F * (x * y + z * w),
        1.0F - 2.0F * (x * x + z * z),
        2.0F * (y * z - x * w),
        2.0F * (x * z - y * w),
        2.0F * (y * z + x * w),
        1.0F - 2.0F * (x * x + y * y),
    };
    float camera_gaussian_rotation[9]{};
    for (std::uint32_t row = 0U; row < 3U; ++row) {
        for (std::uint32_t column = 0U; column < 3U; ++column) {
            for (std::uint32_t inner = 0U; inner < 3U; ++inner) {
                camera_gaussian_rotation[row * 3U + column] +=
                    camera.rotation[row * 3U + inner] *
                    gaussian_rotation[inner * 3U + column];
            }
        }
    }
    float scale[3]{};
    float covariance_factor[9]{};
    for (std::uint32_t column = 0U; column < 3U; ++column) {
        scale[column] = expf(gaussian.log_scale[column]);
        if (!isfinite(scale[column]) || scale[column] <= 0.0F) {
            return;
        }
        for (std::uint32_t row = 0U; row < 3U; ++row) {
            covariance_factor[row * 3U + column] =
                camera_gaussian_rotation[row * 3U + column] *
                scale[column];
        }
    }

    const float inverse_depth = 1.0F / camera_z;
    const float inverse_depth_squared =
        inverse_depth * inverse_depth;
    const float inverse_depth_cubed =
        inverse_depth_squared * inverse_depth;
    const float jacobian[6]{
        camera.fx * inverse_depth,
        0.0F,
        -camera.fx * camera_x * inverse_depth_squared,
        0.0F,
        camera.fy * inverse_depth,
        -camera.fy * camera_y * inverse_depth_squared,
    };
    float projected_factor[6]{};
    for (std::uint32_t row = 0U; row < 2U; ++row) {
        for (std::uint32_t column = 0U; column < 3U; ++column) {
            for (std::uint32_t inner = 0U; inner < 3U; ++inner) {
                projected_factor[row * 3U + column] +=
                    jacobian[row * 3U + inner] *
                    covariance_factor[inner * 3U + column];
            }
        }
    }
    float covariance_xx = 0.0F;
    float covariance_xy = 0.0F;
    float covariance_yy = 0.0F;
    for (std::uint32_t column = 0U; column < 3U; ++column) {
        covariance_xx +=
            projected_factor[column] * projected_factor[column];
        covariance_xy +=
            projected_factor[column] *
            projected_factor[3U + column];
        covariance_yy +=
            projected_factor[3U + column] *
            projected_factor[3U + column];
    }
    const float trace = covariance_xx + covariance_yy;
    const float difference = covariance_xx - covariance_yy;
    const float spectral_gap = sqrtf(fmaxf(
        0.0F,
        difference * difference +
            4.0F * covariance_xy * covariance_xy));
    const float eigenvalue_maximum =
        0.5F * (trace + spectral_gap);
    const float eigenvalue_minimum =
        0.5F * (trace - spectral_gap);
    const float clamped_maximum = fminf(
        maximum_projected_variance,
        fmaxf(minimum_projected_variance, eigenvalue_maximum));
    const float clamped_minimum = fminf(
        maximum_projected_variance,
        fmaxf(minimum_projected_variance, eigenvalue_minimum));
    float clamped_xx = 0.0F;
    float clamped_xy = 0.0F;
    float clamped_yy = 0.0F;
    if (spectral_gap > 1.0e-8F) {
        const float projector_xx =
            (covariance_xx - eigenvalue_minimum) / spectral_gap;
        const float projector_xy =
            covariance_xy / spectral_gap;
        const float projector_yy =
            (covariance_yy - eigenvalue_minimum) / spectral_gap;
        const float clamped_gap =
            clamped_maximum - clamped_minimum;
        clamped_xx =
            clamped_minimum + clamped_gap * projector_xx;
        clamped_xy = clamped_gap * projector_xy;
        clamped_yy =
            clamped_minimum + clamped_gap * projector_yy;
    } else {
        const float variance = fminf(
            maximum_projected_variance,
            fmaxf(minimum_projected_variance, 0.5F * trace));
        clamped_xx = variance;
        clamped_xy = 0.0F;
        clamped_yy = variance;
    }
    const float determinant =
        clamped_xx * clamped_yy -
        clamped_xy * clamped_xy;
    if (!isfinite(determinant) || determinant <= 1.0e-12F) {
        return;
    }
    const float conic_xx = clamped_yy / determinant;
    const float conic_xy = -clamped_xy / determinant;
    const float conic_yy = clamped_xx / determinant;

    const float conic_gradient[4]{
        conic_xx_gradient,
        0.5F * conic_xy_gradient,
        0.5F * conic_xy_gradient,
        conic_yy_gradient,
    };
    const float conic[4]{
        conic_xx, conic_xy,
        conic_xy, conic_yy,
    };
    float conic_times_gradient[4]{};
    for (std::uint32_t row = 0U; row < 2U; ++row) {
        for (std::uint32_t column = 0U; column < 2U; ++column) {
            for (std::uint32_t inner = 0U; inner < 2U; ++inner) {
                conic_times_gradient[row * 2U + column] +=
                    conic[row * 2U + inner] *
                    conic_gradient[inner * 2U + column];
            }
        }
    }
    float clamped_covariance_gradient[4]{};
    for (std::uint32_t row = 0U; row < 2U; ++row) {
        for (std::uint32_t column = 0U; column < 2U; ++column) {
            for (std::uint32_t inner = 0U; inner < 2U; ++inner) {
                clamped_covariance_gradient[row * 2U + column] -=
                    conic_times_gradient[row * 2U + inner] *
                    conic[inner * 2U + column];
            }
        }
    }
    const float symmetric_clamped_xy_gradient =
        0.5F *
        (clamped_covariance_gradient[1] +
         clamped_covariance_gradient[2]);
    clamped_covariance_gradient[1] =
        symmetric_clamped_xy_gradient;
    clamped_covariance_gradient[2] =
        symmetric_clamped_xy_gradient;

    float covariance_gradient[4]{};
    if (spectral_gap > 1.0e-8F) {
        const float angle =
            0.5F * atan2f(
                2.0F * covariance_xy,
                covariance_xx - covariance_yy);
        const float cosine = cosf(angle);
        const float sine = sinf(angle);
        const float g00 = clamped_covariance_gradient[0];
        const float g01 = clamped_covariance_gradient[1];
        const float g11 = clamped_covariance_gradient[3];
        const float eigen_gradient_00 =
            cosine * cosine * g00 +
            2.0F * cosine * sine * g01 +
            sine * sine * g11;
        const float eigen_gradient_11 =
            sine * sine * g00 -
            2.0F * cosine * sine * g01 +
            cosine * cosine * g11;
        const float eigen_gradient_01 =
            -cosine * sine * g00 +
            (cosine * cosine - sine * sine) * g01 +
            sine * cosine * g11;
        const float maximum_derivative =
            eigenvalue_maximum > minimum_projected_variance &&
                    eigenvalue_maximum < maximum_projected_variance
                ? 1.0F
                : 0.0F;
        const float minimum_derivative =
            eigenvalue_minimum > minimum_projected_variance &&
                    eigenvalue_minimum < maximum_projected_variance
                ? 1.0F
                : 0.0F;
        const float cross_derivative =
            (clamped_maximum - clamped_minimum) / spectral_gap;
        const float transformed_00 =
            maximum_derivative * eigen_gradient_00;
        const float transformed_11 =
            minimum_derivative * eigen_gradient_11;
        const float transformed_01 =
            cross_derivative * eigen_gradient_01;
        covariance_gradient[0] =
            cosine * cosine * transformed_00 -
            2.0F * cosine * sine * transformed_01 +
            sine * sine * transformed_11;
        covariance_gradient[1] =
            cosine * sine * transformed_00 +
            (cosine * cosine - sine * sine) * transformed_01 -
            sine * cosine * transformed_11;
        covariance_gradient[2] = covariance_gradient[1];
        covariance_gradient[3] =
            sine * sine * transformed_00 +
            2.0F * sine * cosine * transformed_01 +
            cosine * cosine * transformed_11;
    } else {
        const float variance = 0.5F * trace;
        const float derivative =
            variance > minimum_projected_variance &&
                    variance < maximum_projected_variance
                ? 1.0F
                : 0.0F;
        for (std::uint32_t component = 0U;
             component < 4U; ++component) {
            covariance_gradient[component] =
                derivative * clamped_covariance_gradient[component];
        }
    }

    const float covariance_xx_gradient = covariance_gradient[0];
    const float covariance_xy_gradient =
        covariance_gradient[1] + covariance_gradient[2];
    const float covariance_yy_gradient = covariance_gradient[3];
    float projected_factor_gradient[6]{};
    for (std::uint32_t column = 0U; column < 3U; ++column) {
        projected_factor_gradient[column] =
            2.0F * covariance_xx_gradient *
                projected_factor[column] +
            covariance_xy_gradient *
                projected_factor[3U + column];
        projected_factor_gradient[3U + column] =
            covariance_xy_gradient *
                projected_factor[column] +
            2.0F * covariance_yy_gradient *
                projected_factor[3U + column];
    }
    float jacobian_gradient[6]{};
    float covariance_factor_gradient[9]{};
    for (std::uint32_t row = 0U; row < 2U; ++row) {
        for (std::uint32_t column = 0U; column < 3U; ++column) {
            for (std::uint32_t inner = 0U; inner < 3U; ++inner) {
                jacobian_gradient[row * 3U + inner] +=
                    projected_factor_gradient[row * 3U + column] *
                    covariance_factor[inner * 3U + column];
                covariance_factor_gradient[inner * 3U + column] +=
                    jacobian[row * 3U + inner] *
                    projected_factor_gradient[row * 3U + column];
            }
        }
    }

    float camera_position_gradient[3]{
        screen_x_gradient * camera.fx * inverse_depth,
        screen_y_gradient * camera.fy * inverse_depth,
        screen_x_gradient *
                (-camera.fx * camera_x *
                 inverse_depth_squared) +
            screen_y_gradient *
                (-camera.fy * camera_y *
                 inverse_depth_squared),
    };
    camera_position_gradient[0] +=
        jacobian_gradient[2] *
        (-camera.fx * inverse_depth_squared);
    camera_position_gradient[1] +=
        jacobian_gradient[5] *
        (-camera.fy * inverse_depth_squared);
    camera_position_gradient[2] +=
        jacobian_gradient[0] *
            (-camera.fx * inverse_depth_squared) +
        jacobian_gradient[2] *
            (2.0F * camera.fx * camera_x *
             inverse_depth_cubed) +
        jacobian_gradient[4] *
            (-camera.fy * inverse_depth_squared) +
        jacobian_gradient[5] *
            (2.0F * camera.fy * camera_y *
             inverse_depth_cubed);

    for (std::uint32_t axis = 0U; axis < 3U; ++axis) {
        xyz_gradient[index * 3U + axis] =
            camera.rotation[axis] * camera_position_gradient[0] +
            camera.rotation[3U + axis] *
                camera_position_gradient[1] +
            camera.rotation[6U + axis] *
                camera_position_gradient[2];
    }

    float camera_gaussian_rotation_gradient[9]{};
    for (std::uint32_t column = 0U; column < 3U; ++column) {
        float scale_gradient = 0.0F;
        for (std::uint32_t row = 0U; row < 3U; ++row) {
            const auto offset = row * 3U + column;
            camera_gaussian_rotation_gradient[offset] =
                covariance_factor_gradient[offset] * scale[column];
            scale_gradient +=
                covariance_factor_gradient[offset] *
                camera_gaussian_rotation[offset];
        }
        log_scale_gradient[index * 3U + column] =
            scale_gradient * scale[column];
    }
    float gaussian_rotation_gradient[9]{};
    for (std::uint32_t row = 0U; row < 3U; ++row) {
        for (std::uint32_t column = 0U; column < 3U; ++column) {
            for (std::uint32_t camera_axis = 0U;
                 camera_axis < 3U; ++camera_axis) {
                gaussian_rotation_gradient[row * 3U + column] +=
                    camera.rotation[camera_axis * 3U + row] *
                    camera_gaussian_rotation_gradient[
                        camera_axis * 3U + column];
            }
        }
    }

    float normalized_quaternion_gradient[4]{};
    const float* rotation_matrix_gradient =
        gaussian_rotation_gradient;
    normalized_quaternion_gradient[2] +=
        -4.0F * y * rotation_matrix_gradient[0];
    normalized_quaternion_gradient[3] +=
        -4.0F * z * rotation_matrix_gradient[0];
    normalized_quaternion_gradient[1] +=
        2.0F * y * rotation_matrix_gradient[1];
    normalized_quaternion_gradient[2] +=
        2.0F * x * rotation_matrix_gradient[1];
    normalized_quaternion_gradient[3] +=
        -2.0F * w * rotation_matrix_gradient[1];
    normalized_quaternion_gradient[0] +=
        -2.0F * z * rotation_matrix_gradient[1];
    normalized_quaternion_gradient[1] +=
        2.0F * z * rotation_matrix_gradient[2];
    normalized_quaternion_gradient[3] +=
        2.0F * x * rotation_matrix_gradient[2];
    normalized_quaternion_gradient[2] +=
        2.0F * w * rotation_matrix_gradient[2];
    normalized_quaternion_gradient[0] +=
        2.0F * y * rotation_matrix_gradient[2];
    normalized_quaternion_gradient[1] +=
        2.0F * y * rotation_matrix_gradient[3];
    normalized_quaternion_gradient[2] +=
        2.0F * x * rotation_matrix_gradient[3];
    normalized_quaternion_gradient[3] +=
        2.0F * w * rotation_matrix_gradient[3];
    normalized_quaternion_gradient[0] +=
        2.0F * z * rotation_matrix_gradient[3];
    normalized_quaternion_gradient[1] +=
        -4.0F * x * rotation_matrix_gradient[4];
    normalized_quaternion_gradient[3] +=
        -4.0F * z * rotation_matrix_gradient[4];
    normalized_quaternion_gradient[2] +=
        2.0F * z * rotation_matrix_gradient[5];
    normalized_quaternion_gradient[3] +=
        2.0F * y * rotation_matrix_gradient[5];
    normalized_quaternion_gradient[1] +=
        -2.0F * w * rotation_matrix_gradient[5];
    normalized_quaternion_gradient[0] +=
        -2.0F * x * rotation_matrix_gradient[5];
    normalized_quaternion_gradient[1] +=
        2.0F * z * rotation_matrix_gradient[6];
    normalized_quaternion_gradient[3] +=
        2.0F * x * rotation_matrix_gradient[6];
    normalized_quaternion_gradient[2] +=
        -2.0F * w * rotation_matrix_gradient[6];
    normalized_quaternion_gradient[0] +=
        -2.0F * y * rotation_matrix_gradient[6];
    normalized_quaternion_gradient[2] +=
        2.0F * z * rotation_matrix_gradient[7];
    normalized_quaternion_gradient[3] +=
        2.0F * y * rotation_matrix_gradient[7];
    normalized_quaternion_gradient[1] +=
        2.0F * w * rotation_matrix_gradient[7];
    normalized_quaternion_gradient[0] +=
        2.0F * x * rotation_matrix_gradient[7];
    normalized_quaternion_gradient[1] +=
        -4.0F * x * rotation_matrix_gradient[8];
    normalized_quaternion_gradient[2] +=
        -4.0F * y * rotation_matrix_gradient[8];

    float parallel_component = 0.0F;
    for (std::uint32_t component = 0U; component < 4U; ++component) {
        parallel_component +=
            normalized_quaternion_gradient[component] *
            quaternion[component];
    }
    for (std::uint32_t component = 0U; component < 4U; ++component) {
        rotation_gradient[index * 4U + component] =
            (normalized_quaternion_gradient[component] -
             parallel_component * quaternion[component]) /
            quaternion_norm;
    }
}

__global__ void ordered_l1_loss_kernel(
    const float* prediction, const float* transmittance,
    const std::uint8_t* target, float* loss_sum,
    unsigned int* active_pixels, std::size_t pixel_count) {
    const std::size_t pixel =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (pixel >= pixel_count ||
        transmittance[pixel] >= 1.0F) {
        return;
    }
    float pixel_loss = 0.0F;
    for (std::size_t channel = 0U; channel < 3U; ++channel) {
        const auto offset = pixel * 3U + channel;
        const float target_value =
            static_cast<float>(target[offset]) / 255.0F;
        pixel_loss += fabsf(prediction[offset] - target_value);
    }
    atomicAdd(loss_sum, pixel_loss);
    atomicAdd(active_pixels, 1U);
}

__global__ void squared_error_values_kernel(
    const float* prediction, const std::uint8_t* target,
    float* values, std::size_t sample_count) {
    const std::size_t sample =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (sample >= sample_count) {
        return;
    }
    const float target_value =
        static_cast<float>(target[sample]) / 255.0F;
    const float difference = prediction[sample] - target_value;
    values[sample] = difference * difference;
}

__global__ void horizontal_ssim_moments_kernel(
    const float* prediction, const std::uint8_t* target,
    float* moments, std::uint32_t width, std::uint32_t height) {
    const std::size_t sample =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    const std::size_t sample_count =
        static_cast<std::size_t>(width) * height * 3U;
    if (sample >= sample_count) {
        return;
    }
    const std::size_t pixel = sample / 3U;
    const auto channel = static_cast<std::uint32_t>(sample % 3U);
    const auto x = static_cast<std::uint32_t>(pixel % width);
    if (x < ssim_window_radius ||
        x + ssim_window_radius >= width) {
        return;
    }
    float prediction_mean = 0.0F;
    float target_mean = 0.0F;
    float prediction_square = 0.0F;
    float target_square = 0.0F;
    float cross = 0.0F;
    for (int offset = -static_cast<int>(ssim_window_radius);
         offset <= static_cast<int>(ssim_window_radius); ++offset) {
        const auto source_x = static_cast<std::uint32_t>(
            static_cast<int>(x) + offset);
        const auto source_sample =
            (pixel - x + source_x) * 3U + channel;
        const float prediction_value = prediction[source_sample];
        const float target_value =
            static_cast<float>(target[source_sample]) / 255.0F;
        const float weight =
            ssim_gaussian_weights[offset +
                                  static_cast<int>(ssim_window_radius)];
        prediction_mean += weight * prediction_value;
        target_mean += weight * target_value;
        prediction_square +=
            weight * prediction_value * prediction_value;
        target_square += weight * target_value * target_value;
        cross += weight * prediction_value * target_value;
    }
    const auto output = sample * 5U;
    moments[output] = prediction_mean;
    moments[output + 1U] = target_mean;
    moments[output + 2U] = prediction_square;
    moments[output + 3U] = target_square;
    moments[output + 4U] = cross;
}

__global__ void ssim_values_kernel(
    const float* horizontal_moments, float* values,
    float* backward_terms,
    std::uint32_t width, std::uint32_t height) {
    const auto valid_width = width - 2U * ssim_window_radius;
    const auto valid_height = height - 2U * ssim_window_radius;
    const std::size_t valid_sample_count =
        static_cast<std::size_t>(valid_width) * valid_height * 3U;
    const std::size_t output =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (output >= valid_sample_count) {
        return;
    }
    const auto channel = static_cast<std::uint32_t>(output % 3U);
    const std::size_t valid_pixel = output / 3U;
    const auto x = static_cast<std::uint32_t>(
        valid_pixel % valid_width) + ssim_window_radius;
    const auto y = static_cast<std::uint32_t>(
        valid_pixel / valid_width) + ssim_window_radius;
    float prediction_mean = 0.0F;
    float target_mean = 0.0F;
    float prediction_square = 0.0F;
    float target_square = 0.0F;
    float cross = 0.0F;
    for (int offset = -static_cast<int>(ssim_window_radius);
         offset <= static_cast<int>(ssim_window_radius); ++offset) {
        const auto source_y = static_cast<std::uint32_t>(
            static_cast<int>(y) + offset);
        const auto sample =
            (static_cast<std::size_t>(source_y) * width + x) * 3U +
            channel;
        const auto input = sample * 5U;
        const float weight =
            ssim_gaussian_weights[offset +
                                  static_cast<int>(ssim_window_radius)];
        prediction_mean += weight * horizontal_moments[input];
        target_mean += weight * horizontal_moments[input + 1U];
        prediction_square += weight * horizontal_moments[input + 2U];
        target_square += weight * horizontal_moments[input + 3U];
        cross += weight * horizontal_moments[input + 4U];
    }
    const float raw_prediction_variance =
        prediction_square - prediction_mean * prediction_mean;
    const float prediction_variance =
        fmaxf(0.0F, raw_prediction_variance);
    const float target_variance =
        fmaxf(0.0F, target_square - target_mean * target_mean);
    const float covariance =
        cross - prediction_mean * target_mean;
    constexpr float c1 = 0.01F * 0.01F;
    constexpr float c2 = 0.03F * 0.03F;
    const float numerator =
        (2.0F * prediction_mean * target_mean + c1) *
        (2.0F * covariance + c2);
    const float denominator =
        (prediction_mean * prediction_mean +
         target_mean * target_mean + c1) *
        (prediction_variance + target_variance + c2);
    const float ssim = numerator / denominator;
    values[output] = ssim;
    if (backward_terms != nullptr) {
        const float luminance_denominator =
            prediction_mean * prediction_mean +
            target_mean * target_mean + c1;
        const float contrast_denominator =
            prediction_variance + target_variance + c2;
        const float luminance_numerator =
            2.0F * prediction_mean * target_mean + c1;
        const float contrast_numerator =
            2.0F * covariance + c2;
        const auto center_sample =
            (static_cast<std::size_t>(y) * width + x) * 3U +
            channel;
        const auto term = center_sample * 5U;
        backward_terms[term] = prediction_mean;
        backward_terms[term + 1U] = target_mean;
        backward_terms[term + 2U] =
            2.0F * target_mean * contrast_numerator /
                (luminance_denominator * contrast_denominator) -
            ssim * 2.0F * prediction_mean /
                luminance_denominator;
        backward_terms[term + 3U] =
            2.0F * luminance_numerator /
            (luminance_denominator * contrast_denominator);
        backward_terms[term + 4U] =
            raw_prediction_variance > 0.0F
                ? -ssim * 2.0F / contrast_denominator
                : 0.0F;
    }
}

__global__ void ordered_objective_gradient_kernel(
    const float* prediction, const float* transmittance,
    const std::uint8_t* target, const unsigned int* active_pixels,
    const float* ssim_backward_terms, float* image_gradient,
    std::uint32_t width, std::uint32_t height) {
    const std::size_t pixel_count =
        static_cast<std::size_t>(width) * height;
    const std::size_t pixel =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (pixel >= pixel_count) {
        return;
    }
    const unsigned int active = *active_pixels;
    const bool contributes =
        active != 0U && transmittance[pixel] < 1.0F;
    const float l1_normalizer =
        contributes
            ? l1_objective_weight /
                  (3.0F * static_cast<float>(active))
            : 0.0F;
    const auto x = static_cast<std::uint32_t>(pixel % width);
    const auto y = static_cast<std::uint32_t>(pixel / width);
    const auto minimum_center_x = max(
        static_cast<int>(ssim_window_radius),
        static_cast<int>(x) -
            static_cast<int>(ssim_window_radius));
    const auto maximum_center_x = min(
        static_cast<int>(width - ssim_window_radius - 1U),
        static_cast<int>(x) +
            static_cast<int>(ssim_window_radius));
    const auto minimum_center_y = max(
        static_cast<int>(ssim_window_radius),
        static_cast<int>(y) -
            static_cast<int>(ssim_window_radius));
    const auto maximum_center_y = min(
        static_cast<int>(height - ssim_window_radius - 1U),
        static_cast<int>(y) +
            static_cast<int>(ssim_window_radius));
    const std::size_t valid_sample_count =
        static_cast<std::size_t>(
            width - 2U * ssim_window_radius) *
        (height - 2U * ssim_window_radius) * 3U;
    const float dssim_normalizer =
        -dssim_objective_weight /
        static_cast<float>(valid_sample_count);
    for (std::size_t channel = 0U; channel < 3U; ++channel) {
        const auto offset = pixel * 3U + channel;
        const float target_value =
            static_cast<float>(target[offset]) / 255.0F;
        const float difference = prediction[offset] - target_value;
        const float l1_gradient =
            difference > 0.0F
                ? l1_normalizer
                : (difference < 0.0F ? -l1_normalizer : 0.0F);
        float ssim_gradient = 0.0F;
        for (int center_y = minimum_center_y;
             center_y <= maximum_center_y; ++center_y) {
            const float weight_y = ssim_gaussian_weights[
                center_y - static_cast<int>(y) +
                static_cast<int>(ssim_window_radius)];
            for (int center_x = minimum_center_x;
                 center_x <= maximum_center_x; ++center_x) {
                const float weight_x = ssim_gaussian_weights[
                    center_x - static_cast<int>(x) +
                    static_cast<int>(ssim_window_radius)];
                const auto center_sample =
                    (static_cast<std::size_t>(center_y) * width +
                     static_cast<std::size_t>(center_x)) *
                        3U +
                    channel;
                const auto term = center_sample * 5U;
                const float prediction_mean =
                    ssim_backward_terms[term];
                const float target_mean =
                    ssim_backward_terms[term + 1U];
                ssim_gradient += weight_x * weight_y *
                    (ssim_backward_terms[term + 2U] +
                     ssim_backward_terms[term + 3U] *
                         (target_value - target_mean) +
                     ssim_backward_terms[term + 4U] *
                         (prediction[offset] - prediction_mean));
            }
        }
        image_gradient[offset] =
            l1_gradient + dssim_normalizer * ssim_gradient;
    }
}

__global__ void ssim_error_map_kernel(
    const float* ssim_values, float* error_map,
    std::uint32_t width, std::uint32_t height,
    float mean_error) {
    const std::size_t pixel =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    const std::size_t pixel_count =
        static_cast<std::size_t>(width) * height;
    if (pixel >= pixel_count) {
        return;
    }
    const auto x = static_cast<std::uint32_t>(pixel % width);
    const auto y = static_cast<std::uint32_t>(pixel / width);
    if (x < ssim_window_radius ||
        x + ssim_window_radius >= width ||
        y < ssim_window_radius ||
        y + ssim_window_radius >= height) {
        error_map[pixel] = 0.0F;
        return;
    }
    const auto valid_width =
        width - 2U * ssim_window_radius;
    const auto valid_pixel =
        static_cast<std::size_t>(y - ssim_window_radius) *
            valid_width +
        (x - ssim_window_radius);
    const float mean_ssim =
        (ssim_values[valid_pixel * 3U] +
         ssim_values[valid_pixel * 3U + 1U] +
         ssim_values[valid_pixel * 3U + 2U]) /
        3.0F;
    error_map[pixel] =
        fmaxf(0.0F, 1.0F - mean_ssim) /
        fmaxf(mean_error, 1.0e-6F);
}

__global__ void collect_refinement_statistics_kernel(
    const float* frame_refinement_weight,
    const float* frame_visibility_weight,
    float* refine_weight_max, float* visibility_count,
    std::size_t gaussian_count) {
    const std::size_t index =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (index >= gaussian_count) {
        return;
    }
    const float weight = frame_refinement_weight[index];
    const float visible = frame_visibility_weight[index];
    if (visible > 0.0F && isfinite(weight)) {
        refine_weight_max[index] =
            fmaxf(refine_weight_max[index], weight);
        visibility_count[index] += visible;
    }
}

__global__ void split_gaussians_long_axis_kernel(
    Gaussian* gaussians, const std::uint32_t* parent_indices,
    std::size_t parent_count, std::size_t child_start,
    float* first_dc, float* second_dc,
    float* first_opacity, float* second_opacity,
    float* first_xyz, float* second_xyz,
    float* first_log_scale, float* second_log_scale,
    float* first_rotation, float* second_rotation,
    float* refine_weight_max, float* visibility_count) {
    const std::size_t split =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (split >= parent_count) {
        return;
    }
    const std::size_t parent_index = parent_indices[split];
    const std::size_t child_index = child_start + split;
    Gaussian parent = gaussians[parent_index];
    Gaussian child = parent;

    float quaternion_norm_squared = 0.0F;
    for (std::size_t component = 0U; component < 4U; ++component) {
        quaternion_norm_squared +=
            parent.rotation[component] * parent.rotation[component];
    }
    const float inverse_quaternion_norm =
        quaternion_norm_squared > 1.0e-12F
            ? rsqrtf(quaternion_norm_squared)
            : 1.0F;
    const float w = parent.rotation[0] * inverse_quaternion_norm;
    const float x = parent.rotation[1] * inverse_quaternion_norm;
    const float y = parent.rotation[2] * inverse_quaternion_norm;
    const float z = parent.rotation[3] * inverse_quaternion_norm;
    const float rotation[9]{
        1.0F - 2.0F * (y * y + z * z),
        2.0F * (x * y - z * w),
        2.0F * (x * z + y * w),
        2.0F * (x * y + z * w),
        1.0F - 2.0F * (x * x + z * z),
        2.0F * (y * z - x * w),
        2.0F * (x * z - y * w),
        2.0F * (y * z + x * w),
        1.0F - 2.0F * (x * x + y * y),
    };
    std::size_t longest_axis = 0U;
    if (parent.log_scale[1] > parent.log_scale[longest_axis]) {
        longest_axis = 1U;
    }
    if (parent.log_scale[2] > parent.log_scale[longest_axis]) {
        longest_axis = 2U;
    }
    const float offset_magnitude =
        0.5F * expf(parent.log_scale[longest_axis]);
    for (std::size_t axis = 0U; axis < 3U; ++axis) {
        const float offset =
            rotation[axis * 3U + longest_axis] * offset_magnitude;
        parent.xyz[axis] += offset;
        child.xyz[axis] -= offset;
        const float shrink =
            axis == longest_axis ? logf(0.5F) : logf(0.85F);
        parent.log_scale[axis] += shrink;
        child.log_scale[axis] += shrink;
    }
    const float opacity =
        1.0F / (1.0F + expf(-parent.opacity_logit));
    const float split_opacity =
        fminf(1.0F - 1.0e-6F, fmaxf(1.0e-6F, opacity * 0.6F));
    const float split_logit =
        logf(split_opacity / (1.0F - split_opacity));
    parent.opacity_logit = split_logit;
    child.opacity_logit = split_logit;
    gaussians[parent_index] = parent;
    gaussians[child_index] = child;

    for (std::size_t channel = 0U; channel < 3U; ++channel) {
        first_dc[parent_index * 3U + channel] = 0.0F;
        second_dc[parent_index * 3U + channel] = 0.0F;
        first_dc[child_index * 3U + channel] = 0.0F;
        second_dc[child_index * 3U + channel] = 0.0F;
        first_xyz[parent_index * 3U + channel] = 0.0F;
        second_xyz[parent_index * 3U + channel] = 0.0F;
        first_xyz[child_index * 3U + channel] = 0.0F;
        second_xyz[child_index * 3U + channel] = 0.0F;
        first_log_scale[parent_index * 3U + channel] = 0.0F;
        second_log_scale[parent_index * 3U + channel] = 0.0F;
        first_log_scale[child_index * 3U + channel] = 0.0F;
        second_log_scale[child_index * 3U + channel] = 0.0F;
    }
    first_opacity[parent_index] = 0.0F;
    second_opacity[parent_index] = 0.0F;
    first_opacity[child_index] = 0.0F;
    second_opacity[child_index] = 0.0F;
    for (std::size_t component = 0U; component < 4U; ++component) {
        first_rotation[parent_index * 4U + component] = 0.0F;
        second_rotation[parent_index * 4U + component] = 0.0F;
        first_rotation[child_index * 4U + component] = 0.0F;
        second_rotation[child_index * 4U + component] = 0.0F;
    }
    refine_weight_max[parent_index] = 0.0F;
    visibility_count[parent_index] = 0.0F;
    refine_weight_max[child_index] = 0.0F;
    visibility_count[child_index] = 0.0F;
}

__global__ void ordered_adam_update_kernel(
    Gaussian* gaussians, std::size_t gaussian_count,
    const float* dc_gradient, const float* opacity_gradient,
    const float* xyz_gradient, const float* log_scale_gradient,
    const float* rotation_gradient,
    float* first_dc, float* second_dc,
    float* first_opacity, float* second_opacity,
    float* first_xyz, float* second_xyz,
    float* first_log_scale, float* second_log_scale,
    float* first_rotation, float* second_rotation,
    float inverse_bias_first, float inverse_bias_second,
    float position_learning_rate, float minimum_log_scale,
    float maximum_log_scale) {
    const std::size_t gaussian_index =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (gaussian_index >= gaussian_count) {
        return;
    }
    constexpr float beta_first = 0.9F;
    constexpr float beta_second = 0.999F;
    constexpr float color_learning_rate = 0.05F;
    constexpr float opacity_learning_rate = 0.01F;
    constexpr float scale_learning_rate = 0.005F;
    constexpr float rotation_learning_rate = 0.001F;
    constexpr float epsilon = 1.0e-8F;
    for (std::size_t channel = 0U; channel < 3U; ++channel) {
        const auto offset = gaussian_index * 3U + channel;
        const float gradient = dc_gradient[offset];
        first_dc[offset] =
            beta_first * first_dc[offset] +
            (1.0F - beta_first) * gradient;
        second_dc[offset] =
            beta_second * second_dc[offset] +
            (1.0F - beta_second) * gradient * gradient;
        const float corrected_first =
            first_dc[offset] * inverse_bias_first;
        const float corrected_second =
            second_dc[offset] * inverse_bias_second;
        gaussians[gaussian_index].dc[channel] -=
            color_learning_rate * corrected_first /
            (sqrtf(corrected_second) + epsilon);
    }
    const float opacity = opacity_gradient[gaussian_index];
    first_opacity[gaussian_index] =
        beta_first * first_opacity[gaussian_index] +
        (1.0F - beta_first) * opacity;
    second_opacity[gaussian_index] =
        beta_second * second_opacity[gaussian_index] +
        (1.0F - beta_second) * opacity * opacity;
    const float corrected_first =
        first_opacity[gaussian_index] * inverse_bias_first;
    const float corrected_second =
        second_opacity[gaussian_index] * inverse_bias_second;
    gaussians[gaussian_index].opacity_logit -=
        opacity_learning_rate * corrected_first /
        (sqrtf(corrected_second) + epsilon);

    for (std::size_t axis = 0U; axis < 3U; ++axis) {
        const auto offset = gaussian_index * 3U + axis;
        const float position_gradient =
            isfinite(xyz_gradient[offset])
                ? xyz_gradient[offset]
                : 0.0F;
        first_xyz[offset] =
            beta_first * first_xyz[offset] +
            (1.0F - beta_first) * position_gradient;
        second_xyz[offset] =
            beta_second * second_xyz[offset] +
            (1.0F - beta_second) *
                position_gradient * position_gradient;
        const float position_update =
            position_learning_rate *
            first_xyz[offset] * inverse_bias_first /
            (sqrtf(second_xyz[offset] * inverse_bias_second) +
             epsilon);
        const float candidate_position =
            gaussians[gaussian_index].xyz[axis] - position_update;
        if (isfinite(candidate_position)) {
            gaussians[gaussian_index].xyz[axis] =
                candidate_position;
        }

        const float scale_gradient =
            isfinite(log_scale_gradient[offset])
                ? log_scale_gradient[offset]
                : 0.0F;
        first_log_scale[offset] =
            beta_first * first_log_scale[offset] +
            (1.0F - beta_first) * scale_gradient;
        second_log_scale[offset] =
            beta_second * second_log_scale[offset] +
            (1.0F - beta_second) *
                scale_gradient * scale_gradient;
        const float scale_update =
            scale_learning_rate *
            first_log_scale[offset] * inverse_bias_first /
            (sqrtf(
                 second_log_scale[offset] *
                 inverse_bias_second) +
             epsilon);
        const float candidate_scale =
            gaussians[gaussian_index].log_scale[axis] -
            scale_update;
        if (isfinite(candidate_scale)) {
            gaussians[gaussian_index].log_scale[axis] =
                fminf(
                    maximum_log_scale,
                    fmaxf(minimum_log_scale, candidate_scale));
        }
    }

    float quaternion_norm_squared = 0.0F;
    for (std::size_t component = 0U; component < 4U; ++component) {
        const auto offset = gaussian_index * 4U + component;
        const float gradient =
            isfinite(rotation_gradient[offset])
                ? rotation_gradient[offset]
                : 0.0F;
        first_rotation[offset] =
            beta_first * first_rotation[offset] +
            (1.0F - beta_first) * gradient;
        second_rotation[offset] =
            beta_second * second_rotation[offset] +
            (1.0F - beta_second) * gradient * gradient;
        const float update =
            rotation_learning_rate *
            first_rotation[offset] * inverse_bias_first /
            (sqrtf(
                 second_rotation[offset] *
                 inverse_bias_second) +
             epsilon);
        const float candidate =
            gaussians[gaussian_index].rotation[component] - update;
        if (isfinite(candidate)) {
            gaussians[gaussian_index].rotation[component] = candidate;
        }
        quaternion_norm_squared +=
            gaussians[gaussian_index].rotation[component] *
            gaussians[gaussian_index].rotation[component];
    }
    if (isfinite(quaternion_norm_squared) &&
        quaternion_norm_squared > 1.0e-12F) {
        const float inverse_norm = rsqrtf(quaternion_norm_squared);
        for (std::size_t component = 0U; component < 4U; ++component) {
            gaussians[gaussian_index].rotation[component] *=
                inverse_norm;
        }
    } else {
        gaussians[gaussian_index].rotation[0] = 1.0F;
        gaussians[gaussian_index].rotation[1] = 0.0F;
        gaussians[gaussian_index].rotation[2] = 0.0F;
        gaussians[gaussian_index].rotation[3] = 0.0F;
    }
}

void sort_projected_records(
    std::uint64_t* keys_in, std::uint64_t* keys_out,
    DeviceProjectedRecord* records_in, DeviceProjectedRecord* records_out,
    int item_count) {
    std::size_t temporary_bytes = 0U;
    require_cuda(
        cub::DeviceRadixSort::SortPairs(
            nullptr, temporary_bytes, keys_in, keys_out,
            records_in, records_out, item_count),
        "query projected splat sort storage");
    DeviceAllocation<std::uint8_t> temporary(temporary_bytes);
    require_cuda(
        cub::DeviceRadixSort::SortPairs(
            temporary.data(), temporary_bytes, keys_in, keys_out,
            records_in, records_out, item_count),
        "sort projected splats by depth");
}

void scan_pair_counts(
    const std::uint64_t* counts, std::uint64_t* offsets, int item_count) {
    std::size_t temporary_bytes = 0U;
    require_cuda(
        cub::DeviceScan::ExclusiveSum(
            nullptr, temporary_bytes, counts, offsets, item_count),
        "query tile pair scan storage");
    DeviceAllocation<std::uint8_t> temporary(temporary_bytes);
    require_cuda(
        cub::DeviceScan::ExclusiveSum(
            temporary.data(), temporary_bytes, counts, offsets, item_count),
        "scan tile pair counts");
}

void sort_tile_pairs(
    std::uint64_t* keys_in, std::uint64_t* keys_out,
    std::uint32_t* indices_in, std::uint32_t* indices_out,
    int item_count) {
    std::size_t temporary_bytes = 0U;
    require_cuda(
        cub::DeviceRadixSort::SortPairs(
            nullptr, temporary_bytes, keys_in, keys_out,
            indices_in, indices_out, item_count),
        "query tile pair sort storage");
    DeviceAllocation<std::uint8_t> temporary(temporary_bytes);
    require_cuda(
        cub::DeviceRadixSort::SortPairs(
            temporary.data(), temporary_bytes, keys_in, keys_out,
            indices_in, indices_out, item_count),
        "sort tile/splat pairs");
}

}  // namespace

static AlphaRenderBackwardOutput render_alpha_cuda_impl(
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera,
    const std::array<float, 3>& background,
    const std::vector<float>* image_gradient) {
    validate_inputs(gaussians, camera, background);
    const auto pixel_count =
        static_cast<std::size_t>(camera.width) * camera.height;
    if (image_gradient != nullptr &&
        image_gradient->size() != pixel_count * 3U) {
        throw std::invalid_argument(
            "tiled alpha backward image gradient shape is invalid");
    }
    const auto empty_result = [&]() {
        AlphaRenderGradients gradients{};
        if (image_gradient != nullptr) {
            gradients.dc =
                std::vector<std::array<float, 3>>(gaussians.size());
            gradients.opacity_logit =
                std::vector<float>(gaussians.size(), 0.0F);
            gradients.xyz =
                std::vector<std::array<float, 3>>(gaussians.size());
            gradients.log_scale =
                std::vector<std::array<float, 3>>(gaussians.size());
            gradients.rotation =
                std::vector<std::array<float, 4>>(gaussians.size());
        }
        return AlphaRenderBackwardOutput{
            .render =
                render_alpha_reference({}, camera, background),
            .gradients = std::move(gradients),
        };
    };
    if (gaussians.empty()) {
        return empty_result();
    }

    const auto gaussian_count =
        static_cast<std::uint32_t>(gaussians.size());
    const auto device_camera = make_device_camera(camera);
    const auto tile_count =
        static_cast<std::size_t>(device_camera.tiles_x) *
        device_camera.tiles_y;
    DeviceAllocation<Gaussian> device_gaussians(gaussians.size());
    DeviceAllocation<DeviceProjectedRecord> device_records(gaussians.size());
    DeviceAllocation<DeviceProjectedRecord> device_sorted_records(
        gaussians.size());
    DeviceAllocation<std::uint64_t> device_depth_keys(gaussians.size());
    DeviceAllocation<std::uint64_t> device_sorted_depth_keys(
        gaussians.size());
    DeviceAllocation<unsigned long long> device_visible_splats(1U);
    device_gaussians.copy_from_host(gaussians.data());
    device_visible_splats.zero();

    const std::uint32_t projection_blocks =
        (gaussian_count + threads_per_block - 1U) / threads_per_block;
    project_alpha_splats_kernel<<<projection_blocks, threads_per_block>>>(
        device_gaussians.data(), gaussian_count, device_camera,
        device_records.data(), device_depth_keys.data(),
        device_visible_splats.data());
    require_cuda(cudaGetLastError(), "launch alpha projection");

    sort_projected_records(
        device_depth_keys.data(), device_sorted_depth_keys.data(),
        device_records.data(), device_sorted_records.data(),
        static_cast<int>(gaussian_count));

    DeviceAllocation<std::uint64_t> device_pair_counts(
        gaussians.size() + 1U);
    DeviceAllocation<std::uint64_t> device_pair_offsets(
        gaussians.size() + 1U);
    const std::uint32_t count_blocks =
        (gaussian_count + 1U + threads_per_block - 1U) /
        threads_per_block;
    extract_pair_counts_kernel<<<count_blocks, threads_per_block>>>(
        device_sorted_records.data(), gaussian_count,
        device_pair_counts.data());
    require_cuda(cudaGetLastError(), "launch tile pair count extraction");
    scan_pair_counts(
        device_pair_counts.data(), device_pair_offsets.data(),
        static_cast<int>(gaussian_count + 1U));

    std::uint64_t pair_count = 0U;
    require_cuda(
        cudaMemcpy(
            &pair_count, device_pair_offsets.data() + gaussian_count,
            sizeof(pair_count), cudaMemcpyDeviceToHost),
        "copy tile pair count to host");
    if (pair_count == 0U) {
        return empty_result();
    }
    if (pair_count >
        static_cast<std::uint64_t>(std::numeric_limits<int>::max())) {
        throw std::runtime_error(
            "tiled alpha tile/splat pair count exceeds CUB item limit");
    }
    const auto pair_items = static_cast<std::uint32_t>(pair_count);
    DeviceAllocation<std::uint64_t> device_tile_depth_keys(pair_items);
    DeviceAllocation<std::uint64_t> device_sorted_tile_depth_keys(pair_items);
    DeviceAllocation<std::uint32_t> device_record_indices(pair_items);
    DeviceAllocation<std::uint32_t> device_sorted_record_indices(pair_items);
    duplicate_tile_pairs_kernel<<<projection_blocks, threads_per_block>>>(
        device_sorted_records.data(), gaussian_count,
        device_pair_offsets.data(), device_camera.tiles_x,
        device_tile_depth_keys.data(), device_record_indices.data());
    require_cuda(cudaGetLastError(), "launch tile pair duplication");
    sort_tile_pairs(
        device_tile_depth_keys.data(),
        device_sorted_tile_depth_keys.data(),
        device_record_indices.data(), device_sorted_record_indices.data(),
        static_cast<int>(pair_items));

    DeviceAllocation<std::uint64_t> device_tile_starts(tile_count);
    DeviceAllocation<std::uint64_t> device_tile_ends(tile_count);
    device_tile_starts.zero();
    device_tile_ends.zero();
    const std::uint32_t pair_blocks =
        (pair_items + threads_per_block - 1U) / threads_per_block;
    build_tile_ranges_kernel<<<pair_blocks, threads_per_block>>>(
        device_sorted_tile_depth_keys.data(), pair_items,
        device_tile_starts.data(), device_tile_ends.data());
    require_cuda(cudaGetLastError(), "launch tile range construction");

    DeviceAllocation<float> device_rgb(pixel_count * 3U);
    DeviceAllocation<float> device_transmittance(pixel_count);
    DeviceAllocation<DeviceRenderStats> device_stats(1U);
    device_stats.zero();
    const dim3 render_threads(alpha_tile_width, alpha_tile_height);
    const dim3 render_blocks(
        device_camera.tiles_x, device_camera.tiles_y);
    render_alpha_tiles_kernel<<<render_blocks, render_threads>>>(
        device_sorted_records.data(),
        device_sorted_record_indices.data(),
        device_tile_starts.data(), device_tile_ends.data(),
        camera.width, camera.height,
        background[0], background[1], background[2],
        device_rgb.data(), device_transmittance.data(), device_stats.data());
    require_cuda(cudaGetLastError(), "launch tiled alpha renderer");

    std::optional<DeviceAllocation<float>> device_image_gradient;
    std::optional<DeviceAllocation<float>> device_dc_gradient;
    std::optional<DeviceAllocation<float>> device_opacity_logit_gradient;
    std::optional<DeviceAllocation<float>>
        device_projected_geometry_gradient;
    std::optional<DeviceAllocation<float>> device_xyz_gradient;
    std::optional<DeviceAllocation<float>> device_log_scale_gradient;
    std::optional<DeviceAllocation<float>> device_rotation_gradient;
    if (image_gradient != nullptr) {
        device_image_gradient.emplace(image_gradient->size());
        device_dc_gradient.emplace(gaussians.size() * 3U);
        device_opacity_logit_gradient.emplace(gaussians.size());
        device_projected_geometry_gradient.emplace(
            gaussians.size() * 5U);
        device_xyz_gradient.emplace(gaussians.size() * 3U);
        device_log_scale_gradient.emplace(gaussians.size() * 3U);
        device_rotation_gradient.emplace(gaussians.size() * 4U);
        device_image_gradient->copy_from_host(image_gradient->data());
        device_dc_gradient->zero();
        device_opacity_logit_gradient->zero();
        device_projected_geometry_gradient->zero();
        device_xyz_gradient->zero();
        device_log_scale_gradient->zero();
        device_rotation_gradient->zero();
        backward_alpha_tiles_kernel<<<render_blocks, render_threads>>>(
            device_sorted_records.data(),
            device_sorted_record_indices.data(),
            device_tile_starts.data(), device_tile_ends.data(),
            camera.width, camera.height,
            background[0], background[1], background[2],
            device_image_gradient->data(), device_dc_gradient->data(),
            device_opacity_logit_gradient->data(),
            device_projected_geometry_gradient->data(),
            nullptr, nullptr, nullptr);
        require_cuda(cudaGetLastError(), "launch tiled alpha backward");
        backward_projected_geometry_kernel<<<
            projection_blocks, threads_per_block>>>(
            device_gaussians.data(), gaussian_count, device_camera,
            device_projected_geometry_gradient->data(),
            device_xyz_gradient->data(),
            device_log_scale_gradient->data(),
            device_rotation_gradient->data());
        require_cuda(
            cudaGetLastError(),
            "launch tiled alpha geometry backward");
    }
    require_cuda(
        cudaDeviceSynchronize(),
        image_gradient == nullptr
            ? "synchronize tiled alpha renderer"
            : "synchronize tiled alpha forward/backward");

    AlphaRenderOutput output{
        .width = camera.width,
        .height = camera.height,
        .rgb = std::vector<float>(pixel_count * 3U),
        .transmittance = std::vector<float>(pixel_count),
        .stats = {},
    };
    device_rgb.copy_to_host(output.rgb.data());
    device_transmittance.copy_to_host(output.transmittance.data());
    unsigned long long visible_splats = 0U;
    device_visible_splats.copy_to_host(&visible_splats);
    DeviceRenderStats stats{};
    device_stats.copy_to_host(&stats);
    output.stats.visible_splats =
        static_cast<std::size_t>(visible_splats);
    output.stats.evaluated_pairs =
        static_cast<std::uint64_t>(stats.evaluated_pairs);
    output.stats.contributing_pairs =
        static_cast<std::uint64_t>(stats.contributing_pairs);
    AlphaRenderGradients gradients{};
    if (image_gradient != nullptr) {
        gradients.dc =
            std::vector<std::array<float, 3>>(gaussians.size());
        gradients.opacity_logit =
            std::vector<float>(gaussians.size(), 0.0F);
        gradients.xyz =
            std::vector<std::array<float, 3>>(gaussians.size());
        gradients.log_scale =
            std::vector<std::array<float, 3>>(gaussians.size());
        gradients.rotation =
            std::vector<std::array<float, 4>>(gaussians.size());
        device_dc_gradient->copy_to_host(gradients.dc.front().data());
        device_opacity_logit_gradient->copy_to_host(
            gradients.opacity_logit.data());
        device_xyz_gradient->copy_to_host(
            gradients.xyz.front().data());
        device_log_scale_gradient->copy_to_host(
            gradients.log_scale.front().data());
        device_rotation_gradient->copy_to_host(
            gradients.rotation.front().data());
    }
    return {
        .render = std::move(output),
        .gradients = std::move(gradients),
    };
}

AlphaRenderOutput render_alpha_tiled_cuda(
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera,
    const std::array<float, 3>& background) {
    return render_alpha_cuda_impl(
               gaussians, camera, background, nullptr)
        .render;
}

AlphaRenderBackwardOutput render_alpha_tiled_cuda_backward(
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera,
    const std::vector<float>& image_gradient,
    const std::array<float, 3>& background) {
    return render_alpha_cuda_impl(
        gaussians, camera, background, &image_gradient);
}

struct OrderedAlphaTrainingContext::Impl {
    Impl(
        const std::vector<Gaussian>& initial_gaussians,
        std::size_t maximum_pixel_count,
        std::uint64_t requested_maximum_steps,
        std::size_t requested_gaussian_capacity)
        : gaussian_count(initial_gaussians.size()),
          gaussian_capacity(std::max(
              initial_gaussians.size(),
              requested_gaussian_capacity)),
          maximum_pixels(maximum_pixel_count),
          maximum_steps(std::max<std::uint64_t>(
              1U, requested_maximum_steps)) {
        if (gaussian_count == 0U ||
            gaussian_capacity >
                static_cast<std::size_t>(
                    std::numeric_limits<int>::max() - 1)) {
            throw std::invalid_argument(
                "ordered training requires a supported Gaussian count");
        }
        if (maximum_pixels == 0U ||
            maximum_pixels >
                static_cast<std::size_t>(
                    std::numeric_limits<int>::max()) / 3U ||
            maximum_pixels >
                std::numeric_limits<std::size_t>::max() / 15U) {
            throw std::invalid_argument(
                "ordered training requires a valid pixel capacity");
        }
        gaussians.ensure(gaussian_capacity);
        records.ensure(gaussian_capacity);
        sorted_records.ensure(gaussian_capacity);
        depth_keys.ensure(gaussian_capacity);
        sorted_depth_keys.ensure(gaussian_capacity);
        pair_counts.ensure(gaussian_capacity + 1U);
        pair_offsets.ensure(gaussian_capacity + 1U);
        visible_splats.ensure(1U);
        target.ensure(maximum_pixels * 3U);
        rgb.ensure(maximum_pixels * 3U);
        transmittance.ensure(maximum_pixels);
        image_gradient.ensure(maximum_pixels * 3U);
        loss_sum.ensure(1U);
        active_pixels.ensure(1U);
        metric_values.ensure(maximum_pixels * 3U);
        metric_horizontal_moments.ensure(maximum_pixels * 15U);
        ssim_backward_terms.ensure(maximum_pixels * 15U);
        densification_error_map.ensure(maximum_pixels);
        metric_sum.ensure(1U);
        dc_gradient.ensure(gaussian_capacity * 3U);
        opacity_gradient.ensure(gaussian_capacity);
        projected_geometry_gradient.ensure(gaussian_capacity * 5U);
        xyz_gradient.ensure(gaussian_capacity * 3U);
        log_scale_gradient.ensure(gaussian_capacity * 3U);
        rotation_gradient.ensure(gaussian_capacity * 4U);
        first_dc.ensure(gaussian_capacity * 3U);
        second_dc.ensure(gaussian_capacity * 3U);
        first_opacity.ensure(gaussian_capacity);
        second_opacity.ensure(gaussian_capacity);
        first_xyz.ensure(gaussian_capacity * 3U);
        second_xyz.ensure(gaussian_capacity * 3U);
        first_log_scale.ensure(gaussian_capacity * 3U);
        second_log_scale.ensure(gaussian_capacity * 3U);
        first_rotation.ensure(gaussian_capacity * 4U);
        second_rotation.ensure(gaussian_capacity * 4U);
        refine_weight_max.ensure(gaussian_capacity);
        visibility_count.ensure(gaussian_capacity);
        refinement_indices.ensure(gaussian_capacity);
        frame_refinement_weight.ensure(gaussian_capacity);
        frame_visibility_weight.ensure(gaussian_capacity);
        std::array<float, 3> minimum_position{
            std::numeric_limits<float>::max(),
            std::numeric_limits<float>::max(),
            std::numeric_limits<float>::max(),
        };
        std::array<float, 3> maximum_position{
            std::numeric_limits<float>::lowest(),
            std::numeric_limits<float>::lowest(),
            std::numeric_limits<float>::lowest(),
        };
        float initial_minimum_log_scale =
            std::numeric_limits<float>::max();
        float initial_maximum_log_scale =
            std::numeric_limits<float>::lowest();
        for (const auto& gaussian : initial_gaussians) {
            for (std::size_t axis = 0U; axis < 3U; ++axis) {
                if (!std::isfinite(gaussian.xyz[axis]) ||
                    !std::isfinite(gaussian.log_scale[axis])) {
                    throw std::invalid_argument(
                        "ordered training requires finite geometry");
                }
                minimum_position[axis] = std::min(
                    minimum_position[axis], gaussian.xyz[axis]);
                maximum_position[axis] = std::max(
                    maximum_position[axis], gaussian.xyz[axis]);
                initial_minimum_log_scale = std::min(
                    initial_minimum_log_scale,
                    gaussian.log_scale[axis]);
                initial_maximum_log_scale = std::max(
                    initial_maximum_log_scale,
                    gaussian.log_scale[axis]);
            }
        }
        double diagonal_squared = 0.0;
        for (std::size_t axis = 0U; axis < 3U; ++axis) {
            const double extent =
                static_cast<double>(maximum_position[axis]) -
                minimum_position[axis];
            diagonal_squared += extent * extent;
        }
        position_learning_rate_scale = static_cast<float>(
            std::max(std::sqrt(diagonal_squared), 1.0e-6));
        minimum_log_scale = initial_minimum_log_scale - 4.0F;
        maximum_log_scale = initial_maximum_log_scale + 4.0F;
        gaussians.copy_from_host(
            initial_gaussians.data(), gaussian_count);
        first_dc.zero(gaussian_count * 3U);
        second_dc.zero(gaussian_count * 3U);
        first_opacity.zero(gaussian_count);
        second_opacity.zero(gaussian_count);
        first_xyz.zero(gaussian_count * 3U);
        second_xyz.zero(gaussian_count * 3U);
        first_log_scale.zero(gaussian_count * 3U);
        second_log_scale.zero(gaussian_count * 3U);
        first_rotation.zero(gaussian_count * 4U);
        second_rotation.zero(gaussian_count * 4U);
        refine_weight_max.zero(gaussian_capacity);
        visibility_count.zero(gaussian_capacity);
        frame_refinement_weight.zero(gaussian_capacity);
        frame_visibility_weight.zero(gaussian_capacity);
    }

    void sort_records() {
        std::size_t temporary_bytes = 0U;
        require_cuda(
            cub::DeviceRadixSort::SortPairs(
                nullptr, temporary_bytes,
                depth_keys.data(), sorted_depth_keys.data(),
                records.data(), sorted_records.data(),
                static_cast<int>(gaussian_count)),
            "query persistent projected sort storage");
        temporary_storage.ensure(std::max<std::size_t>(
            1U, temporary_bytes));
        require_cuda(
            cub::DeviceRadixSort::SortPairs(
                temporary_storage.data(), temporary_bytes,
                depth_keys.data(), sorted_depth_keys.data(),
                records.data(), sorted_records.data(),
                static_cast<int>(gaussian_count)),
            "sort persistent projected splats");
    }

    void scan_counts() {
        std::size_t temporary_bytes = 0U;
        require_cuda(
            cub::DeviceScan::ExclusiveSum(
                nullptr, temporary_bytes,
                pair_counts.data(), pair_offsets.data(),
                static_cast<int>(gaussian_count + 1U)),
            "query persistent pair scan storage");
        temporary_storage.ensure(std::max<std::size_t>(
            1U, temporary_bytes));
        require_cuda(
            cub::DeviceScan::ExclusiveSum(
                temporary_storage.data(), temporary_bytes,
                pair_counts.data(), pair_offsets.data(),
                static_cast<int>(gaussian_count + 1U)),
            "scan persistent tile pair counts");
    }

    void sort_pairs(std::uint32_t pair_items) {
        std::size_t temporary_bytes = 0U;
        require_cuda(
            cub::DeviceRadixSort::SortPairs(
                nullptr, temporary_bytes,
                tile_depth_keys.data(),
                sorted_tile_depth_keys.data(),
                record_indices.data(),
                sorted_record_indices.data(),
                static_cast<int>(pair_items)),
            "query persistent pair sort storage");
        temporary_storage.ensure(std::max<std::size_t>(
            1U, temporary_bytes));
        require_cuda(
            cub::DeviceRadixSort::SortPairs(
                temporary_storage.data(), temporary_bytes,
                tile_depth_keys.data(),
                sorted_tile_depth_keys.data(),
                record_indices.data(),
                sorted_record_indices.data(),
                static_cast<int>(pair_items)),
            "sort persistent tile pairs");
    }

    float reduce_metric_values(std::size_t value_count) {
        if (value_count == 0U ||
            value_count >
                static_cast<std::size_t>(
                    std::numeric_limits<int>::max())) {
            throw std::invalid_argument(
                "ordered quality metric item count is invalid");
        }
        std::size_t temporary_bytes = 0U;
        require_cuda(
            cub::DeviceReduce::Sum(
                nullptr, temporary_bytes,
                metric_values.data(), metric_sum.data(),
                static_cast<int>(value_count)),
            "query ordered quality reduction storage");
        temporary_storage.ensure(std::max<std::size_t>(
            1U, temporary_bytes));
        require_cuda(
            cub::DeviceReduce::Sum(
                temporary_storage.data(), temporary_bytes,
                metric_values.data(), metric_sum.data(),
                static_cast<int>(value_count)),
            "reduce ordered quality values");
        float result = 0.0F;
        metric_sum.copy_to_host(&result, 1U);
        return result;
    }

    float render_loss(
        const RasterCamera& camera, const std::uint8_t* target_rgb,
        std::size_t target_bytes, bool compute_gradient,
        bool apply_update,
        ImageQualityMetrics* quality,
        std::vector<float>* prediction,
        ImageObjectiveOutput* objective) {
        if (target_rgb == nullptr) {
            throw std::invalid_argument(
                "ordered training target is null");
        }
        const auto pixel_count =
            static_cast<std::size_t>(camera.width) * camera.height;
        if (camera.width == 0U || camera.height == 0U ||
            !std::isfinite(camera.fx) || !std::isfinite(camera.fy) ||
            camera.fx <= 0.0F || camera.fy <= 0.0F ||
            pixel_count > maximum_pixels ||
            target_bytes != pixel_count * 3U ||
            camera.width <= 2U * ssim_window_radius ||
            camera.height <= 2U * ssim_window_radius) {
            throw std::invalid_argument(
                "ordered training frame shape is invalid");
        }
        if (apply_update && !compute_gradient) {
            throw std::logic_error(
                "ordered training update requires an image gradient");
        }
        target.copy_from_host(target_rgb, target_bytes);
        const auto device_camera = make_device_camera(camera);
        const auto gaussian_items =
            static_cast<std::uint32_t>(gaussian_count);
        constexpr std::uint32_t block_size = 256U;
        const auto gaussian_blocks =
            (gaussian_items + block_size - 1U) / block_size;
        visible_splats.zero(1U);
        project_alpha_splats_kernel<<<gaussian_blocks, block_size>>>(
            gaussians.data(), gaussian_items, device_camera,
            records.data(), depth_keys.data(), visible_splats.data());
        require_cuda(
            cudaGetLastError(),
            "launch persistent alpha projection");
        sort_records();

        const auto count_blocks =
            (gaussian_items + 1U + block_size - 1U) / block_size;
        extract_pair_counts_kernel<<<count_blocks, block_size>>>(
            sorted_records.data(), gaussian_items, pair_counts.data());
        require_cuda(
            cudaGetLastError(),
            "launch persistent pair count extraction");
        scan_counts();
        std::uint64_t pair_count = 0U;
        require_cuda(
            cudaMemcpy(
                &pair_count, pair_offsets.data() + gaussian_items,
                sizeof(pair_count), cudaMemcpyDeviceToHost),
            "copy persistent tile pair count");
        if (pair_count == 0U ||
            pair_count >
                static_cast<std::uint64_t>(
                    std::numeric_limits<int>::max())) {
            throw std::runtime_error(
                pair_count == 0U
                    ? "no sparse Gaussian projects into the selected training image"
                    : "ordered training pair count exceeds CUB item limit");
        }
        const auto pair_items =
            static_cast<std::uint32_t>(pair_count);
        tile_depth_keys.ensure(pair_items);
        sorted_tile_depth_keys.ensure(pair_items);
        record_indices.ensure(pair_items);
        sorted_record_indices.ensure(pair_items);
        duplicate_tile_pairs_kernel<<<gaussian_blocks, block_size>>>(
            sorted_records.data(), gaussian_items,
            pair_offsets.data(), device_camera.tiles_x,
            tile_depth_keys.data(), record_indices.data());
        require_cuda(
            cudaGetLastError(),
            "launch persistent tile pair duplication");
        sort_pairs(pair_items);

        const auto tile_count =
            static_cast<std::size_t>(device_camera.tiles_x) *
            device_camera.tiles_y;
        tile_starts.ensure(tile_count);
        tile_ends.ensure(tile_count);
        tile_starts.zero(tile_count);
        tile_ends.zero(tile_count);
        const auto pair_blocks =
            (pair_items + block_size - 1U) / block_size;
        build_tile_ranges_kernel<<<pair_blocks, block_size>>>(
            sorted_tile_depth_keys.data(), pair_items,
            tile_starts.data(), tile_ends.data());
        require_cuda(
            cudaGetLastError(),
            "launch persistent tile range construction");

        loss_sum.zero(1U);
        active_pixels.zero(1U);
        const dim3 render_threads(
            alpha_tile_width, alpha_tile_height);
        const dim3 render_blocks(
            device_camera.tiles_x, device_camera.tiles_y);
        render_alpha_tiles_kernel<<<render_blocks, render_threads>>>(
            sorted_records.data(), sorted_record_indices.data(),
            tile_starts.data(), tile_ends.data(),
            camera.width, camera.height,
            0.0F, 0.0F, 0.0F,
            rgb.data(), transmittance.data(), nullptr);
        require_cuda(
            cudaGetLastError(),
            "launch persistent ordered renderer");
        const auto pixel_blocks = static_cast<std::uint32_t>(
            (pixel_count + block_size - 1U) / block_size);
        ordered_l1_loss_kernel<<<pixel_blocks, block_size>>>(
            rgb.data(), transmittance.data(), target.data(),
            loss_sum.data(), active_pixels.data(), pixel_count);
        require_cuda(
            cudaGetLastError(),
            "launch persistent ordered L1 loss");

        float host_loss_sum = 0.0F;
        unsigned int host_active_pixels = 0U;
        loss_sum.copy_to_host(&host_loss_sum, 1U);
        active_pixels.copy_to_host(&host_active_pixels, 1U);
        if (host_active_pixels == 0U) {
            throw std::runtime_error(
                "ordered training render has no active pixels");
        }
        const float normalizer =
            1.0F /
            (3.0F * static_cast<float>(host_active_pixels));
        const std::size_t sample_count = pixel_count * 3U;
        const auto sample_blocks = static_cast<std::uint32_t>(
            (sample_count + block_size - 1U) / block_size);
        horizontal_ssim_moments_kernel<<<
            sample_blocks, block_size>>>(
            rgb.data(), target.data(),
            metric_horizontal_moments.data(),
            camera.width, camera.height);
        require_cuda(
            cudaGetLastError(),
            "launch ordered horizontal SSIM moments");
        const std::size_t valid_sample_count =
            static_cast<std::size_t>(
                camera.width - 2U * ssim_window_radius) *
            (camera.height - 2U * ssim_window_radius) * 3U;
        const auto valid_blocks = static_cast<std::uint32_t>(
            (valid_sample_count + block_size - 1U) / block_size);
        ssim_values_kernel<<<valid_blocks, block_size>>>(
            metric_horizontal_moments.data(),
            metric_values.data(),
            compute_gradient ? ssim_backward_terms.data() : nullptr,
            camera.width, camera.height);
        require_cuda(
            cudaGetLastError(),
            "launch ordered SSIM objective");
        const float ssim_sum =
            reduce_metric_values(valid_sample_count);
        const float mean_ssim =
            ssim_sum / static_cast<float>(valid_sample_count);
        const float l1_loss = host_loss_sum * normalizer;
        const float objective_loss =
            l1_objective_weight * l1_loss +
            dssim_objective_weight * (1.0F - mean_ssim);
        if (!std::isfinite(objective_loss)) {
            throw std::runtime_error(
                "ordered objective produced a non-finite loss");
        }
        if (compute_gradient) {
            ssim_error_map_kernel<<<pixel_blocks, block_size>>>(
                metric_values.data(),
                densification_error_map.data(),
                camera.width, camera.height,
                1.0F - mean_ssim);
            require_cuda(
                cudaGetLastError(),
                "launch ordered normalized SSIM error map");
        }
        if (quality != nullptr) {
            squared_error_values_kernel<<<sample_blocks, block_size>>>(
                rgb.data(), target.data(), metric_values.data(),
                sample_count);
            require_cuda(
                cudaGetLastError(),
                "launch ordered squared-error metric");
            const float squared_error_sum =
                reduce_metric_values(sample_count);
            const float mean_squared_error = fmaxf(
                squared_error_sum /
                    static_cast<float>(sample_count),
                1.0e-10F);

            quality->psnr =
                10.0F * std::log10(1.0F / mean_squared_error);
            quality->ssim = mean_ssim;
            quality->active_pixel_fraction =
                static_cast<float>(host_active_pixels) /
                static_cast<float>(pixel_count);
            if (!std::isfinite(quality->psnr) ||
                !std::isfinite(quality->ssim) ||
                !std::isfinite(quality->active_pixel_fraction)) {
                throw std::runtime_error(
                    "ordered quality evaluation produced non-finite metrics");
            }
            if (prediction != nullptr) {
                prediction->resize(sample_count);
                rgb.copy_to_host(prediction->data(), sample_count);
            }
        }
        if (compute_gradient) {
            ordered_objective_gradient_kernel<<<
                pixel_blocks, block_size>>>(
                rgb.data(), transmittance.data(), target.data(),
                active_pixels.data(), ssim_backward_terms.data(),
                image_gradient.data(), camera.width, camera.height);
            require_cuda(
                cudaGetLastError(),
                "launch persistent ordered objective gradient");
            dc_gradient.zero(gaussian_count * 3U);
            opacity_gradient.zero(gaussian_count);
            projected_geometry_gradient.zero(gaussian_count * 5U);
            frame_refinement_weight.zero(gaussian_count);
            frame_visibility_weight.zero(gaussian_count);
            xyz_gradient.zero(gaussian_count * 3U);
            log_scale_gradient.zero(gaussian_count * 3U);
            rotation_gradient.zero(gaussian_count * 4U);
            backward_alpha_tiles_kernel<<<render_blocks, render_threads>>>(
                sorted_records.data(), sorted_record_indices.data(),
                tile_starts.data(), tile_ends.data(),
                camera.width, camera.height,
                0.0F, 0.0F, 0.0F,
                image_gradient.data(), dc_gradient.data(),
                opacity_gradient.data(),
                projected_geometry_gradient.data(),
                densification_error_map.data(),
                frame_refinement_weight.data(),
                frame_visibility_weight.data());
            require_cuda(
                cudaGetLastError(),
                "launch persistent ordered backward");
            collect_refinement_statistics_kernel<<<
                gaussian_blocks, block_size>>>(
                frame_refinement_weight.data(),
                frame_visibility_weight.data(),
                refine_weight_max.data(),
                visibility_count.data(),
                gaussian_count);
            require_cuda(
                cudaGetLastError(),
                "launch persistent refinement statistics");
            backward_projected_geometry_kernel<<<
                gaussian_blocks, block_size>>>(
                gaussians.data(), gaussian_items, device_camera,
                projected_geometry_gradient.data(),
                xyz_gradient.data(), log_scale_gradient.data(),
                rotation_gradient.data());
            require_cuda(
                cudaGetLastError(),
                "launch persistent geometry backward");
            if (apply_update) {
                beta_first_power *= 0.9F;
                beta_second_power *= 0.999F;
                ++optimizer_steps;
                const float inverse_bias_first =
                    1.0F / (1.0F - beta_first_power);
                const float inverse_bias_second =
                    1.0F / (1.0F - beta_second_power);
                const float schedule_progress =
                    maximum_steps <= 1U
                        ? 0.0F
                        : fminf(
                              1.0F,
                              static_cast<float>(optimizer_steps - 1U) /
                                  static_cast<float>(
                                      maximum_steps - 1U));
                constexpr float initial_position_rate = 1.6e-4F;
                constexpr float final_position_rate = 1.6e-6F;
                const float position_learning_rate =
                    position_learning_rate_scale *
                    std::exp(
                        std::log(initial_position_rate) *
                            (1.0F - schedule_progress) +
                        std::log(final_position_rate) *
                            schedule_progress);
                ordered_adam_update_kernel<<<
                    gaussian_blocks, block_size>>>(
                    gaussians.data(), gaussian_count,
                    dc_gradient.data(), opacity_gradient.data(),
                    xyz_gradient.data(), log_scale_gradient.data(),
                    rotation_gradient.data(),
                    first_dc.data(), second_dc.data(),
                    first_opacity.data(), second_opacity.data(),
                    first_xyz.data(), second_xyz.data(),
                    first_log_scale.data(), second_log_scale.data(),
                    first_rotation.data(), second_rotation.data(),
                    inverse_bias_first, inverse_bias_second,
                    position_learning_rate, minimum_log_scale,
                    maximum_log_scale);
                require_cuda(
                    cudaGetLastError(),
                    "launch persistent ordered Adam");
            }
        }
        if (objective != nullptr) {
            objective->loss = objective_loss;
            objective->prediction.resize(sample_count);
            objective->gradient.resize(sample_count);
            objective->transmittance.resize(pixel_count);
            rgb.copy_to_host(
                objective->prediction.data(), sample_count);
            image_gradient.copy_to_host(
                objective->gradient.data(), sample_count);
            transmittance.copy_to_host(
                objective->transmittance.data(), pixel_count);
        }
        return objective_loss;
    }

    TopologyRefinementResult refine_topology(
        float gradient_threshold, float grow_fraction) {
        if (!std::isfinite(gradient_threshold) ||
            gradient_threshold < 0.0F ||
            !std::isfinite(grow_fraction) ||
            grow_fraction < 0.0F || grow_fraction > 1.0F) {
            throw std::invalid_argument(
                "ordered topology refinement parameters are invalid");
        }
        std::vector<float> host_weights(gaussian_count);
        std::vector<float> host_visibility(gaussian_count);
        refine_weight_max.copy_to_host(
            host_weights.data(), gaussian_count);
        visibility_count.copy_to_host(
            host_visibility.data(), gaussian_count);
        std::vector<std::pair<float, std::uint32_t>> candidates;
        candidates.reserve(gaussian_count);
        for (std::size_t index = 0U; index < gaussian_count; ++index) {
            if (host_visibility[index] > 0.0F &&
                host_weights[index] > gradient_threshold) {
                candidates.emplace_back(
                    host_weights[index],
                    static_cast<std::uint32_t>(index));
            }
        }
        std::sort(
            candidates.begin(), candidates.end(),
            [](const auto& left, const auto& right) {
                if (left.first != right.first) {
                    return left.first > right.first;
                }
                return left.second < right.second;
            });
        const auto requested = static_cast<std::size_t>(std::llround(
            static_cast<double>(candidates.size()) *
            static_cast<double>(grow_fraction)));
        const auto available = gaussian_capacity - gaussian_count;
        const auto added = std::min(requested, available);
        if (added != 0U) {
            std::vector<std::uint32_t> parent_indices(added);
            for (std::size_t split = 0U; split < added; ++split) {
                parent_indices[split] = candidates[split].second;
            }
            refinement_indices.copy_from_host(
                parent_indices.data(), added);
            constexpr std::uint32_t block_size = 256U;
            const auto blocks = static_cast<std::uint32_t>(
                (added + block_size - 1U) / block_size);
            split_gaussians_long_axis_kernel<<<blocks, block_size>>>(
                gaussians.data(), refinement_indices.data(),
                added, gaussian_count,
                first_dc.data(), second_dc.data(),
                first_opacity.data(), second_opacity.data(),
                first_xyz.data(), second_xyz.data(),
                first_log_scale.data(), second_log_scale.data(),
                first_rotation.data(), second_rotation.data(),
                refine_weight_max.data(), visibility_count.data());
            require_cuda(
                cudaGetLastError(),
                "launch persistent long-axis Gaussian split");
            gaussian_count += added;
        }
        refine_weight_max.zero(gaussian_count);
        visibility_count.zero(gaussian_count);
        return {
            .candidates = candidates.size(),
            .added = added,
            .gaussian_count = gaussian_count,
        };
    }

    std::size_t gaussian_count = 0U;
    std::size_t gaussian_capacity = 0U;
    std::size_t maximum_pixels = 0U;
    std::uint64_t maximum_steps = 1U;
    std::uint64_t optimizer_steps = 0U;
    float position_learning_rate_scale = 1.0F;
    float minimum_log_scale = -16.0F;
    float maximum_log_scale = 16.0F;
    float beta_first_power = 1.0F;
    float beta_second_power = 1.0F;
    ReusableDeviceAllocation<Gaussian> gaussians;
    ReusableDeviceAllocation<DeviceProjectedRecord> records;
    ReusableDeviceAllocation<DeviceProjectedRecord> sorted_records;
    ReusableDeviceAllocation<std::uint64_t> depth_keys;
    ReusableDeviceAllocation<std::uint64_t> sorted_depth_keys;
    ReusableDeviceAllocation<std::uint64_t> pair_counts;
    ReusableDeviceAllocation<std::uint64_t> pair_offsets;
    ReusableDeviceAllocation<unsigned long long> visible_splats;
    ReusableDeviceAllocation<std::uint64_t> tile_depth_keys;
    ReusableDeviceAllocation<std::uint64_t> sorted_tile_depth_keys;
    ReusableDeviceAllocation<std::uint32_t> record_indices;
    ReusableDeviceAllocation<std::uint32_t> sorted_record_indices;
    ReusableDeviceAllocation<std::uint64_t> tile_starts;
    ReusableDeviceAllocation<std::uint64_t> tile_ends;
    ReusableDeviceAllocation<std::uint8_t> temporary_storage;
    ReusableDeviceAllocation<std::uint8_t> target;
    ReusableDeviceAllocation<float> rgb;
    ReusableDeviceAllocation<float> transmittance;
    ReusableDeviceAllocation<float> image_gradient;
    ReusableDeviceAllocation<float> loss_sum;
    ReusableDeviceAllocation<unsigned int> active_pixels;
    ReusableDeviceAllocation<float> metric_values;
    ReusableDeviceAllocation<float> metric_horizontal_moments;
    ReusableDeviceAllocation<float> ssim_backward_terms;
    ReusableDeviceAllocation<float> densification_error_map;
    ReusableDeviceAllocation<float> metric_sum;
    ReusableDeviceAllocation<float> dc_gradient;
    ReusableDeviceAllocation<float> opacity_gradient;
    ReusableDeviceAllocation<float> projected_geometry_gradient;
    ReusableDeviceAllocation<float> xyz_gradient;
    ReusableDeviceAllocation<float> log_scale_gradient;
    ReusableDeviceAllocation<float> rotation_gradient;
    ReusableDeviceAllocation<float> first_dc;
    ReusableDeviceAllocation<float> second_dc;
    ReusableDeviceAllocation<float> first_opacity;
    ReusableDeviceAllocation<float> second_opacity;
    ReusableDeviceAllocation<float> first_xyz;
    ReusableDeviceAllocation<float> second_xyz;
    ReusableDeviceAllocation<float> first_log_scale;
    ReusableDeviceAllocation<float> second_log_scale;
    ReusableDeviceAllocation<float> first_rotation;
    ReusableDeviceAllocation<float> second_rotation;
    ReusableDeviceAllocation<float> refine_weight_max;
    ReusableDeviceAllocation<float> visibility_count;
    ReusableDeviceAllocation<std::uint32_t> refinement_indices;
    ReusableDeviceAllocation<float> frame_refinement_weight;
    ReusableDeviceAllocation<float> frame_visibility_weight;
};

OrderedAlphaTrainingContext::OrderedAlphaTrainingContext(
    const std::vector<Gaussian>& gaussians,
    std::size_t maximum_pixels,
    std::uint64_t maximum_steps,
    std::size_t maximum_gaussians)
    : impl_(std::make_unique<Impl>(
          gaussians, maximum_pixels, maximum_steps,
          maximum_gaussians)) {}

OrderedAlphaTrainingContext::~OrderedAlphaTrainingContext() = default;

OrderedAlphaTrainingContext::OrderedAlphaTrainingContext(
    OrderedAlphaTrainingContext&&) noexcept = default;

OrderedAlphaTrainingContext& OrderedAlphaTrainingContext::operator=(
    OrderedAlphaTrainingContext&&) noexcept = default;

float OrderedAlphaTrainingContext::evaluate(
    const RasterCamera& camera, const std::uint8_t* target_rgb,
    std::size_t target_bytes) {
    return impl_->render_loss(
        camera, target_rgb, target_bytes, false, false,
        nullptr, nullptr, nullptr);
}

ImageQualityMetrics OrderedAlphaTrainingContext::evaluate_quality(
    const RasterCamera& camera, const std::uint8_t* target_rgb,
    std::size_t target_bytes,
    std::vector<float>* prediction) {
    ImageQualityMetrics result;
    static_cast<void>(impl_->render_loss(
        camera, target_rgb, target_bytes, false, false,
        &result, prediction, nullptr));
    return result;
}

ImageObjectiveOutput
OrderedAlphaTrainingContext::evaluate_objective_gradient(
    const RasterCamera& camera, const std::uint8_t* target_rgb,
    std::size_t target_bytes) {
    ImageObjectiveOutput result;
    static_cast<void>(impl_->render_loss(
        camera, target_rgb, target_bytes, true, false,
        nullptr, nullptr, &result));
    return result;
}

float OrderedAlphaTrainingContext::train_step(
    const RasterCamera& camera, const std::uint8_t* target_rgb,
    std::size_t target_bytes) {
    return impl_->render_loss(
        camera, target_rgb, target_bytes, true, true,
        nullptr, nullptr, nullptr);
}

TopologyRefinementResult
OrderedAlphaTrainingContext::refine_topology(
    float gradient_threshold, float grow_fraction) {
    return impl_->refine_topology(
        gradient_threshold, grow_fraction);
}

std::size_t OrderedAlphaTrainingContext::size() const noexcept {
    return impl_->gaussian_count;
}

void OrderedAlphaTrainingContext::download(
    std::vector<Gaussian>& output) const {
    output.resize(impl_->gaussian_count);
    impl_->gaussians.copy_to_host(
        output.data(), impl_->gaussian_count);
}

}  // namespace dronegs
