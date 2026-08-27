// SPDX-License-Identifier: MIT
#include "../cuda/topology_snapshot.cuh"

#include <algorithm>
#include <array>
#include <cmath>
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

void compare_cpu_snapshot(std::size_t count, std::size_t scratch_rows) {
    using Snapshot = dronegs::detail::PruningSnapshot;
    std::vector<dronegs::Gaussian> original(count);
    std::vector<std::uint32_t> words(count * sizeof(dronegs::Gaussian) / sizeof(std::uint32_t));
    for (std::size_t i = 0; i < words.size(); ++i) {
        words[i] = static_cast<std::uint32_t>(i * 2654435761U);
    }
    std::memcpy(original.data(), words.data(), words.size() * sizeof(std::uint32_t));
    // Every opacity-SH lane separately: signed zeros, denormals, normals,
    // infinities, positive/negative quiet/signaling NaNs and payloads.
    constexpr std::array<std::uint32_t, 16U> special{
        0U, 0x80000000U, 1U, 0x80000001U, 0x007fffffU, 0x00800000U,
        0x3f800000U, 0xbf800000U, 0x7f7fffffU, 0xff7fffffU,
        0x7f800000U, 0xff800000U, 0x7fc00123U, 0xffc00123U,
        0x7f800001U, 0xff800001U};
    for (std::size_t i = 0; i < count; ++i) {
        auto& gaussian = original[i];
        gaussian.opacity_sh.fill(1.25F);
        const auto bits = special[(i / dronegs::maximum_opacity_sh_coefficients) % special.size()];
        std::memcpy(&gaussian.opacity_sh[i % dronegs::maximum_opacity_sh_coefficients], &bits, sizeof(bits));
        // These ignored fields must not influence pruning validity.
        gaussian.dc.fill(std::numeric_limits<float>::quiet_NaN());
        gaussian.sh_rest.fill(std::numeric_limits<float>::infinity());
        gaussian.rotation.fill(std::numeric_limits<float>::quiet_NaN());
        if (i < special.size()) {
            std::memcpy(&gaussian.xyz[0], &special[i], sizeof(float));
            std::memcpy(&gaussian.log_scale[1], &special[i], sizeof(float));
            std::memcpy(&gaussian.opacity_logit, &special[i], sizeof(float));
        }
    }
    std::vector<Snapshot> expected(count + 2U);
    std::memset(expected.data(), 0x5a, expected.size() * sizeof(Snapshot));
    auto actual = expected;
    for (std::size_t i = 0; i < count; ++i) {
        auto& snapshot = expected[i + 1U];
        snapshot.xyz = original[i].xyz;
        snapshot.log_scale = original[i].log_scale;
        snapshot.opacity_logit = original[i].opacity_logit;
        snapshot.opacity_sh_finite = std::all_of(original[i].opacity_sh.begin(), original[i].opacity_sh.end(),
            [](float coefficient) { return std::isfinite(coefficient); }) ? 1U : 0U;
    }
    Buffer<dronegs::Gaussian> source(count);
    const auto scratch_bytes = scratch_rows * sizeof(Snapshot) + 3U;
    Buffer<std::uint8_t> scratch(scratch_bytes);
    check(cudaMemset(scratch.data, 0xa5, scratch_bytes));
    check(cudaMemcpy(source.data, original.data(), count * sizeof(dronegs::Gaussian), cudaMemcpyHostToDevice));
    check(dronegs::detail::download_pruning_snapshot(source.data, count, actual.data() + 1U, scratch.data, scratch_bytes));
    if (std::memcmp(actual.data(), expected.data(), actual.size() * sizeof(Snapshot)) != 0) {
        throw std::runtime_error("compact pruning snapshot differs from CPU fields/finiteness or overwrites host guards");
    }
    const auto used_bytes = std::min<std::size_t>(scratch_bytes, 16U * 1024U * 1024U) / sizeof(Snapshot) * sizeof(Snapshot);
    std::vector<std::uint8_t> scratch_tail(scratch_bytes - used_bytes);
    check(cudaMemcpy(scratch_tail.data(), scratch.data + used_bytes, scratch_tail.size(), cudaMemcpyDeviceToHost));
    if (!std::all_of(scratch_tail.begin(), scratch_tail.end(), [](auto value) { return value == 0xa5U; })) {
        throw std::runtime_error("pruning snapshot exceeded its scratch/chunk cap");
    }
    std::vector<dronegs::Gaussian> after(count);
    check(cudaMemcpy(after.data(), source.data, count * sizeof(dronegs::Gaussian), cudaMemcpyDeviceToHost));
    if (std::memcmp(after.data(), original.data(), count * sizeof(dronegs::Gaussian)) != 0) {
        throw std::runtime_error("pruning snapshot changed device Gaussian state");
    }
    const auto invalid = [&](const dronegs::Gaussian* input, std::size_t rows, Snapshot* output,
                             void* workspace, std::size_t bytes) {
        if (dronegs::detail::download_pruning_snapshot(input, rows, output, workspace, bytes) != cudaErrorInvalidValue) {
            throw std::runtime_error("invalid pruning snapshot buffer bounds accepted");
        }
    };
    invalid(nullptr, count, actual.data(), scratch.data, scratch_bytes);
    invalid(source.data, count, nullptr, scratch.data, scratch_bytes);
    invalid(source.data, count, actual.data(), nullptr, scratch_bytes);
    invalid(source.data, count, actual.data(), source.data, scratch_bytes);
    invalid(source.data, count, actual.data(), scratch.data, sizeof(Snapshot) - 1U);
    invalid(source.data, count, actual.data(), scratch.data + 1U, scratch_bytes - 1U);
    invalid(source.data, std::numeric_limits<std::size_t>::max(), actual.data(), scratch.data, scratch_bytes);
}
}  // namespace

void test_topology_pruning_snapshot() {
    check(dronegs::detail::download_pruning_snapshot(nullptr, 0U, nullptr, nullptr, 0U));
    compare_cpu_snapshot(1U, 1U);
    compare_cpu_snapshot(2U, 1U);
    compare_cpu_snapshot(257U, 1U);
    compare_cpu_snapshot(257U, 17U);
    compare_cpu_snapshot(524293U, 524293U);  // Exceed the internal 16 MiB cap.
}
