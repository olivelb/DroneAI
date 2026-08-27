/* SPDX-FileCopyrightText: 2026 DroneAI authors
 * SPDX-FileCopyrightText: 2025 LichtFeld Studio Authors
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * The dev.15 MRNF error-weighted contribution and long-axis split behavior,
 * plus the dev.16 Gumbel selection and edge-guidance behavior and dev.17 MRNF
 * optimizer schedules, are adapted from the pinned LichtFeld implementation.
 * Dev.18 dual-profile selection and update telemetry and dev.19 family-isolated
 * epsilon/rate ablations, the dev.20 DC-plus-opacity combination, and the
 * dev.21 intermediate-DC sweep, dev.24 progressive SH integration, and
 * dev.25 deterministic prune/reuse/noise/decay/compaction lifecycle are
 * DroneAI additions. Dev.27 sorts compact 48-byte render records on native
 * sm_89 while keeping SH bases in a separate source-indexed buffer, avoiding
 * CUB's large-value shared-memory limit without a full-record gather. Dev.28
 * selects CUB's Policy610 radix kernels and caps native sm_89 CUDA compilation
 * at 64 registers to improve Ada occupancy. Dev.29 cooperatively batches
 * forward recomputation and reverse gradient traversal through tile-local
 * shared memory. Dev.30 removes the Ada-only radix and register overrides,
 * leaving architecture selection and stable radix policy to CMake, nvcc, and
 * CUB for portable Turing-through-Blackwell builds. Dev.32 matches FastGS's
 * [0,4] SH color ceiling and corresponding live-gradient interval. Dev.36
 * independently adapts the homodirectional projected-mean gradient statistic
 * described by AbsGS (arXiv:2404.10484). The Apache-2.0 gsplat implementation
 * was inspected at 2b902ff1891fc7f73f0f9b8c8bfc932cef2b198c to validate the
 * per-pixel absolute-value placement; no gsplat source is linked or vendored.
 * Dev.38 adapts the coupled FastGS projected-covariance dilation, extended
 * field-of-view Jacobian clamp, opacity-dependent support, fragment-alpha
 * ceiling, and matching smooth backward behavior from pinned LichtFeld
 * revision 1004c0841a3776e3f67866ff34101fbc9677397f.
 * Dev.42 adapts that revision's FastGS bucket/checkpoint warp traversal and
 * fused L1/SSIM CUDA structure to DroneGS's interleaved renderer and valid
 * padding objective; modified 2026-07-26. DroneGS orchestration remains
 * independent and does not link to or launch LichtFeld at runtime.
 * Dev.52 parallelizes DroneGS's original color/opacity SH Adam coefficient
 * updates without changing the adapted MRNF or FastGS algorithms.
 * Dev.54 excludes constant high tile bits from DroneGS's CUB pair sort.
 * Dev.55 parallelizes DroneGS's scalar Adam parameters by component.
 * Dev.56 reduces fused L1/SSIM loss and active-pixel counts per CUDA tile
 * before the global accumulators, without changing objective equations.
 * Dev.61 rejects only splats whose conservative maximum screen-space support
 * lies outside the image before evaluating SH and exact covariance.
 * Dev.62 aligns scalar Adam on 16-lane Gaussian groups and normalizes each
 * quaternion inside the same kernel, removing a full model-memory pass.
 * Dev.63 specializes SH Adam by active degree and interleaves each parameter's
 * first/second moments for one vector load/store while retaining checkpoint
 * format v5.
 * Dev.64 schedules four independent FastGS backward buckets per CUDA block,
 * raising resident warp occupancy without changing per-bucket traversal.
 * The
 * pre-existing DroneGS
 * rasterizer, loss, gradient, and optimizer code in this file was original MIT
 * code; this combined translation unit is conservatively distributed under
 * GPL-3.0-or-later from dev.15 onward.
 */
#include "dronegs/rasterization.hpp"
#include "dronegs/ordered_training.hpp"

#include <cub/cub.cuh>
#include <cuda_runtime.h>

#include <algorithm>
#include <array>
#include <bit>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

#ifndef _WIN32
#include <fcntl.h>
#include <unistd.h>
#endif

namespace dronegs {
namespace {

// Reads the host clock only when a caller requests diagnostics. No CUDA events
// or extra synchronization: transfers include existing waits, kernels are submit time.
class TopologyPhaseTimer {
public:
    explicit TopologyPhaseTimer(TopologyRefinementTelemetry* telemetry)
        : telemetry_(telemetry),
          mark_(telemetry ? Clock::now() : Clock::time_point{}) {}

    void finish(double TopologyRefinementTelemetry::*phase) {
        if (telemetry_ == nullptr) return;
        const auto now = Clock::now();
        telemetry_->*phase += std::chrono::duration<double>(now - mark_).count();
        mark_ = now;
    }

private:
    using Clock = std::chrono::steady_clock;
    TopologyRefinementTelemetry* telemetry_;
    Clock::time_point mark_;
};

constexpr float sh_c0 = 0.28209479177387814F;
constexpr float sh_c1 = 0.4886025119029199F;
constexpr float minimum_depth = 1.0e-4F;
constexpr float minimum_projected_variance = 0.75F * 0.75F;
constexpr float maximum_projected_variance = 8.0F * 8.0F;
constexpr float gaussian_support = 2.5F;
constexpr float maximum_splat_color = 4.0F;
constexpr float fastgs_dilation = 0.3F;
constexpr float fastgs_maximum_fragment_alpha = 0.999F;
// sqrt(2 * log(1 / alpha_minimum_contribution)), rounded upward.
constexpr float fastgs_maximum_support = 3.33F;
constexpr float conservative_radius_margin = 1.0F;
constexpr std::uint32_t fastgs_checkpoint_interval = 32U;
// One portable NVIDIA warp owns one bucket. Grouping independent warps avoids
// the resident-block limit of the former one-warp launch geometry; no
// architecture-specific instruction or warp-width assumption is added here.
constexpr std::uint32_t fastgs_backward_warps_per_block = 4U;
constexpr float fastgs_checkpoint_color_scale = 255.0F / 4.0F;
constexpr float fastgs_checkpoint_color_inverse_scale = 4.0F / 255.0F;
constexpr float dev37_antialias_filter_variance_005 = 0.05F;
constexpr float dev37_antialias_filter_variance_015 = 0.15F;
constexpr float dev37_antialias_filter_variance_030 = 0.30F;
constexpr std::uint32_t threads_per_block = 256U;
static_assert(
    fastgs_backward_warps_per_block * fastgs_checkpoint_interval <=
    threads_per_block);
constexpr std::size_t scalar_adam_lanes_per_gaussian = 16U;
static_assert(sizeof(float2) == 2U * sizeof(float));
constexpr std::uint32_t ssim_window_radius = 5U;
constexpr float l1_objective_weight = 0.8F;
constexpr float dssim_objective_weight = 0.2F;
constexpr float mrnf_edge_score_weight = 0.25F;
constexpr float mrnf_position_learning_rate_initial = 2.0e-5F;
constexpr float mrnf_position_learning_rate_final = 2.0e-7F;
constexpr float mrnf_dc_learning_rate = 2.0e-3F;
constexpr float mrnf_opacity_learning_rate = 1.2e-2F;
constexpr float mrnf_scale_learning_rate_initial = 7.0e-3F;
constexpr float mrnf_scale_learning_rate_final = 5.0e-3F;
constexpr float mrnf_rotation_learning_rate = 2.0e-3F;
constexpr float mrnf_adam_epsilon = 1.0e-15F;
constexpr float dev16_position_learning_rate_initial = 1.6e-4F;
constexpr float dev16_position_learning_rate_final = 1.6e-6F;
constexpr float dev16_dc_learning_rate = 5.0e-2F;
constexpr float dev16_opacity_learning_rate = 1.0e-2F;
constexpr float dev16_scale_learning_rate = 5.0e-3F;
constexpr float dev16_rotation_learning_rate = 1.0e-3F;
constexpr float dev16_adam_epsilon = 1.0e-8F;

__device__ __forceinline__ std::uint32_t pack_fastgs_checkpoint_color(
    float value) {
    return min(
        255U,
        static_cast<std::uint32_t>(
            fmaxf(0.0F, fminf(maximum_splat_color, value)) *
                fastgs_checkpoint_color_scale +
            0.5F));
}

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

class GpuStageTimer {
public:
    explicit GpuStageTimer(bool enabled) : enabled_(enabled) {
        if (!enabled_) {
            return;
        }
        std::size_t created = 0U;
        for (; created < events_.size(); ++created) {
            const auto status = cudaEventCreate(&events_[created]);
            if (status != cudaSuccess) {
                for (std::size_t index = 0U; index < created; ++index) {
                    static_cast<void>(cudaEventDestroy(events_[index]));
                    events_[index] = nullptr;
                }
                require_cuda(status, "create GPU stage telemetry event");
            }
        }
        require_cuda(
            cudaEventRecord(events_[0]),
            "record GPU stage telemetry start");
    }

    ~GpuStageTimer() {
        for (auto& event : events_) {
            if (event != nullptr) {
                static_cast<void>(cudaEventDestroy(event));
                event = nullptr;
            }
        }
    }

    GpuStageTimer(const GpuStageTimer&) = delete;
    GpuStageTimer& operator=(const GpuStageTimer&) = delete;

    void mark(std::size_t boundary) {
        if (!enabled_) {
            return;
        }
        if (boundary == 0U || boundary >= events_.size()) {
            throw std::logic_error("GPU stage telemetry boundary is invalid");
        }
        require_cuda(
            cudaEventRecord(events_[boundary]),
            "record GPU stage telemetry boundary");
    }

    MrnfGpuStageTelemetry finish(std::uint64_t step) {
        if (!enabled_) {
            throw std::logic_error("GPU stage telemetry is disabled");
        }
        mark(events_.size() - 1U);
        require_cuda(
            cudaEventSynchronize(events_.back()),
            "synchronize GPU stage telemetry");
        const auto elapsed = [this](std::size_t begin, std::size_t end) {
            float milliseconds = 0.0F;
            require_cuda(
                cudaEventElapsedTime(
                    &milliseconds, events_[begin], events_[end]),
                "read GPU stage telemetry");
            return milliseconds;
        };
        const float projection_ms = elapsed(0U, 1U);
        const float record_sort_ms = elapsed(1U, 2U);
        const float binning_ms = elapsed(2U, 3U);
        const float pair_sort_ms = elapsed(3U, 4U);
        const float bucket_ms = elapsed(4U, 5U);
        const float objective_gradient_ms = elapsed(7U, 8U);
        const float gradient_reset_ms = elapsed(8U, 9U);
        const float raster_backward_ms = elapsed(9U, 10U);
        const float geometry_backward_ms = elapsed(10U, 11U);
        const float scalar_optimizer_ms = elapsed(11U, 12U);
        const float sh_optimizer_ms = elapsed(12U, 13U);
        const float optimizer_post_ms = elapsed(13U, 14U);
        return MrnfGpuStageTelemetry{
            .step = step,
            .projection_ms = projection_ms,
            .record_sort_ms = record_sort_ms,
            .binning_ms = binning_ms,
            .pair_sort_ms = pair_sort_ms,
            .bucket_ms = bucket_ms,
            .preprocess_ms = projection_ms + record_sort_ms +
                binning_ms + pair_sort_ms + bucket_ms,
            .raster_ms = elapsed(5U, 6U),
            .objective_ms = elapsed(6U, 7U),
            .objective_gradient_ms = objective_gradient_ms,
            .gradient_reset_ms = gradient_reset_ms,
            .raster_backward_ms = raster_backward_ms,
            .geometry_backward_ms = geometry_backward_ms,
            .backward_ms = objective_gradient_ms + gradient_reset_ms +
                raster_backward_ms + geometry_backward_ms,
            .scalar_optimizer_ms = scalar_optimizer_ms,
            .sh_optimizer_ms = sh_optimizer_ms,
            .optimizer_post_ms = optimizer_post_ms,
            .optimizer_ms = scalar_optimizer_ms + sh_optimizer_ms +
                optimizer_post_ms,
        };
    }

private:
    bool enabled_ = false;
    std::array<cudaEvent_t, 15U> events_{};
};

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

