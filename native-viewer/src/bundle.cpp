#include "bundle.hpp"
#include <windows.h>

#include <bcrypt.h>
#include <chrono>
#include <cstring>
#include <fstream>
#include <future>
#include <numeric>
#include <queue>
#include <set>
#include <stdexcept>
#include <thread>
namespace gs {
namespace {
void require(bool ok, const std::string &why) {
  if (!ok)
    throw std::runtime_error(why);
}
uint64_t integer(const Json &j, const char *key, uint64_t minimum = 0,
                 uint64_t maximum = UINT64_MAX) {
  const auto &v = j.at(key);
  require(v.is_number_unsigned() || (v.is_number_integer() && v.get<int64_t>() >= 0),
          std::string("Invalid integer: ") + key);
  auto n = v.get<uint64_t>();
  require(n >= minimum && n <= maximum, std::string("Out of range: ") + key);
  return n;
}
float finite(const Json &j) {
  require(j.is_number(), "Expected finite number");
  double v = j.get<double>();
  require(std::isfinite(v) && std::abs(v) <= 1e30, "Non-finite or oversized value");
  return static_cast<float>(v);
}
Vec3 vec(const Json &j) {
  require(j.is_array() && j.size() == 3, "Expected three coordinates");
  return {finite(j[0]), finite(j[1]), finite(j[2])};
}
Bounds bounds(const Json &j) {
  Bounds b{vec(j.at("min")), vec(j.at("max"))};
  for (int i = 0; i < 3; i++)
    require(b.lo[i] <= b.hi[i], "Reversed bounds");
  return b;
}
bool contains(Bounds a, Bounds b) {
  for (int i = 0; i < 3; i++)
    if (a.lo[i] > b.lo[i] || a.hi[i] < b.hi[i])
      return false;
  return true;
}
bool digest(const std::string &s) {
  return s.size() == 64 && s.find_first_not_of("0123456789abcdef") == std::string::npos;
}
uint16_t u16(const uint8_t *p) { return uint16_t(p[0]) | (uint16_t(p[1]) << 8); }
uint32_t u32(const uint8_t *p) { return uint32_t(u16(p)) | (uint32_t(u16(p + 2)) << 16); }
} // namespace
std::string sha256(const uint8_t *bytes, size_t count) {
  require(count <= ULONG_MAX, "SHA input is too large");
  BCRYPT_ALG_HANDLE alg{};
  require(BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, nullptr, 0) >= 0,
          "Cannot initialize SHA256");
  uint8_t out[32]{};
  auto result =
      BCryptHash(alg, nullptr, 0, const_cast<PUCHAR>(bytes), static_cast<ULONG>(count), out, 32);
  BCryptCloseAlgorithmProvider(alg, 0);
  require(result >= 0, "SHA256 failed");
  const char *hex = "0123456789abcdef";
  std::string s;
  s.reserve(64);
  for (auto b : out) {
    s += hex[b >> 4];
    s += hex[b & 15];
  }
  return s;
}
uint32_t crc32(const uint8_t *data, size_t count) {
  static const auto table = [] {
    std::array<uint32_t, 256> t{};
    for (uint32_t i = 0; i < 256; i++) {
      uint32_t x = i;
      for (int k = 0; k < 8; k++)
        x = (x >> 1) ^ ((x & 1) ? 0xedb88320u : 0);
      t[i] = x;
    }
    return t;
  }();
  uint32_t x = ~0u;
  for (size_t i = 0; i < count; i++)
    x = table[(x ^ data[i]) & 255] ^ (x >> 8);
  return ~x;
}
std::filesystem::path safePath(const std::filesystem::path &root, const std::string &relative) {
  require(!relative.empty() && relative.front() != '/' &&
              relative.find_first_of("\\:") == std::string::npos,
          "Unsafe pack path");
  size_t start = 0;
  while (start < relative.size()) {
    size_t end = relative.find('/', start);
    if (end == std::string::npos)
      end = relative.size();
    auto part = relative.substr(start, end - start);
    require(!part.empty() && part != "." && part != ".." && part.back() != '.' &&
                part.back() != ' ',
            "Unsafe pack path component");
    start = end + 1;
  }
  require(relative.back() != '/', "Unsafe trailing slash");
  auto base = std::filesystem::absolute(root).lexically_normal();
  auto path = (base / std::filesystem::path(std::u8string(relative.begin(), relative.end())))
                  .lexically_normal();
  auto a = base.begin(), b = path.begin();
  for (; a != base.end(); ++a, ++b)
    require(b != path.end() && _wcsicmp(a->c_str(), b->c_str()) == 0,
            "Pack escapes bundle directory");
  require(b != path.end(), "Pack path is the bundle directory");
  return path;
}
Quant parseQuant(const Json &j) {
  Quant q;
  for (auto item : {std::pair{"position", 0}, std::pair{"logScale", 6}}) {
    auto b = bounds(j.at(item.first));
    for (int i = 0; i < 3; i++) {
      q.v[item.second + i] = b.lo[i];
      q.v[item.second + 3 + i] = (b.hi[i] - b.lo[i]) / 65535.f;
    }
    if (item.second == 6)
      for (int i = 0; i < 3; i++)
        require(b.lo[i] >= -80 && b.hi[i] <= 30, "Log scale outside supported finite range");
  }
  q.v[12] = finite(j.at("opacityLogit").at("min"));
  float hi = finite(j.at("opacityLogit").at("max"));
  require(hi >= q.v[12] && std::abs(hi) <= 10000 && std::abs(q.v[12]) <= 10000,
          "Invalid opacity range");
  q.v[13] = (hi - q.v[12]) / 65535.f;
  auto copy = [&](const char *name, size_t size, size_t offset) {
    auto a = j.at(name);
    require(a.is_array() && a.size() == size, std::string("Wrong quantization shape: ") + name);
    for (size_t i = 0; i < size; i++) {
      q.v[offset + i] = finite(a[i]);
      require(q.v[offset + i] >= 0 && q.v[offset + i] <= 1e10, "Invalid SH scale");
    }
  };
  copy("colorDcScale", 3, 14);
  copy("colorShScale", 45, 17);
  copy("opacityShScale", 15, 62);
  require(j.at("rotation").at("encoding") == "snorm16x4", "Unsupported rotation encoding");
  return q;
}
Vec3 position(const Raw &r, const Quant &q) {
  const auto *b = reinterpret_cast<const uint8_t *>(r.data.data());
  return {q.v[0] + u16(b) * q.v[3], q.v[1] + u16(b + 2) * q.v[4], q.v[2] + u16(b + 4) * q.v[5]};
}
Bundle::Bundle(const std::filesystem::path &path) {
  directory = std::filesystem::is_directory(path) ? path : path.parent_path();
  auto manifest = directory / L"manifest.json";
  require(std::filesystem::file_size(manifest) <= 256ull * 1024 * 1024, "Manifest exceeds 256 MiB");
  std::ifstream input(manifest, std::ios::binary);
  require(bool(input), "Cannot open manifest.json");
  Json j = Json::parse(input);
  require(j.at("schema") == "droneai-gstile" && integer(j, "version") == 1,
          "Unsupported GSTile schema/version");
  require(j.at("profile") == "dronegs-sh3-opacity-sh3-q96-adaptive-lod-v4",
          "Unsupported GSTile profile; regenerate the bundle with V4");
  id = j.at("bundleId");
  require(id.starts_with("sha256:") && digest(id.substr(7)), "Invalid bundle identity");
  sourceCount = integer(j.at("source"), "gaussianCount", 1);
  require(digest(j.at("source").at("sha256")), "Invalid source identity");
  require(j.at("packs").is_array() && !j.at("packs").empty() && j.at("packs").size() <= 1000000,
          "Invalid pack list");
  require(j.at("nodes").is_array() && !j.at("nodes").empty() && j.at("nodes").size() <= 1000000,
          "Invalid node list");
  std::unordered_map<std::string, size_t> packIds, nodeIds;
  for (const auto &p : j.at("packs")) {
    Pack pack;
    pack.id = p.at("id");
    pack.path = p.at("path");
    pack.sha = p.at("sha256");
    require(!pack.id.empty() && packIds.emplace(pack.id, packs.size()).second, "Duplicate pack id");
    safePath(directory, pack.path);
    require(digest(pack.sha), "Invalid pack digest");
    pack.count = integer(p, "recordCount", 1, 16000000);
    pack.bytes = integer(p, "byteLength", 32, 1536000032);
    require(integer(p, "byteOffset") == 32 && pack.bytes == 32 + 96 * pack.count,
            "Invalid pack length");
    if (p.contains("streams")) {
      const auto &s = p.at("streams");
      require(integer(s, "version") == 1, "Unsupported attribute streams");
      for (auto [kind, stride, output] :
           {std::tuple{"base", 36ull, &pack.base}, std::tuple{"sh", 60ull, &pack.sh}}) {
        const auto &stream = s.at(kind);
        output->path = stream.at("path");
        safePath(directory, output->path);
        output->sha = stream.at("sha256");
        output->bytes = integer(stream, "byteLength");
        require(digest(output->sha) && output->bytes == 32 + pack.count * stride,
                "Invalid attribute stream identity");
      }
      require(pack.base.path != pack.sh.path, "Duplicate stream path");
    }
    if (p.contains("storage")) {
      require(p.at("storage") == "streams" && !pack.base.path.empty() && !p.contains("encodings"),
              "Invalid stream-only storage");
      pack.streamsOnly = true;
      pack.q96Header = p.at("q96Header");
      require(digest(pack.q96Header), "Invalid virtual Q96 header");
      std::array<uint8_t, 32> header{};
      for (size_t k = 0; k < header.size(); k++)
        header[k] = uint8_t(std::stoul(pack.q96Header.substr(k * 2, 2), nullptr, 16));
      require(std::memcmp(header.data(), "GSTILE1\0", 8) == 0 && u16(header.data() + 8) == 1 &&
                  u16(header.data() + 10) == 32 && u16(header.data() + 12) == 96 &&
                  u16(header.data() + 14) == 0 && u32(header.data() + 16) == pack.count,
              "Invalid virtual Q96 layout");
    }
    packs.push_back(std::move(pack));
  }
  for (const auto &n : j.at("nodes")) {
    Node node;
    node.id = n.at("id");
    require(!node.id.empty() && nodeIds.emplace(node.id, nodes.size()).second, "Duplicate node id");
    nodes.push_back(std::move(node));
  }
  std::vector<std::vector<std::pair<uint64_t, uint64_t>>> ranges(packs.size());
  for (size_t i = 0; i < nodes.size(); i++) {
    auto &node = nodes[i];
    const auto &n = j.at("nodes")[i];
    node.bounds = bounds(n.at("bounds"));
    node.renderBounds = bounds(n.at("renderBounds"));
    require(contains(node.renderBounds, node.bounds), "Render bounds must contain centers");
    node.error = finite(n.at("geometricError"));
    require(node.error >= 0, "Negative geometric error");
    node.count = integer(n, "gaussianCount", 1);
    bool inner = n.contains("children");
    require(inner != n.contains("tile") && inner == n.contains("lodTile"),
            "Invalid node representation");
    if (inner) {
      require(n.at("children").is_array() && !n.at("children").empty(), "Empty internal node");
      for (const auto &c : n.at("children"))
        node.children.push_back(nodeIds.at(c.get<std::string>()));
    }
    const auto &t = n.at(inner ? "lodTile" : "tile");
    node.tile.pack = packIds.at(t.at("pack").get<std::string>());
    const auto &pack = packs[node.tile.pack];
    node.tile.count = static_cast<uint32_t>(integer(t, "recordCount", 1, pack.count));
    node.tile.offset = integer(t, "byteOffset", 32, pack.bytes);
    node.tile.bytes = integer(t, "byteLength", 96, pack.bytes);
    require(t.at("sha256") == pack.sha && node.tile.bytes == 96ull * node.tile.count &&
                (node.tile.offset - 32) % 96 == 0 &&
                node.tile.offset + node.tile.bytes <= pack.bytes,
            "Invalid tile range or digest");
    require(inner ? node.tile.count <= node.count : node.tile.count == node.count,
            "Invalid tile population");
    ranges[node.tile.pack].emplace_back(node.tile.offset, node.tile.offset + node.tile.bytes);
    node.tile.quant = parseQuant(t.at("quantization"));
    for (int k = 0; k < 3; k++)
      node.support = std::max(
          node.support, std::exp(node.tile.quant.v[6 + k] + 65535.f * node.tile.quant.v[9 + k]));
  }
  for (size_t i = 0; i < packs.size(); i++) {
    auto &rs = ranges[i];
    std::sort(rs.begin(), rs.end());
    uint64_t end = 32;
    for (auto [a, b] : rs) {
      require(a == end, "Pack ranges have overlap or gaps");
      end = b;
    }
    require(end == packs[i].bytes, "Pack payload is not fully referenced");
  }
  root = nodeIds.at(j.at("root").get<std::string>());
  std::vector<uint32_t> incoming(nodes.size());
  uint64_t leaves = 0;
  for (auto &n : nodes) {
    uint64_t sum = 0;
    for (auto child : n.children) {
      require(++incoming[child] == 1, "Node has multiple parents or duplicate children");
      require(contains(n.renderBounds, nodes[child].renderBounds),
              "Parent render bounds do not contain child");
      sum += nodes[child].count;
    }
    if (n.children.empty())
      leaves += n.count;
    else
      require(sum == n.count, "Child population mismatch");
  }
  require(incoming[root] == 0 && nodes[root].count == sourceCount && leaves == sourceCount,
          "Invalid tree root or source count");
  std::vector<size_t> stack{root};
  std::vector<bool> seen(nodes.size());
  size_t reached = 0;
  while (!stack.empty()) {
    auto i = stack.back();
    stack.pop_back();
    require(!seen[i], "Cycle in node tree");
    seen[i] = true;
    reached++;
    for (auto c : nodes[i].children)
      stack.push_back(c);
  }
  require(reached == nodes.size(), "Disconnected node tree");
}
Selection Bundle::select(const Camera &cam, int width, int height, uint64_t budget, float threshold,
                         const Selection *previous) const {
  require(width > 0 && height > 0 && budget > 0 && threshold > 0, "Invalid LOD parameters");
  const float focal = height / (2 * std::tan(cam.fov * .5f)), ty = std::tan(cam.fov * .5f),
              tx = ty * width / height;
  const Vec3 f = cam.forward(), r = cam.right(), u = cam.vertical();
  const std::array<Vec3, 5> planes = {f, f * tx + r, f * tx - r, f * ty + u, f * ty - u};
  std::vector<bool> refined(nodes.size()), visible(nodes.size());
  if (previous) {
    std::vector<size_t> parent(nodes.size(), nodes.size());
    for (size_t i = 0; i < nodes.size(); i++)
      for (auto c : nodes[i].children)
        parent[c] = i;
    for (auto i : previous->nodes)
      for (auto p = parent[i]; p < nodes.size(); p = parent[p])
        refined[p] = true;
  }
  std::vector<float> errors(nodes.size()), candidates{0};
  for (size_t i = 0; i < nodes.size(); i++) {
    const auto &n = nodes[i];
    const Vec3 d = n.renderBounds.center() - cam.eye;
    const Vec3 h = (n.renderBounds.hi - n.renderBounds.lo) * .5f;
    // Test the anisotropic support box against each frustum plane. Its enclosing
    // sphere admits long, wholly offscreen tiles and spends their budget.
    visible[i] = std::all_of(planes.begin(), planes.end(), [&](Vec3 p) {
      return dot(d, p) + h.x * std::abs(p.x) + h.y * std::abs(p.y) + h.z * std::abs(p.z) >= 0;
    });
    if (!visible[i] || n.children.empty())
      continue;
    const float geometric = std::max(n.error, n.support);
    const float geometricPixels =
        geometric * focal / std::max(n.renderBounds.distance(cam.eye), geometric);
    const float radius = n.renderBounds.radius();
    const float footprintPixels =
        std::min(focal, radius * focal / std::max(dot(d, f) - radius, 1e-5f)) / 64;
    errors[i] = std::max(geometricPixels, footprintPixels) / (refined[i] ? .8f : 1.f);
    candidates.push_back(errors[i]);
  }
  if (!visible[root])
    return {};
  require(nodes[root].tile.count <= budget,
          "Gaussian budget is smaller than the root representation");
  // One screen-error threshold for the whole view: never spend on a cheaper,
  // sharper neighbour while a coarser branch is blocked. Equal errors are
  // refined together, independent of manifest order.
  auto buildCut = [&](float maximumError) {
    Selection out;
    std::vector<size_t> pending{root};
    while (!pending.empty()) {
      const size_t i = pending.back();
      pending.pop_back();
      if (!visible[i])
        continue;
      const auto &n = nodes[i];
      const bool hasVisibleChildren =
          std::any_of(n.children.begin(), n.children.end(), [&](size_t c) { return visible[c]; });
      if (!n.children.empty() && !hasVisibleChildren)
        continue;
      if (hasVisibleChildren && errors[i] > maximumError) {
        pending.insert(pending.end(), n.children.begin(), n.children.end());
      } else {
        out.nodes.push_back(i);
        out.count += n.tile.count;
        out.maxError = std::max(out.maxError, errors[i]);
      }
    }
    return out;
  };
  // Culling can make the exact cut cheaper than intermediate proxies: cost is
  // not monotone. Never miss a fully detailed cut that already fits.
  auto finest = buildCut(0);
  if (finest.count <= budget) {
    std::sort(finest.nodes.begin(), finest.nodes.end());
    return finest;
  }
  const bool limited = buildCut(threshold).count > budget;
  std::sort(candidates.begin(), candidates.end());
  candidates.erase(std::unique(candidates.begin(), candidates.end()), candidates.end());
  size_t lower = 0, upper = candidates.size();
  Selection best;
  while (lower < upper) {
    const size_t middle = lower + (upper - lower) / 2;
    auto cut = buildCut(candidates[middle]);
    if (cut.count <= budget) {
      best = std::move(cut);
      upper = middle;
    } else {
      lower = middle + 1;
    }
  }
  best.limited = limited;
  std::sort(best.nodes.begin(), best.nodes.end());
  return best;
}
std::shared_ptr<std::vector<uint8_t>> Bundle::readPack(size_t index) {
  {
    std::lock_guard lock(cacheMutex);
    auto it = cache.find(index);
    if (it != cache.end()) {
      it->second.used = ++clock;
      return it->second.bytes;
    }
  }
  const auto &p = packs[index];
  auto data = std::make_shared<std::vector<uint8_t>>(static_cast<size_t>(p.bytes));
  if (p.streamsOnly) {
    const auto base = readStream(index, false), sh = readStream(index, true);
    for (size_t k = 0; k < 32; k++)
      (*data)[k] = uint8_t(std::stoul(p.q96Header.substr(k * 2, 2), nullptr, 16));
    for (size_t k = 0; k < p.count; k++) {
      auto dst = data->data() + 32 + k * 96;
      std::memcpy(dst, base->data() + 32 + k * 36, 28);
      std::memcpy(dst + 88, base->data() + 32 + k * 36 + 28, 8);
      std::memcpy(dst + 28, sh->data() + 32 + k * 60, 60);
    }
  } else {
    auto path = std::filesystem::weakly_canonical(safePath(directory, p.path));
    auto canonicalRoot = std::filesystem::weakly_canonical(directory);
    auto rootPart = canonicalRoot.begin(), pathPart = path.begin();
    for (; rootPart != canonicalRoot.end(); ++rootPart, ++pathPart)
      require(pathPart != path.end() && _wcsicmp(rootPart->c_str(), pathPart->c_str()) == 0,
              "Pack symlink escapes bundle");
    require(std::filesystem::file_size(path) == p.bytes,
            "Pack size differs from manifest: " + p.path);
    std::ifstream f(path, std::ios::binary);
    require(bool(f.read(reinterpret_cast<char *>(data->data()), data->size())),
            "Cannot read pack: " + p.path);
    fileReadBytes += data->size();
  }
  const auto *b = data->data();
  require(std::memcmp(b, "GSTILE1\0", 8) == 0 && u16(b + 8) == 1 && u16(b + 10) == 32 &&
              u16(b + 12) == 96 && u16(b + 14) == 0,
          "Unsupported GSTile pack header: " + p.path);
  require(u32(b + 16) == p.count, "Pack record count mismatch: " + p.path);
  require(crc32(b + 32, data->size() - 32) == u32(b + 28), "Pack CRC32 mismatch: " + p.path);
  require(sha256(b, data->size()) == p.sha, "Pack SHA256 mismatch: " + p.path);
  // Check quaternion payload before any GPU allocation.
  for (size_t offset = 32; offset < data->size(); offset += 96) {
    int64_t norm = 0;
    for (int k = 0; k < 4; k++) {
      int16_t v = static_cast<int16_t>(u16(b + offset + 12 + 2 * k));
      norm += int64_t(v) * v;
    }
    require(norm > 0, "Invalid zero quaternion in " + p.path);
  }
  {
    std::lock_guard lock(cacheMutex);
    constexpr size_t limit = 768ull * 1024 * 1024;
    while (cachedBytes + data->size() > limit && !cache.empty()) {
      auto oldest = std::min_element(cache.begin(), cache.end(), [](auto &a, auto &b) {
        return a.second.used < b.second.used;
      });
      cachedBytes -= oldest->second.bytes->size();
      cache.erase(oldest);
    }
    if (data->size() <= limit) {
      cache[index] = {data, ++clock};
      cachedBytes += data->size();
    }
  }
  return data;
}
std::shared_ptr<std::vector<uint8_t>> Bundle::readStream(size_t index, bool sh) {
  const size_t key = packs.size() + 2 * index + size_t(sh);
  {
    std::lock_guard lock(cacheMutex);
    if (auto it = cache.find(key); it != cache.end()) {
      it->second.used = ++clock;
      return it->second.bytes;
    }
  }
  const auto &pack = packs.at(index);
  const auto &s = sh ? pack.sh : pack.base;
  auto path = std::filesystem::weakly_canonical(safePath(directory, s.path));
  auto rootPath = std::filesystem::weakly_canonical(directory);
  auto p = path.begin();
  for (auto part = rootPath.begin(); part != rootPath.end(); ++part, ++p)
    require(p != path.end() && _wcsicmp(part->c_str(), p->c_str()) == 0,
            "Stream path escapes bundle");
  require(std::filesystem::file_size(path) == s.bytes, "Attribute stream size mismatch");
  auto bytes = std::make_shared<std::vector<uint8_t>>(size_t(s.bytes));
  std::ifstream file(path, std::ios::binary);
  require(bool(file.read(reinterpret_cast<char *>(bytes->data()), bytes->size())),
          "Cannot read attribute stream");
  fileReadBytes += bytes->size();
  const auto *b = bytes->data();
  require(std::memcmp(b, "GSATTR1\0", 8) == 0 && u16(b + 8) == 1 && u16(b + 10) == 32 &&
              u16(b + 12) == (sh ? 60 : 36) && u16(b + 14) == (sh ? 2 : 1) &&
              u32(b + 16) == pack.count && u32(b + 20) == 0 && u32(b + 24) == 0 &&
              crc32(b + 32, bytes->size() - 32) == u32(b + 28) && sha256(b, bytes->size()) == s.sha,
          "Attribute stream integrity mismatch");
  {
    std::lock_guard lock(cacheMutex);
    constexpr size_t limit = 768ull * 1024 * 1024;
    while (cachedBytes + bytes->size() > limit && !cache.empty()) {
      auto oldest = std::min_element(cache.begin(), cache.end(), [](auto &a, auto &b) {
        return a.second.used < b.second.used;
      });
      cachedBytes -= oldest->second.bytes->size();
      cache.erase(oldest);
    }
    if (bytes->size() <= limit) {
      cache[key] = {bytes, ++clock};
      cachedBytes += bytes->size();
    }
  }
  return bytes;
}
std::shared_ptr<Scene> Bundle::loadTiles(const Selection &selection,
                                         const std::vector<size_t> &fullSh, uint64_t budget,
                                         const std::atomic_bool &cancel,
                                         const std::vector<std::shared_ptr<TilePage>> &resident) {
  const auto start = std::chrono::steady_clock::now();
  auto out = std::make_shared<Scene>();
  out->bundleId = id;
  out->selection = selection;
  out->budget = budget;
  out->quants.reserve(nodes.size());
  for (const auto &n : nodes)
    out->quants.push_back(n.tile.quant);
  out->pages.resize(selection.nodes.size());
  std::unordered_map<size_t, std::shared_ptr<TilePage>> activePages;
  for (auto &page : resident)
    activePages.emplace(page->node, page);
  std::atomic_size_t cursor{};
  std::mutex failureMutex;
  std::exception_ptr failure;
  auto work = [&] {
    try {
      while (!cancel) {
        size_t k = cursor.fetch_add(1);
        if (k >= selection.nodes.size())
          break;
        size_t node = selection.nodes[k];
        const auto &tile = nodes.at(node).tile;
        const auto &pack = packs.at(tile.pack);
        bool full =
            pack.base.path.empty() || std::find(fullSh.begin(), fullSh.end(), node) != fullSh.end();
        std::shared_ptr<TilePage> page;
        if (const auto it = activePages.find(node); it != activePages.end() &&
                                                    (it->second->fullSh || !full) &&
                                                    it->second->records.size() == tile.count) {
          // Active pages are already verified and may exceed the optional LRU
          // capacity at high budgets. Reuse their ownership, never re-read them.
          out->pages[k] = it->second;
          continue;
        }
        {
          std::lock_guard lock(cacheMutex);
          auto it = pageCache.find(node * 2 + 1);
          if (it == pageCache.end() && !full)
            it = pageCache.find(node * 2);
          if (it != pageCache.end()) {
            page = it->second.page;
            it->second.used = ++clock;
          }
        }
        if (!page) {
          auto base = pack.base.path.empty() ? readPack(tile.pack) : readStream(tile.pack, false);
          auto sh = !pack.base.path.empty() && full ? readStream(tile.pack, true) : nullptr;
          if (cancel)
            break;
          page = std::make_shared<TilePage>();
          page->node = node;
          page->fullSh = full;
          page->records.resize(tile.count);
          const size_t first = (tile.offset - 32) / 96;
          for (size_t i = 0; i < tile.count; i++) {
            auto &raw = page->records[i];
            auto *dst = reinterpret_cast<uint8_t *>(raw.data.data());
            if (pack.base.path.empty())
              std::memcpy(dst, base->data() + tile.offset + 96 * i, 96);
            else {
              const auto *src = base->data() + 32 + (first + i) * 36;
              std::memcpy(dst, src, 28);
              std::memcpy(dst + 88, src + 28, 8);
              if (sh)
                std::memcpy(dst + 28, sh->data() + 32 + (first + i) * 60, 60);
              int64_t norm = 0;
              for (int j = 0; j < 4; j++) {
                int16_t v = int16_t(u16(dst + 12 + 2 * j));
                norm += int64_t(v) * v;
              }
              require(norm > 0, "Invalid stream quaternion");
            }
            raw.tile = static_cast<uint32_t>(node);
          }
          {
            std::lock_guard lock(cacheMutex);
            constexpr size_t limit = 768ull * 1024 * 1024;
            size_t bytes = page->records.size() * sizeof(Raw);
            while (pageBytes + bytes > limit && !pageCache.empty()) {
              auto oldest =
                  std::min_element(pageCache.begin(), pageCache.end(),
                                   [](auto &a, auto &b) { return a.second.used < b.second.used; });
              pageBytes -= oldest->second.page->records.size() * sizeof(Raw);
              pageCache.erase(oldest);
            }
            if (bytes <= limit) {
              uint64_t key = node * 2 + size_t(full);
              if (!pageCache.contains(key)) {
                pageCache[key] = {page, ++clock};
                pageBytes += bytes;
              }
            }
          }
        }
        out->pages[k] = std::move(page);
      }
    } catch (...) {
      std::lock_guard lock(failureMutex);
      if (!failure)
        failure = std::current_exception();
    }
  };
  std::vector<std::thread> workers;
  for (size_t i = 0; i < std::min<size_t>(4, selection.nodes.size()); i++)
    workers.emplace_back(work);
  for (auto &w : workers)
    w.join();
  if (failure)
    std::rethrow_exception(failure);
  if (cancel)
    return {};
  out->loadMs =
      std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
  return out;
}
std::shared_ptr<Scene> Bundle::cachedTiles(const Selection &selection, uint64_t budget,
                                           const std::vector<std::shared_ptr<TilePage>> &resident) {
  auto out = std::make_shared<Scene>();
  out->bundleId = id;
  out->selection = selection;
  out->budget = budget;
  std::unordered_map<size_t, std::shared_ptr<TilePage>> activePages;
  for (auto &page : resident)
    activePages.emplace(page->node, page);
  {
    std::lock_guard lock(cacheMutex);
    for (auto node : selection.nodes) {
      std::shared_ptr<TilePage> page;
      if (auto it = activePages.find(node); it != activePages.end() && it->second->fullSh)
        page = it->second;
      else if (auto cachedPage = pageCache.find(node * 2 + 1); cachedPage != pageCache.end()) {
        page = cachedPage->second.page;
        cachedPage->second.used = ++clock;
      }
      if (!page)
        return {};
      out->pages.push_back(std::move(page));
    }
  }
  out->quants.reserve(nodes.size());
  for (auto &n : nodes)
    out->quants.push_back(n.tile.quant);
  return out;
}
size_t Bundle::cacheBytes() const {
  std::lock_guard lock(cacheMutex);
  return cachedBytes;
}
std::shared_ptr<Scene> Bundle::load(const Selection &selection, const std::atomic_bool &cancel) {
  auto start = std::chrono::steady_clock::now();
  require(selection.count <= 8000000, "Selected scene exceeds eight million Gaussians");
  auto result = std::make_shared<Scene>();
  result->selection = selection;
  result->records.resize(static_cast<size_t>(selection.count));
  result->quants.resize(selection.nodes.size());
  struct Job {
    size_t pack;
    std::vector<std::pair<size_t, size_t>> tiles;
  };
  std::vector<Job> jobs;
  std::unordered_map<size_t, size_t> byPack;
  size_t offset = 0;
  for (size_t k = 0; k < selection.nodes.size(); k++) {
    const auto &t = nodes.at(selection.nodes[k]).tile;
    auto [it, inserted] = byPack.emplace(t.pack, jobs.size());
    if (inserted)
      jobs.push_back({t.pack, {}});
    jobs[it->second].tiles.emplace_back(k, offset);
    offset += t.count;
    result->quants[k] = t.quant;
  }
  require(offset == selection.count, "Selection count mismatch");
  std::atomic_size_t cursor{};
  std::atomic_bool failed{};
  std::mutex errorMutex;
  std::exception_ptr error;
  auto work = [&] {
    try {
      while (!cancel && !failed) {
        size_t job = cursor.fetch_add(1);
        if (job >= jobs.size())
          break;
        const auto &j = jobs[job];
        auto bytes = readPack(j.pack);
        for (auto [k, destination] : j.tiles) {
          const auto &t = nodes[selection.nodes[k]].tile;
          for (uint32_t i = 0; i < t.count; i++) {
            auto &raw = result->records[destination + i];
            std::memcpy(raw.data.data(), bytes->data() + t.offset + 96ull * i, 96);
            raw.tile = static_cast<uint32_t>(k);
          }
        }
      }
    } catch (...) {
      std::lock_guard lock(errorMutex);
      if (!error)
        error = std::current_exception();
      failed = true;
    }
  };
  std::vector<std::thread> workers;
  for (size_t i = 0; i < std::min<size_t>(4, jobs.size()); i++)
    workers.emplace_back(work);
  for (auto &worker : workers)
    worker.join();
  if (error)
    std::rethrow_exception(error);
  if (cancel)
    return {};
  result->loadMs =
      std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
  return result;
}
} // namespace gs
