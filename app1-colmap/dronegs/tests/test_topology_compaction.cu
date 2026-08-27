// SPDX-License-Identifier: MIT
#include "../cuda/topology_compaction.cuh"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <vector>

namespace {
void check(cudaError_t result) {
    if (result != cudaSuccess) throw std::runtime_error(cudaGetErrorString(result));
}

template <typename T> struct Buffer {
    explicit Buffer(std::size_t count) {
        check(cudaMalloc(reinterpret_cast<void**>(&data), count * sizeof(T)));
    }
    ~Buffer() { static_cast<void>(cudaFree(data)); }
    Buffer(const Buffer&) = delete;
    Buffer& operator=(const Buffer&) = delete;
    T* data = nullptr;
};

template <typename T>
void compare_cpu_gather(std::size_t count, std::size_t components,
                        const std::vector<std::uint32_t>& survivors, std::size_t scratch_rows) {
    std::vector<T> original(count * components);
    // Exercise every bit, signed zero, denormals and NaN payloads: compaction
    // is a byte-preserving move, not floating-point arithmetic.
    std::vector<std::uint32_t> words(original.size() * sizeof(T) / sizeof(std::uint32_t));
    for (std::size_t i = 0; i < words.size(); ++i) {
        words[i] = static_cast<std::uint32_t>(i * 2654435761U);
        if (i % 17U == 0U) words[i] = 0x80000000U;
        if (i % 19U == 0U) words[i] = 0x7fc00123U;
    }
    std::memcpy(original.data(), words.data(), words.size() * sizeof(std::uint32_t));
    auto expected = original;
    for (std::size_t d = 0; d < survivors.size(); ++d) {
        std::copy_n(original.data() + survivors[d] * components, components, expected.data() + d * components);
    }
    Buffer<T> device(original.size());
    Buffer<std::uint32_t> indices(std::max<std::size_t>(1U, survivors.size()));
    const auto scratch_bytes = scratch_rows * components * sizeof(T) + 3U;
    Buffer<std::uint8_t> scratch(scratch_bytes);
    check(cudaMemcpy(device.data, original.data(), original.size() * sizeof(T), cudaMemcpyHostToDevice));
    if (!survivors.empty()) {
        check(cudaMemcpy(indices.data, survivors.data(), survivors.size() * sizeof(std::uint32_t), cudaMemcpyHostToDevice));
    }
    check(dronegs::detail::compact_survivor_values(
        device.data, count, survivors.size(), components, indices.data, scratch.data, scratch_bytes));
    std::vector<T> actual(original.size());
    check(cudaMemcpy(actual.data(), device.data, actual.size() * sizeof(T), cudaMemcpyDeviceToHost));
    if (std::memcmp(actual.data(), expected.data(), actual.size() * sizeof(T)) != 0) {
        throw std::runtime_error("GPU stable compaction differs from CPU gather or overwrote the tail");
    }
    const auto invalid = [&](std::size_t source_count, std::size_t survivors_count,
                             std::size_t fields, void* workspace, std::size_t bytes) {
        if (dronegs::detail::compact_survivor_values(
            device.data, source_count, survivors_count, fields, indices.data, workspace, bytes) != cudaErrorInvalidValue) {
            throw std::runtime_error("invalid GPU compaction bounds were accepted");
        }
    };
    invalid(count, count + 1U, components, scratch.data, scratch_bytes);
    invalid(count, 1U, 0U, scratch.data, scratch_bytes);
    invalid(count, 1U, components, nullptr, scratch_bytes);
    invalid(count, 1U, components, device.data, scratch_bytes);
    invalid(count, 1U, components, scratch.data, components * sizeof(T) - 1U);
    invalid(std::numeric_limits<std::size_t>::max(), 1U, components, scratch.data, scratch_bytes);
}

template <typename T> void check_components(std::size_t components) {
    compare_cpu_gather<T>(1U, components, {0U}, 1U);
    compare_cpu_gather<T>(2U, components, {1U}, 1U);
    compare_cpu_gather<T>(257U, components, {}, 1U);
    compare_cpu_gather<T>(257U, components, {256U}, 1U);
    std::vector<std::uint32_t> identity, sparse, shift;
    for (std::uint32_t i = 0U; i < 257U; ++i) {
        identity.push_back(i);
        if (i % 3U != 1U) sparse.push_back(i);
        if (i != 0U) shift.push_back(i);
    }
    compare_cpu_gather<T>(257U, components, identity, 17U);
    compare_cpu_gather<T>(257U, components, sparse, 17U);
    compare_cpu_gather<T>(257U, components, shift, 17U);
}
}  // namespace

void test_topology_device_compaction() {
    check_components<float>(1U);
    check_components<float>(3U);
    check_components<float>(4U);
    check_components<float2>(15U);
    check_components<float2>(45U);
    std::vector<std::uint32_t> shift;
    for (std::uint32_t i = 1U; i < 65539U; ++i) shift.push_back(i);
    // Exceed the internal 16 MiB cap; cross-chunk source/destination overlap.
    compare_cpu_gather<float2>(65539U, 45U, shift, 65539U);
}
