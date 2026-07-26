// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (c) 2026 DroneAI contributors
//
// The MRNF local-scale formula is adapted from LichtFeld-Studio
// src/core/splat_data.cpp at commit
// 1004c0841a3776e3f67866ff34101fbc9677397f. The balanced KD tree is an
// independent DroneAI implementation.
#include "dronegs/model.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <future>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <thread>
#include <vector>

namespace dronegs {
namespace {

using Point = std::array<float, 3>;

constexpr std::uint32_t no_node = std::numeric_limits<std::uint32_t>::max();

struct KdNode {
    std::uint32_t point = no_node;
    std::uint32_t left = no_node;
    std::uint32_t right = no_node;
    std::uint8_t axis = 0U;
};

class KdTree {
public:
    explicit KdTree(const std::vector<Point>& points)
        : points_(points), order_(points.size()) {
        if (points.size() > no_node) {
            throw std::overflow_error("too many points for local-scale KD tree");
        }
        std::iota(order_.begin(), order_.end(), std::uint32_t{0});
        nodes_.reserve(points.size());
        root_ = build(0U, order_.size(), 0U);
    }

    [[nodiscard]] std::array<float, 2> nearest_two(
        std::uint32_t query_index) const {
        std::array<float, 2> best{
            std::numeric_limits<float>::infinity(),
            std::numeric_limits<float>::infinity(),
        };
        query(root_, query_index, best);
        return best;
    }

private:
    [[nodiscard]] std::uint32_t build(
        std::size_t begin, std::size_t end, std::uint8_t depth) {
        if (begin == end) {
            return no_node;
        }
        const std::uint8_t axis = static_cast<std::uint8_t>(depth % 3U);
        const std::size_t middle = begin + (end - begin) / 2U;
        std::nth_element(
            order_.begin() + static_cast<std::ptrdiff_t>(begin),
            order_.begin() + static_cast<std::ptrdiff_t>(middle),
            order_.begin() + static_cast<std::ptrdiff_t>(end),
            [this, axis](std::uint32_t left, std::uint32_t right) {
                const float left_value = points_[left][axis];
                const float right_value = points_[right][axis];
                return left_value < right_value ||
                       (left_value == right_value && left < right);
            });

        const auto node_index = static_cast<std::uint32_t>(nodes_.size());
        nodes_.push_back(KdNode{.point = order_[middle], .axis = axis});
        const auto next_depth = static_cast<std::uint8_t>(depth + 1U);
        nodes_[node_index].left = build(begin, middle, next_depth);
        nodes_[node_index].right = build(middle + 1U, end, next_depth);
        return node_index;
    }

    void query(
        std::uint32_t node_index,
        std::uint32_t query_index,
        std::array<float, 2>& best) const {
        if (node_index == no_node || best[1] == 0.0F) {
            return;
        }
        const auto& node = nodes_[node_index];
        const auto& target = points_[query_index];
        const auto& candidate = points_[node.point];

        if (node.point != query_index) {
            float squared_distance = 0.0F;
            for (std::size_t axis = 0U; axis < 3U; ++axis) {
                const float delta = candidate[axis] - target[axis];
                squared_distance += delta * delta;
            }
            if (squared_distance < best[0]) {
                best[1] = best[0];
                best[0] = squared_distance;
            } else if (squared_distance < best[1]) {
                best[1] = squared_distance;
            }
        }

        const float split_delta = target[node.axis] - candidate[node.axis];
        const std::uint32_t near =
            split_delta <= 0.0F ? node.left : node.right;
        const std::uint32_t far =
            split_delta <= 0.0F ? node.right : node.left;
        query(near, query_index, best);
        if (split_delta * split_delta <= best[1]) {
            query(far, query_index, best);
        }
    }

