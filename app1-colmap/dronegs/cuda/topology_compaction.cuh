// SPDX-License-Identifier: MIT
#pragma once

#include <cuda_runtime.h>
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <limits>

namespace dronegs::detail {

template <typename T>
__global__ void gather_survivor_values(
    const T* source, T* scratch, const std::uint32_t* survivors,
    std::size_t offset, std::size_t count, std::size_t components) {
    const auto value = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (value < count * components) {
        const auto destination = value / components;
        scratch[value] = source[static_cast<std::size_t>(survivors[offset + destination]) * components +
                                value % components];
    }
}

// Preconditions: survivor indices are strictly increasing and in range;
// scratch is aligned for T, distinct from source/indices, and disposable.
// Ascending destinations make chunked in-place compaction safe: a committed
// chunk ends before every later destination, hence before every later source
// (survivors[d] >= d). Gather must complete before its D2D commit, and the
// commit before the next gather. A single stream establishes that order.
template <typename T>
cudaError_t compact_survivor_values(
    T* source, std::size_t source_count, std::size_t survivor_count,
    std::size_t components, const std::uint32_t* survivors,
    void* scratch, std::size_t scratch_bytes, cudaStream_t stream = nullptr) {
    if (components == 0U || components > std::numeric_limits<std::size_t>::max() / sizeof(T) ||
        survivor_count > source_count || source == nullptr || survivors == nullptr ||
        scratch == nullptr || scratch == source ||
        reinterpret_cast<std::uintptr_t>(scratch) % alignof(T) != 0U) {
        return cudaErrorInvalidValue;
    }
    const auto row_bytes = components * sizeof(T);
    if (source_count > std::numeric_limits<std::size_t>::max() / row_bytes) return cudaErrorInvalidValue;
    // Limit launch size and temporary working set even if the borrowed buffer is huge.
    constexpr std::size_t maximum_scratch_bytes = 16U * 1024U * 1024U;
    const auto chunk_rows = std::min(scratch_bytes, maximum_scratch_bytes) / row_bytes;
    if (chunk_rows == 0U) return cudaErrorInvalidValue;
    constexpr std::uint32_t block_size = 256U;
    for (std::size_t offset = 0U; offset < survivor_count;) {
        const auto count = std::min(chunk_rows, survivor_count - offset);
        const auto blocks = static_cast<std::uint32_t>((count * components + block_size - 1U) / block_size);
        gather_survivor_values<<<blocks, block_size, 0U, stream>>>(
            source, static_cast<T*>(scratch), survivors, offset, count, components);
        auto result = cudaGetLastError();
        if (result != cudaSuccess) return result;
        result = cudaMemcpyAsync(source + offset * components, scratch,
                                 count * row_bytes, cudaMemcpyDeviceToDevice, stream);
        if (result != cudaSuccess) return result;
        offset += count;
    }
    return cudaSuccess;
}

}  // namespace dronegs::detail
