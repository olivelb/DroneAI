// SPDX-License-Identifier: MIT
#include "dronegs/rasterization.hpp"

#include <cuda_runtime.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <vector>

namespace dronegs {
namespace {

static_assert(std::is_trivially_copyable_v<ProjectedAlphaSplat>);

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

struct TileBins {
    std::uint32_t tiles_x = 0;
    std::uint32_t tiles_y = 0;
    std::vector<std::uint64_t> offsets;
    std::vector<std::uint32_t> splat_indices;
};

TileBins build_tile_bins(
    const std::vector<ProjectedAlphaSplat>& splats,
    const RasterCamera& camera) {
    if (splats.size() > std::numeric_limits<std::uint32_t>::max()) {
        throw std::runtime_error("projected splat count exceeds uint32");
    }
    const std::uint32_t tiles_x =
        (camera.width + alpha_tile_width - 1U) / alpha_tile_width;
    const std::uint32_t tiles_y =
        (camera.height + alpha_tile_height - 1U) / alpha_tile_height;
    const auto tile_count =
        static_cast<std::size_t>(tiles_x) * tiles_y;
    std::vector<std::vector<std::uint32_t>> lists(tile_count);
    for (std::size_t index = 0; index < splats.size(); ++index) {
        const auto& splat = splats[index];
        const int support =
            static_cast<int>(std::ceil(2.5F * splat.sigma));
        const int minimum_x = std::max(
            0, static_cast<int>(std::floor(splat.x)) - support);
        const int maximum_x = std::min(
            static_cast<int>(camera.width) - 1,
            static_cast<int>(std::floor(splat.x)) + support);
        const int minimum_y = std::max(
            0, static_cast<int>(std::floor(splat.y)) - support);
        const int maximum_y = std::min(
            static_cast<int>(camera.height) - 1,
            static_cast<int>(std::floor(splat.y)) + support);
        const auto minimum_tile_x =
            static_cast<std::uint32_t>(minimum_x) / alpha_tile_width;
        const auto maximum_tile_x =
            static_cast<std::uint32_t>(maximum_x) / alpha_tile_width;
        const auto minimum_tile_y =
            static_cast<std::uint32_t>(minimum_y) / alpha_tile_height;
        const auto maximum_tile_y =
            static_cast<std::uint32_t>(maximum_y) / alpha_tile_height;
        for (std::uint32_t tile_y = minimum_tile_y;
             tile_y <= maximum_tile_y; ++tile_y) {
            for (std::uint32_t tile_x = minimum_tile_x;
                 tile_x <= maximum_tile_x; ++tile_x) {
                lists[static_cast<std::size_t>(tile_y) * tiles_x + tile_x]
                    .push_back(static_cast<std::uint32_t>(index));
            }
        }
    }
    TileBins bins{
        .tiles_x = tiles_x,
        .tiles_y = tiles_y,
        .offsets = std::vector<std::uint64_t>(tile_count + 1U, 0U),
        .splat_indices = {},
    };
    for (std::size_t tile = 0; tile < tile_count; ++tile) {
        bins.offsets[tile + 1U] =
            bins.offsets[tile] + lists[tile].size();
    }
    if (bins.offsets.back() > std::numeric_limits<std::size_t>::max()) {
        throw std::runtime_error("tile/splat pair count exceeds host address space");
    }
    bins.splat_indices.reserve(
        static_cast<std::size_t>(bins.offsets.back()));
    for (const auto& list : lists) {
        bins.splat_indices.insert(
            bins.splat_indices.end(), list.begin(), list.end());
    }
    return bins;
}

struct DeviceRenderStats {
    unsigned long long evaluated_pairs = 0U;
    unsigned long long contributing_pairs = 0U;
};

__global__ void render_alpha_tiles_kernel(
    const ProjectedAlphaSplat* splats,
    const std::uint32_t* splat_indices,
    const std::uint64_t* tile_offsets,
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
        (width + alpha_tile_width - 1U) / alpha_tile_width;
    const std::size_t tile =
        static_cast<std::size_t>(blockIdx.y) * tiles_x + blockIdx.x;
    const std::uint64_t start = tile_offsets[tile];
    const std::uint64_t end = tile_offsets[tile + 1U];

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
            batch[thread_index] = splats[splat_indices[load_index]];
        }
        __syncthreads();
        const auto batch_count = static_cast<std::uint32_t>(
            min(
                static_cast<std::uint64_t>(threads_per_tile),
                end - base));
        if (valid_pixel && remaining > alpha_minimum_transmittance) {
            for (std::uint32_t index = 0; index < batch_count; ++index) {
                const auto& splat = batch[index];
                const int support =
                    static_cast<int>(ceilf(2.5F * splat.sigma));
                const int center_x = static_cast<int>(floorf(splat.x));
                const int center_y = static_cast<int>(floorf(splat.y));
                if (static_cast<int>(x) < center_x - support ||
                    static_cast<int>(x) > center_x + support ||
                    static_cast<int>(y) < center_y - support ||
                    static_cast<int>(y) > center_y + support) {
                    continue;
                }
                ++evaluated;
                const float delta_x =
                    (static_cast<float>(x) + 0.5F) - splat.x;
                const float delta_y =
                    (static_cast<float>(y) + 0.5F) - splat.y;
                const float inverse_two_variance =
                    0.5F / (splat.sigma * splat.sigma);
                const float gaussian_weight = expf(
                    -(delta_x * delta_x + delta_y * delta_y) *
                    inverse_two_variance);
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
    atomicAdd(&stats->evaluated_pairs, evaluated);
    atomicAdd(&stats->contributing_pairs, contributing);
}

}  // namespace

AlphaRenderOutput render_alpha_tiled_cuda(
    const std::vector<Gaussian>& gaussians, const RasterCamera& camera,
    const std::array<float, 3>& background) {
    const auto splats = project_alpha_splats(gaussians, camera);
    for (const float channel : background) {
        if (!std::isfinite(channel) || channel < 0.0F || channel > 1.0F) {
            throw std::invalid_argument("tiled alpha background is invalid");
        }
    }
    if (splats.empty()) {
        return render_alpha_reference({}, camera, background);
    }
    const auto bins = build_tile_bins(splats, camera);
    if (bins.splat_indices.empty()) {
        return render_alpha_reference({}, camera, background);
    }
    const auto pixel_count =
        static_cast<std::size_t>(camera.width) * camera.height;
    DeviceAllocation<ProjectedAlphaSplat> device_splats(splats.size());
    DeviceAllocation<std::uint32_t> device_indices(
        bins.splat_indices.size());
    DeviceAllocation<std::uint64_t> device_offsets(bins.offsets.size());
    DeviceAllocation<float> device_rgb(pixel_count * 3U);
    DeviceAllocation<float> device_transmittance(pixel_count);
    DeviceAllocation<DeviceRenderStats> device_stats(1U);
    device_splats.copy_from_host(splats.data());
    device_indices.copy_from_host(bins.splat_indices.data());
    device_offsets.copy_from_host(bins.offsets.data());
    device_stats.zero();

    const dim3 threads(alpha_tile_width, alpha_tile_height);
    const dim3 blocks(bins.tiles_x, bins.tiles_y);
    render_alpha_tiles_kernel<<<blocks, threads>>>(
        device_splats.data(), device_indices.data(), device_offsets.data(),
        camera.width, camera.height,
        background[0], background[1], background[2],
        device_rgb.data(), device_transmittance.data(), device_stats.data());
    require_cuda(cudaGetLastError(), "launch tiled alpha renderer");
    require_cuda(cudaDeviceSynchronize(), "synchronize tiled alpha renderer");

    AlphaRenderOutput output{
        .width = camera.width,
        .height = camera.height,
        .rgb = std::vector<float>(pixel_count * 3U),
        .transmittance = std::vector<float>(pixel_count),
        .stats = {
            .visible_splats = splats.size(),
        },
    };
    device_rgb.copy_to_host(output.rgb.data());
    device_transmittance.copy_to_host(output.transmittance.data());
    DeviceRenderStats stats{};
    device_stats.copy_to_host(&stats);
    output.stats.evaluated_pairs =
        static_cast<std::uint64_t>(stats.evaluated_pairs);
    output.stats.contributing_pairs =
        static_cast<std::uint64_t>(stats.contributing_pairs);
    return output;
}

}  // namespace dronegs
