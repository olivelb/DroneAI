#pragma once
#include "bundle.hpp"
#include "arena.hpp"
#include <d3d11.h>
#include <dxgi1_6.h>
#include <optional>
#include <windows.h>
#include <wrl/client.h>
namespace gs {
using Microsoft::WRL::ComPtr;
struct Buffer {
  ComPtr<ID3D11Buffer> buffer;
  ComPtr<ID3D11ShaderResourceView> srv;
  ComPtr<ID3D11UnorderedAccessView> uav;
};
struct Frame {
  float eye[4]{}, right[4]{}, up[4]{}, forward[4]{}, screen[4]{};
  uint32_t count{}, groups{}, shift{}, shConfig{31}; // color 3, opacity 3, enabled
  float pointer[4]{}, tone[4]{1, 1, 0, 0};
};
static_assert(sizeof(Frame) == 128);
class Renderer {
public:
  Renderer(HWND window, int width, int height, bool debug = false);
  void resize(int width, int height);
  void upload(std::shared_ptr<Scene> scene);
  void prefetch(std::shared_ptr<Scene> scene);
  void cancelPrefetch();
  bool prefetchPending() const { return pendingScene && pendingPrefetch; }
  bool cached(size_t node, bool fullSh) const;
  uint64_t cacheReserveGaussians() const;
  uint64_t prefetchedGaussians{}, cacheActivations{}, totalUploadBytes{};
  bool advanceUpload(uint32_t maxBytes = 4 * 1024 * 1024);
  bool uploadPending() const { return pendingScene != nullptr; }
  uint64_t lastUploadBytes{}, lastReusedGaussians{};
  void render(const Camera &camera, bool changed, bool vsync, float exposure = 1,
              const std::filesystem::path &capture = {});
  std::optional<Vec3> pick(int x, int y);
  void screenshot(const std::filesystem::path &path);
  void setEdgeOpacity(float multiplier);
  void setShOptions(uint32_t colorDegree, uint32_t opacityDegree, bool opacityEnabled);
  void verifySort();
  void verifyGpuContracts();
  std::string adapterName;
  uint64_t adapterMemory{};
  double gpuMs{};
  uint64_t gpuSamples{};
  std::shared_ptr<Scene> scene;

private:
  Buffer buffer(uint32_t count, uint32_t stride, const void *initial = nullptr,
                bool writable = false);
  void constants();
  void clearBindings();
  void dispatch(ID3D11ComputeShader *shader, uint32_t groups);
  void target();
  ComPtr<ID3D11Device> device;
  ComPtr<ID3D11DeviceContext> context;
  ComPtr<IDXGISwapChain1> swap;
  ComPtr<ID3D11RenderTargetView> rtv;
  ComPtr<ID3D11Buffer> constantBuffer;
  ComPtr<ID3D11ComputeShader> project, histogram, prefix, scatter, pickShader;
  ComPtr<ID3D11VertexShader> vs;
  ComPtr<ID3D11PixelShader> ps;
  ComPtr<ID3D11BlendState> blend;
  ComPtr<ID3D11RasterizerState> rasterizer;
  Buffer raw, quant, projected, sort[2], hist, prefixes, totals, picks, active;
  void allocateWork(uint32_t count);
  void stageTiles(std::shared_ptr<Scene>);
  struct GpuPage {
    std::vector<Span> spans;
    uint64_t used{};
  };
  struct UploadPart {
    std::shared_ptr<TilePage> page;
    Span span;
    uint32_t source{};
  };
  std::unordered_map<uint64_t, GpuPage> gpuPages;
  std::vector<UploadPart> uploadParts;
  size_t uploadPart{};
  uint32_t uploadOffset{};
  uint32_t arenaCapacity{}, workCapacity{};
  ArenaSlots arenaSlots;
  std::string arenaBundle;
  std::shared_ptr<Scene> pendingScene;
  bool pendingPrefetch{};
  uint64_t cacheClock{};
  ComPtr<ID3D11Buffer> pickReadback;
  struct Timer {
    ComPtr<ID3D11Query> disjoint, start, end;
    bool pending{};
  };
  Timer timers[4];
  unsigned timerIndex{};
  int width{}, height{};
  Frame frame;
  bool dirty = true;
};
} // namespace gs
