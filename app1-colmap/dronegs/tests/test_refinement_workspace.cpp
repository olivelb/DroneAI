// SPDX-License-Identifier: MIT
#include "dronegs/detail/refinement_workspace.hpp"

#include <algorithm>
#include <array>
#include <bit>
#include <cstdint>
#include <cstring>
#include <limits>
#include <stdexcept>

namespace {
void require(bool valid) {
    if (!valid) throw std::runtime_error("reusable refinement host workspace contract failed");
}

auto fields(dronegs::detail::RefinementHostViews views) {
    return std::array{views.weights, views.visibility, views.edge_weights, views.absgrad_sum, views.absgrad_count};
}
constexpr std::array<std::uint32_t, 5U> sentinels{
    0x80000000U, 0x7fc01234U, 0xff800000U, 0x00000001U, 0x3f123456U};
}  // namespace

void test_refinement_workspace() {
    dronegs::detail::RefinementHostWorkspace workspace;
    require(workspace.capacity() == 0U && workspace.retained_bytes() == 0U);
    std::size_t peak = 0U;
    for (const std::size_t count : {0U, 1U, 257U, 17U, 257U, 0U, 1025U, 3U, 1025U, 262145U, 9U}) {
        auto views = workspace.prepare(count);
        peak = std::max(peak, count);
        require(workspace.capacity() == peak && workspace.retained_bytes() == peak * 52U);
        require(views.pruning.size() == count);
        auto statistics = fields(views);
        for (std::size_t field = 0U; field < statistics.size(); ++field) {
            require(statistics[field].size() == count);
            if (count != 0U) require(statistics[field].data() == statistics[0].data() + field * peak);
            // Initialize every active element, including non-finite/signed-zero
            // sentinels. No read of a fresh overwrite allocation is permitted.
            std::fill(statistics[field].begin(), statistics[field].end(), std::bit_cast<float>(sentinels[field]));
        }
        if (count != 0U) std::memset(views.pruning.data(), 0xa5, count * sizeof(views.pruning[0]));
        const auto same = workspace.prepare(count);
        require(same.pruning.data() == views.pruning.data());
        const auto same_statistics = fields(same);
        for (std::size_t field = 0U; field < statistics.size(); ++field) {
            require(same_statistics[field].data() == statistics[field].data());
            for (const auto value : same_statistics[field]) require(std::bit_cast<std::uint32_t>(value) == sentinels[field]);
        }
        if (count != 0U) {
            const auto* bytes = reinterpret_cast<const unsigned char*>(same.pruning.data());
            for (std::size_t i = 0U; i < count * sizeof(same.pruning[0]); ++i) require(bytes[i] == 0xa5U);
            const auto smaller = workspace.prepare(count / 2U);
            const auto regrown = workspace.prepare(count);
            require(smaller.pruning.size() == count / 2U && regrown.pruning.data() == views.pruning.data());
            require(fields(regrown)[4].data() == statistics[4].data());
        }
    }
    const auto bytes_before = workspace.retained_bytes();
    bool rejected = false;
    try {
        static_cast<void>(workspace.prepare(std::numeric_limits<std::size_t>::max() / 52U + 1U));
    } catch (const std::length_error&) {
        rejected = true;
    }
    require(rejected && workspace.retained_bytes() == bytes_before);
    dronegs::detail::RefinementHostWorkspace independent;
    auto other = independent.prepare(9U);
    require(other.pruning.data() != workspace.prepare(9U).pruning.data());
}
