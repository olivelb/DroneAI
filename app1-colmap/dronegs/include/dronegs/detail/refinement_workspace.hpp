// SPDX-License-Identifier: MIT
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <span>
#include <stdexcept>
#include <type_traits>
#include <vector>

namespace dronegs::detail {

// Transient pruning input only; not a Gaussian ABI or checkpoint format.
struct PruningSnapshot {
    std::array<float, 3U> xyz;
    std::array<float, 3U> log_scale;
    float opacity_logit;
    std::uint32_t opacity_sh_finite;
};
static_assert(sizeof(PruningSnapshot) == 32U);
static_assert(std::is_trivially_copyable_v<PruningSnapshot>);
static_assert(std::is_trivially_default_constructible_v<PruningSnapshot>);
static_assert(sizeof(float) == 4U && std::numeric_limits<float>::is_iec559);

struct RefinementHostViews {
    std::span<PruningSnapshot> pruning;
    std::span<float> weights;
    std::span<float> visibility;
    std::span<float> edge_weights;
    std::span<float> absgrad_sum;
    std::span<float> absgrad_count;
};

// One context owns this scratch. Every active element must be overwritten by
// completed downloads before it is read. No checkpointed or scientific state.
// Views survive only until growth/destruction; shrink does not free storage.
class RefinementHostWorkspace {
public:
    static constexpr std::size_t bytes_per_gaussian = sizeof(PruningSnapshot) + 5U * sizeof(float);

    RefinementHostWorkspace() = default;
    RefinementHostWorkspace(const RefinementHostWorkspace&) = delete;
    RefinementHostWorkspace& operator=(const RefinementHostWorkspace&) = delete;
    RefinementHostWorkspace(RefinementHostWorkspace&&) = delete;
    RefinementHostWorkspace& operator=(RefinementHostWorkspace&&) = delete;

    RefinementHostViews prepare(std::size_t count) {
        if (count > std::numeric_limits<std::size_t>::max() / bytes_per_gaussian) {
            throw std::length_error("refinement host workspace size overflow");
        }
        if (count > capacity_) {
            // Keep the reference's six separate value-initialized vectors.
            // Combining statistics or omitting initialization regressed fresh
            // contexts. All allocations succeed before replacing old storage.
            std::vector<PruningSnapshot> pruning(count);
            std::array<std::vector<float>, 5U> statistics;
            for (auto& values : statistics) values = std::vector<float>(count);
            pruning_.swap(pruning);
            statistics_.swap(statistics);
            capacity_ = count;
        }
        if (count == 0U) return {};
        return {
            {pruning_.data(), count},
            {statistics_[0].data(), count},
            {statistics_[1].data(), count},
            {statistics_[2].data(), count},
            {statistics_[3].data(), count},
            {statistics_[4].data(), count},
        };
    }

    std::size_t capacity() const noexcept { return capacity_; }
    std::size_t retained_bytes() const noexcept { return capacity_ * bytes_per_gaussian; }

private:
    std::vector<PruningSnapshot> pruning_;
    std::array<std::vector<float>, 5U> statistics_;
    std::size_t capacity_ = 0U;
};

}  // namespace dronegs::detail
