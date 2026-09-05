#pragma once
#include "math.hpp"
#include <array>
#include <atomic>
#include <filesystem>
#include <memory>
#include <mutex>
#include <nlohmann/json.hpp>
#include <string>
#include <unordered_map>
#include <vector>
namespace gs {
using Json = nlohmann::json;
struct Quant {
  std::array<float, 80> v{};
};
struct Raw {
  std::array<uint32_t, 24> data{};
  uint32_t tile{};
};
static_assert(sizeof(Raw) == 100 && sizeof(Quant) == 320);
struct AttributeStream {
  std::string path, sha;
  uint64_t bytes{};
};
struct Pack {
  bool streamsOnly{};
  std::string q96Header;
  AttributeStream base, sh;
  std::string id, path, sha;
  uint64_t bytes{}, count{};
};
struct Tile {
  size_t pack{};
  uint64_t offset{}, bytes{};
  uint32_t count{};
  Quant quant;
};
struct Node {
  std::string id;
  Bounds bounds, renderBounds;
  float error{}, support{};
  uint64_t count{};
  Tile tile;
  std::vector<size_t> children;
};
struct Selection {
  std::vector<size_t> nodes;
  uint64_t count{};
  float maxError{};
  bool limited{};
  bool operator==(const Selection &b) const { return nodes == b.nodes; }
};
struct TilePage {
  size_t node{};
  bool fullSh{};
  std::vector<Raw> records;
};
struct Scene {
  std::string bundleId;
  uint64_t budget{};
  std::vector<std::shared_ptr<TilePage>> pages;
  std::vector<Raw> records;
  std::vector<Quant> quants;
  Selection selection;
  double loadMs{};
};
std::string sha256(const uint8_t *bytes, size_t count);
uint32_t crc32(const uint8_t *bytes, size_t count);
std::filesystem::path safePath(const std::filesystem::path &root, const std::string &relative);
Quant parseQuant(const Json &value);
Vec3 position(const Raw &raw, const Quant &quant);
class Bundle {
public:
  std::filesystem::path directory;
  std::string id;
  std::vector<Pack> packs;
  std::vector<Node> nodes;
  size_t root{};
  uint64_t sourceCount{};
  std::atomic_uint64_t fileReadBytes{};
  explicit Bundle(const std::filesystem::path &path);
  Selection select(const Camera &camera, int width, int height, uint64_t budget, float error,
                   const Selection *previous = nullptr) const;
  std::shared_ptr<Scene> load(const Selection &selection, const std::atomic_bool &cancel);
  std::shared_ptr<Scene> loadTiles(const Selection &, const std::vector<size_t> &fullSh,
                                   uint64_t budget, const std::atomic_bool &cancel,
                                   const std::vector<std::shared_ptr<TilePage>> &resident = {});
  std::shared_ptr<Scene> cachedTiles(const Selection &, uint64_t budget,
                                     const std::vector<std::shared_ptr<TilePage>> &resident);
  size_t cacheBytes() const;

private:
  struct Cached {
    std::shared_ptr<std::vector<uint8_t>> bytes;
    uint64_t used{};
  };
  mutable std::mutex cacheMutex;
  std::unordered_map<size_t, Cached> cache;
  size_t cachedBytes{};
  uint64_t clock{};
  std::shared_ptr<std::vector<uint8_t>> readPack(size_t index);
  std::shared_ptr<std::vector<uint8_t>> readStream(size_t index, bool sh);
  struct CachedPage {
    std::shared_ptr<TilePage> page;
    uint64_t used{};
  };
  std::unordered_map<uint64_t, CachedPage> pageCache;
  size_t pageBytes{};
};
} // namespace gs
