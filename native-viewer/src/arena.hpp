#pragma once
#include <algorithm>
#include <cstdint>
#include <stdexcept>
#include <vector>
namespace gs {
struct Span {
  uint32_t offset{}, count{};
};
class ArenaSlots {
public:
  std::vector<Span> free;
  uint64_t available() const {
    uint64_t n = 0;
    for (auto s : free)
      n += s.count;
    return n;
  }
  void release(Span span) {
    if (!span.count)
      return;
    free.push_back(span);
    std::sort(free.begin(), free.end(), [](auto a, auto b) { return a.offset < b.offset; });
    std::vector<Span> merged;
    for (auto s : free) {
      if (!merged.empty() && merged.back().offset + merged.back().count == s.offset)
        merged.back().count += s.count;
      else
        merged.push_back(s);
    }
    free = std::move(merged);
  }
  std::vector<Span> allocate(uint32_t count) {
    if (available() < count)
      throw std::runtime_error("GPU arena capacity exhausted");
    std::vector<Span> result;
    while (count) {
      auto &s = free.front();
      auto take = std::min(count, s.count);
      result.push_back({s.offset, take});
      s.offset += take;
      s.count -= take;
      count -= take;
      if (!s.count)
        free.erase(free.begin());
    }
    return result;
  }
};
} // namespace gs