    void copy_from_host(
        const T* source, std::size_t count,
        std::size_t destination_offset = 0U) {
        if (destination_offset > capacity_ ||
            count > capacity_ - destination_offset) {
            throw std::out_of_range(
                "reusable host-to-device copy exceeds capacity");
        }
        require_cuda(
            cudaMemcpy(
                data_ + destination_offset,
                source, count * sizeof(T),
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

struct DeviceProjectedSplat {
    float depth = 0.0F;
    float x = 0.0F;
    float y = 0.0F;
    float radius_x = 0.0F;
    float radius_y = 0.0F;
    float conic_xx = 0.0F;
    float conic_xy = 0.0F;
    float conic_yy = 0.0F;
    float opacity = 0.0F;
    float compensation = 1.0F;
    std::array<float, 3> color{};
};

struct DeviceProjectedRecord {
    DeviceProjectedSplat splat{};
};

static_assert(std::is_trivially_copyable_v<DeviceProjectedRecord>);
static_assert(sizeof(DeviceProjectedRecord) == 52U);

struct DeviceTileBounds {
    std::uint32_t minimum_x = 0U;
    std::uint32_t maximum_x = 0U;
    std::uint32_t minimum_y = 0U;
    std::uint32_t maximum_y = 0U;
};

__device__ bool projected_tile_bounds(
    const DeviceProjectedSplat& splat, std::uint32_t width,
    std::uint32_t height, DeviceTileBounds& bounds) {
    if (!(splat.depth > minimum_depth) || !isfinite(splat.depth)) {
        return false;
    }
    const int pixel_support_x = static_cast<int>(ceilf(splat.radius_x));
    const int pixel_support_y = static_cast<int>(ceilf(splat.radius_y));
    const int center_x = static_cast<int>(floorf(splat.x));
    const int center_y = static_cast<int>(floorf(splat.y));
    const int minimum_x = max(0, center_x - pixel_support_x);
    const int maximum_x =
        min(static_cast<int>(width) - 1, center_x + pixel_support_x);
    const int minimum_y = max(0, center_y - pixel_support_y);
    const int maximum_y =
        min(static_cast<int>(height) - 1, center_y + pixel_support_y);
    if (minimum_x > maximum_x || minimum_y > maximum_y) {
        return false;
    }
    bounds.minimum_x =
        static_cast<std::uint32_t>(minimum_x) / alpha_tile_width;
    bounds.maximum_x =
        static_cast<std::uint32_t>(maximum_x) / alpha_tile_width;
    bounds.minimum_y =
        static_cast<std::uint32_t>(minimum_y) / alpha_tile_height;
    bounds.maximum_y =
        static_cast<std::uint32_t>(maximum_y) / alpha_tile_height;
    return true;
}

__device__ float projected_conic_value(
    const DeviceProjectedSplat& splat, float delta_x,
    float delta_y) {
    return fmaxf(
        0.0F,
        splat.conic_xx * delta_x * delta_x +
            2.0F * splat.conic_xy * delta_x * delta_y +
            splat.conic_yy * delta_y * delta_y);
}

__device__ bool projected_splat_intersects_tile(
    const DeviceProjectedSplat& splat, std::uint32_t tile_x,
    std::uint32_t tile_y, std::uint32_t width,
    std::uint32_t height) {
    const std::uint32_t pixel_minimum_x =
        tile_x * alpha_tile_width;
    const std::uint32_t pixel_minimum_y =
        tile_y * alpha_tile_height;
    if (pixel_minimum_x >= width || pixel_minimum_y >= height) {
        return false;
    }
    const std::uint32_t pixel_maximum_x = min(
        width - 1U,
        pixel_minimum_x + alpha_tile_width - 1U);
    const std::uint32_t pixel_maximum_y = min(
        height - 1U,
        pixel_minimum_y + alpha_tile_height - 1U);

    // Pixel samples are evaluated at their centres. Minimise the positive
    // definite conic over the continuous rectangle enclosing those centres.
    // The continuous minimum is a conservative lower bound for the discrete
    // samples, so rejecting a tile cannot discard a contributing pixel.
    const float minimum_x =
        static_cast<float>(pixel_minimum_x) + 0.5F - splat.x;
    const float maximum_x =
        static_cast<float>(pixel_maximum_x) + 0.5F - splat.x;
    const float minimum_y =
        static_cast<float>(pixel_minimum_y) + 0.5F - splat.y;
    const float maximum_y =
        static_cast<float>(pixel_maximum_y) + 0.5F - splat.y;
    const float clamped_x = fminf(maximum_x, fmaxf(minimum_x, 0.0F));
    const float clamped_y = fminf(maximum_y, fmaxf(minimum_y, 0.0F));
    float minimum_conic =
        projected_conic_value(splat, clamped_x, clamped_y);

    const float edge_x[2]{minimum_x, maximum_x};
    const float edge_y[2]{minimum_y, maximum_y};
    for (std::uint32_t edge = 0U; edge < 2U; ++edge) {
        const float x = edge_x[edge];
        const float optimal_y = fminf(
            maximum_y,
            fmaxf(
                minimum_y,
                -splat.conic_xy * x / splat.conic_yy));
        minimum_conic = fminf(
            minimum_conic,
            projected_conic_value(splat, x, optimal_y));

        const float y = edge_y[edge];
        const float optimal_x = fminf(
            maximum_x,
            fmaxf(
                minimum_x,
                -splat.conic_xy * y / splat.conic_xx));
        minimum_conic = fminf(
            minimum_conic,
            projected_conic_value(splat, optimal_x, y));
    }

    const float peak_alpha = splat.opacity * splat.compensation;
    if (!(peak_alpha >= alpha_minimum_contribution) ||
        !isfinite(peak_alpha)) {
        return false;
    }
    const float maximum_conic =
        2.0F * logf(peak_alpha / alpha_minimum_contribution);
    return isfinite(minimum_conic) &&
           minimum_conic <= maximum_conic;
}

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

__device__ bool load_gaussian_scale_device(
    const Gaussian& gaussian, float scale[3]) {
    for (std::uint32_t axis = 0U; axis < 3U; ++axis) {
        scale[axis] = expf(gaussian.log_scale[axis]);
        if (!isfinite(scale[axis]) || scale[axis] <= 0.0F) {
            return false;
        }
    }
    return true;
}

__device__ bool may_overlap_image_device(
    DeviceRasterCamera camera, float camera_x, float camera_y,
    float camera_z, float image_x, float image_y,
    const float scale[3], float antialias_filter_variance,
    bool fastgs_compatibility) {
    float radius_x = gaussian_support * sqrtf(
        maximum_projected_variance +
        fmaxf(0.0F, antialias_filter_variance));
    float radius_y = radius_x;
    if (fastgs_compatibility) {
        const float inverse_depth = 1.0F / camera_z;
        const float normalized_x = camera_x * inverse_depth;
        const float normalized_y = camera_y * inverse_depth;
        const float projected_x = fminf(
            (1.15F * static_cast<float>(camera.width) - camera.cx) /
                camera.fx,
            fmaxf(
                (-0.15F * static_cast<float>(camera.width) - camera.cx) /
                    camera.fx,
                normalized_x));
        const float projected_y = fminf(
            (1.15F * static_cast<float>(camera.height) - camera.cy) /
                camera.fy,
            fmaxf(
                (-0.15F * static_cast<float>(camera.height) - camera.cy) /
                    camera.fy,
                normalized_y));
        const float jacobian_xx = camera.fx * inverse_depth;
        const float jacobian_xz =
            -camera.fx * projected_x * inverse_depth;
        const float jacobian_yy = camera.fy * inverse_depth;
        const float jacobian_yz =
            -camera.fy * projected_y * inverse_depth;
        float row_x_l1 = 0.0F;
        float row_y_l1 = 0.0F;
        for (std::uint32_t column = 0U; column < 3U; ++column) {
            row_x_l1 += fabsf(
                jacobian_xx * camera.rotation[column] +
                jacobian_xz * camera.rotation[2U * 3U + column]);
            row_y_l1 += fabsf(
                jacobian_yy * camera.rotation[1U * 3U + column] +
                jacobian_yz * camera.rotation[2U * 3U + column]);
        }
        const float maximum_scale =
            fmaxf(scale[0], fmaxf(scale[1], scale[2]));
        // Each normalized quaternion rotation coefficient is at most one in
        // magnitude.  sqrt(3), rounded upward, therefore turns the L1 camera
        // row bound into an upper bound for every projected covariance row.
        const float row_bound_factor =
            1.733F * maximum_scale;
        radius_x = fmaxf(
            fastgs_maximum_support * sqrtf(
                row_bound_factor * row_bound_factor *
                    row_x_l1 * row_x_l1 +
                fastgs_dilation) -
                0.5F,
            0.0F);
        radius_y = fmaxf(
            fastgs_maximum_support * sqrtf(
                row_bound_factor * row_bound_factor *
                    row_y_l1 * row_y_l1 +
                fastgs_dilation) -
                0.5F,
            0.0F);
    }
    radius_x += conservative_radius_margin;
    radius_y += conservative_radius_margin;
    // An unbounded estimate is not grounds for culling: the exact projection
    // below remains the authority.  The extra pixel absorbs float roundoff at
    // the image boundary, so this prefilter cannot remove a visible splat.
    if (!isfinite(radius_x) || !isfinite(radius_y)) {
        return true;
    }
    return image_x + radius_x >= 0.0F &&
           image_y + radius_y >= 0.0F &&
           image_x - radius_x < static_cast<float>(camera.width) &&
           image_y - radius_y < static_cast<float>(camera.height);
}

__device__ bool project_covariance_device(
    const Gaussian& gaussian, DeviceRasterCamera camera,
    float camera_x, float camera_y, float camera_z,
    const float scale[3],
    float opacity, float antialias_filter_variance,
    bool fastgs_compatibility,
    float& radius_x, float& radius_y,
    float& conic_xx, float& conic_xy, float& conic_yy,
    float& compensation) {
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
    const float inverse_depth = 1.0F / camera_z;
    const float normalized_x = camera_x * inverse_depth;
    const float normalized_y = camera_y * inverse_depth;
    const float projected_x = fastgs_compatibility
        ? fminf(
              (1.15F * static_cast<float>(camera.width) - camera.cx) /
                  camera.fx,
              fmaxf(
                  (-0.15F * static_cast<float>(camera.width) - camera.cx) /
                      camera.fx,
                  normalized_x))
        : normalized_x;
    const float projected_y = fastgs_compatibility
        ? fminf(
              (1.15F * static_cast<float>(camera.height) - camera.cy) /
                  camera.fy,
              fmaxf(
                  (-0.15F * static_cast<float>(camera.height) - camera.cy) /
                      camera.fy,
                  normalized_y))
        : normalized_y;
    const float jacobian_xx = camera.fx * inverse_depth;
    const float jacobian_xz =
        -camera.fx * projected_x * inverse_depth;
    const float jacobian_yy = camera.fy * inverse_depth;
    const float jacobian_yz =
        -camera.fy * projected_y * inverse_depth;
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
    if (fastgs_compatibility) {
        covariance_xx += fastgs_dilation;
        covariance_yy += fastgs_dilation;
    } else {
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
    }
    const float original_determinant =
        covariance_xx * covariance_yy -
        covariance_xy * covariance_xy;
    if (!isfinite(original_determinant) ||
        original_determinant <= 1.0e-12F) {
        return false;
    }
    if (!fastgs_compatibility) {
        covariance_xx += antialias_filter_variance;
        covariance_yy += antialias_filter_variance;
    }
    const float determinant =
        covariance_xx * covariance_yy -
        covariance_xy * covariance_xy;
    if (!isfinite(determinant) || determinant <= 1.0e-12F) {
        return false;
    }
    compensation = !fastgs_compatibility &&
            antialias_filter_variance > 0.0F
        ? sqrtf(fmaxf(1.0e-8F, original_determinant / determinant))
        : 1.0F;
    if (fastgs_compatibility) {
        const float power_threshold =
            logf(opacity / alpha_minimum_contribution);
        const float support =
            sqrtf(fmaxf(0.0F, 2.0F * power_threshold));
        radius_x =
            fmaxf(support * sqrtf(covariance_xx) - 0.5F, 0.0F);
        radius_y =
            fmaxf(support * sqrtf(covariance_yy) - 0.5F, 0.0F);
    } else {
        radius_x = gaussian_support * sqrtf(covariance_xx);
        radius_y = gaussian_support * sqrtf(covariance_yy);
    }
    conic_xx = covariance_yy / determinant;
    conic_xy = -covariance_xy / determinant;
    conic_yy = covariance_xx / determinant;
    return isfinite(radius_x) && isfinite(radius_y) &&
           isfinite(conic_xx) && isfinite(conic_xy) &&
           isfinite(conic_yy) && isfinite(compensation);
}

__device__ float gaussian_weight_device(
    const DeviceProjectedSplat& splat, float delta_x, float delta_y) {
    const float squared_distance = fmaxf(
        0.0F,
        splat.conic_xx * delta_x * delta_x +
            2.0F * splat.conic_xy * delta_x * delta_y +
            splat.conic_yy * delta_y * delta_y);
    return expf(-0.5F * squared_distance);
}

__device__ void evaluate_sh_basis_device(
    const Gaussian& gaussian, DeviceRasterCamera camera,
    std::uint32_t active_degree, float* basis) {
    for (std::uint32_t index = 0U; index < 16U; ++index) {
        basis[index] = 0.0F;
    }
    basis[0] = sh_c0;
    if (active_degree == 0U) {
        return;
    }
    const float center_x = -(
        camera.rotation[0] * camera.translation[0] +
        camera.rotation[3] * camera.translation[1] +
        camera.rotation[6] * camera.translation[2]);
    const float center_y = -(
        camera.rotation[1] * camera.translation[0] +
        camera.rotation[4] * camera.translation[1] +
        camera.rotation[7] * camera.translation[2]);
    const float center_z = -(
        camera.rotation[2] * camera.translation[0] +
        camera.rotation[5] * camera.translation[1] +
        camera.rotation[8] * camera.translation[2]);
    float x = gaussian.xyz[0] - center_x;
    float y = gaussian.xyz[1] - center_y;
    float z = gaussian.xyz[2] - center_z;
    const float inverse_norm =
        rsqrtf(fmaxf(1.0e-20F, x * x + y * y + z * z));
    x *= inverse_norm;
    y *= inverse_norm;
    z *= inverse_norm;
    basis[1] = -sh_c1 * y;
    basis[2] = sh_c1 * z;
    basis[3] = -sh_c1 * x;
    if (active_degree == 1U) {
        return;
    }
    const float xx = x * x;
    const float yy = y * y;
    const float zz = z * z;
    basis[4] = 1.0925484305920792F * x * y;
    basis[5] = -1.0925484305920792F * y * z;
    basis[6] = 0.31539156525252005F * (2.0F * zz - xx - yy);
    basis[7] = -1.0925484305920792F * x * z;
    basis[8] = 0.5462742152960396F * (xx - yy);
    if (active_degree == 2U) {
        return;
    }
    basis[9] = -0.5900435899266435F * y * (3.0F * xx - yy);
    basis[10] = 2.890611442640554F * x * y * z;
    basis[11] =
        -0.4570457994644658F * y * (4.0F * zz - xx - yy);
    basis[12] =
        0.3731763325901154F * z *
        (2.0F * zz - 3.0F * xx - 3.0F * yy);
    basis[13] =
        -0.4570457994644658F * x * (4.0F * zz - xx - yy);
    basis[14] = 1.445305721320277F * z * (xx - yy);
    basis[15] =
        -0.5900435899266435F * x * (xx - 3.0F * yy);
}

__global__ void project_alpha_splats_kernel(
    const Gaussian* gaussians, std::uint32_t gaussian_count,
    DeviceRasterCamera camera, DeviceProjectedRecord* records,
    float* projected_sh_basis, std::uint64_t* depth_keys,
    unsigned long long* visible_splats,
    std::uint32_t active_sh_degree,
    std::uint32_t active_opacity_sh_degree,
    float antialias_filter_variance,
    bool fastgs_compatibility) {
    const std::uint32_t index =
        blockIdx.x * blockDim.x + threadIdx.x;
    if (index >= gaussian_count) {
        return;
    }

    DeviceProjectedRecord record{};
    std::uint64_t depth_key =
        std::numeric_limits<std::uint64_t>::max();
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
        float scale[3]{};
        if (!isfinite(x) || !isfinite(y) ||
            !load_gaussian_scale_device(gaussian, scale) ||
            !may_overlap_image_device(
                camera, camera_x, camera_y, camera_z, x, y, scale,
                antialias_filter_variance, fastgs_compatibility)) {
            records[index] = record;
            depth_keys[index] = depth_key;
            return;
        }
        float sh_basis[16]{};
        evaluate_sh_basis_device(
            gaussian, camera, active_sh_degree, sh_basis);
        const std::uint32_t active_coefficients =
            (active_sh_degree + 1U) * (active_sh_degree + 1U) - 1U;
        const std::uint32_t active_opacity_coefficients =
            (active_opacity_sh_degree + 1U) *
                (active_opacity_sh_degree + 1U) -
            1U;
        float opacity_logit = gaussian.opacity_logit;
        for (std::uint32_t coefficient = 0U;
             coefficient < active_opacity_coefficients; ++coefficient) {
            opacity_logit += sh_basis[coefficient + 1U] *
                gaussian.opacity_sh[coefficient];
        }
        const float opacity =
            1.0F / (1.0F + expf(-opacity_logit));
        float radius_x = 0.0F;
        float radius_y = 0.0F;
        float conic_xx = 0.0F;
        float conic_xy = 0.0F;
        float conic_yy = 0.0F;
        float compensation = 1.0F;
        const bool valid_covariance =
            project_covariance_device(
                gaussian, camera, camera_x, camera_y, camera_z,
                scale,
                opacity, antialias_filter_variance,
                fastgs_compatibility,
                radius_x, radius_y, conic_xx, conic_xy, conic_yy,
                compensation);
        const bool visible =
            isfinite(x) && isfinite(y) && valid_covariance &&
            (!fastgs_compatibility ||
             opacity >= alpha_minimum_contribution) &&
            x + radius_x >= 0.0F && y + radius_y >= 0.0F &&
            x - radius_x < static_cast<float>(camera.width) &&
            y - radius_y < static_cast<float>(camera.height);
        if (visible) {
            float color[3]{};
            for (std::uint32_t channel = 0U; channel < 3U; ++channel) {
                float value = 0.5F + sh_basis[0] * gaussian.dc[channel];
                for (std::uint32_t coefficient = 0U;
                     coefficient < active_coefficients; ++coefficient) {
                    value += sh_basis[coefficient + 1U] *
                        gaussian.sh_rest[
                            channel * maximum_sh_rest_coefficients +
                            coefficient];
                }
                color[channel] =
                    fminf(maximum_splat_color, fmaxf(0.0F, value));
            }
            record.splat = {
                .depth = camera_z,
                .x = x,
                .y = y,
                .radius_x = radius_x,
                .radius_y = radius_y,
                .conic_xx = conic_xx,
                .conic_xy = conic_xy,
                .conic_yy = conic_yy,
                .opacity = opacity,
                .compensation = compensation,
                .color = {color[0], color[1], color[2]},
            };
            for (std::uint32_t coefficient = 0U;
                 coefficient < 16U; ++coefficient) {
                projected_sh_basis[index * 16U + coefficient] =
                    sh_basis[coefficient];
            }
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
    std::uint32_t width, std::uint32_t height, std::uint64_t* counts,
    bool precise_tile_culling) {
    const std::uint32_t index =
        blockIdx.x * blockDim.x + threadIdx.x;
    if (index < record_count) {
        DeviceTileBounds bounds{};
        const auto& splat = records[index].splat;
        std::uint64_t count = 0U;
        const bool has_bounds =
            projected_tile_bounds(splat, width, height, bounds);
        if (has_bounds && !precise_tile_culling) {
            count = static_cast<std::uint64_t>(
                        bounds.maximum_x - bounds.minimum_x + 1U) *
                (bounds.maximum_y - bounds.minimum_y + 1U);
        } else if (has_bounds) {
            for (std::uint32_t tile_y = bounds.minimum_y;
                 tile_y <= bounds.maximum_y; ++tile_y) {
                for (std::uint32_t tile_x = bounds.minimum_x;
                     tile_x <= bounds.maximum_x; ++tile_x) {
                    count += projected_splat_intersects_tile(
                                 splat, tile_x, tile_y,
                                 width, height)
                        ? 1U
                        : 0U;
                }
            }
        }
        counts[index] = count;
    } else if (index == record_count) {
        counts[index] = 0U;
    }
}

__global__ void duplicate_tile_pairs_kernel(
    const DeviceProjectedRecord* records, std::uint32_t record_count,
    const std::uint64_t* pair_offsets, std::uint32_t width,
    std::uint32_t height, std::uint32_t tiles_x,
    std::uint64_t* tile_depth_keys, std::uint32_t* record_indices,
    bool precise_tile_culling) {
    const std::uint32_t record_index =
        blockIdx.x * blockDim.x + threadIdx.x;
    if (record_index >= record_count) {
        return;
    }
    const auto& record = records[record_index];
    DeviceTileBounds bounds{};
    if (!projected_tile_bounds(
            record.splat, width, height, bounds)) {
        return;
    }
    std::uint64_t pair_index = pair_offsets[record_index];
    const std::uint64_t depth_bits =
        static_cast<std::uint64_t>(__float_as_uint(record.splat.depth));
    for (std::uint32_t tile_y = bounds.minimum_y;
         tile_y <= bounds.maximum_y; ++tile_y) {
        for (std::uint32_t tile_x = bounds.minimum_x;
             tile_x <= bounds.maximum_x; ++tile_x) {
            if (precise_tile_culling &&
                !projected_splat_intersects_tile(
                    record.splat, tile_x, tile_y,
                    width, height)) {
                continue;
            }
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

__global__ void extract_bucket_counts_kernel(
    const std::uint64_t* tile_starts, const std::uint64_t* tile_ends,
    std::uint32_t* bucket_counts, std::size_t tile_count) {
    const std::size_t tile =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (tile >= tile_count) {
        return;
    }
    const auto pair_count = tile_ends[tile] - tile_starts[tile];
    bucket_counts[tile] = static_cast<std::uint32_t>(
        (pair_count + fastgs_checkpoint_interval - 1U) /
        fastgs_checkpoint_interval);
}

__global__ void initialize_bucket_tiles_kernel(
    const std::uint32_t* bucket_offsets, std::uint32_t* bucket_tiles,
    std::size_t tile_count) {
    const std::size_t tile =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (tile >= tile_count) {
        return;
    }
    const std::uint32_t begin =
        tile == 0U ? 0U : bucket_offsets[tile - 1U];
    const std::uint32_t end = bucket_offsets[tile];
    for (std::uint32_t bucket = begin; bucket < end; ++bucket) {
        bucket_tiles[bucket] = static_cast<std::uint32_t>(tile);
    }
}

__global__ void render_alpha_tiles_kernel(
    const DeviceProjectedRecord* records,
    const std::uint32_t* record_indices,
    const std::uint64_t* tile_starts, const std::uint64_t* tile_ends,
    std::uint32_t width, std::uint32_t height,
    float maximum_fragment_alpha,
    float background_r, float background_g, float background_b,
    float* rgb, float* transmittance,
    std::uint64_t* active_ends,
    const std::uint32_t* bucket_offsets,
    std::uint32_t* pixel_contributions,
    std::uint32_t* tile_max_contributions,
    std::uint32_t* bucket_checkpoints,
    DeviceRenderStats* stats) {
    constexpr std::uint32_t threads_per_tile =
        alpha_tile_width * alpha_tile_height;
    __shared__ DeviceProjectedSplat batch[threads_per_tile];
    __shared__ std::uint32_t block_max_contributions;

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
    std::uint64_t active_end = end;
    std::uint32_t possible_contributions = 0U;
    std::uint32_t contributions = 0U;
    unsigned long long evaluated = 0U;
    unsigned long long contributing = 0U;
    if (thread_index == 0U) {
        block_max_contributions = 0U;
    }
    __syncthreads();
    for (std::uint64_t base = start; base < end;
         base += threads_per_tile) {
        const bool done =
            !valid_pixel ||
            remaining <= alpha_minimum_transmittance;
        if (__syncthreads_count(done) ==
            static_cast<int>(threads_per_tile)) {
            break;
        }
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
                const auto relative = static_cast<std::uint32_t>(
                    base - start + index);
                if (bucket_checkpoints != nullptr &&
                    relative % fastgs_checkpoint_interval == 0U) {
                    const std::uint32_t first_bucket =
                        tile == 0U ? 0U : bucket_offsets[tile - 1U];
                    const std::uint32_t bucket =
                        first_bucket +
                        relative / fastgs_checkpoint_interval;
                    const std::uint32_t packed_r =
                        pack_fastgs_checkpoint_color(red);
                    const std::uint32_t packed_g =
                        pack_fastgs_checkpoint_color(green);
                    const std::uint32_t packed_b =
                        pack_fastgs_checkpoint_color(blue);
                    const std::uint32_t packed_t = min(
                        255U,
                        static_cast<std::uint32_t>(
                            fmaxf(0.0F, remaining) * 255.0F + 0.5F));
                    bucket_checkpoints[
                        static_cast<std::size_t>(bucket) *
                            threads_per_tile +
                        thread_index] =
                        packed_r | (packed_g << 8U) |
                        (packed_b << 16U) | (packed_t << 24U);
                }
                ++possible_contributions;
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
                    maximum_fragment_alpha,
                    splat.opacity * splat.compensation *
                        gaussian_weight);
                if (alpha < alpha_minimum_contribution) {
                    continue;
                }
                ++contributing;
                contributions = possible_contributions;
                const float weight = remaining * alpha;
                red += weight * splat.color[0];
                green += weight * splat.color[1];
                blue += weight * splat.color[2];
                remaining *= 1.0F - alpha;
                if (remaining <= alpha_minimum_transmittance) {
                    active_end = base + index + 1U;
                    break;
                }
            }
        }
        __syncthreads();
    }
    std::size_t pixel = 0U;
    if (valid_pixel) {
        pixel = static_cast<std::size_t>(y) * width + x;
        rgb[pixel * 3U] = red + remaining * background_r;
        rgb[pixel * 3U + 1U] = green + remaining * background_g;
        rgb[pixel * 3U + 2U] = blue + remaining * background_b;
        transmittance[pixel] = remaining;
        if (active_ends != nullptr) {
            active_ends[pixel] = active_end;
        }
        if (pixel_contributions != nullptr) {
            pixel_contributions[pixel] = contributions;
            atomicMax(&block_max_contributions, contributions);
        }
    }
    __syncthreads();
    if (thread_index == 0U && tile_max_contributions != nullptr) {
        tile_max_contributions[tile] = block_max_contributions;
    }
    if (valid_pixel && stats != nullptr) {
        atomicAdd(&stats->evaluated_pairs, evaluated);
        atomicAdd(&stats->contributing_pairs, contributing);
    }
}

__global__ void backward_alpha_tiles_kernel(
    const DeviceProjectedRecord* records,
    const std::uint64_t* sorted_depth_keys,
    const float* projected_sh_basis,
    const std::uint32_t* record_indices,
    const std::uint64_t* tile_starts, const std::uint64_t* tile_ends,
    std::uint32_t width, std::uint32_t height,
    float maximum_fragment_alpha,
    float background_r, float background_g, float background_b,
    const float* final_transmittance,
    const std::uint64_t* active_ends,
    const float* image_gradient, float* dc_gradient,
    float* sh_rest_gradient, std::uint32_t active_sh_degree,
    float* opacity_logit_gradient,
    float* opacity_sh_gradient,
    std::uint32_t active_opacity_sh_degree,
    float* projected_geometry_gradient,
    const float* densification_error_map,
    const float* densification_edge_map,
    float* frame_refinement_weight,
    float* frame_visibility_weight,
    float* frame_edge_weight,
    float* frame_abs_projected_gradient) {
    constexpr std::uint32_t threads_per_tile =
        alpha_tile_width * alpha_tile_height;
    // One coalesced load serves every pixel in the tile during both the
    // forward render and the reverse gradient traversal.
    __shared__ DeviceProjectedSplat batch[threads_per_tile];
    __shared__ std::uint32_t batch_sources[threads_per_tile];
    __shared__ unsigned long long block_active_end;

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

    const auto pixel = valid_pixel
        ? static_cast<std::size_t>(y) * width + x
        : 0U;
    const std::uint64_t active_end =
        valid_pixel ? active_ends[pixel] : start;
    if (thread_index == 0U) {
        block_active_end = start;
    }
    __syncthreads();
    if (valid_pixel) {
        atomicMax(&block_active_end, active_end);
    }
    __syncthreads();
    const float upstream[3]{
        image_gradient[pixel * 3U],
        image_gradient[pixel * 3U + 1U],
        image_gradient[pixel * 3U + 2U],
    };
    float tail[3]{background_r, background_g, background_b};
    float transmittance_after =
        valid_pixel ? final_transmittance[pixel] : 1.0F;
    for (std::uint64_t batch_end =
             min(end, static_cast<std::uint64_t>(block_active_end));
         batch_end > start;) {
        const auto batch_start =
            batch_end - start > threads_per_tile
            ? batch_end - threads_per_tile
            : start;
        const auto load_index = batch_start + thread_index;
        if (load_index < batch_end) {
            const auto record_index = record_indices[load_index];
            batch[thread_index] = records[record_index].splat;
            batch_sources[thread_index] =
                static_cast<std::uint32_t>(
                    sorted_depth_keys[record_index]);
        }
        __syncthreads();
        if (valid_pixel && active_end > batch_start) {
            const auto local_end = static_cast<std::uint32_t>(
                min(active_end, batch_end) - batch_start);
            for (std::uint32_t local_cursor = local_end;
                 local_cursor > 0U; --local_cursor) {
        const auto& splat = batch[local_cursor - 1U];
        const auto source = batch_sources[local_cursor - 1U];
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
        const float raw_alpha =
            splat.opacity * splat.compensation * gaussian_weight;
        const float alpha = fminf(maximum_fragment_alpha, raw_alpha);
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
                &frame_visibility_weight[source],
                blending_weight);
            atomicAdd(
                &frame_refinement_weight[source],
                blending_weight *
                    densification_error_map[pixel]);
            atomicAdd(
                &frame_edge_weight[source],
                blending_weight *
                    densification_edge_map[pixel]);
        }
        float alpha_gradient = 0.0F;
        for (std::size_t channel = 0U; channel < 3U; ++channel) {
            if (splat.color[channel] > 0.0F &&
                splat.color[channel] < maximum_splat_color) {
                const float color_gradient =
                    upstream[channel] * transmittance_before * alpha;
                atomicAdd(
                    &dc_gradient[source * 3U + channel],
                    sh_c0 * color_gradient);
                const std::uint32_t active_coefficients =
                    (active_sh_degree + 1U) *
                        (active_sh_degree + 1U) -
                    1U;
                for (std::uint32_t coefficient = 0U;
                     coefficient < active_coefficients; ++coefficient) {
                    atomicAdd(
                        &sh_rest_gradient[
                            source * maximum_sh_rest_values +
                            channel * maximum_sh_rest_coefficients +
                            coefficient],
                        projected_sh_basis[
                            source * 16U + coefficient + 1U] *
                            color_gradient);
                }
            }
            alpha_gradient +=
                upstream[channel] * transmittance_before *
                (splat.color[channel] - tail[channel]);
        }
        if (raw_alpha < maximum_fragment_alpha) {
            const float logit_gradient =
                alpha_gradient * gaussian_weight *
                splat.compensation * splat.opacity *
                (1.0F - splat.opacity);
            atomicAdd(
                &opacity_logit_gradient[source],
                logit_gradient);
            const std::uint32_t active_opacity_coefficients =
                (active_opacity_sh_degree + 1U) *
                    (active_opacity_sh_degree + 1U) -
                1U;
            for (std::uint32_t coefficient = 0U;
                 coefficient < active_opacity_coefficients; ++coefficient) {
                atomicAdd(
                    &opacity_sh_gradient[
                        source * maximum_opacity_sh_coefficients +
                        coefficient],
                    projected_sh_basis[
                        source * 16U + coefficient + 1U] *
                        logit_gradient);
            }
            if (projected_geometry_gradient != nullptr) {
                const float squared_distance_gradient =
                    -0.5F * alpha_gradient * splat.opacity *
                    splat.compensation * gaussian_weight;
                const float screen_x_contribution =
                    squared_distance_gradient *
                    -2.0F *
                    (splat.conic_xx * delta_x +
                     splat.conic_xy * delta_y);
                const float screen_y_contribution =
                    squared_distance_gradient *
                    -2.0F *
                    (splat.conic_xy * delta_x +
                     splat.conic_yy * delta_y);
                atomicAdd(
                    &projected_geometry_gradient[source * 6U],
                    screen_x_contribution);
                atomicAdd(
                    &projected_geometry_gradient[source * 6U + 1U],
                    screen_y_contribution);
                if (frame_abs_projected_gradient != nullptr) {
                    atomicAdd(
                        &frame_abs_projected_gradient[source * 2U],
                        fabsf(screen_x_contribution));
                    atomicAdd(
                        &frame_abs_projected_gradient[source * 2U + 1U],
                        fabsf(screen_y_contribution));
                }
                atomicAdd(
                    &projected_geometry_gradient[source * 6U + 2U],
                    squared_distance_gradient * delta_x * delta_x);
                atomicAdd(
                    &projected_geometry_gradient[source * 6U + 3U],
                    squared_distance_gradient *
                        2.0F * delta_x * delta_y);
                atomicAdd(
                    &projected_geometry_gradient[source * 6U + 4U],
                    squared_distance_gradient * delta_y * delta_y);
                atomicAdd(
                    &projected_geometry_gradient[source * 6U + 5U],
                    alpha_gradient * splat.opacity *
                        gaussian_weight);
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
        __syncthreads();
        batch_end = batch_start;
    }
}

// Adapted from LichtFeld Studio FastGS blend_backward_cu
// (GPL-3.0-or-later). DroneGS keeps its own projection, sorting, optimizer,
// orchestration and public API; this kernel only ports the bucket/checkpoint
// warp traversal used by the raster backward pass.
__global__ void backward_alpha_buckets_kernel(
    const DeviceProjectedRecord* records,
    const std::uint64_t* sorted_depth_keys,
    const std::uint32_t* record_indices,
    const std::uint64_t* tile_starts,
    const std::uint64_t* tile_ends,
    const std::uint32_t* bucket_offsets,
    const std::uint32_t* bucket_tiles,
    const std::uint32_t* bucket_checkpoints,
    const std::uint32_t* tile_max_contributions,
    const std::uint32_t* pixel_contributions,
    std::uint32_t bucket_count,
    std::uint32_t width, std::uint32_t height,
    float maximum_fragment_alpha,
    const float* final_rgb,
    const float* image_gradient,
    float* dc_gradient,
    float* opacity_logit_gradient,
    float* projected_geometry_gradient,
    const float* densification_error_map,
    const float* densification_edge_map,
    float* frame_refinement_weight,
    float* frame_visibility_weight,
    float* frame_edge_weight,
    float* frame_abs_projected_gradient) {
    const std::uint32_t warp =
        threadIdx.x / fastgs_checkpoint_interval;
    const std::uint32_t lane =
        threadIdx.x % fastgs_checkpoint_interval;
    const std::uint32_t bucket =
        blockIdx.x * fastgs_backward_warps_per_block + warp;
    if (bucket >= bucket_count) {
        return;
    }

    const std::uint32_t tile = bucket_tiles[bucket];
    const std::uint32_t first_bucket =
        tile == 0U ? 0U : bucket_offsets[tile - 1U];
    const std::uint32_t tile_bucket = bucket - first_bucket;
    const std::uint32_t relative =
        tile_bucket * fastgs_checkpoint_interval + lane;
    if (tile_bucket * fastgs_checkpoint_interval >=
        tile_max_contributions[tile]) {
        return;
    }

    const std::uint64_t tile_start = tile_starts[tile];
    const std::uint64_t tile_end = tile_ends[tile];
    const bool valid_primitive =
        tile_start + relative < tile_end;
    std::uint32_t source = 0U;
    DeviceProjectedSplat splat{};
    if (valid_primitive) {
        const std::uint32_t record_index =
            record_indices[tile_start + relative];
        splat = records[record_index].splat;
        source = static_cast<std::uint32_t>(
            sorted_depth_keys[record_index]);
    }

    float dc_accum[3]{0.0F, 0.0F, 0.0F};
    float opacity_accum = 0.0F;
    float geometry_accum[6]{
        0.0F, 0.0F, 0.0F, 0.0F, 0.0F, 0.0F};
    float abs_screen_accum[2]{0.0F, 0.0F};
    float visibility_accum = 0.0F;
    float refinement_accum = 0.0F;
    float edge_accum = 0.0F;

    const std::uint32_t tiles_x =
        width / alpha_tile_width +
        (width % alpha_tile_width != 0U ? 1U : 0U);
    const std::uint32_t tile_x = tile % tiles_x;
    const std::uint32_t tile_y = tile / tiles_x;
    const std::uint32_t start_x = tile_x * alpha_tile_width;
    const std::uint32_t start_y = tile_y * alpha_tile_height;

    __shared__ std::uint32_t collected_last_contributor[
        fastgs_backward_warps_per_block][fastgs_checkpoint_interval];
    __shared__ float collected_color_after[
        fastgs_backward_warps_per_block][fastgs_checkpoint_interval][3];
    __shared__ float collected_transmittance[
        fastgs_backward_warps_per_block][fastgs_checkpoint_interval];
    __shared__ float collected_gradient[
        fastgs_backward_warps_per_block][fastgs_checkpoint_interval][3];

    std::uint32_t last_contributor = 0U;
    float color_after[3]{0.0F, 0.0F, 0.0F};
    float transmittance = 1.0F;
    float upstream[3]{0.0F, 0.0F, 0.0F};
    constexpr unsigned int warp_mask = 0xffffffffU;
    constexpr int tile_pixels =
        static_cast<int>(alpha_tile_width * alpha_tile_height);

#pragma unroll
    for (int diagonal = 0;
         diagonal < tile_pixels +
             static_cast<int>(fastgs_checkpoint_interval) - 1;
         ++diagonal) {
        if (diagonal %
                static_cast<int>(fastgs_checkpoint_interval) ==
            0) {
            const int local = diagonal + static_cast<int>(lane);
            std::uint32_t packed = 0U;
            bool pixel_in_bounds = false;
            std::size_t pixel = 0U;
            if (local < tile_pixels) {
                packed = bucket_checkpoints[
                    static_cast<std::size_t>(bucket) *
                        tile_pixels +
                    static_cast<std::size_t>(local)];
                const std::uint32_t local_x =
                    static_cast<std::uint32_t>(local) %
                    alpha_tile_width;
                const std::uint32_t local_y =
                    static_cast<std::uint32_t>(local) /
                    alpha_tile_width;
                const std::uint32_t x = start_x + local_x;
                const std::uint32_t y = start_y + local_y;
                pixel_in_bounds = x < width && y < height;
                pixel = static_cast<std::size_t>(y) * width + x;
            }
            const float checkpoint_color[3]{
                static_cast<float>(packed & 0xffU) *
                    fastgs_checkpoint_color_inverse_scale,
                static_cast<float>((packed >> 8U) & 0xffU) *
                    fastgs_checkpoint_color_inverse_scale,
                static_cast<float>((packed >> 16U) & 0xffU) *
                    fastgs_checkpoint_color_inverse_scale,
            };
            collected_last_contributor[warp][lane] =
                pixel_in_bounds
                ? pixel_contributions[pixel]
                : 0U;
            collected_transmittance[warp][lane] =
                static_cast<float>((packed >> 24U) & 0xffU) /
                255.0F;
            for (std::uint32_t channel = 0U;
                 channel < 3U; ++channel) {
                collected_color_after[warp][lane][channel] =
                    pixel_in_bounds
                    ? final_rgb[pixel * 3U + channel] -
                          checkpoint_color[channel]
                    : 0.0F;
                collected_gradient[warp][lane][channel] =
                    pixel_in_bounds
                    ? image_gradient[pixel * 3U + channel]
                    : 0.0F;
            }
            __syncwarp(warp_mask);
        }

        if (diagonal > 0) {
            last_contributor = __shfl_up_sync(
                warp_mask, last_contributor, 1);
            transmittance = __shfl_up_sync(
                warp_mask, transmittance, 1);
            for (std::uint32_t channel = 0U;
                 channel < 3U; ++channel) {
                color_after[channel] = __shfl_up_sync(
                    warp_mask, color_after[channel], 1);
                upstream[channel] = __shfl_up_sync(
                    warp_mask, upstream[channel], 1);
            }
        }

        const int local =
            diagonal - static_cast<int>(lane);
        const bool local_in_tile =
            local >= 0 && local < tile_pixels;
        const std::uint32_t local_x = local_in_tile
            ? static_cast<std::uint32_t>(local) %
                  alpha_tile_width
            : 0U;
        const std::uint32_t local_y = local_in_tile
            ? static_cast<std::uint32_t>(local) /
                  alpha_tile_width
            : 0U;
        const std::uint32_t x = start_x + local_x;
        const std::uint32_t y = start_y + local_y;
        const bool valid_pixel =
            local_in_tile && x < width && y < height;

        if (lane == 0U && valid_primitive && valid_pixel) {
            const std::uint32_t shared_index =
                static_cast<std::uint32_t>(diagonal) %
                fastgs_checkpoint_interval;
            last_contributor =
                collected_last_contributor[warp][shared_index];
            transmittance =
                collected_transmittance[warp][shared_index];
            for (std::uint32_t channel = 0U;
                 channel < 3U; ++channel) {
                color_after[channel] =
                    collected_color_after[
                        warp][shared_index][channel];
                upstream[channel] =
                    collected_gradient[
                        warp][shared_index][channel];
            }
        }

        if (!valid_primitive || !valid_pixel ||
            relative >= last_contributor) {
            continue;
        }
        const int support_x =
            static_cast<int>(ceilf(splat.radius_x));
        const int support_y =
            static_cast<int>(ceilf(splat.radius_y));
        const int center_x =
            static_cast<int>(floorf(splat.x));
        const int center_y =
            static_cast<int>(floorf(splat.y));
        if (static_cast<int>(x) < center_x - support_x ||
            static_cast<int>(x) > center_x + support_x ||
            static_cast<int>(y) < center_y - support_y ||
            static_cast<int>(y) > center_y + support_y) {
            continue;
        }

        const std::size_t pixel =
            static_cast<std::size_t>(y) * width + x;
        const float delta_x =
            (static_cast<float>(x) + 0.5F) - splat.x;
        const float delta_y =
            (static_cast<float>(y) + 0.5F) - splat.y;
        const float gaussian_weight =
            gaussian_weight_device(splat, delta_x, delta_y);
        const float raw_alpha =
            splat.opacity * splat.compensation *
            gaussian_weight;
        const float alpha =
            fminf(maximum_fragment_alpha, raw_alpha);
        if (alpha < alpha_minimum_contribution) {
            continue;
        }

        const float blending_weight =
            transmittance * alpha;
        visibility_accum += blending_weight;
        if (densification_error_map != nullptr) {
            refinement_accum +=
                blending_weight *
                densification_error_map[pixel];
        }
        if (densification_edge_map != nullptr) {
            edge_accum +=
                blending_weight *
                densification_edge_map[pixel];
        }

        for (std::uint32_t channel = 0U;
             channel < 3U; ++channel) {
            if (splat.color[channel] > 0.0F &&
                splat.color[channel] < maximum_splat_color) {
                dc_accum[channel] +=
                    upstream[channel] * blending_weight;
            }
            color_after[channel] -=
                blending_weight * splat.color[channel];
        }

        const float one_minus_alpha = 1.0F - alpha;
        const float inverse_one_minus_alpha =
            1.0F / fmaxf(one_minus_alpha, 1.0e-4F);
        float alpha_gradient = 0.0F;
        for (std::uint32_t channel = 0U;
             channel < 3U; ++channel) {
            alpha_gradient += upstream[channel] *
                (transmittance * splat.color[channel] -
                 color_after[channel] *
                     inverse_one_minus_alpha);
        }
        if (raw_alpha < maximum_fragment_alpha) {
            opacity_accum +=
                alpha_gradient * gaussian_weight *
                splat.compensation * splat.opacity *
                (1.0F - splat.opacity);
            const float squared_distance_gradient =
                -0.5F * alpha_gradient * splat.opacity *
                splat.compensation * gaussian_weight;
            const float screen_x_contribution =
                squared_distance_gradient * -2.0F *
                (splat.conic_xx * delta_x +
                 splat.conic_xy * delta_y);
            const float screen_y_contribution =
                squared_distance_gradient * -2.0F *
                (splat.conic_xy * delta_x +
                 splat.conic_yy * delta_y);
            geometry_accum[0] += screen_x_contribution;
            geometry_accum[1] += screen_y_contribution;
            geometry_accum[2] +=
                squared_distance_gradient *
                delta_x * delta_x;
            geometry_accum[3] +=
                squared_distance_gradient *
                2.0F * delta_x * delta_y;
            geometry_accum[4] +=
                squared_distance_gradient *
                delta_y * delta_y;
            geometry_accum[5] +=
                alpha_gradient * splat.opacity *
                gaussian_weight;
            abs_screen_accum[0] +=
                fabsf(screen_x_contribution);
            abs_screen_accum[1] +=
                fabsf(screen_y_contribution);
        }
        transmittance *= one_minus_alpha;
    }

    if (!valid_primitive) {
        return;
    }
    for (std::uint32_t channel = 0U;
         channel < 3U; ++channel) {
        atomicAdd(
            &dc_gradient[source * 3U + channel],
            dc_accum[channel]);
    }
    atomicAdd(
        &opacity_logit_gradient[source],
        opacity_accum);
    for (std::uint32_t component = 0U;
         component < 6U; ++component) {
        atomicAdd(
            &projected_geometry_gradient[
                source * 6U + component],
            geometry_accum[component]);
    }
    if (frame_visibility_weight != nullptr) {
        atomicAdd(
            &frame_visibility_weight[source],
            visibility_accum);
        atomicAdd(
            &frame_refinement_weight[source],
            refinement_accum);
        atomicAdd(
            &frame_edge_weight[source],
            edge_accum);
    }
    if (frame_abs_projected_gradient != nullptr) {
        atomicAdd(
            &frame_abs_projected_gradient[source * 2U],
            abs_screen_accum[0]);
        atomicAdd(
            &frame_abs_projected_gradient[
                source * 2U + 1U],
            abs_screen_accum[1]);
    }
}

__global__ void backward_projected_geometry_kernel(
    const Gaussian* gaussians, std::uint32_t gaussian_count,
    DeviceRasterCamera camera,
    float antialias_filter_variance,
    bool fastgs_compatibility,
    const float* projected_geometry_gradient,
    float* xyz_gradient, float* log_scale_gradient,
    float* rotation_gradient) {
    const std::uint32_t index =
        blockIdx.x * blockDim.x + threadIdx.x;
    if (index >= gaussian_count) {
        return;
    }
    const float* projected_gradient =
        projected_geometry_gradient + index * 6U;
    const float screen_x_gradient = projected_gradient[0];
    const float screen_y_gradient = projected_gradient[1];
    const float conic_xx_gradient = projected_gradient[2];
    const float conic_xy_gradient = projected_gradient[3];
    const float conic_yy_gradient = projected_gradient[4];
    const float compensation_gradient = projected_gradient[5];
    if (screen_x_gradient == 0.0F &&
        screen_y_gradient == 0.0F &&
        conic_xx_gradient == 0.0F &&
        conic_xy_gradient == 0.0F &&
        conic_yy_gradient == 0.0F &&
        compensation_gradient == 0.0F) {
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
    const float normalized_x = camera_x * inverse_depth;
    const float normalized_y = camera_y * inverse_depth;
    const float projected_x = fastgs_compatibility
        ? fminf(
              (1.15F * static_cast<float>(camera.width) - camera.cx) /
                  camera.fx,
              fmaxf(
                  (-0.15F * static_cast<float>(camera.width) - camera.cx) /
                      camera.fx,
                  normalized_x))
        : normalized_x;
    const float projected_y = fastgs_compatibility
        ? fminf(
              (1.15F * static_cast<float>(camera.height) - camera.cy) /
                  camera.fy,
              fmaxf(
                  (-0.15F * static_cast<float>(camera.height) - camera.cy) /
                      camera.fy,
                  normalized_y))
        : normalized_y;
    const float jacobian[6]{
        camera.fx * inverse_depth,
        0.0F,
        -camera.fx * projected_x * inverse_depth,
        0.0F,
        camera.fy * inverse_depth,
        -camera.fy * projected_y * inverse_depth,
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
    if (fastgs_compatibility) {
        clamped_xx = covariance_xx + fastgs_dilation;
        clamped_xy = covariance_xy;
        clamped_yy = covariance_yy + fastgs_dilation;
    }
    const float original_determinant =
        clamped_xx * clamped_yy -
        clamped_xy * clamped_xy;
    if (!isfinite(original_determinant) ||
        original_determinant <= 1.0e-12F) {
        return;
    }
    const float blurred_xx =
        clamped_xx +
        (fastgs_compatibility ? 0.0F : antialias_filter_variance);
    const float blurred_yy =
        clamped_yy +
        (fastgs_compatibility ? 0.0F : antialias_filter_variance);
    const float determinant =
        blurred_xx * blurred_yy -
        clamped_xy * clamped_xy;
    if (!isfinite(determinant) || determinant <= 1.0e-12F) {
        return;
    }
    const float conic_xx = blurred_yy / determinant;
    const float conic_xy = -clamped_xy / determinant;
    const float conic_yy = blurred_xx / determinant;
    const float compensation =
        !fastgs_compatibility &&
                antialias_filter_variance > 0.0F
        ? sqrtf(fmaxf(
              1.0e-8F, original_determinant / determinant))
        : 1.0F;

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
    if (!fastgs_compatibility &&
        antialias_filter_variance > 0.0F &&
        compensation_gradient != 0.0F) {
        const float conic_determinant =
            conic_xx * conic_yy - conic_xy * conic_xy;
        const float squared_compensation_gradient =
            compensation_gradient * 0.5F /
            (compensation + 1.0e-6F);
        const float one_minus_squared_compensation =
            1.0F - compensation * compensation;
        clamped_covariance_gradient[0] +=
            squared_compensation_gradient *
            (one_minus_squared_compensation * conic_xx -
             antialias_filter_variance * conic_determinant);
        clamped_covariance_gradient[1] +=
            squared_compensation_gradient *
            one_minus_squared_compensation * conic_xy;
        clamped_covariance_gradient[2] +=
            squared_compensation_gradient *
            one_minus_squared_compensation * conic_xy;
        clamped_covariance_gradient[3] +=
            squared_compensation_gradient *
            (one_minus_squared_compensation * conic_yy -
             antialias_filter_variance * conic_determinant);
    }

    float covariance_gradient[4]{};
    if (fastgs_compatibility) {
        covariance_gradient[0] = clamped_covariance_gradient[0];
        covariance_gradient[1] = clamped_covariance_gradient[1];
        covariance_gradient[2] = clamped_covariance_gradient[2];
        covariance_gradient[3] = clamped_covariance_gradient[3];
    } else if (spectral_gap > 1.0e-8F) {
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
    unsigned int* active_pixels, std::size_t pixel_count,
    bool include_empty_pixels) {
    const std::size_t pixel =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (pixel >= pixel_count ||
        (!include_empty_pixels && transmittance[pixel] >= 1.0F)) {
        return;
    }
    float pixel_loss = 0.0F;
    for (std::size_t channel = 0U; channel < 3U; ++channel) {
        const auto offset = pixel * 3U + channel;
        const float target_value =
            static_cast<float>(target[offset]) / 255.0F;
        const float difference = prediction[offset] - target_value;
        pixel_loss += fabsf(difference);
    }
    atomicAdd(loss_sum, pixel_loss);
    atomicAdd(active_pixels, 1U);
}

__global__ void active_squared_error_values_kernel(
    const float* prediction, const float* transmittance,
    const std::uint8_t* target, float* values,
    std::size_t sample_count, bool include_empty_pixels) {
    const std::size_t sample =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (sample >= sample_count) {
        return;
    }
    const std::size_t pixel = sample / 3U;
    if (!include_empty_pixels && transmittance[pixel] >= 1.0F) {
        values[sample] = 0.0F;
        return;
    }
    const float target_value =
        static_cast<float>(target[sample]) / 255.0F;
    const float difference = prediction[sample] - target_value;
    values[sample] = difference * difference;
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

template <bool mixed_mse>
__global__ void ordered_objective_gradient_kernel(
    const float* prediction, const float* transmittance,
    const std::uint8_t* target, const unsigned int* active_pixels,
    const float* ssim_backward_terms, float* image_gradient,
    std::uint32_t width, std::uint32_t height,
    float base_objective_weight, float mse_objective_weight,
    bool include_empty_pixels) {
    const std::size_t pixel_count =
        static_cast<std::size_t>(width) * height;
    const std::size_t pixel =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (pixel >= pixel_count) {
        return;
    }
    const unsigned int active = *active_pixels;
    const bool contributes =
        active != 0U &&
        (include_empty_pixels || transmittance[pixel] < 1.0F);
    const float effective_base_weight =
        mixed_mse ? base_objective_weight : 1.0F;
    const float l1_normalizer =
        contributes
            ? effective_base_weight * l1_objective_weight /
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
        -effective_base_weight * dssim_objective_weight /
        static_cast<float>(valid_sample_count);
    const float mse_normalizer = mixed_mse && contributes
            ? 2.0F * mse_objective_weight /
                  (3.0F * static_cast<float>(active))
            : 0.0F;
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
        float gradient =
            l1_gradient + dssim_normalizer * ssim_gradient;
        if constexpr (mixed_mse) {
            gradient += mse_normalizer * difference;
        }
        image_gradient[offset] = gradient;
    }
}

// Adapted from LichtFeld Studio fusedL1SSIMForwardCUDA and
// fusedL1SSIMBackwardCUDA (GPL-3.0-or-later). This variant keeps DroneGS'
// interleaved RGB layout, uint8 targets, valid-window SSIM semantics and
// active-pixel L1 normalization.
__global__ void fused_ordered_l1_ssim_forward_kernel(
    const float* prediction, const float* transmittance,
    const std::uint8_t* target,
    float* loss_sum, unsigned int* active_pixels,
    float* ssim_values, float* backward_terms,
    std::uint32_t width, std::uint32_t height,
    bool include_empty_pixels) {
    constexpr int block_width = 16;
    constexpr int block_height = 16;
    constexpr int halo = static_cast<int>(ssim_window_radius);
    constexpr int shared_width = block_width + 2 * halo;
    constexpr int shared_height = block_height + 2 * halo;
    constexpr int block_threads = block_width * block_height;
    __shared__ float image_tile[shared_height][shared_width][2];
    __shared__ float horizontal[shared_height][block_width][5];
    __shared__ float block_loss[block_threads];
    __shared__ unsigned int block_active[block_threads];

    const int x =
        static_cast<int>(blockIdx.x) * block_width + threadIdx.x;
    const int y =
        static_cast<int>(blockIdx.y) * block_height + threadIdx.y;
    const bool in_bounds =
        x < static_cast<int>(width) &&
        y < static_cast<int>(height);
    const std::size_t pixel = in_bounds
        ? static_cast<std::size_t>(y) * width +
              static_cast<std::size_t>(x)
        : 0U;
    const int flat_thread =
        threadIdx.y * block_width + threadIdx.x;
    float pixel_l1 = 0.0F;

    for (std::uint32_t channel = 0U;
         channel < 3U; ++channel) {
        constexpr int tile_items = shared_width * shared_height;
        for (int item = flat_thread;
             item < tile_items; item += block_threads) {
            const int local_y = item / shared_width;
            const int local_x = item % shared_width;
            const int source_x =
                static_cast<int>(blockIdx.x) * block_width +
                local_x - halo;
            const int source_y =
                static_cast<int>(blockIdx.y) * block_height +
                local_y - halo;
            float prediction_value = 0.0F;
            float target_value = 0.0F;
            if (source_x >= 0 &&
                source_x < static_cast<int>(width) &&
                source_y >= 0 &&
                source_y < static_cast<int>(height)) {
                const auto source =
                    (static_cast<std::size_t>(source_y) * width +
                     static_cast<std::size_t>(source_x)) *
                        3U +
                    channel;
                prediction_value = prediction[source];
                target_value =
                    static_cast<float>(target[source]) / 255.0F;
            }
            image_tile[local_y][local_x][0] =
                prediction_value;
            image_tile[local_y][local_x][1] =
                target_value;
        }
        __syncthreads();

        const int convolution_x = threadIdx.x + halo;
        for (int pass = 0; pass < 2; ++pass) {
            const int convolution_y =
                threadIdx.y + pass * block_height;
            if (convolution_y < shared_height) {
                float moments[5]{
                    0.0F, 0.0F, 0.0F, 0.0F, 0.0F};
#pragma unroll
                for (int offset = -halo;
                     offset <= halo; ++offset) {
                    const float weight =
                        ssim_gaussian_weights[offset + halo];
                    const float predicted =
                        image_tile[
                            convolution_y]
                            [convolution_x + offset][0];
                    const float expected =
                        image_tile[
                            convolution_y]
                            [convolution_x + offset][1];
                    moments[0] += weight * predicted;
                    moments[1] += weight * expected;
                    moments[2] +=
                        weight * predicted * predicted;
                    moments[3] +=
                        weight * expected * expected;
                    moments[4] +=
                        weight * predicted * expected;
                }
                for (int moment = 0; moment < 5; ++moment) {
                    horizontal[convolution_y]
                              [threadIdx.x][moment] =
                        moments[moment];
                }
            }
        }
        __syncthreads();

        if (in_bounds) {
            float moments[5]{
                0.0F, 0.0F, 0.0F, 0.0F, 0.0F};
            const int center_y = threadIdx.y + halo;
#pragma unroll
            for (int offset = -halo;
                 offset <= halo; ++offset) {
                const float weight =
                    ssim_gaussian_weights[offset + halo];
                for (int moment = 0;
                     moment < 5; ++moment) {
                    moments[moment] +=
                        weight *
                        horizontal[center_y + offset]
                                  [threadIdx.x][moment];
                }
            }
            const float prediction_mean = moments[0];
            const float target_mean = moments[1];
            const float raw_prediction_variance =
                moments[2] -
                prediction_mean * prediction_mean;
            const float prediction_variance =
                fmaxf(0.0F, raw_prediction_variance);
            const float target_variance = fmaxf(
                0.0F,
                moments[3] - target_mean * target_mean);
            const float covariance =
                moments[4] - prediction_mean * target_mean;
            constexpr float c1 = 0.01F * 0.01F;
            constexpr float c2 = 0.03F * 0.03F;
            const float luminance_denominator =
                prediction_mean * prediction_mean +
                target_mean * target_mean + c1;
            const float contrast_denominator =
                prediction_variance + target_variance + c2;
            const float luminance_numerator =
                2.0F * prediction_mean * target_mean + c1;
            const float contrast_numerator =
                2.0F * covariance + c2;
            const float ssim =
                luminance_numerator * contrast_numerator /
                (luminance_denominator *
                 contrast_denominator);
            const bool valid_center =
                x >= halo &&
                x + halo < static_cast<int>(width) &&
                y >= halo &&
                y + halo < static_cast<int>(height);
            if (valid_center) {
                const std::uint32_t valid_width =
                    width - 2U * ssim_window_radius;
                const std::size_t valid_pixel =
                    static_cast<std::size_t>(
                        y - halo) *
                        valid_width +
                    static_cast<std::size_t>(x - halo);
                ssim_values[
                    valid_pixel * 3U + channel] = ssim;
                if (backward_terms != nullptr) {
                    const std::size_t term =
                        (pixel * 3U + channel) * 5U;
                    backward_terms[term] =
                        prediction_mean;
                    backward_terms[term + 1U] =
                        target_mean;
                    backward_terms[term + 2U] =
                        2.0F * target_mean *
                            contrast_numerator /
                            (luminance_denominator *
                             contrast_denominator) -
                        ssim * 2.0F *
                            prediction_mean /
                            luminance_denominator;
                    backward_terms[term + 3U] =
                        2.0F * luminance_numerator /
                        (luminance_denominator *
                         contrast_denominator);
                    backward_terms[term + 4U] =
                        raw_prediction_variance > 0.0F
                        ? -ssim * 2.0F /
                              contrast_denominator
                        : 0.0F;
                }
            }
            const std::size_t sample =
                pixel * 3U + channel;
            const float target_value =
                static_cast<float>(target[sample]) / 255.0F;
            const float difference =
                prediction[sample] - target_value;
            pixel_l1 += fabsf(difference);
        }
        __syncthreads();
    }

    const bool active =
        in_bounds &&
        (include_empty_pixels || transmittance[pixel] < 1.0F);
    block_loss[flat_thread] = active ? pixel_l1 : 0.0F;
    block_active[flat_thread] = active ? 1U : 0U;
    __syncthreads();

#pragma unroll
    for (int stride = block_threads / 2;
         stride > 0; stride /= 2) {
        if (flat_thread < stride) {
            block_loss[flat_thread] +=
                block_loss[flat_thread + stride];
            block_active[flat_thread] +=
                block_active[flat_thread + stride];
        }
        __syncthreads();
    }
    if (flat_thread == 0 && block_active[0] != 0U) {
        atomicAdd(loss_sum, block_loss[0]);
        atomicAdd(active_pixels, block_active[0]);
    }
}

template <bool mixed_mse>
__global__ void fused_ordered_l1_ssim_backward_kernel(
    const float* prediction, const float* transmittance,
    const std::uint8_t* target,
    const unsigned int* active_pixels,
    const float* backward_terms,
    float* image_gradient,
    std::uint32_t width, std::uint32_t height,
    float base_objective_weight, float mse_objective_weight,
    bool include_empty_pixels) {
    constexpr int block_width = 16;
    constexpr int block_height = 16;
    constexpr int halo = static_cast<int>(ssim_window_radius);
    constexpr int shared_width = block_width + 2 * halo;
    constexpr int shared_height = block_height + 2 * halo;
    __shared__ float derivative_tile[shared_height][shared_width][3];
    __shared__ float horizontal[shared_height][block_width][3];

    const int x =
        static_cast<int>(blockIdx.x) * block_width + threadIdx.x;
    const int y =
        static_cast<int>(blockIdx.y) * block_height + threadIdx.y;
    const bool in_bounds =
        x < static_cast<int>(width) &&
        y < static_cast<int>(height);
    const std::size_t pixel = in_bounds
        ? static_cast<std::size_t>(y) * width +
              static_cast<std::size_t>(x)
        : 0U;
    const std::size_t valid_sample_count =
        static_cast<std::size_t>(
            width - 2U * ssim_window_radius) *
        (height - 2U * ssim_window_radius) * 3U;
    const float effective_base_weight =
        mixed_mse ? base_objective_weight : 1.0F;
    const float dssim_normalizer =
        -effective_base_weight * dssim_objective_weight /
        static_cast<float>(valid_sample_count);

    for (std::uint32_t channel = 0U;
         channel < 3U; ++channel) {
        constexpr int tile_items = shared_width * shared_height;
        constexpr int block_threads = block_width * block_height;
        const int flat_thread =
            threadIdx.y * block_width + threadIdx.x;
        for (int item = flat_thread;
             item < tile_items; item += block_threads) {
            const int local_y = item / shared_width;
            const int local_x = item % shared_width;
            const int center_x =
                static_cast<int>(blockIdx.x) * block_width +
                local_x - halo;
            const int center_y =
                static_cast<int>(blockIdx.y) * block_height +
                local_y - halo;
            float values[3]{0.0F, 0.0F, 0.0F};
            if (center_x >= halo &&
                center_x + halo <
                    static_cast<int>(width) &&
                center_y >= halo &&
                center_y + halo <
                    static_cast<int>(height)) {
                const auto center_sample =
                    (static_cast<std::size_t>(center_y) *
                         width +
                     static_cast<std::size_t>(center_x)) *
                        3U +
                    channel;
                const auto term = center_sample * 5U;
                const float prediction_mean =
                    backward_terms[term];
                const float target_mean =
                    backward_terms[term + 1U];
                const float mean_term =
                    backward_terms[term + 2U];
                const float target_term =
                    backward_terms[term + 3U];
                const float prediction_term =
                    backward_terms[term + 4U];
                values[0] = dssim_normalizer *
                    (mean_term -
                     target_term * target_mean -
                     prediction_term *
                         prediction_mean);
                values[1] =
                    dssim_normalizer * target_term;
                values[2] =
                    dssim_normalizer * prediction_term;
            }
            for (int derivative = 0;
                 derivative < 3; ++derivative) {
                derivative_tile[local_y][local_x][derivative] =
                    values[derivative];
            }
        }
        __syncthreads();

        const int convolution_x = threadIdx.x + halo;
        for (int pass = 0; pass < 2; ++pass) {
            const int convolution_y =
                threadIdx.y + pass * block_height;
            if (convolution_y < shared_height) {
                float values[3]{0.0F, 0.0F, 0.0F};
#pragma unroll
                for (int offset = -halo;
                     offset <= halo; ++offset) {
                    const float weight =
                        ssim_gaussian_weights[offset + halo];
                    for (int derivative = 0;
                         derivative < 3; ++derivative) {
                        values[derivative] +=
                            weight *
                            derivative_tile[
                                convolution_y]
                                [convolution_x + offset]
                                [derivative];
                    }
                }
                for (int derivative = 0;
                     derivative < 3; ++derivative) {
                    horizontal[convolution_y]
                              [threadIdx.x][derivative] =
                        values[derivative];
                }
            }
        }
        __syncthreads();

        if (in_bounds) {
            float values[3]{0.0F, 0.0F, 0.0F};
            const int center_y = threadIdx.y + halo;
#pragma unroll
            for (int offset = -halo;
                 offset <= halo; ++offset) {
                const float weight =
                    ssim_gaussian_weights[offset + halo];
                for (int derivative = 0;
                     derivative < 3; ++derivative) {
                    values[derivative] +=
                        weight *
                        horizontal[center_y + offset]
                                  [threadIdx.x][derivative];
                }
            }
            const std::size_t sample =
                pixel * 3U + channel;
            const float predicted = prediction[sample];
            const float expected =
                static_cast<float>(target[sample]) / 255.0F;
            const float ssim_gradient =
                values[0] +
                expected * values[1] +
                predicted * values[2];
            const unsigned int active = *active_pixels;
            const bool contributes =
                active != 0U &&
                (include_empty_pixels ||
                 transmittance[pixel] < 1.0F);
            const float l1_normalizer =
                contributes
                ? effective_base_weight * l1_objective_weight /
                      (3.0F *
                       static_cast<float>(active))
                : 0.0F;
            const float difference = predicted - expected;
            const float l1_gradient =
                difference > 0.0F
                ? l1_normalizer
                : (difference < 0.0F
                       ? -l1_normalizer
                       : 0.0F);
            float gradient = l1_gradient + ssim_gradient;
            if constexpr (mixed_mse) {
                const float mse_gradient =
                    contributes
                        ? 2.0F * mse_objective_weight * difference /
                              (3.0F * static_cast<float>(active))
                        : 0.0F;
                gradient += mse_gradient;
            }
            image_gradient[sample] = gradient;
        }
        __syncthreads();
    }
}

__global__ void ssim_error_map_kernel(
    const float* ssim_values, float* error_map,
    std::uint32_t width, std::uint32_t height,
    const float* ssim_sum) {
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
    const auto valid_height =
        height - 2U * ssim_window_radius;
    const auto valid_pixel =
        static_cast<std::size_t>(y - ssim_window_radius) *
            valid_width +
        (x - ssim_window_radius);
    const float mean_ssim =
        (ssim_values[valid_pixel * 3U] +
         ssim_values[valid_pixel * 3U + 1U] +
         ssim_values[valid_pixel * 3U + 2U]) /
        3.0F;
    const float global_mean_ssim =
        *ssim_sum /
        static_cast<float>(
            static_cast<std::size_t>(valid_width) *
            valid_height * 3U);
    const float mean_error = 1.0F - global_mean_ssim;
    error_map[pixel] =
        fmaxf(0.0F, 1.0F - mean_ssim) /
        fmaxf(mean_error, 1.0e-6F);
}

__global__ void validate_training_scalars_kernel(
    const float* loss_sum, const unsigned int* active_pixels,
    const float* ssim_sum, unsigned int* error_flags) {
    if (blockIdx.x != 0U || threadIdx.x != 0U) {
        return;
    }
    unsigned int flags = 0U;
    if (*active_pixels == 0U) {
        flags |= 1U;
    }
    if (!isfinite(*loss_sum) || !isfinite(*ssim_sum)) {
        flags |= 2U;
    }
    if (flags != 0U) {
        atomicOr(error_flags, flags);
    }
}

__device__ float target_luminance(
    const std::uint8_t* target, std::uint32_t width,
    std::uint32_t x, std::uint32_t y) {
    const auto sample =
        (static_cast<std::size_t>(y) * width + x) * 3U;
    return (
        0.2126F * static_cast<float>(target[sample]) +
        0.7152F * static_cast<float>(target[sample + 1U]) +
        0.0722F * static_cast<float>(target[sample + 2U])) /
        255.0F;
}

__global__ void target_sobel_edge_map_kernel(
    const std::uint8_t* target, float* edge_map,
    std::uint32_t width, std::uint32_t height) {
    const std::size_t pixel =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    const std::size_t pixel_count =
        static_cast<std::size_t>(width) * height;
    if (pixel >= pixel_count) {
        return;
    }
    const auto x = static_cast<std::uint32_t>(pixel % width);
    const auto y = static_cast<std::uint32_t>(pixel / width);
    if (x == 0U || x + 1U >= width ||
        y == 0U || y + 1U >= height) {
        edge_map[pixel] = 0.0F;
        return;
    }
    const float upper_left =
        target_luminance(target, width, x - 1U, y - 1U);
    const float upper =
        target_luminance(target, width, x, y - 1U);
    const float upper_right =
        target_luminance(target, width, x + 1U, y - 1U);
    const float left =
        target_luminance(target, width, x - 1U, y);
    const float right =
        target_luminance(target, width, x + 1U, y);
    const float lower_left =
        target_luminance(target, width, x - 1U, y + 1U);
    const float lower =
        target_luminance(target, width, x, y + 1U);
    const float lower_right =
        target_luminance(target, width, x + 1U, y + 1U);
    const float gradient_x =
        -upper_left + upper_right -
        2.0F * left + 2.0F * right -
        lower_left + lower_right;
    const float gradient_y =
        -upper_left - 2.0F * upper - upper_right +
        lower_left + 2.0F * lower + lower_right;
    edge_map[pixel] = sqrtf(
        gradient_x * gradient_x +
        gradient_y * gradient_y);
}

__device__ std::uint64_t mrnf_splitmix64_device(std::uint64_t value) {
    value += 0x9E3779B97F4A7C15ULL;
    value = (value ^ (value >> 30U)) * 0xBF58476D1CE4E5B9ULL;
    value = (value ^ (value >> 27U)) * 0x94D049BB133111EBULL;
    return value ^ (value >> 31U);
}

__device__ float mrnf_uniform_device(std::uint64_t value) {
    const auto bits = mrnf_splitmix64_device(value);
    return fminf(
        1.0F - 1.0e-7F,
        fmaxf(
            1.0e-7F,
            static_cast<float>(bits >> 40U) *
                (1.0F / 16777216.0F)));
}

__global__ void mrnf_inject_means_noise_kernel(
    Gaussian* gaussians, std::size_t gaussian_count,
    std::uint64_t step, std::uint64_t seed,
    float position_learning_rate) {
    const std::size_t index =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (index >= gaussian_count) {
        return;
    }
    auto& gaussian = gaussians[index];
    const float opacity =
        1.0F / (1.0F + expf(-gaussian.opacity_logit));
    const float weight =
        powf(fmaxf(0.0F, 1.0F - opacity), 150.0F) *
        position_learning_rate * 50.0F;
    if (!(weight > 0.0F) || !isfinite(weight)) {
        return;
    }
    float scale[3]{
        expf(gaussian.log_scale[0]),
        expf(gaussian.log_scale[1]),
        expf(gaussian.log_scale[2]),
    };
    if (scale[0] > scale[1]) {
        const float temporary = scale[0];
        scale[0] = scale[1];
        scale[1] = temporary;
    }
    if (scale[1] > scale[2]) {
        const float temporary = scale[1];
        scale[1] = scale[2];
        scale[2] = temporary;
    }
    if (scale[0] > scale[1]) {
        const float temporary = scale[0];
        scale[0] = scale[1];
        scale[1] = temporary;
    }
    const float clamp_scale = fmaxf(scale[1], 1.0e-12F);
    for (std::size_t axis = 0U; axis < 3U; ++axis) {
        const auto key =
            seed ^ (step * 0xD2B74407B1CE6E93ULL) ^
            (static_cast<std::uint64_t>(index) *
             0x9E3779B97F4A7C15ULL) ^
            (static_cast<std::uint64_t>(axis) *
             0x94D049BB133111EBULL);
        const float u1 = mrnf_uniform_device(key);
        const float u2 = mrnf_uniform_device(
            key ^ 0xBF58476D1CE6E93ULL);
        const float normal =
            sqrtf(-2.0F * logf(u1)) *
            cosf(6.283185307179586F * u2);
        const float noise = fminf(
            clamp_scale,
            fmaxf(-clamp_scale, normal * weight));
        const float candidate = gaussian.xyz[axis] + noise;
        if (isfinite(candidate)) {
            gaussian.xyz[axis] = candidate;
        }
    }
}

__global__ void mrnf_apply_decay_kernel(
    Gaussian* gaussians, std::size_t gaussian_count,
    float train_fraction) {
    const std::size_t index =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (index >= gaussian_count) {
        return;
    }
    auto& gaussian = gaussians[index];
    const float remaining =
        1.0F - fminf(1.0F, fmaxf(0.0F, train_fraction));
    const float opacity =
        1.0F / (1.0F + expf(-gaussian.opacity_logit));
    const float decayed_opacity = fminf(
        1.0F - 1.0e-6F,
        fmaxf(1.0e-6F, opacity - 0.004F * remaining));
    gaussian.opacity_logit =
        logf(decayed_opacity / (1.0F - decayed_opacity));
    const float scale_factor =
        fmaxf(1.0e-6F, 1.0F - 0.002F * remaining);
    const float log_scale_decay = logf(scale_factor);
    for (std::size_t axis = 0U; axis < 3U; ++axis) {
        gaussian.log_scale[axis] += log_scale_decay;
    }
}

__global__ void collect_refinement_statistics_kernel(
    const float* frame_refinement_weight,
    const float* frame_visibility_weight,
    const float* frame_edge_weight,
    const float* frame_abs_projected_gradient,
    float* refine_weight_max, float* visibility_count,
    float* edge_weight_sum, float* absgrad_sum,
    float* absgrad_observation_count,
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
        edge_weight_sum[index] += frame_edge_weight[index];
    }
    const float abs_x =
        frame_abs_projected_gradient[index * 2U];
    const float abs_y =
        frame_abs_projected_gradient[index * 2U + 1U];
    const float abs_norm = hypotf(abs_x, abs_y);
    if (abs_norm > 0.0F && isfinite(abs_norm)) {
        absgrad_sum[index] += abs_norm;
        absgrad_observation_count[index] += 1.0F;
    }
}

__global__ void split_gaussians_long_axis_kernel(
    Gaussian* gaussians, const std::uint32_t* parent_indices,
    const std::uint32_t* child_indices,
    std::size_t parent_count,
    float* first_dc, float* second_dc,
    float2* sh_rest_moments,
    float* first_opacity, float* second_opacity,
    float2* opacity_sh_moments,
    float* first_xyz, float* second_xyz,
    float* first_log_scale, float* second_log_scale,
    float* first_rotation, float* second_rotation,
    float* refine_weight_max, float* visibility_count,
    float* edge_weight_sum, float* absgrad_sum,
    float* absgrad_observation_count) {
    const std::size_t split =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (split >= parent_count) {
        return;
    }
    const std::size_t parent_index = parent_indices[split];
    const std::size_t child_index = child_indices[split];
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
    for (std::size_t coefficient = 0U;
         coefficient < maximum_sh_rest_values; ++coefficient) {
        sh_rest_moments[
            parent_index * maximum_sh_rest_values + coefficient] =
            make_float2(0.0F, 0.0F);
        sh_rest_moments[
            child_index * maximum_sh_rest_values + coefficient] =
            make_float2(0.0F, 0.0F);
    }
    first_opacity[parent_index] = 0.0F;
    second_opacity[parent_index] = 0.0F;
    first_opacity[child_index] = 0.0F;
    second_opacity[child_index] = 0.0F;
    for (std::size_t coefficient = 0U;
         opacity_sh_moments != nullptr &&
         coefficient < maximum_opacity_sh_coefficients; ++coefficient) {
        opacity_sh_moments[
            parent_index * maximum_opacity_sh_coefficients + coefficient] =
            make_float2(0.0F, 0.0F);
        opacity_sh_moments[
            child_index * maximum_opacity_sh_coefficients + coefficient] =
            make_float2(0.0F, 0.0F);
    }
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
    edge_weight_sum[parent_index] = 0.0F;
    edge_weight_sum[child_index] = 0.0F;
    absgrad_sum[parent_index] = 0.0F;
    absgrad_sum[child_index] = 0.0F;
    absgrad_observation_count[parent_index] = 0.0F;
    absgrad_observation_count[child_index] = 0.0F;
}

struct DeviceOptimizerTelemetry {
    float gradient_squared[5];
    float update_squared[5];
    float parameter_squared[5];
    unsigned int samples[5];
};

template <std::uint32_t ActiveCoefficients, std::size_t LanesPerGaussian>
__global__ void ordered_sh_adam_update_kernel(
    Gaussian* gaussians, std::size_t gaussian_count,
    const float* sh_rest_gradient,
    const float* opacity_sh_gradient,
    const float* dc_gradient,
    const float* opacity_gradient,
    const float* projected_sh_basis,
    float2* sh_rest_moments,
    float2* opacity_sh_moments,
    float inverse_bias_first, float inverse_bias_second,
    float sh_rest_learning_rate, float opacity_sh_learning_rate,
    float dc_epsilon, float opacity_epsilon,
    bool expand_fastgs_gradients) {
    static_assert(
        ActiveCoefficients == 3U || ActiveCoefficients == 8U ||
        ActiveCoefficients == 15U);
    static_assert(
        LanesPerGaussian == ActiveCoefficients * 3U ||
        LanesPerGaussian == ActiveCoefficients * 4U);
    const std::size_t item_index =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    const std::size_t item_count = gaussian_count * LanesPerGaussian;
    if (item_index >= item_count) {
        return;
    }
    constexpr float beta_first = 0.9F;
    constexpr float beta_second = 0.999F;
    const std::size_t gaussian_index = item_index / LanesPerGaussian;
    const std::size_t local_index = item_index % LanesPerGaussian;
    constexpr std::size_t active_items = LanesPerGaussian;
    if (local_index >= active_items) {
        return;
    }
    constexpr std::size_t color_item_count = ActiveCoefficients * 3U;
    if (local_index < color_item_count) {
        const std::size_t channel = local_index / ActiveCoefficients;
        const std::size_t coefficient = local_index % ActiveCoefficients;
        const auto offset =
            gaussian_index * maximum_sh_rest_values +
            channel * maximum_sh_rest_coefficients + coefficient;
        const float gradient = expand_fastgs_gradients
            ? projected_sh_basis[
                  gaussian_index * 16U + coefficient + 1U] *
                dc_gradient[gaussian_index * 3U + channel]
            : sh_rest_gradient[offset];
        auto moments = sh_rest_moments[offset];
        moments.x =
            beta_first * moments.x +
            (1.0F - beta_first) * gradient;
        moments.y =
            beta_second * moments.y +
            (1.0F - beta_second) * gradient * gradient;
        sh_rest_moments[offset] = moments;
        gaussians[gaussian_index].sh_rest[
            channel * maximum_sh_rest_coefficients + coefficient] -=
            sh_rest_learning_rate *
            moments.x * inverse_bias_first /
            (sqrtf(moments.y * inverse_bias_second) +
             dc_epsilon);
        return;
    }
    if constexpr (LanesPerGaussian == ActiveCoefficients * 3U) {
        return;
    }
    const std::size_t coefficient = local_index - color_item_count;
    const auto offset =
        gaussian_index * maximum_opacity_sh_coefficients + coefficient;
    const float gradient = expand_fastgs_gradients
        ? projected_sh_basis[
              gaussian_index * 16U + coefficient + 1U] *
            opacity_gradient[gaussian_index]
        : opacity_sh_gradient[offset];
    auto moments = opacity_sh_moments[offset];
    moments.x =
        beta_first * moments.x +
        (1.0F - beta_first) * gradient;
    moments.y =
        beta_second * moments.y +
        (1.0F - beta_second) * gradient * gradient;
    opacity_sh_moments[offset] = moments;
    gaussians[gaussian_index].opacity_sh[coefficient] -=
        opacity_sh_learning_rate *
        moments.x * inverse_bias_first /
        (sqrtf(moments.y * inverse_bias_second) +
         opacity_epsilon);
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
    float position_learning_rate, float color_learning_rate,
    float opacity_learning_rate, float scale_learning_rate,
    float rotation_learning_rate,
    float position_epsilon, float dc_epsilon,
    float opacity_epsilon, float scale_epsilon,
    float rotation_epsilon,
    float minimum_log_scale, float maximum_log_scale,
    DeviceOptimizerTelemetry* telemetry,
    std::size_t telemetry_stride,
    bool expand_fastgs_gradients) {
    const std::size_t gaussian_index =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (gaussian_index >= gaussian_count) {
        return;
    }
    constexpr float beta_first = 0.9F;
    constexpr float beta_second = 0.999F;
    const bool collect_telemetry =
        telemetry != nullptr &&
        gaussian_index % telemetry_stride == 0U;
    float gradient_squared[5]{};
    float update_squared[5]{};
    float parameter_squared[5]{};
    float original_rotation[4]{};
    if (collect_telemetry) {
        for (std::size_t component = 0U; component < 4U; ++component) {
            original_rotation[component] =
                gaussians[gaussian_index].rotation[component];
        }
    }
    for (std::size_t channel = 0U; channel < 3U; ++channel) {
        const auto offset = gaussian_index * 3U + channel;
        const float gradient = dc_gradient[offset] *
            (expand_fastgs_gradients ? sh_c0 : 1.0F);
        const float original = gaussians[gaussian_index].dc[channel];
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
            (sqrtf(corrected_second) + dc_epsilon);
        if (collect_telemetry) {
            const float applied =
                gaussians[gaussian_index].dc[channel] - original;
            gradient_squared[0] +=
                isfinite(gradient) ? gradient * gradient : 0.0F;
            update_squared[0] += applied * applied;
            parameter_squared[0] +=
                gaussians[gaussian_index].dc[channel] *
                gaussians[gaussian_index].dc[channel];
        }
    }
    const float opacity = opacity_gradient[gaussian_index];
    const float original_opacity =
        gaussians[gaussian_index].opacity_logit;
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
        (sqrtf(corrected_second) + opacity_epsilon);
    if (collect_telemetry) {
        const float applied =
            gaussians[gaussian_index].opacity_logit -
            original_opacity;
        gradient_squared[1] =
            isfinite(opacity) ? opacity * opacity : 0.0F;
        update_squared[1] = applied * applied;
        parameter_squared[1] =
            gaussians[gaussian_index].opacity_logit *
            gaussians[gaussian_index].opacity_logit;
    }
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
             position_epsilon);
        const float original_position =
            gaussians[gaussian_index].xyz[axis];
        const float candidate_position =
            original_position - position_update;
        if (isfinite(candidate_position)) {
            gaussians[gaussian_index].xyz[axis] =
                candidate_position;
        }
        if (collect_telemetry) {
            const float applied =
                gaussians[gaussian_index].xyz[axis] -
                original_position;
            gradient_squared[2] +=
                position_gradient * position_gradient;
            update_squared[2] += applied * applied;
            parameter_squared[2] +=
                gaussians[gaussian_index].xyz[axis] *
                gaussians[gaussian_index].xyz[axis];
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
                 scale_epsilon);
        const float original_scale =
            gaussians[gaussian_index].log_scale[axis];
        const float candidate_scale =
            original_scale - scale_update;
        if (isfinite(candidate_scale)) {
            gaussians[gaussian_index].log_scale[axis] =
                fminf(
                    maximum_log_scale,
                    fmaxf(minimum_log_scale, candidate_scale));
        }
        if (collect_telemetry) {
            const float applied =
                gaussians[gaussian_index].log_scale[axis] -
                original_scale;
            gradient_squared[3] +=
                scale_gradient * scale_gradient;
            update_squared[3] += applied * applied;
            parameter_squared[3] +=
                gaussians[gaussian_index].log_scale[axis] *
                gaussians[gaussian_index].log_scale[axis];
        }
    }

    float quaternion_norm_squared = 0.0F;
    for (std::size_t component = 0U; component < 4U; ++component) {
        const auto offset = gaussian_index * 4U + component;
        const float gradient =
            isfinite(rotation_gradient[offset])
                ? rotation_gradient[offset]
                : 0.0F;
        if (collect_telemetry) {
            gradient_squared[4] += gradient * gradient;
        }
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
             rotation_epsilon);
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
    if (collect_telemetry) {
        for (std::size_t component = 0U; component < 4U; ++component) {
            const float value =
                gaussians[gaussian_index].rotation[component];
            const float applied =
                value - original_rotation[component];
            update_squared[4] += applied * applied;
            parameter_squared[4] += value * value;
        }
        constexpr unsigned int component_counts[5]{
            3U, 1U, 3U, 3U, 4U};
        for (std::size_t family = 0U; family < 5U; ++family) {
            atomicAdd(
                &telemetry->gradient_squared[family],
                gradient_squared[family]);
            atomicAdd(
                &telemetry->update_squared[family],
                update_squared[family]);
            atomicAdd(
                &telemetry->parameter_squared[family],
                parameter_squared[family]);
            atomicAdd(
                &telemetry->samples[family],
                component_counts[family]);
        }
    }
}

__device__ float ordered_adam_delta(
    float gradient, float& first, float& second,
    float inverse_bias_first, float inverse_bias_second,
    float learning_rate, float epsilon) {
    constexpr float beta_first = 0.9F;
    constexpr float beta_second = 0.999F;
    first = beta_first * first + (1.0F - beta_first) * gradient;
    second = beta_second * second +
        (1.0F - beta_second) * gradient * gradient;
    return learning_rate * first * inverse_bias_first /
        (sqrtf(second * inverse_bias_second) + epsilon);
}

__global__ void ordered_scalar_adam_update_kernel(
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
    float position_learning_rate, float color_learning_rate,
    float opacity_learning_rate, float scale_learning_rate,
    float rotation_learning_rate,
    float position_epsilon, float dc_epsilon,
    float opacity_epsilon, float scale_epsilon,
    float rotation_epsilon,
    float minimum_log_scale, float maximum_log_scale,
    bool expand_fastgs_gradients) {
    constexpr std::size_t scalar_parameters = 14U;
    const std::size_t item_index =
        static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    const std::size_t item_count =
        gaussian_count * scalar_adam_lanes_per_gaussian;
    if (item_index >= item_count) {
        return;
    }
    const std::size_t gaussian_index =
        item_index / scalar_adam_lanes_per_gaussian;
    const std::size_t parameter =
        item_index % scalar_adam_lanes_per_gaussian;
    auto& gaussian = gaussians[gaussian_index];
    if (parameter < 3U) {
        const auto offset = gaussian_index * 3U + parameter;
        const float gradient = dc_gradient[offset] *
            (expand_fastgs_gradients ? sh_c0 : 1.0F);
        gaussian.dc[parameter] -= ordered_adam_delta(
            gradient, first_dc[offset], second_dc[offset],
            inverse_bias_first, inverse_bias_second,
            color_learning_rate, dc_epsilon);
    } else if (parameter == 3U) {
        gaussian.opacity_logit -= ordered_adam_delta(
            opacity_gradient[gaussian_index],
            first_opacity[gaussian_index],
            second_opacity[gaussian_index],
            inverse_bias_first, inverse_bias_second,
            opacity_learning_rate, opacity_epsilon);
    } else if (parameter < 7U) {
        const std::size_t axis = parameter - 4U;
        const auto offset = gaussian_index * 3U + axis;
        const float gradient = isfinite(xyz_gradient[offset])
            ? xyz_gradient[offset]
            : 0.0F;
        const float candidate = gaussian.xyz[axis] - ordered_adam_delta(
            gradient, first_xyz[offset], second_xyz[offset],
            inverse_bias_first, inverse_bias_second,
            position_learning_rate, position_epsilon);
        if (isfinite(candidate)) {
            gaussian.xyz[axis] = candidate;
        }
    } else if (parameter < 10U) {
        const std::size_t axis = parameter - 7U;
        const auto offset = gaussian_index * 3U + axis;
        const float gradient = isfinite(log_scale_gradient[offset])
            ? log_scale_gradient[offset]
            : 0.0F;
        const float candidate =
            gaussian.log_scale[axis] - ordered_adam_delta(
                gradient,
                first_log_scale[offset], second_log_scale[offset],
                inverse_bias_first, inverse_bias_second,
                scale_learning_rate, scale_epsilon);
        if (isfinite(candidate)) {
            gaussian.log_scale[axis] = fminf(
                maximum_log_scale,
                fmaxf(minimum_log_scale, candidate));
        }
    } else if (parameter < scalar_parameters) {
        const std::size_t component = parameter - 10U;
        const auto offset = gaussian_index * 4U + component;
        const float gradient = isfinite(rotation_gradient[offset])
            ? rotation_gradient[offset]
            : 0.0F;
        const float candidate =
            gaussian.rotation[component] - ordered_adam_delta(
                gradient, first_rotation[offset], second_rotation[offset],
                inverse_bias_first, inverse_bias_second,
                rotation_learning_rate, rotation_epsilon);
        if (isfinite(candidate)) {
            gaussian.rotation[component] = candidate;
        }
    }

    // Two padded Gaussian groups fit exactly in a warp.  Lanes 14 and 15 do
    // no Adam update but participate in the barrier and quaternion reduction,
    // allowing rotation normalization without a second model-memory pass.
    const unsigned int subgroup_mask =
        0xFFFFU << (threadIdx.x & 16U);
    __syncwarp(subgroup_mask);
    float rotation_value = 0.0F;
    if (parameter >= 10U && parameter < scalar_parameters) {
        rotation_value = gaussian.rotation[parameter - 10U];
    }
    const float local_squared = rotation_value * rotation_value;
    float norm_squared = __shfl_sync(
        subgroup_mask, local_squared, 10, 16);
    norm_squared += __shfl_sync(
        subgroup_mask, local_squared, 11, 16);
    norm_squared += __shfl_sync(
        subgroup_mask, local_squared, 12, 16);
    norm_squared += __shfl_sync(
        subgroup_mask, local_squared, 13, 16);
    if (parameter < 10U || parameter >= scalar_parameters) {
        return;
    }
    if (isfinite(norm_squared) && norm_squared > 1.0e-12F) {
        gaussian.rotation[parameter - 10U] =
            rotation_value * rsqrtf(norm_squared);
        return;
    }
    gaussian.rotation[parameter - 10U] =
        parameter == 10U ? 1.0F : 0.0F;
}

template <typename Key, typename Value>
cudaError_t sort_pairs_portable(
    void* temporary_storage, std::size_t& temporary_bytes,
    Key* keys_in, Key* keys_out, Value* values_in, Value* values_out,
    int item_count, int begin_bit = 0,
    int end_bit = static_cast<int>(sizeof(Key) * 8U)) {
    return cub::DeviceRadixSort::SortPairs(
        temporary_storage, temporary_bytes, keys_in, keys_out,
        values_in, values_out, item_count, begin_bit, end_bit);
}

void sort_projected_records(
    std::uint64_t* keys_in, std::uint64_t* keys_out,
    DeviceProjectedRecord* records_in, DeviceProjectedRecord* records_out,
    int item_count) {
    std::size_t temporary_bytes = 0U;
    require_cuda(
        sort_pairs_portable(
            nullptr, temporary_bytes, keys_in, keys_out,
            records_in, records_out, item_count),
        "query compact projected splat sort storage");
    DeviceAllocation<std::uint8_t> temporary(temporary_bytes);
    require_cuda(
        sort_pairs_portable(
            temporary.data(), temporary_bytes, keys_in, keys_out,
            records_in, records_out, item_count),
        "sort compact projected splats by depth");
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
        sort_pairs_portable(
            nullptr, temporary_bytes, keys_in, keys_out,
            indices_in, indices_out, item_count),
        "query tile pair sort storage");
    DeviceAllocation<std::uint8_t> temporary(temporary_bytes);
    require_cuda(
        sort_pairs_portable(
            temporary.data(), temporary_bytes, keys_in, keys_out,
            indices_in, indices_out, item_count),
        "sort tile/splat pairs");
}

std::uint64_t checkpoint_checksum(
    const std::filesystem::path& path,
    std::uint64_t byte_count) {
    constexpr std::uint64_t offset_basis = 14695981039346656037ULL;
    constexpr std::uint64_t prime = 1099511628211ULL;
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        throw std::runtime_error(
            "cannot read checkpoint for checksum: " + path.string());
    }
    std::uint64_t hash = offset_basis;
    std::array<char, 64U * 1024U> buffer{};
    while (byte_count != 0U) {
        const auto requested = static_cast<std::streamsize>(
            std::min<std::uint64_t>(buffer.size(), byte_count));
        stream.read(buffer.data(), requested);
        const auto read = stream.gcount();
        if (read <= 0) {
            throw std::runtime_error(
                "checkpoint checksum source is truncated");
        }
        for (std::streamsize index = 0; index < read; ++index) {
            hash ^= static_cast<unsigned char>(
                buffer[static_cast<std::size_t>(index)]);
            hash *= prime;
        }
        byte_count -= static_cast<std::uint64_t>(read);
    }
    return hash;
}

void sync_checkpoint_file(const std::filesystem::path& path) {
#ifndef _WIN32
    const int descriptor = ::open(path.c_str(), O_RDONLY);
    if (descriptor < 0) {
        throw std::runtime_error(
            "cannot open checkpoint for fsync: " + path.string());
    }
    const int result = ::fsync(descriptor);
    ::close(descriptor);
    if (result != 0) {
        throw std::runtime_error(
            "cannot fsync checkpoint: " + path.string());
    }
#else
    static_cast<void>(path);
#endif
}

void sync_checkpoint_directory(const std::filesystem::path& path) {
#ifndef _WIN32
    const int descriptor =
        ::open(path.parent_path().c_str(), O_RDONLY | O_DIRECTORY);
    if (descriptor >= 0) {
        static_cast<void>(::fsync(descriptor));
        ::close(descriptor);
    }
#else
    static_cast<void>(path);
#endif
}

}  // namespace

static AlphaRenderBackwardOutput render_alpha_cuda_impl(
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera,
    const std::array<float, 3>& background,
    const std::vector<float>* image_gradient,
    std::uint32_t active_sh_degree) {
    validate_inputs(gaussians, camera, background);
    if (active_sh_degree > maximum_sh_degree) {
        throw std::invalid_argument("active SH degree must be between 0 and 3");
    }
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
            gradients.sh_rest = std::vector<
                std::array<float, maximum_sh_rest_values>>(
                    gaussians.size());
            gradients.opacity_logit =
                std::vector<float>(gaussians.size(), 0.0F);
            gradients.opacity_sh = std::vector<
                std::array<float, maximum_opacity_sh_coefficients>>(
                    gaussians.size());
            gradients.xyz =
                std::vector<std::array<float, 3>>(gaussians.size());
            gradients.log_scale =
                std::vector<std::array<float, 3>>(gaussians.size());
            gradients.rotation =
                std::vector<std::array<float, 4>>(gaussians.size());
        }
        return AlphaRenderBackwardOutput{
            .render =
                render_alpha_reference(
                    {}, camera, background, active_sh_degree),
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
    DeviceAllocation<float> device_projected_sh_basis(
        gaussians.size() * 16U);
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
        device_records.data(), device_projected_sh_basis.data(),
        device_depth_keys.data(),
        device_visible_splats.data(), active_sh_degree,
        active_sh_degree, 0.0F, false);
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
        camera.width, camera.height,
        device_pair_counts.data(), true);
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
        device_pair_offsets.data(), camera.width, camera.height,
        device_camera.tiles_x,
        device_tile_depth_keys.data(), device_record_indices.data(), true);
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
    DeviceAllocation<std::uint64_t> device_active_ends(pixel_count);
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
        alpha_maximum,
        background[0], background[1], background[2],
        device_rgb.data(), device_transmittance.data(),
        device_active_ends.data(),
        nullptr, nullptr, nullptr, nullptr,
        device_stats.data());
    require_cuda(cudaGetLastError(), "launch tiled alpha renderer");

