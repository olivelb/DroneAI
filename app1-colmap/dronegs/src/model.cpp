// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (c) 2026 DroneAI contributors
//
// The MRNF local-scale formula is adapted from LichtFeld-Studio
// src/core/splat_data.cpp at commit
// 1004c0841a3776e3f67866ff34101fbc9677397f. The balanced KD tree is an
// independent DroneAI implementation.
#include "dronegs/model.hpp"
#include "dronegs/image.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <future>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <thread>
#include <unordered_map>
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

struct InitializationView {
    std::array<double, 9> rotation{};
    std::array<double, 3> translation{};
    ImageRegion region;
    double source_fx = 0.0;
    double source_fy = 0.0;
    double source_cx = 0.0;
    double source_cy = 0.0;
    double target_fx = 0.0;
    double target_fy = 0.0;
};

[[nodiscard]] std::array<double, 9> quaternion_rotation(
    const Image& image) {
    const double norm = std::sqrt(
        image.qvec[0] * image.qvec[0] +
        image.qvec[1] * image.qvec[1] +
        image.qvec[2] * image.qvec[2] +
        image.qvec[3] * image.qvec[3]);
    if (!std::isfinite(norm) || norm <= 1.0e-12) {
        throw std::invalid_argument(
            "projected scale initialization requires finite camera poses");
    }
    const double w = image.qvec[0] / norm;
    const double x = image.qvec[1] / norm;
    const double y = image.qvec[2] / norm;
    const double z = image.qvec[3] / norm;
    return {
        1.0 - 2.0 * (y * y + z * z),
        2.0 * (x * y - z * w),
        2.0 * (x * z + y * w),
        2.0 * (x * y + z * w),
        1.0 - 2.0 * (x * x + z * z),
        2.0 * (y * z - x * w),
        2.0 * (x * z - y * w),
        2.0 * (y * z + x * w),
        1.0 - 2.0 * (x * x + y * y),
    };
}

[[nodiscard]] std::vector<InitializationView> initialization_views(
    const Scene& scene,
    const InitialScaleConfiguration& configuration) {
    std::unordered_map<std::uint32_t, const Camera*> cameras;
    cameras.reserve(scene.cameras.size());
    for (const auto& camera : scene.cameras) {
        cameras.emplace(camera.id, &camera);
    }

    std::vector<InitializationView> views;
    views.reserve(
        scene.images.size() *
        static_cast<std::size_t>(configuration.tile_mode));
    for (const auto& image : scene.images) {
        const auto camera_entry = cameras.find(image.camera_id);
        if (camera_entry == cameras.end()) {
            throw std::invalid_argument(
                "projected scale initialization found an unknown camera");
        }
        const auto& camera = *camera_entry->second;
        if (camera.model_id != 0 && camera.model_id != 1) {
            throw std::invalid_argument(
                "projected scale initialization supports pinhole cameras only");
        }
        const auto expected_parameters = camera.model_id == 0 ? 3U : 4U;
        if (camera.parameters.size() != expected_parameters ||
            camera.width > std::numeric_limits<std::uint32_t>::max() ||
            camera.height > std::numeric_limits<std::uint32_t>::max()) {
            throw std::invalid_argument(
                "projected scale initialization found invalid intrinsics");
        }
        const double source_fx = camera.parameters[0];
        const double source_fy = camera.model_id == 0
            ? camera.parameters[0]
            : camera.parameters[1];
        const double source_cx = camera.model_id == 0
            ? camera.parameters[1]
            : camera.parameters[2];
        const double source_cy = camera.model_id == 0
            ? camera.parameters[2]
            : camera.parameters[3];
        const ImageRegion source_region = image.source_width > 0U
            ? ImageRegion{
                  .source_x = image.source_x,
                  .source_y = image.source_y,
                  .width = image.source_width,
                  .height = image.source_height,
              }
            : ImageRegion{
                  .source_x = 0U,
                  .source_y = 0U,
                  .width = static_cast<std::uint32_t>(camera.width),
                  .height = static_cast<std::uint32_t>(camera.height),
              };
        const auto regions = configuration.adaptive_native_crop_tiles
            ? make_adaptive_training_tiles(
                  source_region,
                  static_cast<std::uint32_t>(camera.width),
                  static_cast<std::uint32_t>(camera.height),
                  configuration.tile_mode)
            : make_training_tiles(source_region, configuration.tile_mode);
        const auto rotation = quaternion_rotation(image);
        for (const auto& region : regions) {
            const auto [target_width, target_height] =
                training_image_dimensions(
                    region,
                    configuration.resize_factor,
                    configuration.maximum_image_width);
            views.push_back({
                .rotation = rotation,
                .translation = image.tvec,
                .region = region,
                .source_fx = source_fx,
                .source_fy = source_fy,
                .source_cx = source_cx,
                .source_cy = source_cy,
                .target_fx = source_fx *
                    static_cast<double>(target_width) / region.width,
                .target_fy = source_fy *
                    static_cast<double>(target_height) / region.height,
            });
        }
    }
    return views;
}