    const std::vector<Point>& points_;
    std::vector<std::uint32_t> order_;
    std::vector<KdNode> nodes_;
    std::uint32_t root_ = no_node;
};

[[nodiscard]] std::vector<Point> finite_points(const Scene& scene) {
    std::vector<Point> points;
    points.reserve(scene.points.size());
    for (const auto& sparse_point : scene.points) {
        Point point{};
        for (std::size_t axis = 0U; axis < 3U; ++axis) {
            if (!std::isfinite(sparse_point.xyz[axis])) {
                throw std::invalid_argument(
                    "cannot initialize Gaussians from non-finite sparse points");
            }
            point[axis] = static_cast<float>(sparse_point.xyz[axis]);
        }
        points.push_back(point);
    }
    return points;
}

[[nodiscard]] float robust_max_scale(const std::vector<Point>& points) {
    constexpr float central_percentile = 0.75F;
    std::array<std::vector<float>, 3> coordinates;
    for (auto& values : coordinates) {
        values.reserve(points.size());
    }
    for (const auto& point : points) {
        for (std::size_t axis = 0U; axis < 3U; ++axis) {
            coordinates[axis].push_back(point[axis]);
        }
    }

    std::array<float, 3> half_extents{};
    for (std::size_t axis = 0U; axis < 3U; ++axis) {
        auto& values = coordinates[axis];
        std::sort(values.begin(), values.end());
        const auto count = values.size();
        const auto lower = static_cast<std::size_t>(
            ((1.0F - central_percentile) * 0.5F) *
            static_cast<float>(count));
        const auto upper = std::min(
            count - 1U,
            static_cast<std::size_t>(
                ((1.0F + central_percentile) * 0.5F) *
                static_cast<float>(count)));
        half_extents[axis] = (values[upper] - values[lower]) * 0.5F;
    }
    std::sort(half_extents.begin(), half_extents.end());
    const float median_size = std::max(half_extents[1] * 2.0F, 0.01F);
    return median_size * 0.1F;
}

[[nodiscard]] std::vector<float> local_log_scales(
    const std::vector<Point>& points) {
    if (points.size() < 3U) {
        return std::vector<float>(points.size(), 0.0F);
    }

    const float maximum_scale = robust_max_scale(points);
    const KdTree tree(points);
    std::vector<float> result(points.size());

    const auto hardware_threads =
        std::max(1U, std::thread::hardware_concurrency());
    const std::size_t worker_count = std::min<std::size_t>(
        points.size(), static_cast<std::size_t>(hardware_threads));
    const std::size_t chunk =
        (points.size() + worker_count - 1U) / worker_count;
    std::vector<std::future<void>> workers;
    workers.reserve(worker_count);
    for (std::size_t worker = 0U; worker < worker_count; ++worker) {
        const std::size_t begin = worker * chunk;
        const std::size_t end = std::min(points.size(), begin + chunk);
        if (begin == end) {
            break;
        }
        workers.push_back(std::async(
            std::launch::async,
            [&tree, &result, begin, end, maximum_scale]() {
                for (std::size_t index = begin; index < end; ++index) {
                    const auto distances = tree.nearest_two(
                        static_cast<std::uint32_t>(index));
                    const float first =
                        std::sqrt(std::max(distances[0], 0.0F));
                    const float second =
                        std::sqrt(std::max(distances[1], 0.0F));
                    const float scale = std::clamp(
                        (first + second) * 0.25F,
                        1.0e-3F,
                        maximum_scale);
                    result[index] = std::log(scale);
                }
            }));
    }
    for (auto& worker : workers) {
        worker.get();
    }
    return result;
}

}  // namespace

std::vector<Gaussian> initialize_fixed_topology(const Scene& scene) {
    if (scene.points.empty()) {
        throw std::invalid_argument(
            "cannot initialize Gaussians without sparse points");
    }
    const auto points = finite_points(scene);
    const auto log_scales = local_log_scales(points);
    constexpr double sh_c0 = 0.28209479177387814;
    constexpr float opacity_logit = -2.197224577F;  // logit(0.1)

    std::vector<Gaussian> gaussians;
    gaussians.reserve(scene.points.size());
    for (std::size_t index = 0U; index < scene.points.size(); ++index) {
        const auto& point = scene.points[index];
        Gaussian gaussian;
        for (std::size_t axis = 0; axis < 3; ++axis) {
            gaussian.xyz[axis] = points[index][axis];
            const double color = static_cast<double>(point.rgb[axis]) / 255.0;
            gaussian.dc[axis] = static_cast<float>((color - 0.5) / sh_c0);
            gaussian.log_scale[axis] = log_scales[index];
        }
        gaussian.opacity_logit = opacity_logit;
        gaussians.push_back(gaussian);
    }
    return gaussians;
}

}  // namespace dronegs