    std::optional<DeviceAllocation<float>> device_image_gradient;
    std::optional<DeviceAllocation<float>> device_dc_gradient;
    std::optional<DeviceAllocation<float>> device_sh_rest_gradient;
    std::optional<DeviceAllocation<float>> device_opacity_logit_gradient;
    std::optional<DeviceAllocation<float>> device_opacity_sh_gradient;
    std::optional<DeviceAllocation<float>>
        device_projected_geometry_gradient;
    std::optional<DeviceAllocation<float>> device_xyz_gradient;
    std::optional<DeviceAllocation<float>> device_log_scale_gradient;
    std::optional<DeviceAllocation<float>> device_rotation_gradient;
    if (image_gradient != nullptr) {
        device_image_gradient.emplace(image_gradient->size());
        device_dc_gradient.emplace(gaussians.size() * 3U);
        device_sh_rest_gradient.emplace(
            gaussians.size() * maximum_sh_rest_values);
        device_opacity_logit_gradient.emplace(gaussians.size());
        device_opacity_sh_gradient.emplace(
            gaussians.size() * maximum_opacity_sh_coefficients);
        device_projected_geometry_gradient.emplace(
            gaussians.size() * 6U);
        device_xyz_gradient.emplace(gaussians.size() * 3U);
        device_log_scale_gradient.emplace(gaussians.size() * 3U);
        device_rotation_gradient.emplace(gaussians.size() * 4U);
        device_image_gradient->copy_from_host(image_gradient->data());
        device_dc_gradient->zero();
        device_sh_rest_gradient->zero();
        device_opacity_logit_gradient->zero();
        device_opacity_sh_gradient->zero();
        device_projected_geometry_gradient->zero();
        device_xyz_gradient->zero();
        device_log_scale_gradient->zero();
        device_rotation_gradient->zero();
        backward_alpha_tiles_kernel<<<render_blocks, render_threads>>>(
            device_sorted_records.data(),
            device_sorted_depth_keys.data(),
            device_projected_sh_basis.data(),
            device_sorted_record_indices.data(),
            device_tile_starts.data(), device_tile_ends.data(),
            camera.width, camera.height,
            alpha_maximum,
            background[0], background[1], background[2],
            device_transmittance.data(), device_active_ends.data(),
            device_image_gradient->data(), device_dc_gradient->data(),
            device_sh_rest_gradient->data(), active_sh_degree,
            device_opacity_logit_gradient->data(),
            device_opacity_sh_gradient->data(),
            active_sh_degree,
            device_projected_geometry_gradient->data(),
            nullptr, nullptr, nullptr, nullptr, nullptr, nullptr);
        require_cuda(cudaGetLastError(), "launch tiled alpha backward");
        backward_projected_geometry_kernel<<<
            projection_blocks, threads_per_block>>>(
            device_gaussians.data(), gaussian_count, device_camera,
            0.0F, false,
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
        gradients.sh_rest = std::vector<
            std::array<float, maximum_sh_rest_values>>(gaussians.size());
        gradients.opacity_logit =
            std::vector<float>(gaussians.size(), 0.0F);
        gradients.opacity_sh = std::vector<
            std::array<float, maximum_opacity_sh_coefficients>>(
                gaussians.size());
        gradients.xyz =
            std::vector<std::array<float, 3>>(gaussians.size());
        gradients.log_scale =
            std::vector<std::array<float, 3>>(gaussians.size());
        gradients.rotation =
            std::vector<std::array<float, 4>>(gaussians.size());
        device_dc_gradient->copy_to_host(gradients.dc.front().data());
        device_sh_rest_gradient->copy_to_host(
            gradients.sh_rest.front().data());
        device_opacity_logit_gradient->copy_to_host(
            gradients.opacity_logit.data());
        device_opacity_sh_gradient->copy_to_host(
            gradients.opacity_sh.front().data());
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
    const std::array<float, 3>& background,
    std::uint32_t active_sh_degree) {
    return render_alpha_cuda_impl(
               gaussians, camera, background, nullptr,
               active_sh_degree)
        .render;
}

AlphaRenderBackwardOutput render_alpha_tiled_cuda_backward(
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera,
    const std::vector<float>& image_gradient,
    const std::array<float, 3>& background,
    std::uint32_t active_sh_degree) {
    return render_alpha_cuda_impl(
        gaussians, camera, background, &image_gradient,
        active_sh_degree);
}

static std::uint64_t splitmix64(std::uint64_t value) {
    value += 0x9E3779B97F4A7C15ULL;
    value = (value ^ (value >> 30U)) * 0xBF58476D1CE4E5B9ULL;
    value = (value ^ (value >> 27U)) * 0x94D049BB133111EBULL;
    return value ^ (value >> 31U);
}

static double deterministic_gumbel(
    std::uint64_t seed, std::uint32_t source_index) {
    const auto bits = splitmix64(
        seed ^
        (static_cast<std::uint64_t>(source_index) *
         0xD2B74407B1CE6E93ULL));
    constexpr double inverse_two_to_53 =
        1.0 / 9007199254740992.0;
    const double uniform =
        (static_cast<double>(bits >> 11U) + 0.5) *
        inverse_two_to_53;
    return -std::log(-std::log(uniform));
}

static float positive_median(std::vector<float> values) {
    values.erase(
        std::remove_if(
            values.begin(), values.end(),
            [](float value) {
                return !std::isfinite(value) || value <= 0.0F;
            }),
        values.end());
    if (values.empty()) {
        return 0.0F;
    }
    const auto middle = values.size() / 2U;
    std::nth_element(
        values.begin(), values.begin() + middle, values.end());
    const float upper = values[middle];
    if (values.size() % 2U != 0U) {
        return upper;
    }
    const float lower = *std::max_element(
        values.begin(), values.begin() + middle);
    return 0.5F * (lower + upper);
}

static float percentile80_median_size(
    const std::vector<Gaussian>& gaussians) {
    if (gaussians.empty()) {
        return 0.0F;
    }
    const auto low_index = static_cast<std::size_t>(
        0.1 * static_cast<double>(gaussians.size() - 1U));
    const auto high_index = static_cast<std::size_t>(
        0.9 * static_cast<double>(gaussians.size() - 1U));
    std::array<float, 3> widths{};
    std::vector<float> values(gaussians.size());
    for (std::size_t axis = 0U; axis < 3U; ++axis) {
        for (std::size_t index = 0U;
             index < gaussians.size(); ++index) {
            values[index] = gaussians[index].xyz[axis];
        }
        std::nth_element(
            values.begin(), values.begin() + low_index, values.end());
        const float lower = values[low_index];
        std::nth_element(
            values.begin(), values.begin() + high_index, values.end());
        const float upper = values[high_index];
        widths[axis] = std::max(0.0F, upper - lower);
    }
    std::sort(widths.begin(), widths.end());
    return widths[1U];
}

static float bounding_box_diagonal(
    const std::vector<Gaussian>& gaussians) {
    std::array<float, 3> minimum{
        std::numeric_limits<float>::max(),
        std::numeric_limits<float>::max(),
        std::numeric_limits<float>::max(),
    };
    std::array<float, 3> maximum{
        std::numeric_limits<float>::lowest(),
        std::numeric_limits<float>::lowest(),
        std::numeric_limits<float>::lowest(),
    };
    for (const auto& gaussian : gaussians) {
        for (std::size_t axis = 0U; axis < 3U; ++axis) {
            minimum[axis] = std::min(minimum[axis], gaussian.xyz[axis]);
            maximum[axis] = std::max(maximum[axis], gaussian.xyz[axis]);
        }
    }
    double squared = 0.0;
    for (std::size_t axis = 0U; axis < 3U; ++axis) {
        const double extent =
            static_cast<double>(maximum[axis]) - minimum[axis];
        squared += extent * extent;
    }
    return static_cast<float>(
        std::max(std::sqrt(squared), 1.0e-6));
}

static MrnfLearningRates mrnf_learning_rates(
    std::uint64_t optimizer_step, std::uint64_t maximum_steps,
    float position_scale, MrnfOptimizerProfile profile) {
    const bool reference_all = uses_reference_absolute_optimizer(profile);
    const bool calibrated_dc_opacity =
        profile == MrnfOptimizerProfile::calibrated_dc_005_opacity ||
        profile == MrnfOptimizerProfile::calibrated_dc_010_opacity ||
        profile == MrnfOptimizerProfile::calibrated_dc_020_opacity ||
        profile == MrnfOptimizerProfile::calibrated_dc_010_opacity_024 ||
        profile == MrnfOptimizerProfile::calibrated_dc_010_opacity_048 ||
        profile == MrnfOptimizerProfile::calibrated_dc_010_opacity_096 ||
        profile == MrnfOptimizerProfile::dev34_opacity096_reference_scale ||
        profile == MrnfOptimizerProfile::dev34_opacity096_reference_rotation ||
        profile ==
            MrnfOptimizerProfile::dev34_opacity096_reference_scale_rotation ||
        profile ==
            MrnfOptimizerProfile::
                dev35_opacity096_reference_scale_staged_rotation004 ||
        profile ==
            MrnfOptimizerProfile::
                dev35_opacity096_reference_scale_staged_rotation008 ||
        profile ==
            MrnfOptimizerProfile::dev36_staged_rotation008_absgrad025 ||
        profile ==
            MrnfOptimizerProfile::dev36_staged_rotation008_absgrad050 ||
        profile ==
            MrnfOptimizerProfile::
                dev37_staged_rotation008_absgrad050_aa005 ||
        profile ==
            MrnfOptimizerProfile::
                dev37_staged_rotation008_absgrad050_aa015 ||
        profile ==
            MrnfOptimizerProfile::
                dev37_staged_rotation008_absgrad050_aa030 ||
        profile ==
            MrnfOptimizerProfile::
                dev38_staged_rotation008_absgrad050_fastgs;
    const bool reference_dc =
        reference_all ||
        profile == MrnfOptimizerProfile::reference_dc_only ||
        profile == MrnfOptimizerProfile::reference_dc_opacity ||
        calibrated_dc_opacity;
    const bool reference_position =
        reference_all ||
        profile == MrnfOptimizerProfile::reference_position_only;
    const bool reference_opacity =
        reference_all ||
        profile == MrnfOptimizerProfile::reference_opacity_only ||
        profile == MrnfOptimizerProfile::reference_dc_opacity ||
        calibrated_dc_opacity;
    const bool reference_scale =
        reference_all ||
        profile == MrnfOptimizerProfile::reference_scale_only ||
        profile == MrnfOptimizerProfile::dev34_opacity096_reference_scale ||
        profile ==
            MrnfOptimizerProfile::dev34_opacity096_reference_scale_rotation ||
        profile ==
            MrnfOptimizerProfile::
                dev35_opacity096_reference_scale_staged_rotation004 ||
        profile ==
            MrnfOptimizerProfile::
                dev35_opacity096_reference_scale_staged_rotation008 ||
        profile ==
            MrnfOptimizerProfile::dev36_staged_rotation008_absgrad025 ||
        profile ==
            MrnfOptimizerProfile::dev36_staged_rotation008_absgrad050 ||
        profile ==
            MrnfOptimizerProfile::
                dev37_staged_rotation008_absgrad050_aa005 ||
        profile ==
            MrnfOptimizerProfile::
                dev37_staged_rotation008_absgrad050_aa015 ||
        profile ==
            MrnfOptimizerProfile::
                dev37_staged_rotation008_absgrad050_aa030 ||
        profile ==
            MrnfOptimizerProfile::
                dev38_staged_rotation008_absgrad050_fastgs;
    const bool staged_rotation004 =
        profile ==
        MrnfOptimizerProfile::
            dev35_opacity096_reference_scale_staged_rotation004;
    const bool staged_rotation008 =
        profile ==
        MrnfOptimizerProfile::
            dev35_opacity096_reference_scale_staged_rotation008 ||
        profile ==
            MrnfOptimizerProfile::dev36_staged_rotation008_absgrad025 ||
        profile ==
            MrnfOptimizerProfile::dev36_staged_rotation008_absgrad050 ||
        profile ==
            MrnfOptimizerProfile::
                dev37_staged_rotation008_absgrad050_aa005 ||
        profile ==
            MrnfOptimizerProfile::
                dev37_staged_rotation008_absgrad050_aa015 ||
        profile ==
            MrnfOptimizerProfile::
                dev37_staged_rotation008_absgrad050_aa030 ||
        profile ==
            MrnfOptimizerProfile::
                dev38_staged_rotation008_absgrad050_fastgs;
    const bool staged_rotation =
        staged_rotation004 || staged_rotation008;
    const bool reference_rotation =
        reference_all ||
        profile == MrnfOptimizerProfile::reference_rotation_only ||
        profile == MrnfOptimizerProfile::dev34_opacity096_reference_rotation ||
        profile ==
            MrnfOptimizerProfile::dev34_opacity096_reference_scale_rotation ||
        staged_rotation;
    const double reference_progress =
        optimizer_step <= 1U || maximum_steps == 0U
            ? 0.0
            : std::min(
                  1.0,
                  static_cast<double>(optimizer_step - 1U) /
                      static_cast<double>(maximum_steps));
    const double dev16_progress =
        maximum_steps <= 1U
            ? 0.0
            : std::min(
                  1.0,
                  static_cast<double>(optimizer_step - 1U) /
                      static_cast<double>(maximum_steps - 1U));
    const auto exponential = [](
        double progress,
        float initial, float final) {
        return static_cast<float>(std::exp(
            std::log(static_cast<double>(initial)) *
                (1.0 - progress) +
            std::log(static_cast<double>(final)) * progress));
    };
    float dc_learning_rate = reference_dc
        ? mrnf_dc_learning_rate
        : dev16_dc_learning_rate;
    if (profile ==
        MrnfOptimizerProfile::calibrated_dc_005_opacity) {
        dc_learning_rate = 5.0e-3F;
    } else if (
        profile ==
            MrnfOptimizerProfile::calibrated_dc_010_opacity ||
        profile ==
            MrnfOptimizerProfile::calibrated_dc_010_opacity_024 ||
        profile ==
            MrnfOptimizerProfile::calibrated_dc_010_opacity_048 ||
        profile ==
            MrnfOptimizerProfile::calibrated_dc_010_opacity_096 ||
        profile ==
            MrnfOptimizerProfile::dev34_opacity096_reference_scale ||
        profile ==
            MrnfOptimizerProfile::dev34_opacity096_reference_rotation ||
        profile ==
            MrnfOptimizerProfile::dev34_opacity096_reference_scale_rotation ||
        staged_rotation) {
        dc_learning_rate = 1.0e-2F;
    } else if (
        profile ==
        MrnfOptimizerProfile::calibrated_dc_020_opacity) {
        dc_learning_rate = 2.0e-2F;
    }
    float opacity_learning_rate = reference_opacity
        ? mrnf_opacity_learning_rate
        : dev16_opacity_learning_rate;
    if (profile ==
        MrnfOptimizerProfile::calibrated_dc_010_opacity_024) {
        opacity_learning_rate = 2.4e-2F;
    } else if (
        profile ==
        MrnfOptimizerProfile::calibrated_dc_010_opacity_048) {
        opacity_learning_rate = 4.8e-2F;
    } else if (
        profile ==
        MrnfOptimizerProfile::calibrated_dc_010_opacity_096) {
        opacity_learning_rate = 9.6e-2F;
    } else if (
        profile == MrnfOptimizerProfile::dev34_opacity096_reference_scale ||
        profile == MrnfOptimizerProfile::dev34_opacity096_reference_rotation ||
        profile ==
            MrnfOptimizerProfile::dev34_opacity096_reference_scale_rotation ||
        staged_rotation) {
        opacity_learning_rate = 9.6e-2F;
    }
    float rotation_learning_rate = reference_rotation
        ? mrnf_rotation_learning_rate
        : dev16_rotation_learning_rate;
    if (staged_rotation && dev16_progress < 0.4) {
        rotation_learning_rate = dev16_rotation_learning_rate;
    } else if (staged_rotation004) {
        rotation_learning_rate = 4.0e-3F;
    } else if (staged_rotation008) {
        rotation_learning_rate = 8.0e-3F;
    }
    return {
        .position =
            position_scale * exponential(
                reference_position
                    ? reference_progress
                    : dev16_progress,
                reference_position
                    ? mrnf_position_learning_rate_initial
                    : dev16_position_learning_rate_initial,
                reference_position
                    ? mrnf_position_learning_rate_final
                    : dev16_position_learning_rate_final),
        .dc = dc_learning_rate,
        .opacity = opacity_learning_rate,
        .scale = reference_scale
            ? exponential(
                  reference_progress,
                  mrnf_scale_learning_rate_initial,
                  mrnf_scale_learning_rate_final)
            : dev16_scale_learning_rate,
        .rotation = rotation_learning_rate,
        .position_epsilon = reference_position
            ? mrnf_adam_epsilon
            : dev16_adam_epsilon,
        .dc_epsilon = reference_dc
            ? mrnf_adam_epsilon
            : dev16_adam_epsilon,
        .opacity_epsilon = reference_opacity
            ? mrnf_adam_epsilon
            : dev16_adam_epsilon,
        .scale_epsilon = reference_scale
            ? mrnf_adam_epsilon
            : dev16_adam_epsilon,
        .rotation_epsilon = reference_rotation
            ? mrnf_adam_epsilon
            : dev16_adam_epsilon,
    };
}

static float antialias_filter_variance_for_profile(
    MrnfOptimizerProfile profile) {
    if (profile ==
        MrnfOptimizerProfile::
            dev37_staged_rotation008_absgrad050_aa005) {
        return dev37_antialias_filter_variance_005;
    }
    if (profile ==
        MrnfOptimizerProfile::
            dev37_staged_rotation008_absgrad050_aa015) {
        return dev37_antialias_filter_variance_015;
    }
    if (profile ==
        MrnfOptimizerProfile::
            dev37_staged_rotation008_absgrad050_aa030) {
        return dev37_antialias_filter_variance_030;
    }
    return 0.0F;
}

struct OrderedAlphaTrainingContext::Impl {
    Impl(
        const std::vector<Gaussian>& initial_gaussians,
        std::size_t maximum_pixel_count,
        std::uint64_t requested_maximum_steps,
        std::size_t requested_gaussian_capacity,
        MrnfOptimizerProfile requested_optimizer_profile,
        std::uint32_t requested_maximum_sh_degree,
        std::uint32_t requested_sh_degree_interval,
        std::uint64_t requested_noise_seed,
        std::optional<bool> fastgs_compatibility_override,
        float requested_maximum_scale_growth_factor,
        bool requested_opacity_sh_enabled)
        : gaussian_count(initial_gaussians.size()),
          gaussian_capacity(std::max(
              initial_gaussians.size(),
              requested_gaussian_capacity)),
          maximum_pixels(maximum_pixel_count),
          maximum_steps(std::max<std::uint64_t>(
              1U, requested_maximum_steps)),
          noise_until_iteration(
              topology_growth_end_iteration(maximum_steps)),
          optimizer_profile(requested_optimizer_profile),
          antialias_filter_variance(
              antialias_filter_variance_for_profile(
                  requested_optimizer_profile)),
          fastgs_compatibility(
              fastgs_compatibility_override.value_or(
                  requested_optimizer_profile ==
                  MrnfOptimizerProfile::
                      dev38_staged_rotation008_absgrad050_fastgs)),
          maximum_active_sh_degree(requested_maximum_sh_degree),
          opacity_sh_enabled(requested_opacity_sh_enabled),
          sh_degree_interval(requested_sh_degree_interval),
          noise_seed(requested_noise_seed) {
        if (gaussian_count == 0U ||
            gaussian_capacity >
                static_cast<std::size_t>(
                    std::numeric_limits<int>::max() - 1)) {
            throw std::invalid_argument(
                "ordered training requires a supported Gaussian count");
        }
        if (maximum_active_sh_degree > maximum_sh_degree ||
            sh_degree_interval == 0U) {
            throw std::invalid_argument(
                "ordered training requires a valid progressive SH schedule");
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
        projected_sh_basis.ensure(gaussian_capacity * 16U);
        depth_keys.ensure(gaussian_capacity);
        pair_counts.ensure(gaussian_capacity + 1U);
        pair_offsets.ensure(gaussian_capacity + 1U);
        visible_splats.ensure(1U);
        target.ensure(maximum_pixels * 3U);
        rgb.ensure(maximum_pixels * 3U);
        transmittance.ensure(maximum_pixels);
        active_ends.ensure(maximum_pixels);
        pixel_contributions.ensure(maximum_pixels);
        image_gradient.ensure(maximum_pixels * 3U);
        loss_sum.ensure(1U);
        active_pixels.ensure(1U);
        metric_values.ensure(maximum_pixels * 3U);
        metric_horizontal_moments.ensure(maximum_pixels * 15U);
        ssim_backward_terms.ensure(maximum_pixels * 15U);
        densification_error_map.ensure(maximum_pixels);
        densification_edge_map.ensure(maximum_pixels);
        metric_sum.ensure(1U);
        deferred_error_flags.ensure(1U);
        dc_gradient.ensure(gaussian_capacity * 3U);
        sh_rest_gradient.ensure(
            gaussian_capacity * maximum_sh_rest_values);
        opacity_gradient.ensure(gaussian_capacity);
        if (opacity_sh_enabled) {
            opacity_sh_gradient.ensure(
                gaussian_capacity * maximum_opacity_sh_coefficients);
        }
        projected_geometry_gradient.ensure(gaussian_capacity * 6U);
        xyz_gradient.ensure(gaussian_capacity * 3U);
        log_scale_gradient.ensure(gaussian_capacity * 3U);
        rotation_gradient.ensure(gaussian_capacity * 4U);
        first_dc.ensure(gaussian_capacity * 3U);
        second_dc.ensure(gaussian_capacity * 3U);
        sh_rest_moments.ensure(
            gaussian_capacity * maximum_sh_rest_values);
        first_opacity.ensure(gaussian_capacity);
        second_opacity.ensure(gaussian_capacity);
        if (opacity_sh_enabled) {
            opacity_sh_moments.ensure(
                gaussian_capacity * maximum_opacity_sh_coefficients);
        }
        first_xyz.ensure(gaussian_capacity * 3U);
        second_xyz.ensure(gaussian_capacity * 3U);
        first_log_scale.ensure(gaussian_capacity * 3U);
        second_log_scale.ensure(gaussian_capacity * 3U);
        first_rotation.ensure(gaussian_capacity * 4U);
        second_rotation.ensure(gaussian_capacity * 4U);
        refine_weight_max.ensure(gaussian_capacity);
        visibility_count.ensure(gaussian_capacity);
        edge_weight_sum.ensure(gaussian_capacity);
        absgrad_sum.ensure(gaussian_capacity);
        absgrad_observation_count.ensure(gaussian_capacity);
        refinement_indices.ensure(gaussian_capacity);
        refinement_destinations.ensure(gaussian_capacity);
        frame_refinement_weight.ensure(gaussian_capacity);
        frame_visibility_weight.ensure(gaussian_capacity);
        frame_edge_weight.ensure(gaussian_capacity);
        frame_abs_projected_gradient.ensure(gaussian_capacity * 2U);
        optimizer_telemetry.ensure(1U);
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
                initial_minimum_log_scale = std::min(
                    initial_minimum_log_scale,
                    gaussian.log_scale[axis]);
                initial_maximum_log_scale = std::max(
                    initial_maximum_log_scale,
                    gaussian.log_scale[axis]);
            }
        }
        position_learning_rate_scale =
            uses_reference_absolute_optimizer(optimizer_profile) ||
                optimizer_profile ==
                    MrnfOptimizerProfile::reference_position_only
                ? percentile80_median_size(initial_gaussians)
                : bounding_box_diagonal(initial_gaussians);
        learning_rates = mrnf_learning_rates(
            1U, maximum_steps, position_learning_rate_scale,
            optimizer_profile);
        if (!std::isfinite(requested_maximum_scale_growth_factor) ||
            requested_maximum_scale_growth_factor < 1.0F) {
            throw std::invalid_argument(
                "maximum scale growth factor must be finite and at least one");
        }
        minimum_log_scale = initial_minimum_log_scale - 4.0F;
        maximum_log_scale = initial_maximum_log_scale +
            std::log(requested_maximum_scale_growth_factor);
        gaussians.copy_from_host(
            initial_gaussians.data(), gaussian_count);
        first_dc.zero(gaussian_count * 3U);
        second_dc.zero(gaussian_count * 3U);
        sh_rest_moments.zero(
            gaussian_count * maximum_sh_rest_values);
        first_opacity.zero(gaussian_count);
        second_opacity.zero(gaussian_count);
        if (opacity_sh_enabled) {
            opacity_sh_moments.zero(
                gaussian_count * maximum_opacity_sh_coefficients);
        }
        first_xyz.zero(gaussian_count * 3U);
        second_xyz.zero(gaussian_count * 3U);
        first_log_scale.zero(gaussian_count * 3U);
        second_log_scale.zero(gaussian_count * 3U);
        first_rotation.zero(gaussian_count * 4U);
        second_rotation.zero(gaussian_count * 4U);
        refine_weight_max.zero(gaussian_capacity);
        visibility_count.zero(gaussian_capacity);
        edge_weight_sum.zero(gaussian_capacity);
        absgrad_sum.zero(gaussian_capacity);
        absgrad_observation_count.zero(gaussian_capacity);
        frame_refinement_weight.zero(gaussian_capacity);
        frame_visibility_weight.zero(gaussian_capacity);
        frame_edge_weight.zero(gaussian_capacity);
        frame_abs_projected_gradient.zero(gaussian_capacity * 2U);
        optimizer_telemetry.zero(1U);
        deferred_error_flags.zero(1U);
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

    void sort_pairs(
        std::uint32_t pair_items, std::size_t tile_count) {
        if (tile_count == 0U ||
            tile_count >
                static_cast<std::size_t>(
                    std::numeric_limits<std::uint32_t>::max())) {
            throw std::invalid_argument(
                "tile/depth sort requires a supported tile count");
        }
        const auto tile_bits = std::bit_width(
            static_cast<std::uint32_t>(tile_count - 1U));
        const int end_bit = 32 + static_cast<int>(tile_bits);
        std::size_t temporary_bytes = 0U;
        require_cuda(
            sort_pairs_portable(
                nullptr, temporary_bytes,
                tile_depth_keys.data(),
                sorted_tile_depth_keys.data(),
                record_indices.data(),
                sorted_record_indices.data(),
                static_cast<int>(pair_items), 0, end_bit),
            "query persistent pair sort storage");
        temporary_storage.ensure(std::max<std::size_t>(
            1U, temporary_bytes));
        require_cuda(
            sort_pairs_portable(
                temporary_storage.data(), temporary_bytes,
                tile_depth_keys.data(),
                sorted_tile_depth_keys.data(),
                record_indices.data(),
                sorted_record_indices.data(),
                static_cast<int>(pair_items), 0, end_bit),
            "sort persistent tile pairs");
    }

    std::uint32_t build_fastgs_buckets(std::size_t tile_count) {
        if (tile_count == 0U ||
            tile_count >
                static_cast<std::size_t>(
                    std::numeric_limits<int>::max())) {
            throw std::invalid_argument(
                "FastGS bucket construction requires a valid tile count");
        }
        bucket_counts.ensure(tile_count);
        bucket_offsets.ensure(tile_count);
        tile_max_contributions.ensure(tile_count);
        const auto blocks = static_cast<std::uint32_t>(
            (tile_count + threads_per_block - 1U) /
            threads_per_block);
        extract_bucket_counts_kernel<<<blocks, threads_per_block>>>(
            tile_starts.data(), tile_ends.data(),
            bucket_counts.data(), tile_count);
        require_cuda(
            cudaGetLastError(),
            "launch FastGS bucket count extraction");

        std::size_t temporary_bytes = 0U;
        require_cuda(
            cub::DeviceScan::InclusiveSum(
                nullptr, temporary_bytes,
                bucket_counts.data(), bucket_offsets.data(),
                static_cast<int>(tile_count)),
            "query FastGS bucket scan storage");
        temporary_storage.ensure(std::max<std::size_t>(
            1U, temporary_bytes));
        require_cuda(
            cub::DeviceScan::InclusiveSum(
                temporary_storage.data(), temporary_bytes,
                bucket_counts.data(), bucket_offsets.data(),
                static_cast<int>(tile_count)),
            "scan FastGS bucket counts");

        std::uint32_t bucket_count = 0U;
        require_cuda(
            cudaMemcpy(
                &bucket_count,
                bucket_offsets.data() + tile_count - 1U,
                sizeof(bucket_count), cudaMemcpyDeviceToHost),
            "copy FastGS bucket count");
        if (bucket_count == 0U) {
            throw std::runtime_error(
                "FastGS bucket construction produced no buckets");
        }
        bucket_tiles.ensure(bucket_count);
        bucket_checkpoints.ensure(
            static_cast<std::size_t>(bucket_count) *
            alpha_tile_width * alpha_tile_height);
        initialize_bucket_tiles_kernel<<<blocks, threads_per_block>>>(
            bucket_offsets.data(), bucket_tiles.data(), tile_count);
        require_cuda(
            cudaGetLastError(),
            "launch FastGS bucket tile initialization");
        return bucket_count;
    }

    void reduce_values_to_device(
        const float* values, std::size_t value_count) {
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
                values, metric_sum.data(),
                static_cast<int>(value_count)),
            "query ordered quality reduction storage");
        temporary_storage.ensure(std::max<std::size_t>(
            1U, temporary_bytes));
        require_cuda(
            cub::DeviceReduce::Sum(
                temporary_storage.data(), temporary_bytes,
                values, metric_sum.data(),
                static_cast<int>(value_count)),
            "reduce ordered quality values");
    }

