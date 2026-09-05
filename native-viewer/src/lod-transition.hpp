#pragma once
#include "bundle.hpp"
#include <set>
namespace gs {
// Every step is an antichain; a parent stays active until its required children are ready.
inline Selection nextLodCut(const Bundle &bundle, const Selection &current, const Selection &target,
                            uint64_t budget, uint64_t maxAdded = 65536) {
  if ((current.nodes.empty() || current.count > budget) && !target.nodes.empty()) {
    Selection root;
    root.nodes = {bundle.root};
    root.count = bundle.nodes[bundle.root].tile.count;
    return root;
  }
  std::vector<size_t> parent(bundle.nodes.size(), bundle.nodes.size());
  for (size_t i = 0; i < bundle.nodes.size(); i++)
    for (auto child : bundle.nodes[i].children)
      parent[child] = i;
  auto ancestor = [&](size_t a, size_t b) {
    for (auto p = parent[b]; p < parent.size(); p = parent[p])
      if (p == a)
        return true;
    return false;
  };
  auto relevant = [&](size_t id) {
    for (auto t : target.nodes)
      if (id == t || ancestor(id, t) || ancestor(t, id))
        return true;
    return false;
  };
  std::set<size_t> cut;
  uint64_t used = 0, added = 0;
  for (auto id : current.nodes)
    if (relevant(id)) {
      cut.insert(id);
      used += bundle.nodes[id].tile.count;
    }
  auto replace = [&](const std::vector<size_t> &remove, const std::vector<size_t> &insert) {
    uint64_t cost = 0, freed = 0;
    for (auto id : insert)
      cost += bundle.nodes[id].tile.count;
    for (auto id : remove)
      freed += bundle.nodes[id].tile.count;
    if (used - freed + cost > budget || (added && added + cost > maxAdded))
      return false;
    for (auto id : remove)
      cut.erase(id);
    for (auto id : insert)
      cut.insert(id);
    used = used - freed + cost;
    added += cost;
    return true;
  };
  for (auto id : target.nodes) {
    bool covered = cut.contains(id);
    for (auto c : cut)
      covered = covered || ancestor(c, id);
    if (covered)
      continue;
    std::vector<size_t> remove;
    for (auto c : cut)
      if (ancestor(id, c))
        remove.push_back(c);
    replace(remove, {id});
  }
  const auto before = cut;
  for (auto id : before) {
    bool refine = false;
    for (auto t : target.nodes)
      refine = refine || ancestor(id, t);
    if (!refine)
      continue;
    std::vector<size_t> children;
    for (auto c : bundle.nodes[id].children)
      if (relevant(c))
        children.push_back(c);
    if (!children.empty() && !replace({id}, children)) {
      // Intermediate proxies can be larger than the final descendants.
      std::vector<size_t> descendants;
      for (auto t : target.nodes)
        if (ancestor(id, t))
          descendants.push_back(t);
      if (!descendants.empty())
        replace({id}, descendants);
    }
  }
  Selection out = target;
  out.nodes.assign(cut.begin(), cut.end());
  out.count = used;
  return out;
}
} // namespace gs
