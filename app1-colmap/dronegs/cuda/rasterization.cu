// SPDX-License-Identifier: MIT
#include "dronegs/rasterization.hpp"

#include <cub/cub.cuh>
#include <cuda_runtime.h>

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
constexpr std::uint32_t threads_per_block = 256U;

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
        const float world_sigma = expf(
            (gaussian.log_scale[0] + gaussian.log_scale[1] +
             gaussian.log_scale[2]) /
            3.0F);
        const float focal = 0.5F * (camera.fx + camera.fy);
        const float sigma =
            fminf(8.0F, fmaxf(0.75F, world_sigma * focal / camera_z));
        const float support = 2.5F * sigma;
        const bool visible =
            isfinite(x) && isfinite(y) && isfinite(sigma) &&
            x + support >= 0.0F && y + support >= 0.0F &&
            x - support < static_cast<float>(camera.width) &&
            y - support < static_cast<float>(camera.height);
        if (visible) {
            const int pixel_support =
                static_cast<int>(ceilf(2.5F * sigma));
            const int center_x = static_cast<int>(floorf(x));
            const int center_y = static_cast<int>(floorf(y));
            const int minimum_x = max(0, center_x - pixel_support);
            const int maximum_x = min(
                static_cast<int>(camera.width) - 1,
                center_x + pixel_support);
            const int minimum_y = max(0, center_y - pixel_support);
            const int maximum_y = min(
                static_cast<int>(camera.height) - 1,
                center_y + pixel_support);
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
                .sigma = sigma,
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

__global__ void backward_alpha_tiles_kernel(
    const DeviceProjectedRecord* records,
    const std::uint32_t* record_indices,
    const std::uint64_t* tile_starts, const std::uint64_t* tile_ends,
    std::uint32_t width, std::uint32_t height,
    float background_r, float background_g, float background_b,
    const float* image_gradient, float* dc_gradient,
    float* opacity_logit_gradient) {
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
        const float delta_x =
            (static_cast<float>(x) + 0.5F) - splat.x;
        const float delta_y =
            (static_cast<float>(y) + 0.5F) - splat.y;
        const float gaussian_weight = expf(
            -(delta_x * delta_x + delta_y * delta_y) *
            (0.5F / (splat.sigma * splat.sigma)));
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
        const float delta_x =
            (static_cast<float>(x) + 0.5F) - splat.x;
        const float delta_y =
            (static_cast<float>(y) + 0.5F) - splat.y;
        const float gaussian_weight = expf(
            -(delta_x * delta_x + delta_y * delta_y) *
            (0.5F / (splat.sigma * splat.sigma)));
        const float raw_alpha = splat.opacity * gaussian_weight;
        const float alpha = fminf(alpha_maximum, raw_alpha);
        if (alpha < alpha_minimum_contribution) {
            continue;
        }
        const float transmittance_before =
            transmittance_after / (1.0F - alpha);
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
        }
        for (std::size_t channel = 0U; channel < 3U; ++channel) {
            tail[channel] =
                alpha * splat.color[channel] +
                (1.0F - alpha) * tail[channel];
        }
        transmittance_after = transmittance_before;
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
    if (image_gradient != nullptr) {
        device_image_gradient.emplace(image_gradient->size());
        device_dc_gradient.emplace(gaussians.size() * 3U);
        device_opacity_logit_gradient.emplace(gaussians.size());
        device_image_gradient->copy_from_host(image_gradient->data());
        device_dc_gradient->zero();
        device_opacity_logit_gradient->zero();
        backward_alpha_tiles_kernel<<<render_blocks, render_threads>>>(
            device_sorted_records.data(),
            device_sorted_record_indices.data(),
            device_tile_starts.data(), device_tile_ends.data(),
            camera.width, camera.height,
            background[0], background[1], background[2],
            device_image_gradient->data(), device_dc_gradient->data(),
            device_opacity_logit_gradient->data());
        require_cuda(cudaGetLastError(), "launch tiled alpha backward");
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
        device_dc_gradient->copy_to_host(gradients.dc.front().data());
        device_opacity_logit_gradient->copy_to_host(
            gradients.opacity_logit.data());
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

}  // namespace dronegs