    float reduce_values(
        const float* values, std::size_t value_count) {
        reduce_values_to_device(values, value_count);
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
        ImageObjectiveOutput* objective,
        float mse_blend,
        bool readback_metrics = true,
        RefinementStatisticsMode refinement_statistics =
            RefinementStatisticsMode::collect,
        const ImageObjectivePolicy& objective_policy = {}) {
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
        const bool collect_refinement_statistics =
            compute_gradient && apply_update &&
            refinement_statistics == RefinementStatisticsMode::collect;
        if (!readback_metrics &&
            (quality != nullptr || prediction != nullptr ||
             objective != nullptr || !apply_update)) {
            throw std::logic_error(
                "deferred metric readback is valid only for training updates");
        }
        if (!std::isfinite(mse_blend) ||
            mse_blend < 0.0F || mse_blend > 1.0F) {
            throw std::invalid_argument(
                "ordered training MSE blend must be between 0 and 1");
        }
        for (const float channel : objective_policy.background) {
            if (!std::isfinite(channel) || channel < 0.0F || channel > 1.0F) {
                throw std::invalid_argument(
                    "ordered training background channels must be finite and between 0 and 1");
            }
        }
        const auto telemetry_step = optimizer_steps + 1U;
        const auto telemetry_interval =
            std::max<std::uint64_t>(1U, maximum_steps / 5U);
        const bool collect_stage_telemetry =
            apply_update &&
            (telemetry_step == 2U ||
             (maximum_steps > 1U &&
              telemetry_step == maximum_steps - 1U) ||
             (telemetry_step > 1U &&
              (telemetry_step - 1U) % telemetry_interval == 0U));
        if (apply_update && !collect_stage_telemetry) {
            latest_gpu_stage_telemetry.reset();
        }
        GpuStageTimer stage_timer(collect_stage_telemetry);
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
            records.data(), projected_sh_basis.data(), depth_keys.data(),
            visible_splats.data(),
            active_sh_degree,
            opacity_sh_enabled ? active_sh_degree : 0U,
            antialias_filter_variance,
            fastgs_compatibility);
        require_cuda(
            cudaGetLastError(),
            "launch persistent alpha projection");
        stage_timer.mark(1U);
        stage_timer.mark(2U);
        const auto count_blocks =
            (gaussian_items + 1U + block_size - 1U) / block_size;
        extract_pair_counts_kernel<<<count_blocks, block_size>>>(
            records.data(), gaussian_items,
            camera.width, camera.height, pair_counts.data(),
            !fastgs_compatibility);
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
            records.data(), gaussian_items,
            pair_offsets.data(), camera.width, camera.height,
            device_camera.tiles_x,
            tile_depth_keys.data(), record_indices.data(),
            !fastgs_compatibility);
        require_cuda(
            cudaGetLastError(),
            "launch persistent tile pair duplication");
        stage_timer.mark(3U);
        const auto tile_count =
            static_cast<std::size_t>(device_camera.tiles_x) *
            device_camera.tiles_y;
        sort_pairs(pair_items, tile_count);
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
        stage_timer.mark(4U);

