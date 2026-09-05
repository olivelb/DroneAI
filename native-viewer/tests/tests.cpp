#include "bundle.hpp"
#include "arena.hpp"
#include "lod-transition.hpp"
#include "home-view.hpp"
#include "loader.hpp"
#include <cstring>
#include <fstream>
#include <functional>
#include <iostream>
#include <windows.h>
using namespace gs;
namespace {
void expect(bool ok, const char *message) {
  if (!ok)
    throw std::runtime_error(message);
}
void rejects(const std::function<void()> &run) {
  bool rejected = false;
  try {
    run();
  } catch (const std::exception &) {
    rejected = true;
  }
  expect(rejected, "Expected malformed input rejection");
}
void save(const std::filesystem::path &path, const Json &j) { std::ofstream(path) << j.dump(); }
Json makeQuant() {
  Json q;
  q["position"] = {{"min", {-1, -1, -1}}, {"max", {1, 1, 1}}};
  q["logScale"] = {{"min", {-3, -3, -3}}, {"max", {-3, -3, -3}}};
  q["opacityLogit"] = {{"min", 0}, {"max", 0}};
  q["colorDcScale"] = {1, 1, 1};
  q["colorShScale"] = std::vector<float>(45, 0.1f);
  q["opacityShScale"] = std::vector<float>(15, 0.1f);
  q["rotation"] = {{"encoding", "snorm16x4"}};
  return q;
}
} // namespace
int main() {
  try {
    auto dir = std::filesystem::temp_directory_path() /
               ("gstile-native-tests-" + std::to_string(GetCurrentProcessId()));
    std::filesystem::create_directories(dir / "packs");
    std::vector<uint8_t> bytes(32 + 96 * 3);
    std::memcpy(bytes.data(), "GSTILE1\0", 8);
    auto put16 = [&](size_t offset, uint16_t v) { std::memcpy(bytes.data() + offset, &v, 2); };
    auto put32 = [&](size_t offset, uint32_t v) { std::memcpy(bytes.data() + offset, &v, 4); };
    put16(8, 1);
    put16(10, 32);
    put16(12, 96);
    put32(16, 3);
    for (size_t i = 0; i < 3; i++) {
      put16(32 + 96 * i + 12, 32767);
      put16(32 + 96 * i, uint16_t(i * 32767));
    }
    put32(28, crc32(bytes.data() + 32, bytes.size() - 32));
    auto writePack = [&] {
      std::ofstream f(dir / "packs" / "scene.gst", std::ios::binary);
      f.write(reinterpret_cast<char *>(bytes.data()), bytes.size());
    };
    writePack();
    auto hash = sha256(bytes.data(), bytes.size());
    auto tile = [&](int index) {
      return Json{{"pack", "p"},      {"byteOffset", 32 + 96 * index},
                  {"byteLength", 96}, {"recordCount", 1},
                  {"sha256", hash},   {"quantization", makeQuant()}};
    };
    Json bounds = {{"min", {-1, -1, -1}}, {"max", {1, 1, 1}}};
    Json root = {{"id", "r"},           {"bounds", bounds},   {"renderBounds", bounds},
                 {"geometricError", 1}, {"gaussianCount", 2}, {"children", {"a", "b"}},
                 {"lodTile", tile(0)}};
    Json leaf = {{"id", "a"},           {"bounds", bounds},   {"renderBounds", bounds},
                 {"geometricError", 0}, {"gaussianCount", 1}, {"tile", tile(1)}};
    Json other = leaf;
    other["id"] = "b";
    other["tile"] = tile(2);
    Json j = {{"schema", "droneai-gstile"},
              {"version", 1},
              {"profile", "dronegs-sh3-opacity-sh3-q96-adaptive-lod-v4"},
              {"bundleId", "sha256:" + std::string(64, 'a')},
              {"source", {{"gaussianCount", 2}, {"sha256", std::string(64, 'b')}}},
              {"root", "r"},
              {"nodes", {root, leaf, other}},
              {"packs",
               {{{"id", "p"},
                 {"path", "packs/scene.gst"},
                 {"sha256", hash},
                 {"byteLength", bytes.size()},
                 {"recordCount", 3},
                 {"byteOffset", 32}}}}};
    save(dir / "manifest.json", j);
    Bundle bundle(dir);
    Camera camera;
    camera.eye = {0, -5, 0};
    camera.pivot = {0, 0, 0};
    auto cut = bundle.select(camera, 1000, 1000, 1, 1);
    expect(cut.count == 1 && cut.nodes == std::vector<size_t>{0} && cut.limited,
           "Root remains on budget pressure");
    cut = bundle.select(camera, 1000, 1000, 2, 1);
    expect(cut.count == 2 && cut.nodes == std::vector<size_t>({1, 2}),
           "Complete child replacement");
    {
      Bundle lod(dir);
      lod.nodes.resize(7);
      for (size_t i = 0; i < lod.nodes.size(); i++) {
        auto &n = lod.nodes[i];
        n = {};
        n.bounds = n.renderBounds = {{-1, -1, -1}, {1, 1, 1}};
        n.tile.count = i < 3 ? 1 : 4;
        n.error = i == 0 ? 2.f : (i < 3 ? 1.f : 0.f);
      }
      lod.nodes[0].children = {1, 2};
      lod.nodes[1].children = {3, 4};
      lod.nodes[2].children = {5, 6};
      auto balanced = lod.select(camera, 1000, 1000, 9, 1);
      expect(balanced.nodes == std::vector<size_t>({1, 2}) && balanced.limited,
             "Equal visible regions refine together, not by node index");
      lod.nodes[1].error = 1.2f;
      lod.nodes[5].tile.count = lod.nodes[6].tile.count = 1;
      balanced = lod.select(camera, 1000, 1000, 4, 1);
      expect(balanced.nodes == std::vector<size_t>({1, 2}),
             "A blocked coarse branch must not subsidize sharper cheap neighbours");
      balanced = lod.select(camera, 1000, 1000, 16, 1);
      expect(balanced.nodes == std::vector<size_t>({3, 4, 5, 6}),
             "All finest visible tiles are selected when the budget fits");
      // Two costly intermediate proxies, each with a tiny visible exact leaf
      // and a larger offscreen leaf. The exact visible cut fits the budget.
      lod.nodes[1].tile.count = lod.nodes[2].tile.count = 4;
      for (size_t i = 0; i < 3; i++)
        lod.nodes[i].renderBounds.hi.x = 11;
      for (size_t i = 3; i < 7; i++) {
        lod.nodes[i].tile.count = i % 2 ? 1 : 10;
        if (i % 2 == 0)
          lod.nodes[i].bounds = lod.nodes[i].renderBounds = {{10, 0, -.1f}, {11, 1, .1f}};
      }
      balanced = lod.select(camera, 1000, 1000, 4, 1);
      expect(balanced.nodes == std::vector<size_t>({3, 5}) && !balanced.limited,
             "Use affordable exact leaves even when intermediate proxies exceed budget");
      for (size_t i = 0; i < 3; i++) {
        lod.nodes[i].tile.count = 1;
        lod.nodes[i].renderBounds = {{-1, -1, -1}, {1, 1, 1}};
      }
      Camera inside = camera;
      inside.eye = {0, 0, 0};
      inside.pivot = {0, 1, 0};
      balanced = lod.select(inside, 1000, 1000, 1, 1);
      expect(std::isfinite(balanced.maxError) && balanced.maxError <= 867,
             "Entering a support box does not explode projected error");
      lod.nodes.resize(3);
      lod.nodes[1].children.clear();
      lod.nodes[2].children.clear();
      lod.nodes[0].renderBounds = {{-1, -1, -10}, {5.1f, 5, 10}};
      lod.nodes[2].bounds = lod.nodes[2].renderBounds = {{5, -1, -10}, {5.1f, 5, 10}};
      balanced = lod.select(inside, 1000, 1000, 2, 1);
      expect(balanced.nodes == std::vector<size_t>{1},
             "Long offscreen support box is culled even when its sphere intersects");
      lod.nodes[2].renderBounds.lo.x = .1f;
      balanced = lod.select(inside, 1000, 1000, 2, 1);
      expect(balanced.nodes == std::vector<size_t>({1, 2}),
             "Visible Gaussian support remains even when its centers are offscreen");
    }
    std::atomic_bool cancel{};
    auto scene = bundle.load(cut, cancel);
    expect(scene->records.size() == 2 && scene->records[1].tile == 1,
           "Aggregate ranges and tile indexing");
    expect(std::abs(position(scene->records[1], scene->quants[1]).x - 1) < .0001,
           "Q96 position decoding");
    {
      auto active = bundle.loadTiles(cut, cut.nodes, 2, cancel);
      Bundle freshReader(dir);
      auto reused = freshReader.loadTiles(cut, cut.nodes, 2, cancel, active->pages);
      expect(!freshReader.cachedTiles(cut, 2, {}), "Cold target is never published as cached");
      auto warm = freshReader.cachedTiles(cut, 2, active->pages);
      expect(warm && warm->pages == active->pages && warm->selection == cut,
             "Warm exact target is ready without intermediate proxies");
      expect(freshReader.fileReadBytes == 0 && reused->pages == active->pages,
             "Active pages outside a reader LRU are reused without file reads");
    }
    Selection rootCut;
    rootCut.nodes = {0};
    rootCut.count = 1;
    expect(nextLodCut(bundle, rootCut, cut, 2).nodes == cut.nodes, "Bounded child replacement");
    expect(nextLodCut(bundle, cut, rootCut, 1).nodes == rootCut.nodes,
           "Budget shrink coarsens without deadlock");
    ArenaSlots slots;
    slots.release({0, 10});
    auto a = slots.allocate(3), b = slots.allocate(4);
    slots.release(a[0]);
    auto fragmented = slots.allocate(6);
    expect(fragmented.size() == 2 && slots.available() == 0, "Fragmented GPU slots");
    for (auto s : fragmented)
      slots.release(s);
    slots.release(b[0]);
    expect(slots.free.size() == 1 && slots.available() == 10, "GPU spans coalesce");
    rejects([&] { slots.allocate(11); });
    Json splitManifest = j;
    splitManifest["packs"][0]["streams"] = {{"version", 1}};
    for (auto [kind, stride, flag] : {std::tuple{"base", 36u, 1u}, std::tuple{"sh", 60u, 2u}}) {
      std::vector<uint8_t> stream(32 + 3 * stride);
      std::memcpy(stream.data(), "GSATTR1\0", 8);
      auto s16 = [&](size_t p, uint16_t v) { std::memcpy(stream.data() + p, &v, 2); };
      auto s32 = [&](size_t p, uint32_t v) { std::memcpy(stream.data() + p, &v, 4); };
      s16(8, 1);
      s16(10, 32);
      s16(12, uint16_t(stride));
      s16(14, uint16_t(flag));
      s32(16, 3);
      for (size_t i = 0; i < 3; i++) {
        if (flag == 1) {
          std::memcpy(stream.data() + 32 + i * 36, bytes.data() + 32 + i * 96, 28);
          std::memcpy(stream.data() + 60 + i * 36, bytes.data() + 120 + i * 96, 8);
        } else
          for (size_t k = 0; k < 60; k++)
            stream[32 + i * 60 + k] = uint8_t(k + 1);
      }
      s32(28, crc32(stream.data() + 32, stream.size() - 32));
      std::string path = std::string("packs/scene.gst.") + kind;
      {
        std::ofstream file(dir / path, std::ios::binary);
        file.write(reinterpret_cast<char *>(stream.data()), stream.size());
      }
      splitManifest["packs"][0]["streams"][kind] = {
          {"path", path},
          {"byteLength", stream.size()},
          {"sha256", sha256(stream.data(), stream.size())}};
    }
    save(dir / "manifest.json", splitManifest);
    Bundle splitBundle(dir);
    auto baseScene = splitBundle.loadTiles(cut, {}, 2, cancel);
    auto fullScene = splitBundle.loadTiles(cut, {1, 2}, 2, cancel);
    expect(!baseScene->pages[0]->fullSh && fullScene->pages[0]->fullSh, "Deferred SH page upgrade");
    expect(
        reinterpret_cast<const uint8_t *>(baseScene->pages[0]->records[0].data.data())[28] == 0 &&
            reinterpret_cast<const uint8_t *>(fullScene->pages[0]->records[0].data.data())[28] == 1,
        "Split SH bytes are decoded separately");
    auto cached = splitBundle.loadTiles(cut, {}, 2, cancel);
    expect(cached->pages[0] == fullScene->pages[0], "Full SH cached page reused for base request");
    auto virtualBytes = bytes;
    for (size_t i = 0; i < 3; i++)
      for (size_t k = 0; k < 60; k++)
        virtualBytes[32 + i * 96 + 28 + k] = uint8_t(k + 1);
    auto virtualCrc = crc32(virtualBytes.data() + 32, virtualBytes.size() - 32);
    std::memcpy(virtualBytes.data() + 28, &virtualCrc, 4);
    auto virtualHash = sha256(virtualBytes.data(), virtualBytes.size());
    std::string headerHex;
    const char *hex = "0123456789abcdef";
    for (size_t i = 0; i < 32; i++) {
      headerHex += hex[virtualBytes[i] >> 4];
      headerHex += hex[virtualBytes[i] & 15];
    }
    splitManifest["packs"][0]["storage"] = "streams";
    splitManifest["packs"][0]["q96Header"] = headerHex;
    splitManifest["packs"][0]["sha256"] = virtualHash;
    splitManifest["packs"][0]["path"] = "packs/virtual.gst";
    for (auto &n : splitManifest["nodes"])
      n[n.contains("tile") ? "tile" : "lodTile"]["sha256"] = virtualHash;
    save(dir / "manifest.json", splitManifest);
    Bundle onlyStreams(dir);
    expect(!std::filesystem::exists(dir / "packs/virtual.gst"), "Canonical pack is absent");
    auto virtualScene = onlyStreams.load(cut, cancel);
    expect(virtualScene->records.size() == 2 &&
               reinterpret_cast<const uint8_t *>(virtualScene->records[0].data.data())[28] == 1,
           "Full decode reconstructed from independently verified streams");
    auto progressiveScene = onlyStreams.loadTiles(cut, {}, 2, cancel);
    expect(!progressiveScene->pages[0]->fullSh, "Base-first without historical pack");
    {
      Loader asyncLoader;
      LoadRequest req;
      req.generation = 1;
      req.bundle = std::make_shared<Bundle>(dir);
      req.current = progressiveScene;
      req.camera = camera;
      req.width = req.height = 1000;
      req.budget = 2;
      req.error = 1;
      req.withSh = true;
      req.target = cut;
      asyncLoader.submit(req);
      const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
      bool published = false;
      while (!published) {
        for (auto &result : asyncLoader.poll()) {
          expect(result.error.empty() && result.scene && result.scene->pages[0]->fullSh &&
                     result.scene->pages[1]->fullSh,
                 "Async target finishes SH upgrades without a canonical file");
          published = true;
        }
        expect(published || asyncLoader.busy, "Pending async result is not reported as idle");
        expect(std::chrono::steady_clock::now() < deadline, "Async SH upgrade timed out");
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
      }
    }
    splitManifest["packs"][0]["q96Header"] = std::string(64, '0');
    save(dir / "manifest.json", splitManifest);
    rejects([&] { Bundle badHeader(dir); });
    save(dir / "manifest.json", j);
    auto home = decodeHomeCamera(cameraJson(camera));
    expect(length(home.eye - camera.eye) < 1e-6, "Home view round trip");
    auto invalidHome = cameraJson(camera);
    invalidHome["pivot"] = invalidHome["eye"];
    rejects([&] { decodeHomeCamera(invalidHome); });
    cancel = true;
    expect(!bundle.load(cut, cancel), "Cancelled load not published");
    cancel = false;
    expect(crc32(reinterpret_cast<const uint8_t *>("123456789"), 9) == 0xcbf43926,
           "CRC32 known vector");
    expect(sha256(reinterpret_cast<const uint8_t *>("abc"), 3) ==
               "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
           "SHA256 known vector");
    for (const std::string path :
         {"../evil", "/absolute", "packs/../evil", "C:/evil", "packs\\evil", "packs//evil",
          "packs/./evil", "packs/file:stream", "packs/trailing."})
      rejects([&] { safePath(dir, path); });
    auto mutation = [&](const std::function<void(Json &)> &change) {
      Json bad = j;
      change(bad);
      save(dir / "manifest.json", bad);
      rejects([&] { Bundle invalid(dir); });
      save(dir / "manifest.json", j);
    };
    mutation([](Json &b) { b["profile"] = "retired"; });
    mutation([](Json &b) { b["version"] = 2; });
    mutation([](Json &b) { b["nodes"][0]["children"] = {"a", "a"}; });
    mutation([](Json &b) { b["nodes"][1]["tile"]["byteOffset"] = 32; });
    mutation([](Json &b) { b["nodes"][2]["tile"]["recordCount"] = UINT64_MAX; });
    mutation([](Json &b) { b["nodes"][0]["bounds"]["min"] = {4, 0, 0}; });
    mutation([](Json &b) { b["nodes"][0]["lodTile"]["quantization"]["colorShScale"] = {1}; });
    mutation([](Json &b) {
      b["nodes"][0]["lodTile"]["quantization"]["logScale"]["max"] = {1000, 1000, 1000};
    });
    mutation([](Json &b) { b["packs"][0]["sha256"] = std::string(64, 'c'); });
    bytes[70] ^= 1;
    writePack();
    rejects([&] {
      Bundle corrupted(dir);
      corrupted.load(cut, cancel);
    });
    bytes[70] ^= 1;
    writePack();
    Camera nav;
    nav.fit({{-1, -1, -1}, {1, 1, 1}}, 1.6f);
    auto before = nav.distance();
    auto pivot = nav.pivot;
    for (int i = 0; i < 1000; i++)
      nav.orbit(.01f, .02f);
    expect(std::abs(nav.distance() - before) < .001f && length(nav.pivot - pivot) < 1e-5,
           "Orbit preserves pivot and distance");
    expect(std::abs(dot(nav.right(), nav.vertical())) < 1e-5, "Camera basis remains orthogonal");
    auto eye = nav.eye;
    nav.look(.2f, .1f);
    expect(length(nav.eye - eye) < 1e-5, "Look preserves eye position");
    nav.dolly(-.5f);
    expect(nav.distance() < before, "Zoom approaches pivot");
    std::cout << "PASS: hashes, aggregate decoding, budget/LOD, cancellation, malformed manifests, "
                 "corruption, traversal, camera.\nFixtures: "
              << dir << "\n";
    return 0;
  } catch (const std::exception &e) {
    std::cerr << e.what() << "\n";
    return 1;
  }
}
