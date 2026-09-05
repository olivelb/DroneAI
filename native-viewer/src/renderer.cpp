#include "renderer.hpp"
#include <set>
#include "Histogram.hpp"
#include "PS.hpp"
#include "Pick.hpp"
#include "Prefix.hpp"
#include "Project.hpp"
#include "Scatter.hpp"
#include "VS.hpp"
#include <fstream>
#include <stdexcept>
namespace gs {
namespace {
void check(HRESULT hr, const char *action) {
  if (FAILED(hr)) {
    char text[128];
    sprintf_s(text, "%s (HRESULT 0x%08lX)", action, static_cast<unsigned long>(hr));
    throw std::runtime_error(text);
  }
}
void put(float *target, Vec3 v) {
  target[0] = v.x;
  target[1] = v.y;
  target[2] = v.z;
}
} // namespace
Renderer::Renderer(HWND window, int w, int h, bool debug) : width(w), height(h) {
  ComPtr<IDXGIFactory1> factory;
  check(CreateDXGIFactory1(IID_PPV_ARGS(&factory)), "Create DXGI factory");
  ComPtr<IDXGIAdapter1> selected;
  SIZE_T best = 0;
  for (UINT i = 0;; i++) {
    ComPtr<IDXGIAdapter1> adapter;
    if (factory->EnumAdapters1(i, &adapter) == DXGI_ERROR_NOT_FOUND)
      break;
    DXGI_ADAPTER_DESC1 d{};
    adapter->GetDesc1(&d);
    if (!(d.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) && (!selected || d.DedicatedVideoMemory > best)) {
      selected = adapter;
      best = d.DedicatedVideoMemory;
    }
  }
  if (!selected)
    throw std::runtime_error("No hardware Direct3D adapter found");
  DXGI_ADAPTER_DESC1 desc{};
  selected->GetDesc1(&desc);
  char name[256]{};
  WideCharToMultiByte(CP_UTF8, 0, desc.Description, -1, name, 256, nullptr, nullptr);
  adapterName = name;
  adapterMemory = desc.DedicatedVideoMemory;
  D3D_FEATURE_LEVEL levels[] = {D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0}, actual{};
  UINT flags = debug ? D3D11_CREATE_DEVICE_DEBUG : 0;
  auto hr = D3D11CreateDevice(selected.Get(), D3D_DRIVER_TYPE_UNKNOWN, nullptr, flags, levels, 2,
                              D3D11_SDK_VERSION, &device, &actual, &context);
  if (hr == DXGI_ERROR_SDK_COMPONENT_MISSING)
    hr = D3D11CreateDevice(selected.Get(), D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0, levels, 2,
                           D3D11_SDK_VERSION, &device, &actual, &context);
  check(hr, "Create Direct3D 11 hardware device");
  ComPtr<IDXGIFactory2> factory2;
  check(factory.As(&factory2), "DXGI 1.2");
  DXGI_SWAP_CHAIN_DESC1 sd{};
  sd.Width = w;
  sd.Height = h;
  sd.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
  sd.SampleDesc.Count = 1;
  sd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
  sd.BufferCount = 2;
  sd.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
  check(factory2->CreateSwapChainForHwnd(device.Get(), window, &sd, nullptr, nullptr, &swap),
        "Create swap chain");
  factory->MakeWindowAssociation(window, DXGI_MWA_NO_ALT_ENTER);
  ComPtr<IDXGIDevice1> dxgiDevice;
  if (SUCCEEDED(device.As(&dxgiDevice)))
    dxgiDevice->SetMaximumFrameLatency(1);
  target();
  D3D11_BUFFER_DESC cb{};
  cb.ByteWidth = sizeof(Frame);
  cb.Usage = D3D11_USAGE_DEFAULT;
  cb.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
  check(device->CreateBuffer(&cb, nullptr, &constantBuffer), "Create camera constants");
  auto cs = [&](const BYTE *bytes, size_t size, ComPtr<ID3D11ComputeShader> &shader) {
    check(device->CreateComputeShader(bytes, size, nullptr, &shader),
          "Create embedded compute shader");
  };
  cs(shaderProject, sizeof(shaderProject), project);
  cs(shaderHistogram, sizeof(shaderHistogram), histogram);
  cs(shaderPrefix, sizeof(shaderPrefix), prefix);
  cs(shaderScatter, sizeof(shaderScatter), scatter);
  cs(shaderPick, sizeof(shaderPick), pickShader);
  check(device->CreateVertexShader(shaderVS, sizeof(shaderVS), nullptr, &vs),
        "Create vertex shader");
  check(device->CreatePixelShader(shaderPS, sizeof(shaderPS), nullptr, &ps), "Create pixel shader");
  D3D11_BLEND_DESC bd{};
  auto &rt = bd.RenderTarget[0];
  rt.BlendEnable = TRUE;
  rt.SrcBlend = D3D11_BLEND_ONE;
  rt.DestBlend = D3D11_BLEND_INV_SRC_ALPHA;
  rt.BlendOp = D3D11_BLEND_OP_ADD;
  rt.SrcBlendAlpha = D3D11_BLEND_ONE;
  rt.DestBlendAlpha = D3D11_BLEND_INV_SRC_ALPHA;
  rt.BlendOpAlpha = D3D11_BLEND_OP_ADD;
  rt.RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_ALL;
  check(device->CreateBlendState(&bd, &blend), "Create premultiplied alpha blending");
  D3D11_RASTERIZER_DESC rd{};
  rd.FillMode = D3D11_FILL_SOLID;
  rd.CullMode = D3D11_CULL_NONE;
  rd.DepthClipEnable = TRUE;
  check(device->CreateRasterizerState(&rd, &rasterizer), "Create rasterizer");
  for (auto &t : timers) {
    D3D11_QUERY_DESC d{D3D11_QUERY_TIMESTAMP_DISJOINT, 0};
    check(device->CreateQuery(&d, &t.disjoint), "Create GPU timer");
    d.Query = D3D11_QUERY_TIMESTAMP;
    check(device->CreateQuery(&d, &t.start), "Create GPU timestamp");
    check(device->CreateQuery(&d, &t.end), "Create GPU timestamp");
  }
}
Buffer Renderer::buffer(uint32_t count, uint32_t stride, const void *initial, bool writable) {
  Buffer b;
  D3D11_BUFFER_DESC d{};
  d.ByteWidth = std::max(count, 1u) * stride;
  d.StructureByteStride = stride;
  d.Usage = initial ? D3D11_USAGE_IMMUTABLE : D3D11_USAGE_DEFAULT;
  d.BindFlags = D3D11_BIND_SHADER_RESOURCE | (writable ? D3D11_BIND_UNORDERED_ACCESS : 0);
  d.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
  D3D11_SUBRESOURCE_DATA data{};
  data.pSysMem = initial;
  check(device->CreateBuffer(&d, initial ? &data : nullptr, &b.buffer),
        "Allocate GPU buffer (reduce Gaussian budget if out of VRAM)");
  check(device->CreateShaderResourceView(b.buffer.Get(), nullptr, &b.srv), "Create shader view");
  if (writable)
    check(device->CreateUnorderedAccessView(b.buffer.Get(), nullptr, &b.uav),
          "Create compute view");
  return b;
}
void Renderer::target() {
  ComPtr<ID3D11Texture2D> back;
  check(swap->GetBuffer(0, IID_PPV_ARGS(&back)), "Get back buffer");
  check(device->CreateRenderTargetView(back.Get(), nullptr, &rtv), "Create render target");
}
void Renderer::resize(int w, int h) {
  if (w < 1 || h < 1)
    return;
  width = w;
  height = h;
  context->OMSetRenderTargets(0, nullptr, nullptr);
  rtv.Reset();
  check(swap->ResizeBuffers(0, w, h, DXGI_FORMAT_UNKNOWN, 0), "Resize window");
  target();
  dirty = true;
}
void Renderer::upload(std::shared_ptr<Scene> next) {
  if (!next->bundleId.empty()) {
    stageTiles(std::move(next));
    return;
  }
  pendingScene.reset();
  gpuPages.clear();
  arenaSlots.free.clear();
  arenaCapacity = 0;
  arenaBundle.clear();

  clearBindings();
  uint32_t n = static_cast<uint32_t>(next->records.size()), g = (n + 255) / 256;
  // Allocate the replacement fully before releasing the last complete scene.
  auto newRaw = buffer(n, 100, n ? next->records.data() : nullptr);
  auto newQuant = buffer(static_cast<uint32_t>(next->quants.size()), 320,
                         next->quants.empty() ? nullptr : next->quants.data());
  auto newProjected = buffer(n, 48, nullptr, true), sort0 = buffer(n, 8, nullptr, true),
       sort1 = buffer(n, 8, nullptr, true);
  auto newHist = buffer(std::max(g, 1u) * 16, 4, nullptr, true),
       newPrefix = buffer(std::max(g, 1u) * 16, 4, nullptr, true);
  auto newTotals = buffer(16, 4, nullptr, true), newPicks = buffer(g, 8, nullptr, true);
  D3D11_BUFFER_DESC d{};
  newPicks.buffer->GetDesc(&d);
  d.Usage = D3D11_USAGE_STAGING;
  d.BindFlags = 0;
  d.MiscFlags = 0;
  d.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
  d.StructureByteStride = 0;
  ComPtr<ID3D11Buffer> newReadback;
  check(device->CreateBuffer(&d, nullptr, &newReadback), "Create picking readback");
  raw = std::move(newRaw);
  quant = std::move(newQuant);
  projected = std::move(newProjected);
  sort[0] = std::move(sort0);
  sort[1] = std::move(sort1);
  hist = std::move(newHist);
  prefixes = std::move(newPrefix);
  totals = std::move(newTotals);
  picks = std::move(newPicks);
  pickReadback = std::move(newReadback);
  std::vector<uint32_t> identity(n);
  for (uint32_t i = 0; i < n; i++)
    identity[i] = i;
  active = buffer(n, 4, identity.empty() ? nullptr : identity.data());
  workCapacity = n;
  frame.count = n;
  frame.groups = g;
  scene = std::move(next);
  dirty = true;
}
void Renderer::allocateWork(uint32_t n) {
  if (n <= workCapacity && active.buffer)
    return;
  const uint32_t g = (n + 255) / 256;
  projected = buffer(n, 48, nullptr, true);
  sort[0] = buffer(n, 8, nullptr, true);
  sort[1] = buffer(n, 8, nullptr, true);
  hist = buffer(std::max(g, 1u) * 16, 4, nullptr, true);
  prefixes = buffer(std::max(g, 1u) * 16, 4, nullptr, true);
  totals = buffer(16, 4, nullptr, true);
  picks = buffer(std::max(g, 1u), 8, nullptr, true);
  active = buffer(n, 4);
  D3D11_BUFFER_DESC d{};
  picks.buffer->GetDesc(&d);
  d.Usage = D3D11_USAGE_STAGING;
  d.BindFlags = 0;
  d.MiscFlags = 0;
  d.StructureByteStride = 0;
  d.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
  pickReadback.Reset();
  check(device->CreateBuffer(&d, nullptr, &pickReadback), "Create streaming pick readback");
  if (scene && !scene->bundleId.empty() && scene->selection.count) {
    std::vector<uint32_t> previous;
    for (auto &page : scene->pages)
      for (auto span : gpuPages.at(uint64_t(page->node) * 2 + uint64_t(page->fullSh)).spans)
        for (uint32_t i = 0; i < span.count; i++)
          previous.push_back(span.offset + i);
    D3D11_BOX box{0, 0, 0, static_cast<uint32_t>(previous.size() * 4), 1, 1};
    context->UpdateSubresource(active.buffer.Get(), 0, &box, previous.data(), 0, 0);
  }
  workCapacity = n;
  dirty = true;
}
uint64_t Renderer::cacheReserveGaussians() const {
  // Reserve at most 400 MB beyond the visible cut (less on small adapters).
  return std::min<uint64_t>(4000000, adapterMemory / 16 / sizeof(Raw));
}
bool Renderer::cached(size_t node, bool fullSh) const {
  if (pendingScene)
    return false;
  return gpuPages.contains(uint64_t(node) * 2 + 1) ||
         (!fullSh && gpuPages.contains(uint64_t(node) * 2));
}
void Renderer::prefetch(std::shared_ptr<Scene> next) {
  if (!scene || next->bundleId != arenaBundle)
    return;
  stageTiles(std::move(next));
  pendingPrefetch = true;
}
void Renderer::cancelPrefetch() {
  if (!prefetchPending())
    return;
  // Uploaded and not-yet-uploaded portions of a new page share one key;
  // an interrupted page must never be mistaken for a complete cached page.
  for (const auto &part : uploadParts) {
    const uint64_t key = uint64_t(part.page->node) * 2 + uint64_t(part.page->fullSh);
    if (auto it = gpuPages.find(key); it != gpuPages.end()) {
      for (auto span : it->second.spans)
        arenaSlots.release(span);
      gpuPages.erase(it);
    }
  }
  pendingScene.reset();
  uploadParts.clear();
  pendingPrefetch = false;
}
void Renderer::stageTiles(std::shared_ptr<Scene> next) {
  clearBindings();
  if (pendingScene)
    throw std::runtime_error("A GPU transition is already pending");
  auto key = [](const TilePage &p) { return uint64_t(p.node) * 2 + uint64_t(p.fullSh); };
  if (arenaBundle != next->bundleId) {
    scene.reset();
    gpuPages.clear();
    arenaSlots.free.clear();
    arenaCapacity = 0;
    workCapacity = 0;
    arenaBundle = next->bundleId;
    quant = buffer(static_cast<uint32_t>(next->quants.size()), 320, next->quants.data());
  }
  uint32_t additions = 0;
  std::set<uint64_t> protectedKeys;
  if (scene)
    for (auto &p : scene->pages)
      protectedKeys.insert(key(*p));
  for (auto &p : next->pages) {
    protectedKeys.insert(key(*p));
    if (!gpuPages.contains(key(*p)))
      additions += static_cast<uint32_t>(p->records.size());
  }
  const uint32_t required = static_cast<uint32_t>(
      next->budget +
      std::max<uint64_t>(std::min(next->budget * 2, cacheReserveGaussians()), additions));
  if (arenaCapacity < required) {
    const uint32_t capacity = std::max(required, arenaCapacity);
    auto replacement = buffer(capacity, 100);
    if (arenaCapacity) {
      D3D11_BOX box{0, 0, 0, arenaCapacity * 100, 1, 1};
      context->CopySubresourceRegion(replacement.buffer.Get(), 0, 0, 0, 0, raw.buffer.Get(), 0,
                                     &box);
    }
    raw = std::move(replacement);
    arenaSlots.release({arenaCapacity, capacity - arenaCapacity});
    arenaCapacity = capacity;
  }
  allocateWork(
      static_cast<uint32_t>(std::max<uint64_t>(next->budget, scene ? scene->selection.count : 0)));
  // Retain inactive tiles until space is actually needed; small back-and-forth movements reuse
  // them.
  while (arenaSlots.available() < additions) {
    auto oldest = gpuPages.end();
    for (auto it = gpuPages.begin(); it != gpuPages.end(); ++it)
      if (!protectedKeys.contains(it->first) &&
          (oldest == gpuPages.end() || it->second.used < oldest->second.used))
        oldest = it;
    if (oldest == gpuPages.end())
      throw std::runtime_error("GPU cache cannot evict protected pages");
    for (auto span : oldest->second.spans)
      arenaSlots.release(span);
    gpuPages.erase(oldest);
  }
  uploadParts.clear();
  uploadPart = 0;
  uploadOffset = 0;
  lastUploadBytes = 0;
  lastReusedGaussians = 0;
  for (auto &page : next->pages) {
    uint64_t id = key(*page);
    if (gpuPages.contains(id)) {
      gpuPages.at(id).used = ++cacheClock;
      lastReusedGaussians += page->records.size();
      continue;
    }
    auto spans = arenaSlots.allocate(static_cast<uint32_t>(page->records.size()));
    uint32_t source = 0;
    for (auto span : spans) {
      uploadParts.push_back({page, span, source});
      source += span.count;
    }
    gpuPages.emplace(id, GpuPage{std::move(spans), ++cacheClock});
  }
  pendingPrefetch = false;
  pendingScene = std::move(next);
}
bool Renderer::advanceUpload(uint32_t maxBytes) {
  if (!pendingScene)
    return false;
  clearBindings();
  uint32_t remaining = std::max(1u, maxBytes / 100);
  while (uploadPart < uploadParts.size() && remaining) {
    const auto &part = uploadParts[uploadPart];
    uint32_t count = std::min(remaining, part.span.count - uploadOffset);
    D3D11_BOX box{(part.span.offset + uploadOffset) * 100,         0, 0,
                  (part.span.offset + uploadOffset + count) * 100, 1, 1};
    context->UpdateSubresource(raw.buffer.Get(), 0, &box,
                               part.page->records.data() + part.source + uploadOffset, 0, 0);
    lastUploadBytes += uint64_t(count) * 100;
    totalUploadBytes += uint64_t(count) * 100;
    remaining -= count;
    uploadOffset += count;
    if (uploadOffset == part.span.count) {
      uploadOffset = 0;
      uploadPart++;
    }
  }
  if (uploadPart < uploadParts.size())
    return false;
  if (pendingPrefetch) {
    prefetchedGaussians += pendingScene->selection.count;
    pendingScene.reset();
    pendingPrefetch = false;
    uploadParts.clear();
    return false; // Cache-only upload never changes the visible antichain.
  }
  cacheActivations += lastReusedGaussians;
  std::vector<uint32_t> indices;
  indices.reserve(static_cast<size_t>(pendingScene->selection.count));
  for (auto &page : pendingScene->pages)
    for (auto span : gpuPages.at(uint64_t(page->node) * 2 + uint64_t(page->fullSh)).spans)
      for (uint32_t i = 0; i < span.count; i++)
        indices.push_back(span.offset + i);
  if (!indices.empty()) {
    D3D11_BOX box{0, 0, 0, static_cast<uint32_t>(indices.size() * 4), 1, 1};
    context->UpdateSubresource(active.buffer.Get(), 0, &box, indices.data(), 0, 0);
  }
  frame.count = static_cast<uint32_t>(indices.size());
  frame.groups = (frame.count + 255) / 256;
  scene = std::move(pendingScene);
  pendingScene.reset();
  uploadParts.clear();
  dirty = true;
  return true;
}
void Renderer::constants() {
  context->UpdateSubresource(constantBuffer.Get(), 0, nullptr, &frame, 0, 0);
  auto *cb = constantBuffer.Get();
  context->CSSetConstantBuffers(0, 1, &cb);
  context->VSSetConstantBuffers(0, 1, &cb);
  context->PSSetConstantBuffers(0, 1, &cb);
}
void Renderer::clearBindings() {
  ID3D11ShaderResourceView *srvs[8]{};
  ID3D11UnorderedAccessView *uavs[6]{};
  context->CSSetShaderResources(0, 8, srvs);
  context->CSSetUnorderedAccessViews(0, 6, uavs, nullptr);
  context->VSSetShaderResources(0, 7, srvs);
}
void Renderer::dispatch(ID3D11ComputeShader *shader, uint32_t g) {
  context->CSSetShader(shader, nullptr, 0);
  context->Dispatch(g, 1, 1);
}
void Renderer::render(const Camera &cam, bool changed, bool vsync, float exposure,
                      const std::filesystem::path &capture) {
  auto &timer = timers[timerIndex++ % 4];
  bool measure = !timer.pending;
  if (timer.pending) {
    D3D11_QUERY_DATA_TIMESTAMP_DISJOINT d{};
    UINT64 start{}, end{};
    if (context->GetData(timer.disjoint.Get(), &d, sizeof(d), D3D11_ASYNC_GETDATA_DONOTFLUSH) ==
            S_OK &&
        context->GetData(timer.start.Get(), &start, sizeof(start),
                         D3D11_ASYNC_GETDATA_DONOTFLUSH) == S_OK &&
        context->GetData(timer.end.Get(), &end, sizeof(end), D3D11_ASYNC_GETDATA_DONOTFLUSH) ==
            S_OK) {
      if (!d.Disjoint && d.Frequency) {
        gpuMs = double(end - start) * 1000 / d.Frequency;
        gpuSamples++;
      }
      timer.pending = false;
      measure = true;
    }
  }
  if (measure) {
    context->Begin(timer.disjoint.Get());
    context->End(timer.start.Get());
  }
  clearBindings();
  if (scene && frame.count) {
    put(frame.eye, cam.eye);
    put(frame.right, cam.right());
    put(frame.up, cam.vertical());
    put(frame.forward, cam.forward());
    frame.screen[0] = static_cast<float>(width);
    frame.screen[1] = static_cast<float>(height);
    frame.screen[2] = height / (2 * std::tan(cam.fov * .5f));
    frame.screen[3] = std::max(1e-5f, cam.distance() * 1e-5f);
    frame.tone[0] = exposure;
    constants();
    if (changed || dirty) {
      ID3D11ShaderResourceView *input[] = {raw.srv.Get(), quant.srv.Get()};
      context->CSSetShaderResources(0, 2, input);
      auto *indices = active.srv.Get();
      context->CSSetShaderResources(7, 1, &indices);
      ID3D11UnorderedAccessView *outputs[] = {projected.uav.Get(), sort[0].uav.Get()};
      context->CSSetUnorderedAccessViews(0, 2, outputs, nullptr);
      dispatch(project.Get(), frame.groups);
      clearBindings();
      for (uint32_t pass = 0; pass < 8; pass++) {
        frame.shift = pass * 4;
        constants();
        auto *in = sort[pass % 2].srv.Get();
        context->CSSetShaderResources(2, 1, &in);
        auto *hu = hist.uav.Get();
        context->CSSetUnorderedAccessViews(2, 1, &hu, nullptr);
        dispatch(histogram.Get(), frame.groups);
        clearBindings();
        auto *hs = hist.srv.Get();
        context->CSSetShaderResources(4, 1, &hs);
        ID3D11UnorderedAccessView *po[] = {prefixes.uav.Get(), totals.uav.Get()};
        context->CSSetUnorderedAccessViews(3, 2, po, nullptr);
        dispatch(prefix.Get(), 16);
        clearBindings();
        context->CSSetShaderResources(2, 1, &in);
        ID3D11ShaderResourceView *prefixViews[] = {prefixes.srv.Get(), totals.srv.Get()};
        context->CSSetShaderResources(5, 2, prefixViews);
        auto *out = sort[1 - pass % 2].uav.Get();
        context->CSSetUnorderedAccessViews(1, 1, &out, nullptr);
        dispatch(scatter.Get(), frame.groups);
        clearBindings();
      }
      dirty = false;
    }
  }
  const float background[] = {.025f, .033f, .045f, 1};
  context->ClearRenderTargetView(rtv.Get(), background);
  auto *renderTarget = rtv.Get();
  context->OMSetRenderTargets(1, &renderTarget, nullptr);
  D3D11_VIEWPORT vp{0, 0, static_cast<float>(width), static_cast<float>(height), 0, 1};
  context->RSSetViewports(1, &vp);
  context->RSSetState(rasterizer.Get());
  context->OMSetBlendState(blend.Get(), nullptr, ~0u);
  context->IASetInputLayout(nullptr);
  context->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
  context->VSSetShader(vs.Get(), nullptr, 0);
  context->PSSetShader(ps.Get(), nullptr, 0);
  if (scene && frame.count) {
    ID3D11ShaderResourceView *input[] = {sort[0].srv.Get(), projected.srv.Get()};
    context->VSSetShaderResources(2, 2, input);
    context->DrawInstanced(6, frame.count, 0, 0);
  }
  if (measure) {
    context->End(timer.end.Get());
    context->End(timer.disjoint.Get());
    timer.pending = true;
  }
  if (!capture.empty())
    screenshot(capture);
  check(swap->Present(vsync ? 1 : 0, 0), "Present (GPU device lost; reopen viewer)");
}
std::optional<Vec3> Renderer::pick(int x, int y) {
  if (!scene || !frame.count)
    return {};
  clearBindings();
  frame.pointer[0] = static_cast<float>(x);
  frame.pointer[1] = static_cast<float>(y);
  constants();
  auto *s = projected.srv.Get();
  context->CSSetShaderResources(3, 1, &s);
  auto *u = picks.uav.Get();
  context->CSSetUnorderedAccessViews(5, 1, &u, nullptr);
  dispatch(pickShader.Get(), frame.groups);
  clearBindings();
  context->CopyResource(pickReadback.Get(), picks.buffer.Get());
  D3D11_MAPPED_SUBRESOURCE map{};
  check(context->Map(pickReadback.Get(), 0, D3D11_MAP_READ, 0, &map), "Read pivot selection");
  auto *result = static_cast<const uint32_t *>(map.pData);
  uint32_t depth = ~0u, index = ~0u;
  for (uint32_t i = 0; i < frame.groups; i++)
    if (result[2 * i] < depth) {
      depth = result[2 * i];
      index = result[2 * i + 1];
    }
  context->Unmap(pickReadback.Get(), 0);
  if (!scene->bundleId.empty()) {
    for (const auto &page : scene->pages) {
      if (index < page->records.size()) {
        const auto &r = page->records[index];
        return position(r, scene->quants[r.tile]);
      }
      index -= static_cast<uint32_t>(page->records.size());
    }
    return {};
  }
  if (index >= scene->records.size())
    return {};
  const auto &r = scene->records[index];
  return position(r, scene->quants[r.tile]);
}
void Renderer::verifySort() {
  if (!scene || !frame.count)
    throw std::runtime_error("No scene for GPU sort validation");
  D3D11_BUFFER_DESC d{};
  sort[0].buffer->GetDesc(&d);
  d.Usage = D3D11_USAGE_STAGING;
  d.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
  d.BindFlags = 0;
  d.MiscFlags = 0;
  d.StructureByteStride = 0;
  ComPtr<ID3D11Buffer> read;
  check(device->CreateBuffer(&d, nullptr, &read), "Create sort verification");
  clearBindings();
  context->CopyResource(read.Get(), sort[0].buffer.Get());
  D3D11_MAPPED_SUBRESOURCE map{};
  check(context->Map(read.Get(), 0, D3D11_MAP_READ, 0, &map), "Read GPU sort");
  const auto *pairs = static_cast<const uint32_t *>(map.pData);
  std::vector<bool> seen(frame.count);
  bool valid = true;
  for (uint32_t i = 0; i < frame.count; i++) {
    if (i && pairs[2 * i] < pairs[2 * i - 2])
      valid = false;
    auto index = pairs[2 * i + 1];
    if (index >= frame.count || seen[index])
      valid = false;
    else
      seen[index] = true;
  }
  context->Unmap(read.Get(), 0);
  if (!valid)
    throw std::runtime_error("GPU radix sort order/permutation validation failed");
}
void Renderer::setEdgeOpacity(float multiplier) {
  if (!std::isfinite(multiplier) || multiplier < .25f || multiplier > 2.f)
    throw std::invalid_argument("Edge opacity must be between 0.25 and 2");
  frame.tone[1] = 1.f / multiplier;
  dirty = true;
}
void Renderer::setShOptions(uint32_t colorDegree, uint32_t opacityDegree, bool opacityEnabled) {
  if (colorDegree > 3 || opacityDegree > 3)
    throw std::invalid_argument("SH degrees must be between 0 and 3");
  uint32_t config = colorDegree | (opacityDegree << 2) | (opacityEnabled ? 16u : 0u);
  if (frame.shConfig != config) {
    frame.shConfig = config;
    dirty = true;
  }
}
void Renderer::verifyGpuContracts() {
  const uint32_t savedConfig = frame.shConfig;
  // A non-power-of-two population spanning >256 workgroups exercises every
  // radix-prefix chunk boundary. Cardinal SH expectations are independent
  // of the production shader implementation.
  auto fixture = std::make_shared<Scene>();
  fixture->records.resize(65539);
  fixture->quants.resize(1);
  auto &q = fixture->quants[0];
  q.v[0] = -1;
  q.v[1] = -1;
  q.v[2] = -1;
  q.v[3] = q.v[4] = q.v[5] = 2.f / 65535;
  q.v[6] = q.v[7] = q.v[8] = -4;
  q.v[12] = .4f;
  q.v[14] = q.v[15] = q.v[16] = .01f;
  for (int i = 0; i < 45; i++)
    q.v[17 + i] = .02f;
  for (int i = 0; i < 15; i++)
    q.v[62 + i] = .03f;
  for (size_t i = 0; i < fixture->records.size(); i++) {
    auto &r = fixture->records[i];
    auto *b = reinterpret_cast<uint8_t *>(r.data.data());
    auto put = [&](size_t at, uint16_t value) { std::memcpy(b + at, &value, 2); };
    put(0, 32768);
    put(2, uint16_t((i * 7919) % 65536));
    put(4, 32768);
    put(12, 32767);
    put(22, 2);
    put(24, 3);
    put(26, 4);
    for (int k = 0; k < 45; k++)
      b[28 + k] = uint8_t(int8_t((k % 5) - 2));
    for (int k = 0; k < 15; k++)
      b[73 + k] = uint8_t(int8_t((k % 7) - 3));
  }
  upload(fixture);
  Camera cam;
  cam.eye = {0, -5, 0};
  cam.pivot = {0, 0, 0};
  cam.up = {0, 0, 1};
  render(cam, true, false);
  verifySort();
  D3D11_BUFFER_DESC d{};
  projected.buffer->GetDesc(&d);
  d.Usage = D3D11_USAGE_STAGING;
  d.BindFlags = 0;
  d.MiscFlags = 0;
  d.StructureByteStride = 0;
  d.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
  ComPtr<ID3D11Buffer> read;
  check(device->CreateBuffer(&d, nullptr, &read), "Create projection verification");
  auto verify = [&](uint32_t colorDegree, uint32_t opacityDegree, bool opacityEnabled) {
    setShOptions(colorDegree, opacityDegree, opacityEnabled);
    // A stationary camera must still reproject when either SH setting changes.
    render(cam, false, false);
    clearBindings();
    context->CopyResource(read.Get(), projected.buffer.Get());
    D3D11_MAPPED_SUBRESOURCE map{};
    check(context->Map(read.Get(), 0, D3D11_MAP_READ, 0, &map), "Read projection verification");
    const float *values = static_cast<const float *>(map.pData);
    bool valid = true;
    // Independent analytic SH expectations along +Y. Degrees add 3, 5, 7 terms.
    const std::pair<int, double> terms[] = {{0, -.4886025119029199},
                                            {5, -.31539156525252005},
                                            {7, -.5462742152960396},
                                            {8, .5900435899266435},
                                            {10, .4570457994644658}};
    const uint32_t counts[] = {0, 3, 8, 15};
    float expected[4] = {float(.5 + .28209479177387814 * .02), float(.5 + .28209479177387814 * .03),
                         float(.5 + .28209479177387814 * .04), .4f};
    for (auto [k, basis] : terms) {
      if (uint32_t(k) < counts[colorDegree])
        for (int channel = 0; channel < 3; channel++)
          expected[channel] += float(basis * ((k + channel * 15) % 5 - 2) * .02);
      if (opacityEnabled && uint32_t(k) < counts[opacityDegree])
        expected[3] += float(basis * (k % 7 - 3) * .03);
    }
    expected[3] = 1 / (1 + std::exp(-expected[3]));
    for (size_t i = 0; i < fixture->records.size(); i++) {
      auto p = position(fixture->records[i], q);
      const float *v = values + 12 * i;
      valid = valid && std::abs(v[10] - (p.y + 5)) < 1e-4f;
      valid = valid && std::abs(v[0] - (width * .5f + frame.screen[2] * p.x / (p.y + 5))) < .001f;
      for (int c = 0; c < 4; c++)
        valid = valid && std::abs(v[6 + c] - expected[c]) < 1e-4f;
    }
    context->Unmap(read.Get(), 0);
    if (!valid)
      throw std::runtime_error("GPU SH controls regression: color=" + std::to_string(colorDegree) +
                               " opacity=" + std::to_string(opacityDegree) +
                               " enabled=" + std::to_string(opacityEnabled));
    if (!pick(width / 2, height / 2))
      throw std::runtime_error("GPU picking regression");
  };
  for (uint32_t color = 0; color <= 3; color++) {
    for (uint32_t opacity = 0; opacity <= 3; opacity++)
      verify(color, opacity, true);
    verify(color, 3, false);
  }
  for (auto degrees : {std::pair{4u, 3u}, std::pair{3u, 4u}}) {
    bool rejected = false;
    try {
      setShOptions(degrees.first, degrees.second, true);
    } catch (const std::invalid_argument &) {
      rejected = true;
    }
    if (!rejected)
      throw std::runtime_error("Invalid SH degree was accepted");
  }
  setShOptions(savedConfig & 3, (savedConfig >> 2) & 3, (savedConfig & 16) != 0);
  // Real GPU streaming regression: keep the active cut valid during partial transfers.
  auto page = [&](size_t id, uint32_t n) {
    auto p = std::make_shared<TilePage>();
    p->node = id;
    p->fullSh = true;
    p->records.assign(fixture->records.begin(), fixture->records.begin() + n);
    for (auto &raw : p->records)
      raw.tile = static_cast<uint32_t>(id);
    return p;
  };
  auto a = page(0, 257), b = page(1, 263), c = page(2, 259);
  auto streamScene = [&](std::vector<std::shared_ptr<TilePage>> pages, uint64_t budget) {
    auto s = std::make_shared<Scene>();
    s->bundleId = "streaming-gpu-contract";
    s->budget = budget;
    s->quants = {q, q, q};
    s->pages = std::move(pages);
    for (auto &p : s->pages) {
      s->selection.nodes.push_back(p->node);
      s->selection.count += p->records.size();
    }
    return s;
  };
  auto complete = [&] {
    while (uploadPending()) {
      auto before = lastUploadBytes;
      bool committed = advanceUpload(1000);
      if (lastUploadBytes - before > 1000)
        throw std::runtime_error("GPU upload slice exceeded budget");
      if (scene && !committed) {
        render(cam, true, false);
        verifySort();
      }
    }
    render(cam, true, false);
    verifySort();
    if (!pick(width / 2, height / 2))
      throw std::runtime_error("Streaming pivot regression");
  };
  upload(streamScene({a, b}, 1024));
  complete();
  prefetch(streamScene({c}, 1024));
  advanceUpload(1000);
  cancelPrefetch();
  if (cached(2, true) || scene->selection.nodes != std::vector<size_t>({0, 1}))
    throw std::runtime_error("Cancelled prefetch corrupted GPU cache");
  prefetch(streamScene({c}, 1024));
  complete();
  if (!cached(2, true) || scene->selection.nodes != std::vector<size_t>({0, 1}))
    throw std::runtime_error("Prefetch changed the visible cut");
  upload(streamScene({a, c}, 2048));
  complete();
  if (lastUploadBytes != 0 || lastReusedGaussians != 516)
    throw std::runtime_error("GPU tile reuse regression");
  upload(streamScene({a, b}, 1024));
  complete();
  if (lastUploadBytes != 0 || lastReusedGaussians != 520)
    throw std::runtime_error("GPU cached backtracking regression");
}
void Renderer::screenshot(const std::filesystem::path &path) {
  // Snapshot the currently displayed swap-chain image without desktop capture.
  ComPtr<ID3D11Texture2D> back;
  check(swap->GetBuffer(0, IID_PPV_ARGS(&back)), "Get capture buffer");
  D3D11_TEXTURE2D_DESC d{};
  back->GetDesc(&d);
  d.Usage = D3D11_USAGE_STAGING;
  d.BindFlags = 0;
  d.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
  d.MiscFlags = 0;
  ComPtr<ID3D11Texture2D> read;
  check(device->CreateTexture2D(&d, nullptr, &read), "Create capture texture");
  context->CopyResource(read.Get(), back.Get());
  D3D11_MAPPED_SUBRESOURCE map{};
  check(context->Map(read.Get(), 0, D3D11_MAP_READ, 0, &map), "Read capture texture");
  BITMAPFILEHEADER header{};
  header.bfType = 0x4d42;
  header.bfOffBits = sizeof(header) + sizeof(BITMAPINFOHEADER);
  header.bfSize = header.bfOffBits + d.Width * d.Height * 4;
  BITMAPINFOHEADER info{};
  info.biSize = sizeof(info);
  info.biWidth = d.Width;
  info.biHeight = -static_cast<LONG>(d.Height);
  info.biPlanes = 1;
  info.biBitCount = 32;
  info.biCompression = BI_RGB;
  std::ofstream out(path, std::ios::binary);
  out.write(reinterpret_cast<char *>(&header), sizeof(header));
  out.write(reinterpret_cast<char *>(&info), sizeof(info));
  std::vector<uint8_t> row(d.Width * 4);
  for (UINT y = 0; y < d.Height; y++) {
    auto *src = static_cast<uint8_t *>(map.pData) + y * map.RowPitch;
    for (UINT x = 0; x < d.Width; x++) {
      row[4 * x] = src[4 * x + 2];
      row[4 * x + 1] = src[4 * x + 1];
      row[4 * x + 2] = src[4 * x];
      row[4 * x + 3] = 255;
    }
    out.write(reinterpret_cast<char *>(row.data()), row.size());
  }
  context->Unmap(read.Get(), 0);
  if (!out)
    throw std::runtime_error("Cannot write screenshot");
}
} // namespace gs