        const bool use_structural_fastgs =
            fastgs_compatibility && compute_gradient;
        const std::uint32_t fastgs_bucket_count =
            use_structural_fastgs
            ? build_fastgs_buckets(tile_count)
            : 0U;
        stage_timer.mark(5U);
        loss_sum.zero(1U);
        active_pixels.zero(1U);
        const dim3 render_threads(
            alpha_tile_width, alpha_tile_height);
        const dim3 render_blocks(
            device_camera.tiles_x, device_camera.tiles_y);
        render_alpha_tiles_kernel<<<render_blocks, render_threads>>>(
            records.data(), sorted_record_indices.data(),
            tile_starts.data(), tile_ends.data(),
            camera.width, camera.height,
            fastgs_compatibility
                ? fastgs_maximum_fragment_alpha
                : alpha_maximum,
            objective_policy.background[0],
            objective_policy.background[1],
            objective_policy.background[2],
            rgb.data(), transmittance.data(),
            active_ends.data(),
            use_structural_fastgs ? bucket_offsets.data() : nullptr,
            use_structural_fastgs ? pixel_contributions.data() : nullptr,
            use_structural_fastgs
                ? tile_max_contributions.data()
                : nullptr,
            use_structural_fastgs ? bucket_checkpoints.data() : nullptr,
            nullptr);
        require_cuda(
            cudaGetLastError(),
            "launch persistent ordered renderer");
        stage_timer.mark(6U);
        const auto pixel_blocks = static_cast<std::uint32_t>(
            (pixel_count + block_size - 1U) / block_size);
        if (use_structural_fastgs) {
            fused_ordered_l1_ssim_forward_kernel<<<
                render_blocks, render_threads>>>(
                rgb.data(), transmittance.data(), target.data(),
                loss_sum.data(), active_pixels.data(),
                metric_values.data(),
                compute_gradient
                    ? ssim_backward_terms.data()
                    : nullptr,
                camera.width, camera.height,
                objective_policy.include_empty_pixels);
            require_cuda(
                cudaGetLastError(),
                "launch fused ordered L1/SSIM forward");
        } else {
            ordered_l1_loss_kernel<<<pixel_blocks, block_size>>>(
                rgb.data(), transmittance.data(), target.data(),
                loss_sum.data(), active_pixels.data(), pixel_count,
                objective_policy.include_empty_pixels);
            require_cuda(
                cudaGetLastError(),
                "launch persistent ordered L1 loss");
        }

