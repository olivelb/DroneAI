// SPDX-License-Identifier: MIT
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <future>
#include <iostream>
#include <iterator>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

#include "dronegs/cli.hpp"
#include "dronegs/colmap.hpp"
#include "dronegs/manifest.hpp"
#include "dronegs/model.hpp"
#include "dronegs/image.hpp"
#include "dronegs/ply.hpp"

namespace {

void check(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

template <typename T>
void write_value(std::ostream& stream, const T& value) {
    stream.write(reinterpret_cast<const char*>(&value), sizeof(T));
}

void write_fixture(const std::filesystem::path& root) {
    const auto sparse = root / "sparse" / "0";
    std::filesystem::create_directories(sparse);
    std::filesystem::create_directories(root / "images");
    std::ofstream(root / "images" / "frame.jpg", std::ios::binary).put('\0');

    {
        std::ofstream stream(sparse / "cameras.bin", std::ios::binary);
        write_value(stream, std::uint64_t{1});
        write_value(stream, std::uint32_t{7});
        write_value(stream, std::int32_t{1});
        write_value(stream, std::uint64_t{640});
        write_value(stream, std::uint64_t{480});
        for (double parameter : std::array<double, 4>{500.0, 500.0, 320.0, 240.0}) {
            write_value(stream, parameter);
        }
    }
    {
        std::ofstream stream(sparse / "images.bin", std::ios::binary);
        write_value(stream, std::uint64_t{1});
        write_value(stream, std::uint32_t{3});
        for (double pose : std::array<double, 7>{1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0}) {
            write_value(stream, pose);
        }
        write_value(stream, std::uint32_t{7});
        stream.write("frame.jpg", 9);
        stream.put('\0');
        write_value(stream, std::uint64_t{0});
    }
    {
        std::ofstream stream(sparse / "points3D.bin", std::ios::binary);
        write_value(stream, std::uint64_t{2});
        for (std::uint64_t id = 1; id <= 2; ++id) {
            write_value(stream, id);
            write_value(stream, static_cast<double>(id));
            write_value(stream, static_cast<double>(id + 1));
            write_value(stream, static_cast<double>(id + 2));
            write_value(stream, static_cast<std::uint8_t>(10 * id));
            write_value(stream, static_cast<std::uint8_t>(20 * id));
            write_value(stream, static_cast<std::uint8_t>(30 * id));
            write_value(stream, 0.1);
            write_value(stream, std::uint64_t{0});
        }
    }
}

std::vector<char*> mutable_arguments(std::vector<std::string>& values) {
    std::vector<char*> pointers;
    pointers.reserve(values.size());
    for (auto& value : values) {
        pointers.push_back(value.data());
    }
    return pointers;
}

void test_scene_and_ply(const std::filesystem::path& root) {
    write_fixture(root);
    const auto scene = dronegs::load_colmap_scene(root);
    check(scene.cameras.size() == 1, "camera count mismatch");
    check(scene.images.size() == 1, "image count mismatch");
    check(scene.points.size() == 2, "point count mismatch");
    check(scene.images.front().name == "frame.jpg", "image name mismatch");
    check(scene.images.front().qvec[0] == 1.0, "image quaternion mismatch");
    check(scene.images.front().tvec[2] == 0.0, "image translation mismatch");
    check(scene.cameras.front().parameters.size() == 4, "camera parameter count mismatch");
    check(dronegs::dataset_fingerprint(scene).starts_with("fnv1a64:"),
          "fingerprint kind mismatch");

    const auto gaussians = dronegs::initialize_fixed_topology(scene);
    check(gaussians.size() == 2, "Gaussian count mismatch");
    check(gaussians.front().rotation[0] == 1.0F, "identity quaternion missing");
    const auto output = root.parent_path() / "native-output";
    std::filesystem::create_directories(output);
    const auto ply = output / "point_cloud.ply";
    dronegs::write_gaussian_ply(ply, gaussians, 1);

    std::ifstream stream(ply, std::ios::binary);
    std::string line;
    std::string header;
    while (std::getline(stream, line)) {
        header += line + "\n";
        if (line == "end_header") {
            break;
        }
    }
    check(header.starts_with("ply\nformat binary_little_endian 1.0\n"),
          "PLY format mismatch");
    check(header.find("element vertex 2\n") != std::string::npos,
          "PLY vertex count mismatch");
    check(header.find("property float f_rest_8\n") != std::string::npos,
          "PLY degree-1 SH layout mismatch");
    check(header.find("property float rot_3\n") != std::string::npos,
          "PLY rotation layout mismatch");

    dronegs::Options options{
        .data_path = root,
        .output_path = output,
        .run_manifest = output / "trainer_run.json",
        .iterations = 1,
        .strategy = "mrnf",
        .sh_degree = 1,
        .max_cap = 100,
        .resize_factor = 4,
        .max_width = 1600,
        .tile_mode = 4,
        .seed = 42,
    };
    const dronegs::RunMeasurements measurements{
        .started_at = "2026-07-24T10:00:00Z",
        .finished_at = "2026-07-24T10:00:01Z",
        .loading_seconds = 0.1,
        .image_decode_seconds = 0.08,
        .image_wait_seconds = 0.02,
        .startup_seconds = 0.05,
        .training_seconds = 0.15,
        .export_seconds = 0.2,
        .wall_seconds = 0.3,
        .initial_loss = 0.4F,
        .final_loss = 0.2F,
        .image_cache_hits = 3U,
        .image_cache_misses = 2U,
        .image_cache_evictions = 1U,
        .image_cache_capacity_bytes = 1024U,
        .peak_image_cache_bytes = 512U,
        .image_prefetch_started = 4U,
        .image_prefetch_consumed = 3U,
        .image_prefetch_ready = 2U,
        .training_image_count = 7U,
        .held_out_image_count = 1U,
        .topology_refinements = 2U,
        .gaussians_added = 15U,
        .initial_held_out_psnr = 10.0F,
        .initial_held_out_ssim = 0.2F,
        .final_held_out_psnr = 12.0F,
        .final_held_out_ssim = 0.3F,
    };
    dronegs::write_completed_manifest(
        options, scene, dronegs::dataset_fingerprint(scene), measurements,
        ply, gaussians.size());
    std::ifstream manifest(options.run_manifest);
    const std::string manifest_text(
        (std::istreambuf_iterator<char>(manifest)), std::istreambuf_iterator<char>());
    check(manifest_text.find("\"contract_version\": 1") != std::string::npos,
          "manifest contract version missing");
    check(manifest_text.find("\"git_revision\": null") == std::string::npos,
          "manifest Git revision missing");
    check(manifest_text.find("\"image_cache_hits\": 3") != std::string::npos,
          "manifest image cache metrics missing");
    check(manifest_text.find("\"image_decode_seconds\": 0.08") != std::string::npos,
          "manifest image decode timing missing");
    check(manifest_text.find("\"image_prefetch_consumed\": 3") != std::string::npos,
          "manifest image prefetch metrics missing");
    check(manifest_text.find("\"prefetch_depth\": 1") != std::string::npos,
          "manifest prefetch depth missing");
    check(manifest_text.find("\"decode_workers\": 1") != std::string::npos,
          "manifest decode worker count missing");
    check(manifest_text.find("\"jpeg_idct_scale\": 0") != std::string::npos,
          "manifest JPEG IDCT mode missing");
    check(manifest_text.find("\"training_image_count\": 7") != std::string::npos,
          "manifest training split count missing");
    check(manifest_text.find("\"held_out_image_count\": 1") != std::string::npos,
          "manifest held-out split count missing");
    check(manifest_text.find("\"topology_refinements\": 2") !=
              std::string::npos,
          "manifest topology refinement count missing");
    check(manifest_text.find("\"gaussians_added\": 15") !=
              std::string::npos,
          "manifest Gaussian growth count missing");
    check(manifest_text.find("\"growth_gradient_threshold\": 0.003") !=
              std::string::npos,
          "manifest topology growth protocol missing");
    check(manifest_text.find(
              "\"growth_score\": "
              "\"max_normalized_ssim_error_weighted_alpha_contribution\"") !=
              std::string::npos,
          "manifest topology score definition missing");
    check(manifest_text.find("\"psnr\": 12") != std::string::npos,
          "manifest held-out PSNR missing");
    check(manifest_text.find("\"ssim\": 0.300000") != std::string::npos,
          "manifest held-out SSIM missing");
}

void test_cli(const std::filesystem::path& data, const std::filesystem::path& output) {
    std::vector<std::string> values{
        "dronegs",
        "--data-path", data.string(),
        "--output-path", output.string(),
        "--iter", "1",
        "--strategy", "mrnf",
        "--sh-degree", "1",
        "--max-cap", "100",
        "--resize-factor", "4",
        "--max-width", "1600",
        "--tile-mode", "4",
        "--seed", "42",
        "--run-manifest", (output / "trainer_run.json").string(),
    };
    auto arguments = mutable_arguments(values);
    const auto parsed = dronegs::parse_options(
        static_cast<int>(arguments.size()), arguments.data());
    check(parsed.seed == 42, "CLI seed mismatch");
    check(parsed.sh_degree == 1, "CLI SH degree mismatch");
    check(
        parsed.sh_degree_interval == 1000U,
        "CLI SH interval default mismatch");
    check(parsed.prefetch_depth == 1U, "CLI prefetch default mismatch");
    check(parsed.decode_workers == 1U, "CLI decode worker default mismatch");
    check(parsed.jpeg_idct_scale == 0U, "CLI JPEG IDCT default mismatch");
    check(parsed.test_every == 0U, "CLI held-out split default mismatch");
    check(parsed.save_eval_images == 0U, "CLI eval export default mismatch");
    check(
        parsed.optimizer_profile == "dronegs-dev16",
        "CLI optimizer profile default mismatch");

    values.insert(values.end(), {
        "--prefetch-depth", "12",
        "--decode-workers", "3",
        "--jpeg-idct-scale", "0",
        "--test-every", "8",
        "--save-eval-images", "1",
        "--sh-degree-interval", "250",
        "--optimizer-profile", "calibrated-dc-0.010-opacity",
    });
    arguments = mutable_arguments(values);
    const auto tuned = dronegs::parse_options(
        static_cast<int>(arguments.size()), arguments.data());
    check(tuned.prefetch_depth == 12U, "CLI prefetch depth mismatch");
    check(tuned.decode_workers == 3U, "CLI decode worker count mismatch");
    check(tuned.jpeg_idct_scale == 0U, "CLI JPEG IDCT mode mismatch");
    check(tuned.test_every == 8U, "CLI held-out stride mismatch");
    check(tuned.save_eval_images == 1U, "CLI eval export mismatch");
    check(
        tuned.sh_degree_interval == 250U,
        "CLI SH interval mismatch");
    check(
        tuned.optimizer_profile == "calibrated-dc-0.010-opacity",
        "CLI optimizer profile mismatch");
    values.resize(values.size() - 14U);

    values[values.size() - 7] = "4097";  // --max-width value
    arguments = mutable_arguments(values);
    bool rejected = false;
    try {
        static_cast<void>(dronegs::parse_options(
            static_cast<int>(arguments.size()), arguments.data()));
    } catch (const std::invalid_argument&) {
        rejected = true;
    }
    check(rejected, "CLI accepted max-width above contract limit");
}
void test_image_cache() {
    std::uint64_t loads = 0U;
    dronegs::ImageCache cache(
        3U, 12U,
        [&loads](std::size_t index) {
            ++loads;
            dronegs::ImageData image{
                .width = 2U,
                .height = 1U,
                .source_to_image_x = 1.0F,
                .source_to_image_y = 1.0F,
                .rgb = std::vector<std::uint8_t>(6U, static_cast<std::uint8_t>(index)),
            };
            return image;
        });
    static_cast<void>(cache.get(0U));
    static_cast<void>(cache.get(1U));
    static_cast<void>(cache.get(0U));
    static_cast<void>(cache.get(2U));
    check(loads == 3U, "image cache loader count mismatch");
    check(cache.stats().requests == 4U, "image cache request count mismatch");
    check(cache.stats().hits == 1U, "image cache hit count mismatch");
    check(cache.stats().misses == 3U, "image cache miss count mismatch");
    check(cache.stats().evictions == 1U, "image cache eviction count mismatch");
    check(cache.stats().resident_bytes == 12U, "image cache resident bytes mismatch");
    check(cache.stats().peak_resident_bytes <= cache.capacity_bytes(),
          "image cache exceeded its byte capacity");

    bool oversized_rejected = false;
    try {
        dronegs::ImageCache oversized(
            1U, 5U, [](std::size_t) {
                return dronegs::ImageData{.width = 2U, .height = 1U, .rgb = {0, 0, 0, 0, 0, 0}};
            });
        static_cast<void>(oversized.get(0U));
    } catch (const std::runtime_error&) {
        oversized_rejected = true;
    }
    check(oversized_rejected, "image cache accepted an oversized decoded image");
    constexpr std::size_t large_item_count = 2048U;
    constexpr std::size_t large_item_bytes = 1024U;
    constexpr std::size_t large_capacity = 8U * large_item_bytes;
    dronegs::ImageCache large_cache(
        large_item_count, large_capacity,
        [](std::size_t index) {
            return dronegs::ImageData{
                .width = 1U,
                .height = 1U,
                .rgb = std::vector<std::uint8_t>(
                    large_item_bytes, static_cast<std::uint8_t>(index % 256U)),
            };
        });
    for (std::size_t pass = 0; pass < 2U; ++pass) {
        for (std::size_t index = 0; index < large_item_count; ++index) {
            static_cast<void>(large_cache.get(index));
        }
    }
    check(large_cache.stats().peak_resident_bytes <= large_capacity,
          "large-cardinality image cache exceeded its byte capacity");

    std::promise<void> loader_started;
    auto loader_started_future = loader_started.get_future();
    std::promise<void> release_loader;
    auto release_loader_future = release_loader.get_future().share();
    std::atomic<std::uint64_t> async_loads{0U};
    dronegs::ImageCache async_cache(
        3U, 12U,
        [&loader_started, release_loader_future, &async_loads](std::size_t index) {
            ++async_loads;
            loader_started.set_value();
            release_loader_future.wait();
            return dronegs::ImageData{
                .width = 2U,
                .height = 1U,
                .rgb = std::vector<std::uint8_t>(
                    6U, static_cast<std::uint8_t>(index)),
            };
        });
    async_cache.prefetch(1U);
    loader_started_future.wait();
    check(async_cache.stats().prefetch_started == 1U,
          "image cache did not start asynchronous prefetch");
    bool second_prefetch_rejected = false;
    try {
        async_cache.prefetch(2U);
    } catch (const std::logic_error&) {
        second_prefetch_rejected = true;
    }
    check(second_prefetch_rejected,
          "image cache accepted more than one outstanding prefetch");
    release_loader.set_value();
    const auto& prefetched = async_cache.get(1U);
    check(prefetched.rgb.front() == 1U, "prefetched image contents mismatch");
    check(async_loads == 1U, "prefetched image was decoded more than once");
    check(async_cache.stats().prefetch_consumed == 1U,
          "image cache did not consume prefetched image");
    check(async_cache.stats().misses == 1U,
          "prefetched demand should remain a cache miss");
    static_cast<void>(async_cache.get(1U));
    check(async_cache.stats().hits == 1U,
          "prefetched image was not retained by the LRU");
    async_cache.prefetch(1U);
    check(async_cache.stats().prefetch_started == 1U,
          "prefetch unexpectedly decoded an already resident image");

    std::mutex parallel_mutex;
    std::condition_variable parallel_started;
    std::promise<void> release_parallel_loaders;
    const auto release_parallel_future =
        release_parallel_loaders.get_future().share();
    std::atomic<std::uint64_t> parallel_loads{0U};
    std::atomic<std::uint64_t> active_loads{0U};
    std::atomic<std::uint64_t> peak_active_loads{0U};
    dronegs::ImageCache parallel_cache(
        4U, 24U,
        [&parallel_mutex, &parallel_started, release_parallel_future,
         &parallel_loads, &active_loads,
         &peak_active_loads](std::size_t index) {
            ++parallel_loads;
            const auto active = ++active_loads;
            auto peak = peak_active_loads.load();
            while (active > peak &&
                   !peak_active_loads.compare_exchange_weak(
                       peak, active)) {
            }
            parallel_started.notify_all();
            release_parallel_future.wait();
            --active_loads;
            return dronegs::ImageData{
                .width = 2U,
                .height = 1U,
                .rgb = std::vector<std::uint8_t>(
                    6U, static_cast<std::uint8_t>(index)),
            };
        },
        3U, 2U);
    check(parallel_cache.prefetch_capacity() == 3U,
          "parallel cache prefetch capacity mismatch");
    check(parallel_cache.worker_count() == 2U,
          "parallel cache worker count mismatch");
    parallel_cache.prefetch(0U);
    parallel_cache.prefetch(1U);
    parallel_cache.prefetch(2U);
    {
        std::unique_lock lock(parallel_mutex);
        const bool started = parallel_started.wait_for(
            lock, std::chrono::seconds(2), [&parallel_loads]() {
                return parallel_loads.load() >= 2U;
            });
        check(started, "parallel image cache workers did not start");
    }
    check(peak_active_loads.load() >= 2U,
          "image cache did not decode concurrently");
    bool bounded_queue_rejected = false;
    try {
        parallel_cache.prefetch(3U);
    } catch (const std::logic_error&) {
        bounded_queue_rejected = true;
    }
    check(bounded_queue_rejected,
          "parallel image cache exceeded its prefetch capacity");
    release_parallel_loaders.set_value();
    for (std::size_t index = 0U; index < 3U; ++index) {
        const auto& image = parallel_cache.get(index);
        check(image.rgb.front() == index,
              "parallel prefetch image contents mismatch");
    }
    parallel_cache.prefetch(3U);
    check(parallel_cache.get(3U).rgb.front() == 3U,
          "parallel cache failed after refilling the bounded queue");
}


}  // namespace

int main() {
    const auto suffix = std::chrono::steady_clock::now().time_since_epoch().count();
    const auto base = std::filesystem::temp_directory_path() /
                      ("dronegs-core-test-" + std::to_string(suffix));
    const auto data = base / "dataset";
    const auto output = base / "output";
    try {
        test_scene_and_ply(data);
        test_cli(data, output);
        test_image_cache();
        std::filesystem::remove_all(base);
        std::cout << "DroneGS core tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "DroneGS core test failed: " << error.what() << "\n";
        std::filesystem::remove_all(base);
        return 1;
    }
}