[[nodiscard]] double maximum_projection_factor(
    const Point& point,
    const std::vector<InitializationView>& views) {
    double maximum = 0.0;
    for (const auto& view : views) {
        const double camera_x =
            view.rotation[0] * point[0] +
            view.rotation[1] * point[1] +
            view.rotation[2] * point[2] + view.translation[0];
        const double camera_y =
            view.rotation[3] * point[0] +
            view.rotation[4] * point[1] +
            view.rotation[5] * point[2] + view.translation[1];
        const double camera_z =
            view.rotation[6] * point[0] +
            view.rotation[7] * point[1] +
            view.rotation[8] * point[2] + view.translation[2];
        if (!std::isfinite(camera_z) || camera_z <= 1.0e-4) {
            continue;
        }
        const double source_x =
            view.source_fx * camera_x / camera_z + view.source_cx;
        const double source_y =
            view.source_fy * camera_y / camera_z + view.source_cy;
        const double right = static_cast<double>(view.region.source_x) +
            view.region.width;
        const double bottom = static_cast<double>(view.region.source_y) +
            view.region.height;
        if (source_x < view.region.source_x || source_x >= right ||
            source_y < view.region.source_y || source_y >= bottom) {
            continue;
        }

        const double inverse_depth = 1.0 / camera_z;
        const double jacobian_xx = view.target_fx * inverse_depth;
        const double jacobian_xz =
            -view.target_fx * camera_x * inverse_depth * inverse_depth;
        const double jacobian_yy = view.target_fy * inverse_depth;
        const double jacobian_yz =
            -view.target_fy * camera_y * inverse_depth * inverse_depth;
        const double covariance_xx =
            jacobian_xx * jacobian_xx + jacobian_xz * jacobian_xz;
        const double covariance_xy = jacobian_xz * jacobian_yz;
        const double covariance_yy =
            jacobian_yy * jacobian_yy + jacobian_yz * jacobian_yz;
        const double trace = covariance_xx + covariance_yy;
        const double difference = covariance_xx - covariance_yy;
        const double spectral_gap = std::sqrt(std::max(
            0.0,
            difference * difference +
                4.0 * covariance_xy * covariance_xy));
        const double eigenvalue_maximum = 0.5 * (trace + spectral_gap);
        if (std::isfinite(eigenvalue_maximum) && eigenvalue_maximum > 0.0) {
            maximum = std::max(maximum, std::sqrt(eigenvalue_maximum));
        }
    }
    return maximum;
}

[[nodiscard]] float percentile(
    std::vector<float> values,
    float fraction) {
    if (values.empty()) {
        return 0.0F;
    }
    const auto index = std::min(
        values.size() - 1U,
        static_cast<std::size_t>(
            fraction * static_cast<float>(values.size() - 1U)));
    std::nth_element(
        values.begin(),
        values.begin() + static_cast<std::ptrdiff_t>(index),
        values.end());
    return values[index];
}

}  // namespace

std::vector<Gaussian> initialize_fixed_topology(const Scene& scene) {
    return initialize_fixed_topology(
        scene,
        InitialScaleConfiguration{}).gaussians;
}

GaussianInitialization initialize_fixed_topology(
    const Scene& scene,
    const InitialScaleConfiguration& configuration) {
    if (scene.points.empty()) {
        throw std::invalid_argument(
            "cannot initialize Gaussians without sparse points");
    }
    const auto points = finite_points(scene);
    auto log_scales = local_log_scales(points);
    InitialScaleStatistics statistics{
        .gaussian_count = points.size(),
    };
    if (configuration.policy == InitialScalePolicy::projected_knn) {
        if (!std::isfinite(configuration.maximum_projected_sigma_pixels) ||
            configuration.maximum_projected_sigma_pixels <= 0.0F) {
            throw std::invalid_argument(
                "maximum initial projected sigma must be finite and positive");
        }
        const auto views = initialization_views(scene, configuration);
        std::vector<float> before;
        std::vector<float> after;
        before.reserve(points.size());
        after.reserve(points.size());
        for (std::size_t index = 0U; index < points.size(); ++index) {
            const double factor = maximum_projection_factor(
                points[index], views);
            if (factor <= 0.0) {
                continue;
            }
            ++statistics.projection_supported_count;
            const float original_scale = std::exp(log_scales[index]);
            const float projected_before = static_cast<float>(
                factor * original_scale);
            const float scale_limit = static_cast<float>(
                configuration.maximum_projected_sigma_pixels / factor);
            const float adjusted_scale = std::min(original_scale, scale_limit);
            if (adjusted_scale < original_scale) {
                ++statistics.projected_scale_clamped_count;
                log_scales[index] = std::log(adjusted_scale);
            }
            before.push_back(projected_before);
            after.push_back(static_cast<float>(factor * adjusted_scale));
        }
        statistics.projected_sigma_before_p50 = percentile(before, 0.50F);
        statistics.projected_sigma_before_p95 = percentile(before, 0.95F);
        statistics.projected_sigma_before_maximum = percentile(before, 1.0F);
        statistics.projected_sigma_after_p50 = percentile(after, 0.50F);
        statistics.projected_sigma_after_p95 = percentile(after, 0.95F);
        statistics.projected_sigma_after_maximum = percentile(after, 1.0F);
    }
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
    return {
        .gaussians = std::move(gaussians),
        .statistics = statistics,
    };
}

}  // namespace dronegs