        float host_loss_sum = 0.0F;
        unsigned int host_active_pixels = 0U;
        if (readback_metrics) {
            loss_sum.copy_to_host(&host_loss_sum, 1U);
            active_pixels.copy_to_host(&host_active_pixels, 1U);
            if (host_active_pixels == 0U) {
                throw std::runtime_error(
                    "ordered training render has no active pixels");
            }
        }
        const float normalizer = readback_metrics
            ? 1.0F /
                  (3.0F * static_cast<float>(host_active_pixels))
            : 0.0F;
        const std::size_t sample_count = pixel_count * 3U;
        const auto sample_blocks = static_cast<std::uint32_t>(
            (sample_count + block_size - 1U) / block_size);
        const std::size_t valid_sample_count =
            static_cast<std::size_t>(
                camera.width - 2U * ssim_window_radius) *
            (camera.height - 2U * ssim_window_radius) * 3U;
        if (!use_structural_fastgs) {
            horizontal_ssim_moments_kernel<<<
                sample_blocks, block_size>>>(
                rgb.data(), target.data(),
                metric_horizontal_moments.data(),
                camera.width, camera.height);
            require_cuda(
                cudaGetLastError(),
                "launch ordered horizontal SSIM moments");
            const auto valid_blocks = static_cast<std::uint32_t>(
                (valid_sample_count + block_size - 1U) / block_size);
            ssim_values_kernel<<<valid_blocks, block_size>>>(
                metric_horizontal_moments.data(),
                metric_values.data(),
                compute_gradient
                    ? ssim_backward_terms.data()
                    : nullptr,
                camera.width, camera.height);
            require_cuda(
                cudaGetLastError(),
                "launch ordered SSIM objective");
        }
        reduce_values_to_device(
            metric_values.data(), valid_sample_count);
        validate_training_scalars_kernel<<<1U, 1U>>>(
            loss_sum.data(), active_pixels.data(), metric_sum.data(),
            deferred_error_flags.data());
        require_cuda(
            cudaGetLastError(),
            "launch ordered training scalar validation");
        float ssim_sum = 0.0F;
        if (readback_metrics) {
            unsigned int error_flags = 0U;
            deferred_error_flags.copy_to_host(&error_flags, 1U);
            if ((error_flags & 1U) != 0U) {
                throw std::runtime_error(
                    "ordered training render has no active pixels");
            }
            if ((error_flags & 2U) != 0U) {
                throw std::runtime_error(
                    "ordered training produced non-finite device metrics");
            }
            metric_sum.copy_to_host(&ssim_sum, 1U);
        }
        const float mean_ssim = readback_metrics
            ? ssim_sum / static_cast<float>(valid_sample_count)
            : 0.0F;
        const float l1_loss = host_loss_sum * normalizer;
        const float baseline_objective_loss = readback_metrics
            ? l1_objective_weight * l1_loss +
                  dssim_objective_weight * (1.0F - mean_ssim)
            : 0.0F;
        float objective_loss = baseline_objective_loss;
        if (collect_refinement_statistics) {
            ssim_error_map_kernel<<<pixel_blocks, block_size>>>(
                metric_values.data(),
                densification_error_map.data(),
                camera.width, camera.height,
                metric_sum.data());
            require_cuda(
                cudaGetLastError(),
                "launch ordered normalized SSIM error map");
            target_sobel_edge_map_kernel<<<pixel_blocks, block_size>>>(
                target.data(), densification_edge_map.data(),
                camera.width, camera.height);
            require_cuda(
                cudaGetLastError(),
                "launch ordered target Sobel edge map");
        }
        if (mse_blend > 0.0F && !apply_update) {
            active_squared_error_values_kernel<<<
                sample_blocks, block_size>>>(
                rgb.data(), transmittance.data(), target.data(),
                metric_horizontal_moments.data(), sample_count,
                objective_policy.include_empty_pixels);
            require_cuda(
                cudaGetLastError(),
                "launch ordered active squared-error objective");
            const float mse_loss =
                reduce_values(
                    metric_horizontal_moments.data(), sample_count) *
                normalizer;
            objective_loss =
                (1.0F - mse_blend) * baseline_objective_loss +
                mse_blend * mse_loss;
        }
        const float base_objective_weight = 1.0F - mse_blend;
        if (readback_metrics && !std::isfinite(objective_loss)) {
            throw std::runtime_error(
                "ordered objective produced a non-finite loss");
        }
        if (quality != nullptr) {
            squared_error_values_kernel<<<sample_blocks, block_size>>>(
                rgb.data(), target.data(), metric_values.data(),
                sample_count);
            require_cuda(
                cudaGetLastError(),
                "launch ordered squared-error metric");
            const float squared_error_sum =
                reduce_values(metric_values.data(), sample_count);
            const float mean_squared_error = fmaxf(
                squared_error_sum /
                    static_cast<float>(sample_count),
                1.0e-10F);

            quality->mse = mean_squared_error;
            quality->psnr =
                10.0F * std::log10(1.0F / mean_squared_error);
            quality->ssim = mean_ssim;
            quality->active_pixel_fraction =
                static_cast<float>(host_active_pixels) /
                static_cast<float>(pixel_count);
            if (!std::isfinite(quality->mse) ||
                !std::isfinite(quality->psnr) ||
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
        stage_timer.mark(7U);
        if (compute_gradient) {
            if (use_structural_fastgs) {
                if (mse_blend > 0.0F) {
                    fused_ordered_l1_ssim_backward_kernel<true><<<
                        render_blocks, render_threads>>>(
                        rgb.data(), transmittance.data(),
                        target.data(), active_pixels.data(),
                        ssim_backward_terms.data(),
                        image_gradient.data(),
                        camera.width, camera.height,
                        base_objective_weight, mse_blend,
                        objective_policy.include_empty_pixels);
                } else {
                    fused_ordered_l1_ssim_backward_kernel<false><<<
                        render_blocks, render_threads>>>(
                        rgb.data(), transmittance.data(),
                        target.data(), active_pixels.data(),
                        ssim_backward_terms.data(),
                        image_gradient.data(),
                        camera.width, camera.height,
                        1.0F, 0.0F,
                        objective_policy.include_empty_pixels);
                }
            } else {
                if (mse_blend > 0.0F) {
                    ordered_objective_gradient_kernel<true><<<
                        pixel_blocks, block_size>>>(
                        rgb.data(), transmittance.data(),
                        target.data(), active_pixels.data(),
                        ssim_backward_terms.data(),
                        image_gradient.data(),
                        camera.width, camera.height,
                        base_objective_weight, mse_blend,
                        objective_policy.include_empty_pixels);
                } else {
                    ordered_objective_gradient_kernel<false><<<
                        pixel_blocks, block_size>>>(
                        rgb.data(), transmittance.data(),
                        target.data(), active_pixels.data(),
                        ssim_backward_terms.data(),
                        image_gradient.data(),
                        camera.width, camera.height,
                        1.0F, 0.0F,
                        objective_policy.include_empty_pixels);
                }
            }
            require_cuda(
                cudaGetLastError(),
                use_structural_fastgs
                    ? "launch fused ordered L1/SSIM backward"
                    : "launch persistent ordered objective gradient");
            stage_timer.mark(8U);
            dc_gradient.zero(gaussian_count * 3U);
            opacity_gradient.zero(gaussian_count);
            if (!use_structural_fastgs) {
                sh_rest_gradient.zero(
                    gaussian_count * maximum_sh_rest_values);
                if (opacity_sh_enabled) {
                    opacity_sh_gradient.zero(
                        gaussian_count * maximum_opacity_sh_coefficients);
                }
            }
            projected_geometry_gradient.zero(gaussian_count * 6U);
            if (collect_refinement_statistics) {
                frame_refinement_weight.zero(gaussian_count);
                frame_visibility_weight.zero(gaussian_count);
                frame_edge_weight.zero(gaussian_count);
                frame_abs_projected_gradient.zero(gaussian_count * 2U);
            }
            xyz_gradient.zero(gaussian_count * 3U);
            log_scale_gradient.zero(gaussian_count * 3U);
            rotation_gradient.zero(gaussian_count * 4U);
            stage_timer.mark(9U);
            if (use_structural_fastgs) {
                const auto fastgs_backward_blocks =
                    fastgs_bucket_count /
                        fastgs_backward_warps_per_block +
                    (fastgs_bucket_count %
                             fastgs_backward_warps_per_block !=
                         0U
                        ? 1U
                        : 0U);
                backward_alpha_buckets_kernel<<<
                    fastgs_backward_blocks,
                    fastgs_backward_warps_per_block *
                        fastgs_checkpoint_interval>>>(
                    records.data(),
                    depth_keys.data(),
                    sorted_record_indices.data(),
                    tile_starts.data(), tile_ends.data(),
                    bucket_offsets.data(),
                    bucket_tiles.data(),
                    bucket_checkpoints.data(),
                    tile_max_contributions.data(),
                    pixel_contributions.data(),
                    fastgs_bucket_count,
                    camera.width, camera.height,
                    fastgs_maximum_fragment_alpha,
                    rgb.data(), image_gradient.data(),
                    dc_gradient.data(),
                    opacity_gradient.data(),
                    projected_geometry_gradient.data(),
                    collect_refinement_statistics
                        ? densification_error_map.data()
                        : nullptr,
                    collect_refinement_statistics
                        ? densification_edge_map.data()
                        : nullptr,
                    collect_refinement_statistics
                        ? frame_refinement_weight.data()
                        : nullptr,
                    collect_refinement_statistics
                        ? frame_visibility_weight.data()
                        : nullptr,
                    collect_refinement_statistics
                        ? frame_edge_weight.data()
                        : nullptr,
                    collect_refinement_statistics
                        ? frame_abs_projected_gradient.data()
                        : nullptr);
                require_cuda(
                    cudaGetLastError(),
                    "launch FastGS bucket backward");
            } else {
                backward_alpha_tiles_kernel<<<
                    render_blocks, render_threads>>>(
                    records.data(), depth_keys.data(),
                    projected_sh_basis.data(),
                    sorted_record_indices.data(),
                    tile_starts.data(), tile_ends.data(),
                    camera.width, camera.height,
                    alpha_maximum,
                    0.0F, 0.0F, 0.0F,
                    transmittance.data(), active_ends.data(),
                    image_gradient.data(), dc_gradient.data(),
                    sh_rest_gradient.data(), active_sh_degree,
                    opacity_gradient.data(),
                    opacity_sh_gradient.data(),
                    opacity_sh_enabled ? active_sh_degree : 0U,
                    projected_geometry_gradient.data(),
                    collect_refinement_statistics
                        ? densification_error_map.data()
                        : nullptr,
                    collect_refinement_statistics
                        ? densification_edge_map.data()
                        : nullptr,
                    collect_refinement_statistics
                        ? frame_refinement_weight.data()
                        : nullptr,
                    collect_refinement_statistics
                        ? frame_visibility_weight.data()
                        : nullptr,
                    collect_refinement_statistics
                        ? frame_edge_weight.data()
                        : nullptr,
                    collect_refinement_statistics
                        ? frame_abs_projected_gradient.data()
                        : nullptr);
                require_cuda(
                    cudaGetLastError(),
                    "launch bounded ordered backward");
            }
            stage_timer.mark(10U);
            if (collect_refinement_statistics) {
                collect_refinement_statistics_kernel<<<
                    gaussian_blocks, block_size>>>(
                    frame_refinement_weight.data(),
                    frame_visibility_weight.data(),
                    frame_edge_weight.data(),
                    frame_abs_projected_gradient.data(),
                    refine_weight_max.data(),
                    visibility_count.data(),
                    edge_weight_sum.data(),
                    absgrad_sum.data(),
                    absgrad_observation_count.data(),
                    gaussian_count);
                require_cuda(
                    cudaGetLastError(),
                    "launch persistent refinement statistics");
            }
            backward_projected_geometry_kernel<<<
                gaussian_blocks, block_size>>>(
                gaussians.data(), gaussian_items, device_camera,
                antialias_filter_variance,
                fastgs_compatibility,
                projected_geometry_gradient.data(),
                xyz_gradient.data(), log_scale_gradient.data(),
                rotation_gradient.data());
            require_cuda(
                cudaGetLastError(),
                "launch persistent geometry backward");
            stage_timer.mark(11U);
            if (apply_update) {
                beta_first_power *= 0.9F;
                beta_second_power *= 0.999F;
                ++optimizer_steps;
                const float inverse_bias_first =
                    1.0F / (1.0F - beta_first_power);
                const float inverse_bias_second =
                    1.0F / (1.0F - beta_second_power);
                learning_rates = mrnf_learning_rates(
                    optimizer_steps, maximum_steps,
                    position_learning_rate_scale,
                    optimizer_profile);
                const bool collect_telemetry =
                    optimizer_steps == 1U ||
                    optimizer_steps == maximum_steps ||
                    optimizer_steps % telemetry_interval == 0U;
                const auto telemetry_stride =
                    std::max<std::size_t>(
                        1U, gaussian_count / 4096U);
                if (collect_telemetry) {
                    optimizer_telemetry.zero(1U);
                } else {
                    latest_telemetry.reset();
                }
                if (collect_telemetry) {
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
                        learning_rates.position,
                        learning_rates.dc,
                        learning_rates.opacity,
                        learning_rates.scale,
                        learning_rates.rotation,
                        learning_rates.position_epsilon,
                        learning_rates.dc_epsilon,
                        learning_rates.opacity_epsilon,
                        learning_rates.scale_epsilon,
                        learning_rates.rotation_epsilon,
                        minimum_log_scale, maximum_log_scale,
                        optimizer_telemetry.data(), telemetry_stride,
                        use_structural_fastgs);
                    require_cuda(
                        cudaGetLastError(),
                        "launch telemetry ordered Adam");
                } else {
                    static_assert(
                        block_size % scalar_adam_lanes_per_gaussian == 0U);
                    const std::size_t scalar_item_count =
                        gaussian_count * scalar_adam_lanes_per_gaussian;
                    const auto scalar_blocks =
                        static_cast<std::uint32_t>(
                            (scalar_item_count + block_size - 1U) /
                            block_size);
                    ordered_scalar_adam_update_kernel<<<
                        scalar_blocks, block_size>>>(
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
                        learning_rates.position,
                        learning_rates.dc,
                        learning_rates.opacity,
                        learning_rates.scale,
                        learning_rates.rotation,
                        learning_rates.position_epsilon,
                        learning_rates.dc_epsilon,
                        learning_rates.opacity_epsilon,
                        learning_rates.scale_epsilon,
                        learning_rates.rotation_epsilon,
                        minimum_log_scale, maximum_log_scale,
                        use_structural_fastgs);
                    require_cuda(
                        cudaGetLastError(),
                        "launch component-parallel ordered Adam");
                }
                stage_timer.mark(12U);
                const std::uint32_t active_coefficients =
                    (active_sh_degree + 1U) *
                        (active_sh_degree + 1U) -
                    1U;
                if (active_coefficients > 0U) {
                    const auto launch_sh_adam =
                        [&](auto active_tag, auto lane_tag) {
                            constexpr std::uint32_t active =
                                decltype(active_tag)::value;
                            constexpr std::size_t lanes =
                                decltype(lane_tag)::value;
                            const std::size_t sh_item_count =
                                gaussian_count * lanes;
                            const auto sh_blocks =
                                static_cast<std::uint32_t>(
                                    (sh_item_count + block_size - 1U) /
                                    block_size);
                            ordered_sh_adam_update_kernel<active, lanes><<<
                                sh_blocks, block_size>>>(
                                gaussians.data(), gaussian_count,
                                sh_rest_gradient.data(),
                                opacity_sh_gradient.data(),
                                dc_gradient.data(), opacity_gradient.data(),
                                projected_sh_basis.data(),
                                sh_rest_moments.data(),
                                opacity_sh_moments.data(),
                                inverse_bias_first, inverse_bias_second,
                                learning_rates.dc / 20.0F,
                                learning_rates.opacity / 20.0F,
                                learning_rates.dc_epsilon,
                                learning_rates.opacity_epsilon,
                                use_structural_fastgs);
                        };
                    if (active_coefficients == 3U) {
                        if (opacity_sh_enabled) {
                            launch_sh_adam(
                                std::integral_constant<std::uint32_t, 3U>{},
                                std::integral_constant<std::size_t, 12U>{});
                        } else {
                            launch_sh_adam(
                                std::integral_constant<std::uint32_t, 3U>{},
                                std::integral_constant<std::size_t, 9U>{});
                        }
                    } else if (active_coefficients == 8U) {
                        if (opacity_sh_enabled) {
                            launch_sh_adam(
                                std::integral_constant<std::uint32_t, 8U>{},
                                std::integral_constant<std::size_t, 32U>{});
                        } else {
                            launch_sh_adam(
                                std::integral_constant<std::uint32_t, 8U>{},
                                std::integral_constant<std::size_t, 24U>{});
                        }
                    } else if (active_coefficients == 15U) {
                        if (opacity_sh_enabled) {
                            launch_sh_adam(
                                std::integral_constant<std::uint32_t, 15U>{},
                                std::integral_constant<std::size_t, 60U>{});
                        } else {
                            launch_sh_adam(
                                std::integral_constant<std::uint32_t, 15U>{},
                                std::integral_constant<std::size_t, 45U>{});
                        }
                    } else {
                        throw std::logic_error(
                            "unsupported active SH coefficient count");
                    }
                    require_cuda(
                        cudaGetLastError(),
                        "launch degree-specialized ordered SH Adam");
                }
                stage_timer.mark(13U);
                if (optimizer_steps % sh_degree_interval == 0U &&
                    active_sh_degree < maximum_active_sh_degree) {
                    ++active_sh_degree;
                }
                if (optimizer_steps <= noise_until_iteration) {
                    mrnf_inject_means_noise_kernel<<<
                        gaussian_blocks, block_size>>>(
                        gaussians.data(), gaussian_count,
                        optimizer_steps, noise_seed,
                        learning_rates.position);
                    require_cuda(
                        cudaGetLastError(),
                        "launch deterministic MRNF means noise");
                }
                if (collect_telemetry) {
                    DeviceOptimizerTelemetry host{};
                    optimizer_telemetry.copy_to_host(&host, 1U);
                    const auto parameter = [&host](
                        std::size_t family) {
                        const auto samples =
                            static_cast<std::uint64_t>(
                                host.samples[family]);
                        const float inverse =
                            samples == 0U
                                ? 0.0F
                                : 1.0F /
                                      static_cast<float>(samples);
                        return MrnfParameterTelemetry{
                            .gradient_rms = std::sqrt(
                                host.gradient_squared[family] *
                                inverse),
                            .update_rms = std::sqrt(
                                host.update_squared[family] *
                                inverse),
                            .parameter_rms = std::sqrt(
                                host.parameter_squared[family] *
                                inverse),
                            .samples = samples,
                        };
                    };
                    latest_telemetry = MrnfOptimizerTelemetry{
                        .step = optimizer_steps,
                        .dc = parameter(0U),
                        .opacity = parameter(1U),
                        .position = parameter(2U),
                        .scale = parameter(3U),
                        .rotation = parameter(4U),
                    };
                }
                if (collect_stage_telemetry) {
                    latest_gpu_stage_telemetry =
                        stage_timer.finish(optimizer_steps);
                }
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
        float gradient_threshold, float grow_fraction,
        std::uint64_t selection_seed,
        bool spatial_pruning_bounds,
        TopologyRefinementTelemetry* telemetry) {
        TopologyPhaseTimer timer(telemetry);
        if (!std::isfinite(gradient_threshold) ||
            gradient_threshold < 0.0F ||
            !std::isfinite(grow_fraction) ||
            grow_fraction < 0.0F || grow_fraction > 1.0F) {
            throw std::invalid_argument(
                "ordered topology refinement parameters are invalid");
        }
        const auto previous_count = gaussian_count;
        const float absgrad_score_weight =
            mrnf_absgrad_score_weight(optimizer_profile);
        std::vector<Gaussian> host_gaussians(previous_count);
        std::vector<float> host_weights(previous_count);
        std::vector<float> host_visibility(previous_count);
        std::vector<float> host_edge_weights(previous_count);
        std::vector<float> host_absgrad_sum(previous_count);
        std::vector<float> host_absgrad_count(previous_count);
        timer.finish(&TopologyRefinementTelemetry::host_prepare_seconds);
        gaussians.copy_to_host(
            host_gaussians.data(), previous_count);
        refine_weight_max.copy_to_host(
            host_weights.data(), previous_count);
        visibility_count.copy_to_host(
            host_visibility.data(), previous_count);
        edge_weight_sum.copy_to_host(
            host_edge_weights.data(), previous_count);
        absgrad_sum.copy_to_host(
            host_absgrad_sum.data(), previous_count);
        absgrad_observation_count.copy_to_host(
            host_absgrad_count.data(), previous_count);
        if (telemetry) {
            telemetry->snapshot_download_bytes =
                previous_count * (sizeof(Gaussian) + 5U * sizeof(float));
        }
        timer.finish(&TopologyRefinementTelemetry::snapshot_download_seconds);

        std::array<float, 3> lower_bound{
            -std::numeric_limits<float>::infinity(),
            -std::numeric_limits<float>::infinity(),
            -std::numeric_limits<float>::infinity()};
        std::array<float, 3> upper_bound{
            std::numeric_limits<float>::infinity(),
            std::numeric_limits<float>::infinity(),
            std::numeric_limits<float>::infinity()};
        std::array<float, 3> percentile_center{};
        float percentile_max_extent = 0.0F;
        std::array<std::vector<float>, 3> coordinates_by_axis;
        for (auto& coordinates : coordinates_by_axis) {
            coordinates.reserve(previous_count);
        }
        std::vector<float> maximum_scales;
        maximum_scales.reserve(previous_count);
        for (const auto& gaussian : host_gaussians) {
            for (std::size_t axis = 0U; axis < 3U; ++axis) {
                if (std::isfinite(gaussian.xyz[axis])) {
                    coordinates_by_axis[axis].push_back(
                        gaussian.xyz[axis]);
                }
            }
            maximum_scales.push_back(std::exp(std::max({
                gaussian.log_scale[0],
                gaussian.log_scale[1],
                gaussian.log_scale[2]})));
        }
        if (previous_count >= 8U) {
            for (std::size_t axis = 0U; axis < 3U; ++axis) {
                const auto [q10, q90] = exact_floor_percentile_pair(
                    std::move(coordinates_by_axis[axis]), 0.1F, 0.9F);
                percentile_center[axis] = 0.5F * (q10 + q90);
                percentile_max_extent = std::max(
                    percentile_max_extent, 0.5F * (q90 - q10));
                const float margin =
                    3.0F * std::max(1.0e-6F, q90 - q10);
                lower_bound[axis] = q10 - margin;
                upper_bound[axis] = q90 + margin;
            }
            if (spatial_pruning_bounds) {
                const float max_allowed = std::max(
                    1.0e-10F, 100.0F * percentile_max_extent);
                for (std::size_t axis = 0U; axis < 3U; ++axis) {
                    lower_bound[axis] =
                        percentile_center[axis] - max_allowed;
                    upper_bound[axis] =
                        percentile_center[axis] + max_allowed;
                }
            }
        }
        // Spatial bounds constrain centres; they must not disable the robust
        // scale gate. The former branch replaced the p80-derived limit with a
        // scene-extent guard that was orders of magnitude looser, so large
        // finite splats were never pruned in resident runs.
        const float scale_limit = previous_count < 8U
            ? std::numeric_limits<float>::infinity()
            : std::min(
                  std::max(
                      1.0e-10F,
                      10.0F * exact_floor_percentile(
                          maximum_scales, 0.8F)),
                  spatial_pruning_bounds
                      ? std::max(
                            1.0e-10F,
                            100.0F * percentile_max_extent)
                      : std::numeric_limits<float>::infinity());
        constexpr float minimum_opacity_logit = -5.541263545158426F;
        constexpr float minimum_surviving_log_scale =
            -23.025850929940457F;
        std::vector<std::size_t> survivors;
        survivors.reserve(previous_count);
        std::vector<std::uint8_t> survivor_mask(
            previous_count, 0U);
        std::size_t pruned_non_finite = 0U;
        std::size_t pruned_opacity = 0U;
        std::size_t pruned_scale_small = 0U;
        std::size_t pruned_scale_large = 0U;
        std::size_t pruned_spatial = 0U;
        for (std::size_t index = 0U; index < previous_count; ++index) {
            const auto& gaussian = host_gaussians[index];
            bool finite = std::isfinite(gaussian.opacity_logit);
            bool spatial_outlier = false;
            for (std::size_t axis = 0U; axis < 3U; ++axis) {
                finite =
                    finite && std::isfinite(gaussian.xyz[axis]) &&
                    std::isfinite(gaussian.log_scale[axis]);
                spatial_outlier =
                    spatial_outlier ||
                    gaussian.xyz[axis] < lower_bound[axis] ||
                    gaussian.xyz[axis] > upper_bound[axis];
            }
            for (const float coefficient : gaussian.opacity_sh) {
                finite = finite && std::isfinite(coefficient);
            }
            const float maximum_scale = maximum_scales[index];
            const float minimum_log_scale = std::min({
                gaussian.log_scale[0],
                gaussian.log_scale[1],
                gaussian.log_scale[2]});
            const bool opacity =
                gaussian.opacity_logit < minimum_opacity_logit;
            const bool scale_small =
                minimum_log_scale < minimum_surviving_log_scale;
            const bool scale_large = maximum_scale > scale_limit;
            pruned_non_finite += !finite ? 1U : 0U;
            pruned_opacity += opacity ? 1U : 0U;
            pruned_scale_small += scale_small ? 1U : 0U;
            pruned_scale_large += scale_large ? 1U : 0U;
            pruned_spatial += spatial_outlier ? 1U : 0U;
            const bool prune =
                !finite || opacity || scale_small ||
                scale_large || spatial_outlier;
            if (!prune) {
                survivors.push_back(index);
                survivor_mask[index] = 1U;
            }
        }
        if (survivors.empty()) {
            const auto best = std::max_element(
                host_gaussians.begin(), host_gaussians.end(),
                [](const auto& left, const auto& right) {
                    return left.opacity_logit < right.opacity_logit;
                });
            const auto best_index = static_cast<std::size_t>(
                std::distance(host_gaussians.begin(), best));
            survivors.push_back(best_index);
            survivor_mask[best_index] = 1U;
        }
        const auto pruned = previous_count - survivors.size();
        std::size_t eligible_surviving_candidates = 0U;
        for (const auto index : survivors) {
            eligible_surviving_candidates +=
                host_visibility[index] > 0.0F &&
                        host_weights[index] > gradient_threshold
                    ? 1U
                    : 0U;
        }
        const auto requested_recycle =
            static_cast<std::size_t>(std::llround(
                static_cast<double>(
                    eligible_surviving_candidates) *
                static_cast<double>(grow_fraction)));
        const bool in_place_recycle =
            spatial_pruning_bounds &&
            previous_count == gaussian_capacity &&
            pruned != 0U &&
            requested_recycle >= pruned;
        const bool compacted = pruned != 0U && !in_place_recycle;
        timer.finish(&TopologyRefinementTelemetry::cpu_prune_seconds);
        if (compacted) {
            std::vector<Gaussian> compact_gaussians;
            compact_gaussians.reserve(survivors.size());
            std::vector<float> compact_weights;
            std::vector<float> compact_visibility;
            std::vector<float> compact_edge_weights;
            std::vector<float> compact_absgrad_sum;
            std::vector<float> compact_absgrad_count;
            compact_weights.reserve(survivors.size());
            compact_visibility.reserve(survivors.size());
            compact_edge_weights.reserve(survivors.size());
            compact_absgrad_sum.reserve(survivors.size());
            compact_absgrad_count.reserve(survivors.size());
            for (const auto source : survivors) {
                compact_gaussians.push_back(host_gaussians[source]);
                compact_weights.push_back(host_weights[source]);
                compact_visibility.push_back(host_visibility[source]);
                compact_edge_weights.push_back(host_edge_weights[source]);
                compact_absgrad_sum.push_back(
                    host_absgrad_sum[source]);
                compact_absgrad_count.push_back(
                    host_absgrad_count[source]);
            }
            const auto compact_moments = [&](
                auto& allocation, std::size_t components) {
                using value_type = std::remove_cv_t<
                    std::remove_pointer_t<
                        decltype(allocation.data())>>;
                std::vector<value_type> source(
                    previous_count * components);
                timer.finish(&TopologyRefinementTelemetry::compaction_cpu_seconds);
                allocation.copy_to_host(
                    source.data(), source.size());
                if (telemetry) {
                    telemetry->compaction_download_bytes += source.size() * sizeof(value_type);
                }
                timer.finish(&TopologyRefinementTelemetry::compaction_download_seconds);
                std::vector<value_type> compact(
                    survivors.size() * components);
                for (std::size_t destination = 0U;
                     destination < survivors.size(); ++destination) {
                    std::copy_n(
                        source.data() +
                            survivors[destination] * components,
                        components,
                        compact.data() + destination * components);
                }
                timer.finish(&TopologyRefinementTelemetry::compaction_cpu_seconds);
                allocation.copy_from_host(
                    compact.data(), compact.size());
                if (telemetry) {
                    telemetry->compaction_upload_bytes += compact.size() * sizeof(value_type);
                }
                timer.finish(&TopologyRefinementTelemetry::compaction_upload_seconds);
            };
            compact_moments(first_dc, 3U);
            compact_moments(second_dc, 3U);
            compact_moments(
                sh_rest_moments, maximum_sh_rest_values);
            compact_moments(first_opacity, 1U);
            compact_moments(second_opacity, 1U);
            if (opacity_sh_enabled) {
                compact_moments(
                    opacity_sh_moments,
                    maximum_opacity_sh_coefficients);
            }
            compact_moments(first_xyz, 3U);
            compact_moments(second_xyz, 3U);
            compact_moments(first_log_scale, 3U);
            compact_moments(second_log_scale, 3U);
            compact_moments(first_rotation, 4U);
            compact_moments(second_rotation, 4U);
            compact_moments(absgrad_sum, 1U);
            compact_moments(absgrad_observation_count, 1U);
            gaussian_count = compact_gaussians.size();
            timer.finish(&TopologyRefinementTelemetry::compaction_cpu_seconds);
            gaussians.copy_from_host(
                compact_gaussians.data(), gaussian_count);
            if (telemetry) {
                telemetry->compaction_upload_bytes += gaussian_count * sizeof(Gaussian);
            }
            timer.finish(&TopologyRefinementTelemetry::compaction_upload_seconds);
            host_gaussians = std::move(compact_gaussians);
            host_weights = std::move(compact_weights);
            host_visibility = std::move(compact_visibility);
            host_edge_weights = std::move(compact_edge_weights);
            host_absgrad_sum = std::move(compact_absgrad_sum);
            host_absgrad_count = std::move(compact_absgrad_count);
            timer.finish(&TopologyRefinementTelemetry::compaction_cpu_seconds);
        }
        std::vector<float> surviving_edge_weights;
        if (in_place_recycle) {
            surviving_edge_weights.reserve(survivors.size());
            for (const auto index : survivors) {
                surviving_edge_weights.push_back(
                    host_edge_weights[index]);
            }
        }
        const float median_edge = positive_median(
            in_place_recycle
                ? surviving_edge_weights
                : host_edge_weights);
        std::vector<float> host_absgrad_average(gaussian_count, 0.0F);
        for (std::size_t index = 0U; index < gaussian_count; ++index) {
            if (in_place_recycle &&
                survivor_mask[index] == 0U) {
                continue;
            }
            if (host_absgrad_count[index] > 0.0F &&
                std::isfinite(host_absgrad_sum[index])) {
                host_absgrad_average[index] =
                    host_absgrad_sum[index] /
                    host_absgrad_count[index];
            }
        }
        const float median_absgrad =
            positive_median(host_absgrad_average);
        struct Candidate {
            double key;
            std::uint32_t source_index;
        };
        std::vector<Candidate> candidates;
        candidates.reserve(gaussian_count);
        for (std::size_t index = 0U; index < gaussian_count; ++index) {
            if (in_place_recycle &&
                survivor_mask[index] == 0U) {
                continue;
            }
            if (host_visibility[index] > 0.0F &&
                host_weights[index] > gradient_threshold) {
                const float normalized_edge =
                    median_edge > 0.0F &&
                            std::isfinite(host_edge_weights[index])
                        ? std::max(
                              0.0F,
                              host_edge_weights[index] / median_edge)
                        : 0.0F;
                const float normalized_absgrad =
                    absgrad_score_weight > 0.0F &&
                            median_absgrad > 0.0F &&
                            std::isfinite(host_absgrad_average[index])
                        ? std::clamp(
                              host_absgrad_average[index] /
                                  median_absgrad,
                              0.0F, 4.0F)
                        : 0.0F;
                const double guided_weight =
                    static_cast<double>(host_weights[index]) *
                    static_cast<double>(
                        1.0F +
                        mrnf_edge_score_weight * normalized_edge) *
                    static_cast<double>(
                        1.0F +
                        absgrad_score_weight * normalized_absgrad);
                const auto source_index =
                    static_cast<std::uint32_t>(index);
                candidates.push_back({
                    .key =
                        std::log(std::max(
                            guided_weight,
                            std::numeric_limits<double>::min())) +
                        deterministic_gumbel(
                            selection_seed, source_index),
                    .source_index = source_index,
                });
            }
        }
        const auto candidate_order =
            [](const auto& left, const auto& right) {
                if (left.key != right.key) {
                    return left.key > right.key;
                }
                return left.source_index < right.source_index;
            };
        const auto requested = static_cast<std::size_t>(std::llround(
            static_cast<double>(candidates.size()) *
            static_cast<double>(grow_fraction)));
        const auto available =
            gaussian_capacity - gaussian_count +
            (in_place_recycle ? pruned : 0U);
        const auto added = std::min(requested, available);
        const auto physical_reused =
            in_place_recycle ? std::min(added, pruned) : 0U;
        const auto physical_appended = added - physical_reused;
        const auto reported_reused = std::min(added, pruned);
        const auto reported_appended = added - reported_reused;
        timer.finish(&TopologyRefinementTelemetry::cpu_score_seconds);
        if (added != 0U) {
            if (added < candidates.size()) {
                std::nth_element(
                    candidates.begin(), candidates.begin() + added,
                    candidates.end(), candidate_order);
            }
            std::sort(
                candidates.begin(), candidates.begin() + added,
                candidate_order);
            std::vector<std::uint32_t> parent_indices(added);
            std::vector<std::uint32_t> child_indices(added);
            std::vector<std::uint32_t> pruned_indices;
            if (in_place_recycle) {
                pruned_indices.reserve(pruned);
                for (std::size_t index = 0U;
                     index < previous_count; ++index) {
                    if (survivor_mask[index] == 0U) {
                        pruned_indices.push_back(
                            static_cast<std::uint32_t>(index));
                    }
                }
            }
            for (std::size_t split = 0U; split < added; ++split) {
                parent_indices[split] =
                    candidates[split].source_index;
                child_indices[split] =
                    split < physical_reused
                        ? pruned_indices[split]
                        : static_cast<std::uint32_t>(
                              gaussian_count + split -
                              physical_reused);
            }
            timer.finish(&TopologyRefinementTelemetry::cpu_select_seconds);
            refinement_indices.copy_from_host(
                parent_indices.data(), added);
            refinement_destinations.copy_from_host(
                child_indices.data(), added);
            if (telemetry) {
                telemetry->split_upload_bytes = added * 2U * sizeof(std::uint32_t);
            }
            timer.finish(&TopologyRefinementTelemetry::split_upload_seconds);
            constexpr std::uint32_t block_size = 256U;
            const auto blocks = static_cast<std::uint32_t>(
                (added + block_size - 1U) / block_size);
            split_gaussians_long_axis_kernel<<<blocks, block_size>>>(
                gaussians.data(), refinement_indices.data(),
                refinement_destinations.data(), added,
                first_dc.data(), second_dc.data(),
                sh_rest_moments.data(),
                first_opacity.data(), second_opacity.data(),
                opacity_sh_moments.data(),
                first_xyz.data(), second_xyz.data(),
                first_log_scale.data(), second_log_scale.data(),
                first_rotation.data(), second_rotation.data(),
                refine_weight_max.data(), visibility_count.data(),
                edge_weight_sum.data(), absgrad_sum.data(),
                absgrad_observation_count.data());
            require_cuda(
                cudaGetLastError(),
                "launch persistent long-axis Gaussian split");
            gaussian_count += physical_appended;
        }
        constexpr std::uint32_t block_size = 256U;
        const auto decay_blocks = static_cast<std::uint32_t>(
            (gaussian_count + block_size - 1U) / block_size);
        mrnf_apply_decay_kernel<<<decay_blocks, block_size>>>(
            gaussians.data(), gaussian_count,
            static_cast<float>(optimizer_steps) /
                static_cast<float>(maximum_steps));
        require_cuda(
            cudaGetLastError(),
            "launch MRNF opacity and scale decay");
        refine_weight_max.zero(gaussian_count);
        visibility_count.zero(gaussian_count);
        edge_weight_sum.zero(gaussian_count);
        absgrad_sum.zero(gaussian_count);
        absgrad_observation_count.zero(gaussian_count);
        timer.finish(&TopologyRefinementTelemetry::device_submit_seconds);
        return {
            .candidates = candidates.size(),
            .pruned = pruned,
            .pruned_non_finite = pruned_non_finite,
            .pruned_opacity = pruned_opacity,
            .pruned_scale_small = pruned_scale_small,
            .pruned_scale_large = pruned_scale_large,
            .pruned_spatial = pruned_spatial,
            .added = added,
            .reused = reported_reused,
            .appended = reported_appended,
            .gaussian_count = gaussian_count,
            .compacted = compacted,
            .in_place_recycled = in_place_recycle,
        };
    }

    std::size_t gaussian_count = 0U;
    std::size_t gaussian_capacity = 0U;
    std::size_t maximum_pixels = 0U;
    std::uint64_t maximum_steps = 1U;
    std::uint64_t noise_until_iteration = 0U;
    std::uint64_t optimizer_steps = 0U;
    std::uint32_t maximum_active_sh_degree = 0U;
    bool opacity_sh_enabled = false;
    std::uint32_t sh_degree_interval = 1000U;
    std::uint32_t active_sh_degree = 0U;
    std::uint64_t noise_seed = 0U;
    MrnfOptimizerProfile optimizer_profile =
        MrnfOptimizerProfile::dronegs_dev16;
    float antialias_filter_variance = 0.0F;
    bool fastgs_compatibility = false;
    float position_learning_rate_scale = 1.0F;
    MrnfLearningRates learning_rates{};
    std::optional<MrnfOptimizerTelemetry> latest_telemetry;
    std::optional<MrnfGpuStageTelemetry> latest_gpu_stage_telemetry;
    float minimum_log_scale = -16.0F;
    float maximum_log_scale = 16.0F;
    float beta_first_power = 1.0F;
    float beta_second_power = 1.0F;
    ReusableDeviceAllocation<Gaussian> gaussians;
    ReusableDeviceAllocation<DeviceProjectedRecord> records;
    ReusableDeviceAllocation<float> projected_sh_basis;
    ReusableDeviceAllocation<std::uint64_t> depth_keys;
    ReusableDeviceAllocation<std::uint64_t> pair_counts;
    ReusableDeviceAllocation<std::uint64_t> pair_offsets;
    ReusableDeviceAllocation<unsigned long long> visible_splats;
    ReusableDeviceAllocation<std::uint64_t> tile_depth_keys;
    ReusableDeviceAllocation<std::uint64_t> sorted_tile_depth_keys;
    ReusableDeviceAllocation<std::uint32_t> record_indices;
    ReusableDeviceAllocation<std::uint32_t> sorted_record_indices;
    ReusableDeviceAllocation<std::uint64_t> tile_starts;
    ReusableDeviceAllocation<std::uint64_t> tile_ends;
    ReusableDeviceAllocation<std::uint32_t> bucket_counts;
    ReusableDeviceAllocation<std::uint32_t> bucket_offsets;
    ReusableDeviceAllocation<std::uint32_t> bucket_tiles;
    ReusableDeviceAllocation<std::uint32_t> bucket_checkpoints;
    ReusableDeviceAllocation<std::uint32_t> pixel_contributions;
    ReusableDeviceAllocation<std::uint32_t> tile_max_contributions;
    ReusableDeviceAllocation<std::uint8_t> temporary_storage;
    ReusableDeviceAllocation<std::uint8_t> target;
    ReusableDeviceAllocation<float> rgb;
    ReusableDeviceAllocation<float> transmittance;
    ReusableDeviceAllocation<std::uint64_t> active_ends;
    ReusableDeviceAllocation<float> image_gradient;
    ReusableDeviceAllocation<float> loss_sum;
    ReusableDeviceAllocation<unsigned int> active_pixels;
    ReusableDeviceAllocation<float> metric_values;
    ReusableDeviceAllocation<float> metric_horizontal_moments;
    ReusableDeviceAllocation<float> ssim_backward_terms;
    ReusableDeviceAllocation<float> densification_error_map;
    ReusableDeviceAllocation<float> densification_edge_map;
    ReusableDeviceAllocation<float> metric_sum;
    ReusableDeviceAllocation<unsigned int> deferred_error_flags;
    ReusableDeviceAllocation<float> dc_gradient;
    ReusableDeviceAllocation<float> sh_rest_gradient;
    ReusableDeviceAllocation<float> opacity_gradient;
    ReusableDeviceAllocation<float> opacity_sh_gradient;
    ReusableDeviceAllocation<float> projected_geometry_gradient;
    ReusableDeviceAllocation<float> xyz_gradient;
    ReusableDeviceAllocation<float> log_scale_gradient;
    ReusableDeviceAllocation<float> rotation_gradient;
    ReusableDeviceAllocation<float> first_dc;
    ReusableDeviceAllocation<float> second_dc;
    ReusableDeviceAllocation<float2> sh_rest_moments;
    ReusableDeviceAllocation<float> first_opacity;
    ReusableDeviceAllocation<float> second_opacity;
    ReusableDeviceAllocation<float2> opacity_sh_moments;
    ReusableDeviceAllocation<float> first_xyz;
    ReusableDeviceAllocation<float> second_xyz;
    ReusableDeviceAllocation<float> first_log_scale;
    ReusableDeviceAllocation<float> second_log_scale;
    ReusableDeviceAllocation<float> first_rotation;
    ReusableDeviceAllocation<float> second_rotation;
    ReusableDeviceAllocation<float> refine_weight_max;
    ReusableDeviceAllocation<float> visibility_count;
    ReusableDeviceAllocation<float> edge_weight_sum;
    ReusableDeviceAllocation<float> absgrad_sum;
    ReusableDeviceAllocation<float> absgrad_observation_count;
    ReusableDeviceAllocation<std::uint32_t> refinement_indices;
    ReusableDeviceAllocation<std::uint32_t>
        refinement_destinations;
    ReusableDeviceAllocation<float> frame_refinement_weight;
    ReusableDeviceAllocation<float> frame_visibility_weight;
    ReusableDeviceAllocation<float> frame_edge_weight;
    ReusableDeviceAllocation<float> frame_abs_projected_gradient;
    ReusableDeviceAllocation<DeviceOptimizerTelemetry>
        optimizer_telemetry;
};

struct TrainingCheckpointSnapshot::Impl {
    TrainingCheckpointProgress progress;
    std::string dataset_fingerprint;
    std::string configuration_fingerprint;
    std::uint64_t optimizer_steps = 0U;
    std::uint64_t maximum_steps = 0U;
    std::uint64_t noise_seed = 0U;
    std::size_t gaussian_count = 0U;
    std::uint32_t maximum_active_sh_degree = 0U;
    std::uint32_t sh_degree_interval = 0U;
    std::uint32_t active_sh_degree = 0U;
    MrnfOptimizerProfile optimizer_profile =
        MrnfOptimizerProfile::dronegs_dev16;
    bool fastgs_compatibility = false;
    float position_learning_rate_scale = 1.0F;
    float minimum_log_scale = -16.0F;
    float maximum_log_scale = 16.0F;
    float beta_first_power = 1.0F;
    float beta_second_power = 1.0F;
    std::vector<Gaussian> gaussians;
    std::vector<float> first_dc;
    std::vector<float> second_dc;
    std::vector<float2> sh_rest_moments;
    std::vector<float> first_opacity;
    std::vector<float> second_opacity;
    std::vector<float2> opacity_sh_moments;
    std::vector<float> first_xyz;
    std::vector<float> second_xyz;
    std::vector<float> first_log_scale;
    std::vector<float> second_log_scale;
    std::vector<float> first_rotation;
    std::vector<float> second_rotation;
    std::vector<float> refine_weight_max;
    std::vector<float> visibility_count;
    std::vector<float> edge_weight_sum;
    std::vector<float> absgrad_sum;
    std::vector<float> absgrad_observation_count;
};

OrderedAlphaTrainingContext::OrderedAlphaTrainingContext(
    const std::vector<Gaussian>& gaussians,
    std::size_t maximum_pixels,
    std::uint64_t maximum_steps,
    std::size_t maximum_gaussians,
    MrnfOptimizerProfile optimizer_profile,
    std::uint32_t maximum_sh_degree,
    std::uint32_t sh_degree_interval,
    std::uint64_t noise_seed,
    std::optional<bool> fastgs_compatibility_override,
    float maximum_scale_growth_factor,
    bool opacity_sh_enabled)
    : impl_(std::make_unique<Impl>(
          gaussians, maximum_pixels, maximum_steps,
          maximum_gaussians, optimizer_profile,
          maximum_sh_degree, sh_degree_interval,
          noise_seed, fastgs_compatibility_override,
          maximum_scale_growth_factor, opacity_sh_enabled)) {}

OrderedAlphaTrainingContext::~OrderedAlphaTrainingContext() = default;

OrderedAlphaTrainingContext::OrderedAlphaTrainingContext(
    OrderedAlphaTrainingContext&&) noexcept = default;

OrderedAlphaTrainingContext& OrderedAlphaTrainingContext::operator=(
    OrderedAlphaTrainingContext&&) noexcept = default;

float OrderedAlphaTrainingContext::evaluate(
    const RasterCamera& camera, const std::uint8_t* target_rgb,
    std::size_t target_bytes, float mse_blend,
    const ImageObjectivePolicy& objective_policy) {
    return impl_->render_loss(
        camera, target_rgb, target_bytes, false, false,
        nullptr, nullptr, nullptr, mse_blend, true,
        RefinementStatisticsMode::collect, objective_policy);
}

ImageQualityMetrics OrderedAlphaTrainingContext::evaluate_quality(
    const RasterCamera& camera, const std::uint8_t* target_rgb,
    std::size_t target_bytes,
    std::vector<float>* prediction) {
    ImageQualityMetrics result;
    static_cast<void>(impl_->render_loss(
        camera, target_rgb, target_bytes, false, false,
        &result, prediction, nullptr, 0.0F));
    return result;
}

ImageObjectiveOutput
OrderedAlphaTrainingContext::evaluate_objective_gradient(
    const RasterCamera& camera, const std::uint8_t* target_rgb,
    std::size_t target_bytes, float mse_blend,
    const ImageObjectivePolicy& objective_policy) {
    ImageObjectiveOutput result;
    static_cast<void>(impl_->render_loss(
        camera, target_rgb, target_bytes, true, false,
        nullptr, nullptr, &result, mse_blend, true,
        RefinementStatisticsMode::collect, objective_policy));
    return result;
}

float OrderedAlphaTrainingContext::train_step(
    const RasterCamera& camera, const std::uint8_t* target_rgb,
    std::size_t target_bytes, float mse_blend,
    RefinementStatisticsMode refinement_statistics,
    const ImageObjectivePolicy& objective_policy) {
    return impl_->render_loss(
        camera, target_rgb, target_bytes, true, true,
        nullptr, nullptr, nullptr, mse_blend, true,
        refinement_statistics, objective_policy);
}

void OrderedAlphaTrainingContext::train_step_deferred(
    const RasterCamera& camera, const std::uint8_t* target_rgb,
    std::size_t target_bytes, float mse_blend,
    RefinementStatisticsMode refinement_statistics,
    const ImageObjectivePolicy& objective_policy) {
    static_cast<void>(impl_->render_loss(
        camera, target_rgb, target_bytes, true, true,
        nullptr, nullptr, nullptr, mse_blend, false,
        refinement_statistics, objective_policy));
}

TopologyRefinementResult
OrderedAlphaTrainingContext::refine_topology(
    float gradient_threshold, float grow_fraction,
    std::uint64_t selection_seed,
    bool spatial_pruning_bounds,
    TopologyRefinementTelemetry* telemetry) {
    const auto started = telemetry ? std::chrono::steady_clock::now()
                                   : std::chrono::steady_clock::time_point{};
    if (telemetry) *telemetry = {};
    const auto result = impl_->refine_topology(
        gradient_threshold, grow_fraction, selection_seed,
        spatial_pruning_bounds, telemetry);
    if (telemetry) {
        telemetry->measured_calls = 1U;
        telemetry->total_seconds = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - started).count();
        // Includes destruction of host vectors and entry/return bookkeeping.
        telemetry->other_seconds = std::max(
            0.0, telemetry->total_seconds - topology_accounted_seconds(*telemetry));
    }
    return result;
}

MrnfLearningRates
OrderedAlphaTrainingContext::current_learning_rates() const noexcept {
    return impl_->learning_rates;
}

std::optional<MrnfOptimizerTelemetry>
OrderedAlphaTrainingContext::latest_optimizer_telemetry()
    const noexcept {
    return impl_->latest_telemetry;
}

std::optional<MrnfGpuStageTelemetry>
OrderedAlphaTrainingContext::latest_gpu_stage_telemetry()
    const noexcept {
    return impl_->latest_gpu_stage_telemetry;
}

std::size_t OrderedAlphaTrainingContext::size() const noexcept {
    return impl_->gaussian_count;
}

std::size_t OrderedAlphaTrainingContext::opacity_sh_storage_bytes() const noexcept {
    return impl_->opacity_sh_gradient.capacity() * sizeof(float) +
        impl_->opacity_sh_moments.capacity() * sizeof(float2);
}

std::uint32_t
OrderedAlphaTrainingContext::active_sh_degree() const noexcept {
    return impl_->active_sh_degree;
}

void OrderedAlphaTrainingContext::set_active_sh_degree(
    std::uint32_t degree) {
    if (degree > impl_->maximum_active_sh_degree) {
        throw std::invalid_argument(
            "active SH degree exceeds the configured maximum");
    }
    impl_->active_sh_degree = degree;
}

void OrderedAlphaTrainingContext::download(
    std::vector<Gaussian>& output) const {
    output.resize(impl_->gaussian_count);
    impl_->gaussians.copy_to_host(
        output.data(), impl_->gaussian_count);
}

TrainingCheckpointSnapshot::TrainingCheckpointSnapshot(
    std::unique_ptr<Impl> impl)
    : impl_(std::move(impl)) {}

TrainingCheckpointSnapshot::~TrainingCheckpointSnapshot() = default;

TrainingCheckpointSnapshot::TrainingCheckpointSnapshot(
    TrainingCheckpointSnapshot&&) noexcept = default;

TrainingCheckpointSnapshot& TrainingCheckpointSnapshot::operator=(
    TrainingCheckpointSnapshot&&) noexcept = default;

std::size_t TrainingCheckpointSnapshot::gaussian_count() const noexcept {
    return impl_->gaussian_count;
}

TrainingCheckpointSnapshot
OrderedAlphaTrainingContext::capture_checkpoint(
    const TrainingCheckpointProgress& progress,
    const std::string& dataset_fingerprint,
    const std::string& configuration_fingerprint) const {
    require_cuda(
        cudaDeviceSynchronize(),
        "synchronize checkpoint snapshot");
    auto snapshot = std::make_unique<TrainingCheckpointSnapshot::Impl>();
    snapshot->progress = progress;
    snapshot->dataset_fingerprint = dataset_fingerprint;
    snapshot->configuration_fingerprint =
        configuration_fingerprint;
    snapshot->optimizer_steps = impl_->optimizer_steps;
    snapshot->maximum_steps = impl_->maximum_steps;
    snapshot->noise_seed = impl_->noise_seed;
    snapshot->gaussian_count = impl_->gaussian_count;
    snapshot->maximum_active_sh_degree =
        impl_->maximum_active_sh_degree;
    snapshot->sh_degree_interval = impl_->sh_degree_interval;
    snapshot->active_sh_degree = impl_->active_sh_degree;
    snapshot->optimizer_profile = impl_->optimizer_profile;
    snapshot->fastgs_compatibility = impl_->fastgs_compatibility;
    snapshot->position_learning_rate_scale =
        impl_->position_learning_rate_scale;
    snapshot->minimum_log_scale = impl_->minimum_log_scale;
    snapshot->maximum_log_scale = impl_->maximum_log_scale;
    snapshot->beta_first_power = impl_->beta_first_power;
    snapshot->beta_second_power = impl_->beta_second_power;
    const auto capture_device = [](
        const auto& allocation, auto& destination,
        std::size_t count) {
        destination.resize(count);
        if (count != 0U) {
            allocation.copy_to_host(destination.data(), count);
        }
    };
    const auto count = snapshot->gaussian_count;
    capture_device(impl_->gaussians, snapshot->gaussians, count);
    capture_device(impl_->first_dc, snapshot->first_dc, count * 3U);
    capture_device(impl_->second_dc, snapshot->second_dc, count * 3U);
    capture_device(
        impl_->sh_rest_moments, snapshot->sh_rest_moments,
        count * maximum_sh_rest_values);
    capture_device(
        impl_->first_opacity, snapshot->first_opacity, count);
    capture_device(
        impl_->second_opacity, snapshot->second_opacity, count);
    if (impl_->opacity_sh_enabled) {
        capture_device(
            impl_->opacity_sh_moments, snapshot->opacity_sh_moments,
            count * maximum_opacity_sh_coefficients);
    } else {
        // Preserve the V4/V5 wire layout without materializing unused GPU state.
        snapshot->opacity_sh_moments.resize(
            count * maximum_opacity_sh_coefficients,
            make_float2(0.0F, 0.0F));
    }
    capture_device(impl_->first_xyz, snapshot->first_xyz, count * 3U);
    capture_device(impl_->second_xyz, snapshot->second_xyz, count * 3U);
    capture_device(
        impl_->first_log_scale, snapshot->first_log_scale,
        count * 3U);
    capture_device(
        impl_->second_log_scale, snapshot->second_log_scale,
        count * 3U);
    capture_device(
        impl_->first_rotation, snapshot->first_rotation,
        count * 4U);
    capture_device(
        impl_->second_rotation, snapshot->second_rotation,
        count * 4U);
    capture_device(
        impl_->refine_weight_max, snapshot->refine_weight_max, count);
    capture_device(
        impl_->visibility_count, snapshot->visibility_count, count);
    capture_device(
        impl_->edge_weight_sum, snapshot->edge_weight_sum, count);
    capture_device(impl_->absgrad_sum, snapshot->absgrad_sum, count);
    capture_device(
        impl_->absgrad_observation_count,
        snapshot->absgrad_observation_count, count);
    return TrainingCheckpointSnapshot(std::move(snapshot));
}

void TrainingCheckpointSnapshot::write_to(
    const std::filesystem::path& path) const {
    const auto& snapshot = *impl_;
    const auto temporary = path.string() + ".tmp";
    std::ofstream stream(
        temporary, std::ios::binary | std::ios::trunc);
    if (!stream) {
        throw std::runtime_error(
            "cannot create checkpoint: " + temporary);
    }
    const auto write_value = [&stream](const auto& value) {
        stream.write(
            reinterpret_cast<const char*>(&value),
            static_cast<std::streamsize>(sizeof(value)));
    };
    const auto write_string =
        [&stream, &write_value](const std::string& value) {
            const auto size =
                static_cast<std::uint64_t>(value.size());
            write_value(size);
            stream.write(
                value.data(),
                static_cast<std::streamsize>(value.size()));
        };
    const auto write_device =
        [&stream](const auto& allocation, std::size_t count) {
            using value_type = std::remove_cv_t<
                std::remove_pointer_t<
                    decltype(allocation.data())>>;
            if (count != 0U) {
                stream.write(
                    reinterpret_cast<const char*>(allocation.data()),
                    static_cast<std::streamsize>(
                        count * sizeof(value_type)));
            }
        };
    const auto write_moment_pairs =
        [&stream](const std::vector<float2>& moments,
                  std::size_t count) {
            if (count > moments.size()) {
                throw std::logic_error(
                    "checkpoint moment snapshot is truncated");
            }
            if (count == 0U) {
                return;
            }
            constexpr std::size_t chunk_size = 1U << 20U;
            std::vector<float> chunk(std::min(count, chunk_size));
            for (std::size_t component = 0U; component < 2U;
                 ++component) {
                for (std::size_t offset = 0U; offset < count;
                     offset += chunk_size) {
                    const auto size =
                        std::min(chunk_size, count - offset);
                    for (std::size_t index = 0U; index < size; ++index) {
                        const auto pair = moments[offset + index];
                        chunk[index] = component == 0U ? pair.x : pair.y;
                    }
                    stream.write(
                        reinterpret_cast<const char*>(chunk.data()),
                        static_cast<std::streamsize>(
                            size * sizeof(float)));
                }
            }
        };
    constexpr std::array<char, 16> magic{
        'D', 'R', 'O', 'N', 'E', 'G', 'S', '-', 'C', 'K', 'P', 'T',
        '-', 'V', '1', '\0'};
    stream.write(magic.data(), magic.size());
    constexpr std::uint32_t format_version = 5U;
    write_value(format_version);
    write_string(snapshot.dataset_fingerprint);
    write_string(snapshot.configuration_fingerprint);
    write_value(snapshot.progress.completed_iteration);
    write_value(snapshot.progress.topology_refinements);
    write_value(snapshot.progress.gaussians_added);
    write_value(snapshot.progress.gaussians_pruned);
    write_value(snapshot.progress.gaussian_slots_reused);
    write_value(snapshot.progress.topology_compactions);
    write_value(snapshot.progress.initial_loss);
    const std::uint8_t has_initial_held_out_psnr =
        snapshot.progress.initial_held_out_psnr.has_value() ? 1U : 0U;
    const std::uint8_t has_initial_held_out_ssim =
        snapshot.progress.initial_held_out_ssim.has_value() ? 1U : 0U;
    write_value(has_initial_held_out_psnr);
    if (has_initial_held_out_psnr) {
        write_value(*snapshot.progress.initial_held_out_psnr);
    }
    write_value(has_initial_held_out_ssim);
    if (has_initial_held_out_ssim) {
        write_value(*snapshot.progress.initial_held_out_ssim);
    }
    const std::uint8_t has_initial_pixel_weighted_psnr =
        snapshot.progress.initial_pixel_weighted_psnr.has_value()
            ? 1U
            : 0U;
    const std::uint8_t has_initial_pixel_weighted_ssim =
        snapshot.progress.initial_pixel_weighted_ssim.has_value()
            ? 1U
            : 0U;
    write_value(has_initial_pixel_weighted_psnr);
    if (has_initial_pixel_weighted_psnr) {
        write_value(*snapshot.progress.initial_pixel_weighted_psnr);
    }
    write_value(has_initial_pixel_weighted_ssim);
    if (has_initial_pixel_weighted_ssim) {
        write_value(*snapshot.progress.initial_pixel_weighted_ssim);
    }
    write_value(snapshot.optimizer_steps);
    write_value(snapshot.maximum_steps);
    write_value(snapshot.noise_seed);
    const auto count = snapshot.gaussian_count;
    const auto portable_count = static_cast<std::uint64_t>(count);
    write_value(portable_count);
    write_value(snapshot.maximum_active_sh_degree);
    write_value(snapshot.sh_degree_interval);
    write_value(snapshot.active_sh_degree);
    const auto profile =
        static_cast<std::uint32_t>(snapshot.optimizer_profile);
    write_value(profile);
    const std::uint8_t portable_fastgs =
        snapshot.fastgs_compatibility ? 1U : 0U;
    write_value(portable_fastgs);
    write_value(snapshot.position_learning_rate_scale);
    write_value(snapshot.minimum_log_scale);
    write_value(snapshot.maximum_log_scale);
    write_value(snapshot.beta_first_power);
    write_value(snapshot.beta_second_power);
    write_device(snapshot.gaussians, count);
    write_device(snapshot.first_dc, count * 3U);
    write_device(snapshot.second_dc, count * 3U);
    write_moment_pairs(
        snapshot.sh_rest_moments,
        count * maximum_sh_rest_values);
    write_device(snapshot.first_opacity, count);
    write_device(snapshot.second_opacity, count);
    write_moment_pairs(
        snapshot.opacity_sh_moments,
        count * maximum_opacity_sh_coefficients);
    write_device(snapshot.first_xyz, count * 3U);
    write_device(snapshot.second_xyz, count * 3U);
    write_device(snapshot.first_log_scale, count * 3U);
    write_device(snapshot.second_log_scale, count * 3U);
    write_device(snapshot.first_rotation, count * 4U);
    write_device(snapshot.second_rotation, count * 4U);
    write_device(snapshot.refine_weight_max, count);
    write_device(snapshot.visibility_count, count);
    write_device(snapshot.edge_weight_sum, count);
    write_device(snapshot.absgrad_sum, count);
    write_device(snapshot.absgrad_observation_count, count);
    stream.flush();
    if (!stream) {
        throw std::runtime_error(
            "failed to write checkpoint: " + temporary);
    }
    stream.close();
    const auto payload_bytes =
        static_cast<std::uint64_t>(
            std::filesystem::file_size(temporary));
    const auto checksum =
        checkpoint_checksum(temporary, payload_bytes);
    {
        std::ofstream trailer(temporary, std::ios::binary | std::ios::app);
        trailer.write(
            reinterpret_cast<const char*>(&checksum),
            static_cast<std::streamsize>(sizeof(checksum)));
        trailer.flush();
        if (!trailer) {
            throw std::runtime_error(
                "failed to append checkpoint checksum");
        }
    }
    sync_checkpoint_file(temporary);
    std::error_code error;
    std::filesystem::rename(temporary, path, error);
    if (error) {
        const auto backup = path.string() + ".previous";
        std::error_code backup_error;
        std::filesystem::remove(backup, backup_error);
        backup_error.clear();
        if (std::filesystem::exists(path)) {
            std::filesystem::rename(path, backup, backup_error);
        }
        if (backup_error) {
            throw std::runtime_error(
                "cannot preserve previous checkpoint: " +
                backup_error.message());
        }
        error.clear();
        std::filesystem::rename(temporary, path, error);
        if (error && std::filesystem::exists(backup)) {
            std::error_code restore_error;
            std::filesystem::rename(backup, path, restore_error);
        } else {
            std::filesystem::remove(backup, backup_error);
        }
    }
    if (error) {
        throw std::runtime_error(
            "cannot publish checkpoint: " + error.message());
    }
    sync_checkpoint_directory(path);
}

void OrderedAlphaTrainingContext::save_checkpoint(
    const std::filesystem::path& path,
    const TrainingCheckpointProgress& progress,
    const std::string& dataset_fingerprint,
    const std::string& configuration_fingerprint) const {
    capture_checkpoint(
        progress, dataset_fingerprint,
        configuration_fingerprint)
        .write_to(path);
}

TrainingCheckpointProgress
OrderedAlphaTrainingContext::load_checkpoint(
    const std::filesystem::path& path,
    const std::string& expected_dataset_fingerprint,
    const std::string& expected_configuration_fingerprint) {
    const auto total_bytes =
        static_cast<std::uint64_t>(std::filesystem::file_size(path));
    std::uint64_t payload_bytes = total_bytes;
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        throw std::runtime_error(
            "cannot open checkpoint: " + path.string());
    }
    const auto read_value = [&stream](auto& value) {
        stream.read(
            reinterpret_cast<char*>(&value),
            static_cast<std::streamsize>(sizeof(value)));
    };
    const auto read_string = [&stream, &read_value]() {
        std::uint64_t size = 0U;
        read_value(size);
        if (size > 1'048'576U) {
            throw std::runtime_error(
                "checkpoint string is unreasonably large");
        }
        std::string value(static_cast<std::size_t>(size), '\0');
        stream.read(
            value.data(), static_cast<std::streamsize>(size));
        return value;
    };
    const auto read_device =
        [&stream](auto& allocation, std::size_t count) {
            using value_type = std::remove_cv_t<
                std::remove_pointer_t<
                    decltype(allocation.data())>>;
            std::vector<value_type> host(count);
            if (count != 0U) {
                stream.read(
                    reinterpret_cast<char*>(host.data()),
                    static_cast<std::streamsize>(
                        count * sizeof(value_type)));
                if (!stream) {
                    throw std::runtime_error(
                        "checkpoint payload is truncated");
                }
                allocation.copy_from_host(host.data(), count);
            }
        };
    const auto read_moment_pairs =
        [&stream](auto& allocation, std::size_t count, bool restore = true) {
            if (count == 0U) {
                return;
            }
            if (!restore) {
                // The checksum still covers this V4/V5 payload. Consume it
                // without allocating host/GPU moments on the scalar path.
                const auto bytes = static_cast<std::streamsize>(
                    count * 2U * sizeof(float));
                stream.ignore(bytes);
                if (stream.gcount() != bytes) {
                    throw std::runtime_error(
                        "checkpoint moment payload is truncated");
                }
                return;
            }
            std::vector<float> first(count);
            stream.read(
                reinterpret_cast<char*>(first.data()),
                static_cast<std::streamsize>(count * sizeof(float)));
            if (!stream) {
                throw std::runtime_error(
                    "checkpoint moment payload is truncated");
            }
            constexpr std::size_t chunk_size = 1U << 20U;
            std::vector<float> second(std::min(count, chunk_size));
            std::vector<float2> pairs(second.size());
            for (std::size_t offset = 0U; offset < count;
                 offset += chunk_size) {
                const auto size =
                    std::min(chunk_size, count - offset);
                stream.read(
                    reinterpret_cast<char*>(second.data()),
                    static_cast<std::streamsize>(size * sizeof(float)));
                if (!stream) {
                    throw std::runtime_error(
                        "checkpoint moment payload is truncated");
                }
                for (std::size_t index = 0U; index < size; ++index) {
                    pairs[index] = make_float2(
                        first[offset + index], second[index]);
                }
                allocation.copy_from_host(
                    pairs.data(), size, offset);
            }
        };
    constexpr std::array<char, 16> expected_magic{
        'D', 'R', 'O', 'N', 'E', 'G', 'S', '-', 'C', 'K', 'P', 'T',
        '-', 'V', '1', '\0'};
    std::array<char, 16> magic{};
    stream.read(magic.data(), magic.size());
    std::uint32_t format_version = 0U;
    read_value(format_version);
    if (magic != expected_magic ||
        (format_version != 4U && format_version != 5U)) {
        throw std::runtime_error(
            "unsupported DroneGS checkpoint format; opacity-SH training "
            "requires version 4 or 5");
    }
    if (format_version >= 3U) {
        if (total_bytes <= sizeof(std::uint64_t)) {
            throw std::runtime_error(
                "checkpoint is too short for its checksum");
        }
        payload_bytes -= sizeof(std::uint64_t);
        std::ifstream trailer(path, std::ios::binary);
        trailer.seekg(static_cast<std::streamoff>(payload_bytes));
        std::uint64_t expected_checksum = 0U;
        trailer.read(
            reinterpret_cast<char*>(&expected_checksum),
            static_cast<std::streamsize>(sizeof(expected_checksum)));
        if (!trailer ||
            checkpoint_checksum(path, payload_bytes) !=
                expected_checksum) {
            throw std::runtime_error(
                "checkpoint checksum mismatch");
        }
    }
    if (read_string() != expected_dataset_fingerprint) {
        throw std::runtime_error(
            "checkpoint dataset fingerprint mismatch");
    }
    if (read_string() != expected_configuration_fingerprint) {
        throw std::runtime_error(
            "checkpoint configuration fingerprint mismatch");
    }
    TrainingCheckpointProgress progress;
    read_value(progress.completed_iteration);
    read_value(progress.topology_refinements);
    read_value(progress.gaussians_added);
    read_value(progress.gaussians_pruned);
    read_value(progress.gaussian_slots_reused);
    read_value(progress.topology_compactions);
    if (format_version >= 2U) {
        read_value(progress.initial_loss);
        bool has_initial_held_out_psnr = false;
        bool has_initial_held_out_ssim = false;
        if (format_version >= 3U) {
            std::uint8_t value = 0U;
            read_value(value);
            has_initial_held_out_psnr = value != 0U;
        } else {
            read_value(has_initial_held_out_psnr);
        }
        if (has_initial_held_out_psnr) {
            float value = 0.0F;
            read_value(value);
            progress.initial_held_out_psnr = value;
        }
        if (format_version >= 3U) {
            std::uint8_t value = 0U;
            read_value(value);
            has_initial_held_out_ssim = value != 0U;
        } else {
            read_value(has_initial_held_out_ssim);
        }
        if (has_initial_held_out_ssim) {
            float value = 0.0F;
            read_value(value);
            progress.initial_held_out_ssim = value;
        }
        if (format_version >= 5U) {
            std::uint8_t has_initial_pixel_weighted_psnr = 0U;
            read_value(has_initial_pixel_weighted_psnr);
            if (has_initial_pixel_weighted_psnr != 0U) {
                float value = 0.0F;
                read_value(value);
                progress.initial_pixel_weighted_psnr = value;
            }
            std::uint8_t has_initial_pixel_weighted_ssim = 0U;
            read_value(has_initial_pixel_weighted_ssim);
            if (has_initial_pixel_weighted_ssim != 0U) {
                float value = 0.0F;
                read_value(value);
                progress.initial_pixel_weighted_ssim = value;
            }
        }
    }
    std::uint64_t optimizer_steps = 0U;
    std::uint64_t checkpoint_maximum_steps = 0U;
    std::uint64_t checkpoint_noise_seed = 0U;
    std::size_t checkpoint_count = 0U;
    std::uint32_t checkpoint_maximum_sh = 0U;
    std::uint32_t checkpoint_sh_interval = 0U;
    std::uint32_t checkpoint_active_sh = 0U;
    std::uint32_t checkpoint_profile = 0U;
    bool checkpoint_fastgs = false;
    read_value(optimizer_steps);
    read_value(checkpoint_maximum_steps);
    read_value(checkpoint_noise_seed);
    if (format_version >= 3U) {
        std::uint64_t portable_count = 0U;
        read_value(portable_count);
        if (portable_count >
            static_cast<std::uint64_t>(
                std::numeric_limits<std::size_t>::max())) {
            throw std::runtime_error(
                "checkpoint Gaussian count is not portable");
        }
        checkpoint_count =
            static_cast<std::size_t>(portable_count);
    } else {
        read_value(checkpoint_count);
    }
    read_value(checkpoint_maximum_sh);
    read_value(checkpoint_sh_interval);
    read_value(checkpoint_active_sh);
    read_value(checkpoint_profile);
    if (format_version >= 3U) {
        std::uint8_t portable_fastgs = 0U;
        read_value(portable_fastgs);
        checkpoint_fastgs = portable_fastgs != 0U;
    } else {
        read_value(checkpoint_fastgs);
    }
    if (checkpoint_count == 0U ||
        checkpoint_count > impl_->gaussian_capacity ||
        checkpoint_maximum_steps != impl_->maximum_steps ||
        checkpoint_noise_seed != impl_->noise_seed ||
        checkpoint_maximum_sh != impl_->maximum_active_sh_degree ||
        checkpoint_sh_interval != impl_->sh_degree_interval ||
        checkpoint_profile !=
            static_cast<std::uint32_t>(impl_->optimizer_profile) ||
        checkpoint_fastgs != impl_->fastgs_compatibility ||
        optimizer_steps != progress.completed_iteration) {
        throw std::runtime_error(
            "checkpoint optimizer/runtime state mismatch");
    }
    read_value(impl_->position_learning_rate_scale);
    read_value(impl_->minimum_log_scale);
    read_value(impl_->maximum_log_scale);
    read_value(impl_->beta_first_power);
    read_value(impl_->beta_second_power);
    impl_->gaussian_count = checkpoint_count;
    impl_->optimizer_steps = optimizer_steps;
    impl_->active_sh_degree = checkpoint_active_sh;
    const auto count = impl_->gaussian_count;
    read_device(impl_->gaussians, count);
    read_device(impl_->first_dc, count * 3U);
    read_device(impl_->second_dc, count * 3U);
    read_moment_pairs(
        impl_->sh_rest_moments,
        count * maximum_sh_rest_values);
    read_device(impl_->first_opacity, count);
    read_device(impl_->second_opacity, count);
    read_moment_pairs(
        impl_->opacity_sh_moments,
        count * maximum_opacity_sh_coefficients,
        impl_->opacity_sh_enabled);
    read_device(impl_->first_xyz, count * 3U);
    read_device(impl_->second_xyz, count * 3U);
    read_device(impl_->first_log_scale, count * 3U);
    read_device(impl_->second_log_scale, count * 3U);
    read_device(impl_->first_rotation, count * 4U);
    read_device(impl_->second_rotation, count * 4U);
    read_device(impl_->refine_weight_max, count);
    read_device(impl_->visibility_count, count);
    read_device(impl_->edge_weight_sum, count);
    read_device(impl_->absgrad_sum, count);
    read_device(impl_->absgrad_observation_count, count);
    if (!stream) {
        throw std::runtime_error(
            "checkpoint payload is truncated");
    }
    if (format_version >= 3U) {
        const auto consumed = stream.tellg();
        if (consumed < 0 ||
            static_cast<std::uint64_t>(consumed) != payload_bytes) {
            throw std::runtime_error(
                "checkpoint payload size/checksum trailer mismatch");
        }
    }
    impl_->learning_rates = mrnf_learning_rates(
        std::max<std::uint64_t>(1U, optimizer_steps),
        impl_->maximum_steps,
        impl_->position_learning_rate_scale,
        impl_->optimizer_profile);
    impl_->latest_telemetry.reset();
    return progress;
}

}  // namespace dronegs
