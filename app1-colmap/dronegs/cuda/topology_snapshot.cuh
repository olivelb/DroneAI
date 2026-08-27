// SPDX-License-Identifier: MIT
#pragma once

#include "dronegs/types.hpp"
#include "dronegs/detail/refinement_workspace.hpp"

#include <cuda_runtime.h>
#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <type_traits>

namespace dronegs::detail {

// Transient pruning input only; not a Gaussian ABI or checkpoint format.
// All arithmetic (including exp/percentiles) stays on the CPU. Opacity SH is
// only inspected for finiteness, even when directional opacity is disabled.
static __global__ void pack_pruning_snapshot(
    const Gaussian* source, PruningSnapshot* destination,
    std::size_t offset, std::size_t count) {
    const auto index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (index >= count) return;
    const auto& gaussian = source[offset + index];
    auto& snapshot = destination[index];
    snapshot.xyz = gaussian.xyz;
    snapshot.log_scale = gaussian.log_scale;
    snapshot.opacity_logit = gaussian.opacity_logit;
    std::uint32_t finite = 1U;
    for (std::size_t coefficient = 0U; coefficient < maximum_opacity_sh_coefficients; ++coefficient) {
        // Integer exponent inspection preserves NaN/Inf classification without
        // rounding, denormal flushing or dependence on device fast-math flags.
        const auto bits = __float_as_uint(gaussian.opacity_sh[coefficient]);
        finite &= (bits & 0x7f800000U) != 0x7f800000U ? 1U : 0U;
    }
    snapshot.opacity_sh_finite = finite;
}

// Caller provides count writable host rows and a distinct, disposable device
// scratch buffer. Prior Adam work, packing and synchronous D2H copies all use
// the default stream. Each download completes before the scratch is reused;
// no explicit fence or device allocation is necessary.
inline cudaError_t download_pruning_snapshot(
    const Gaussian* source, std::size_t count, PruningSnapshot* host,
    void* scratch, std::size_t scratch_bytes) {
    if (count == 0U) return cudaSuccess;
    if (source == nullptr || host == nullptr || scratch == nullptr ||
        scratch == source || reinterpret_cast<std::uintptr_t>(scratch) % alignof(PruningSnapshot) != 0U ||
        count > std::numeric_limits<std::size_t>::max() / sizeof(Gaussian) ||
        count > std::numeric_limits<std::size_t>::max() / sizeof(PruningSnapshot)) {
        return cudaErrorInvalidValue;
    }
    constexpr std::size_t maximum_scratch_bytes = 16U * 1024U * 1024U;
    const auto chunk_rows = std::min(scratch_bytes, maximum_scratch_bytes) / sizeof(PruningSnapshot);
    if (chunk_rows == 0U) return cudaErrorInvalidValue;
    constexpr std::uint32_t block_size = 256U;
    for (std::size_t offset = 0U; offset < count;) {
        const auto rows = std::min(chunk_rows, count - offset);
        const auto blocks = static_cast<std::uint32_t>((rows + block_size - 1U) / block_size);
        pack_pruning_snapshot<<<blocks, block_size>>>(
            source, static_cast<PruningSnapshot*>(scratch), offset, rows);
        auto result = cudaGetLastError();
        if (result != cudaSuccess) return result;
        result = cudaMemcpy(host + offset, scratch, rows * sizeof(PruningSnapshot), cudaMemcpyDeviceToHost);
        if (result != cudaSuccess) return result;
        offset += rows;
    }
    return cudaSuccess;
}

}  // namespace dronegs::detail
