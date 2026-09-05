#pragma once
#include "bundle.hpp"
#include "lod-transition.hpp"
#include <condition_variable>
#include <deque>
#include <optional>
#include <thread>
namespace gs {
struct LoadRequest {
  uint64_t generation{};
  std::filesystem::path path;
  std::shared_ptr<Bundle> bundle;
  Camera camera;
  int width{}, height{};
  uint64_t budget{};
  float error{};
  std::shared_ptr<std::atomic_bool> cancel;
  std::shared_ptr<Scene> current;
  bool withSh{};
  std::optional<Selection> target;
  bool prefetch{};
};
struct LoadResult {
  uint64_t generation{};
  std::shared_ptr<Bundle> bundle;
  std::shared_ptr<Scene> scene;
  Camera camera;
  bool fit{};
  std::string error;
  bool prefetch{};
};
class Loader {
  std::mutex mutex;
  std::condition_variable cv;
  std::optional<LoadRequest> request;
  std::deque<LoadResult> results;
  std::shared_ptr<std::atomic_bool> cancellation;
  bool stop{}, processing{};
  std::thread thread;
  void publish(LoadResult r, const LoadRequest &req) {
    std::lock_guard lock(mutex);
    if (!*req.cancel)
      results.push_back(std::move(r));
  }
  void run() {
    for (;;) {
      LoadRequest req;
      {
        std::unique_lock lock(mutex);
        cv.wait(lock, [&] { return stop || request.has_value(); });
        if (stop)
          return;
        req = std::move(*request);
        request.reset();
        processing = true;
      }
      try {
        bool opening = !req.path.empty();
        auto bundle = opening ? std::make_shared<Bundle>(req.path) : req.bundle;
        if (!bundle || *req.cancel)
          throw std::runtime_error("Cancelled load request");
        if (opening)
          req.camera.fit(bundle->nodes[bundle->root].bounds, float(req.width) / req.height);
        const Selection current = req.current && req.current->bundleId == bundle->id
                                      ? req.current->selection
                                      : Selection{};
        auto target = req.target ? *req.target
                                 : bundle->select(req.camera, req.width, req.height, req.budget,
                                                  req.error, &current);
        auto selected = req.prefetch ? target : nextLodCut(*bundle, current, target, req.budget);
        std::vector<size_t> full;
        if (req.current && req.current->bundleId == bundle->id)
          for (auto &p : req.current->pages)
            if (p->fullSh)
              full.push_back(p->node);
        if (req.prefetch && req.withSh)
          full = selected.nodes;
        if (!req.prefetch && req.withSh && selected == target && req.current) {
          uint64_t upgraded = 0;
          for (auto id : selected.nodes) {
            if (std::find(full.begin(), full.end(), id) != full.end())
              continue;
            auto count = bundle->nodes[id].tile.count;
            if (upgraded && upgraded + count > 65536)
              break;
            full.push_back(id);
            upgraded += count;
          }
        }
        const auto resident = req.current && req.current->bundleId == bundle->id
                                  ? req.current->pages
                                  : std::vector<std::shared_ptr<TilePage>>{};
        auto scene = bundle->loadTiles(selected, full, req.budget, *req.cancel, resident);
        if (scene)
          publish({req.generation, bundle, scene, req.camera, opening, {}, req.prefetch}, req);
      } catch (const std::exception &e) {
        publish({req.generation, {}, {}, {}, false, e.what(), req.prefetch}, req);
      }
      {
        std::lock_guard lock(mutex);
        processing = false;
        if (!request && results.empty())
          busy = false;
      }
    }
  }

public:
  std::atomic_bool busy{};
  Loader() : thread([this] { run(); }) {}
  ~Loader() {
    {
      std::lock_guard lock(mutex);
      stop = true;
      if (cancellation)
        *cancellation = true;
    }
    cv.notify_all();
    thread.join();
  }
  void cancel() {
    std::lock_guard lock(mutex);
    if (cancellation)
      *cancellation = true;
    request.reset();
    if (!processing && results.empty())
      busy = false;
  }
  void submit(LoadRequest req) {
    std::lock_guard lock(mutex);
    if (cancellation)
      *cancellation = true;
    cancellation = std::make_shared<std::atomic_bool>(false);
    req.cancel = cancellation;
    request = std::move(req);
    busy = true;
    cv.notify_one();
  }
  std::deque<LoadResult> poll() {
    std::lock_guard lock(mutex);
    std::deque<LoadResult> out;
    out.swap(results);
    if (!processing && !request)
      busy = false;
    return out;
  }
};
} // namespace gs
