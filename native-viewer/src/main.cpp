#include "loader.hpp"
#include "renderer.hpp"
#include "home-view.hpp"
#include <chrono>
#include <fstream>
#include <shellapi.h>
#include <shobjidl.h>
#include <windowsx.h>
using namespace gs;
using Clock = std::chrono::steady_clock;
namespace {
std::wstring wide(const std::string &s) {
  if (s.empty())
    return {};
  int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), int(s.size()), nullptr, 0);
  std::wstring r(n, 0);
  MultiByteToWideChar(CP_UTF8, 0, s.data(), int(s.size()), r.data(), n);
  return r;
}
std::string utf8(const std::wstring &s) {
  int n = WideCharToMultiByte(CP_UTF8, 0, s.data(), int(s.size()), nullptr, 0, nullptr, nullptr);
  std::string r(n, 0);
  WideCharToMultiByte(CP_UTF8, 0, s.data(), int(s.size()), r.data(), n, nullptr, nullptr);
  return r;
}
enum MenuId {
  Open = 100,
  Fit,
  Help,
  Exit,
  Fly,
  Vsync,
  Fullscreen,
  RollLeft,
  RollRight,
  ResetRoll,
  Top,
  Front,
  Side,
  ExposureDown,
  ExposureUp,
  ExposureReset,
  BudgetBase = 200,
  QualityBase = 220,
  ColorShBase = 240,
  OpacityShBase = 250,
  OpacityShEnabled = 260,
  SaveHome,
  RestoreHome,
  HaloCache,
  EdgeDown,
  EdgeUp,
  EdgeBase = 280
};
constexpr uint64_t budgets[] = {250000, 500000, 1000000, 2000000, 4000000, 8000000};
constexpr float qualities[] = {.5f, 1.f, 2.f, 4.f};
struct App {
  HWND window{};
  std::unique_ptr<Renderer> renderer;
  Loader loader;
  std::shared_ptr<Bundle> bundle;
  Camera camera;
  uint64_t generation{}, budget = 2000000;
  float quality = 2, exposure = 1, edgeOpacity = 1;
  uint32_t colorShDegree = 3, opacityShDegree = 3;
  bool opacityShEnabled = true;
  int width = 1440, height = 900, lastX{}, lastY{}, button{};
  bool running = true, dirty = true, lodDirty{}, fly{}, vsync = true, fullscreen{}, minimized{};
  WINDOWPLACEMENT placement{sizeof(WINDOWPLACEMENT)};
  Clock::time_point lastMotion = Clock::now(), lastLodRequest{};
  std::string notice = "Ouvrir un dossier GSTile";
  std::shared_ptr<Bundle> targetBundle;
  Clock::time_point targetMotion{};
  uint64_t targetBudget{};
  float targetQuality{};
  int targetWidth{}, targetHeight{};
  Selection cachedTarget;
  bool haloEnabled = true, haloLoading{};
  Clock::time_point haloMotion{};
  std::vector<size_t> haloNodes;
  size_t haloCursor{};
  uint64_t haloErrors{};
  void cancelHalo() {
    if (haloLoading) {
      loader.cancel();
      generation++;
      haloLoading = false;
    }
    if (renderer)
      renderer->cancelPrefetch();
    haloMotion = {};
  }
  void prefetchHalo() {
    if (!haloEnabled || !bundle || lodDirty || loader.busy || renderer->uploadPending() ||
        !renderer->scene || Clock::now() - lastMotion < std::chrono::milliseconds(350))
      return;
    if (haloMotion != lastMotion) {
      Camera expanded = camera;
      expanded.fov = std::max(camera.fov, std::min(camera.fov * 1.5f, 2.094395f));
      const float scale = std::tan(expanded.fov * .5f) / std::tan(camera.fov * .5f);
      auto halo =
          bundle->select(expanded, int(width * scale), int(height * scale), budget * 2, quality);
      haloNodes.clear();
      uint64_t planned = 0;
      const uint64_t limit = std::min(budget, renderer->cacheReserveGaussians() / 2);
      const auto &visible = targetSelection().nodes;
      for (auto id : halo.nodes) {
        if (std::find(visible.begin(), visible.end(), id) != visible.end())
          continue;
        const auto cost = bundle->nodes[id].tile.count;
        if (planned + cost > limit)
          continue;
        haloNodes.push_back(id);
        planned += cost;
      }
      haloCursor = 0;
      haloMotion = lastMotion;
    }
    const bool withSh = colorShDegree > 0 || (opacityShEnabled && opacityShDegree > 0);
    Selection batch;
    while (haloCursor < haloNodes.size()) {
      const auto id = haloNodes[haloCursor];
      const auto count = bundle->nodes[id].tile.count;
      if (renderer->cached(id, withSh)) {
        haloCursor++;
        continue;
      }
      // One bounded background read/upload group, preemptible by visible work.
      if (batch.count && batch.count + count > 65536)
        break;
      haloCursor++;
      batch.nodes.push_back(id);
      batch.count += count;
    }
    if (batch.nodes.empty())
      return;
    LoadRequest req;
    req.generation = ++generation;
    req.bundle = bundle;
    req.target = batch;
    req.budget = budget;
    req.withSh = withSh;
    req.prefetch = true;
    haloLoading = true;
    loader.submit(std::move(req));
  }
  const Selection &targetSelection() {
    if (targetBundle != bundle || targetMotion != lastMotion || targetBudget != budget ||
        targetQuality != quality || targetWidth != width || targetHeight != height) {
      cachedTarget = bundle->select(camera, width, height, budget, quality,
                                    renderer->scene ? &renderer->scene->selection : nullptr);
      targetBundle = bundle;
      targetMotion = lastMotion;
      targetBudget = budget;
      targetQuality = quality;
      targetWidth = width;
      targetHeight = height;
    }
    return cachedTarget;
  }
  void requestLoad(std::filesystem::path path = {}) {
    if (path.empty() && !bundle)
      return;
    cancelHalo();
    generation++;
    LoadRequest request{generation, path, bundle, camera, width, height, budget, quality, {}};
    request.current = renderer ? renderer->scene : nullptr;
    if (path.empty())
      request.target = targetSelection();
    request.withSh = (colorShDegree > 0 || (opacityShEnabled && opacityShDegree > 0)) &&
                     Clock::now() - lastMotion > std::chrono::milliseconds(200);
    loader.submit(std::move(request));
    lastLodRequest = Clock::now();
    notice = path.empty() ? "Affinage LOD..." : "Lecture du manifeste...";
    lodDirty = false;
  }
  void changed() {
    cancelHalo();
    dirty = true;
    lodDirty = true;
    lastMotion = Clock::now();
  }
  void open() {
    ComPtr<IFileOpenDialog> dialog;
    if (FAILED(CoCreateInstance(CLSID_FileOpenDialog, nullptr, CLSCTX_INPROC_SERVER,
                                IID_PPV_ARGS(&dialog))))
      throw std::runtime_error("Cannot open Windows folder dialog");
    DWORD flags{};
    dialog->GetOptions(&flags);
    dialog->SetOptions(flags | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST);
    dialog->SetTitle(L"Choisir le dossier GSTile contenant manifest.json");
    if (dialog->Show(window) != S_OK)
      return;
    ComPtr<IShellItem> item;
    if (FAILED(dialog->GetResult(&item)))
      return;
    PWSTR path{};
    if (FAILED(item->GetDisplayName(SIGDN_FILESYSPATH, &path)))
      return;
    std::filesystem::path selected(path);
    CoTaskMemFree(path);
    requestLoad(selected);
  }
  void toggleFullscreen() {
    fullscreen = !fullscreen;
    if (fullscreen) {
      GetWindowPlacement(window, &placement);
      MONITORINFO m{sizeof(m)};
      GetMonitorInfo(MonitorFromWindow(window, MONITOR_DEFAULTTONEAREST), &m);
      SetWindowLongPtr(window, GWL_STYLE, WS_POPUP | WS_VISIBLE);
      SetWindowPos(window, HWND_TOP, m.rcMonitor.left, m.rcMonitor.top,
                   m.rcMonitor.right - m.rcMonitor.left, m.rcMonitor.bottom - m.rcMonitor.top,
                   SWP_FRAMECHANGED);
    } else {
      SetWindowLongPtr(window, GWL_STYLE, WS_OVERLAPPEDWINDOW | WS_VISIBLE);
      SetWindowPlacement(window, &placement);
      SetWindowPos(window, nullptr, 0, 0, 0, 0,
                   SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED);
    }
  }
  void command(int id) {
    if ((id >= EdgeBase && id < EdgeBase + 8) || id == EdgeDown || id == EdgeUp) {
      edgeOpacity = id == EdgeDown ? std::max(.25f, edgeOpacity - .05f)
                    : id == EdgeUp ? std::min(2.f, edgeOpacity + .05f)
                                   : .25f * (id - EdgeBase + 1);
      renderer->setEdgeOpacity(edgeOpacity);
      dirty = true;
      title();
      return;
    }
    if (id >= BudgetBase && id < BudgetBase + 6) {
      budget = budgets[id - BudgetBase];
      requestLoad();
      return;
    }
    if (id >= QualityBase && id < QualityBase + 4) {
      quality = qualities[id - QualityBase];
      requestLoad();
      return;
    }
    if ((id >= ColorShBase && id < ColorShBase + 4) ||
        (id >= OpacityShBase && id < OpacityShBase + 4) || id == OpacityShEnabled) {
      if (id >= ColorShBase && id < ColorShBase + 4)
        colorShDegree = id - ColorShBase;
      else if (id >= OpacityShBase && id < OpacityShBase + 4)
        opacityShDegree = id - OpacityShBase;
      else
        opacityShEnabled = !opacityShEnabled;
      cancelHalo();
      renderer->setShOptions(colorShDegree, opacityShDegree, opacityShEnabled);
      lodDirty = true;
      dirty = true;
      title();
      return;
    }
    switch (id) {
    case HaloCache:
      haloEnabled = !haloEnabled;
      cancelHalo();
      title();
      break;
    case SaveHome:
      if (bundle) {
        saveHomeCamera(bundle->id, camera);
        notice = "Vue d'accueil enregistree";
      }
      break;
    case RestoreHome:
      if (bundle && restoreHomeCamera(bundle->id, camera)) {
        changed();
        notice = "Vue d'accueil";
      }
      break;
    case Open:
      open();
      break;
    case Fit:
      if (bundle) {
        camera.fit(bundle->nodes[bundle->root].bounds, float(width) / height);
        changed();
      }
      break;
    case Fly:
      fly = !fly;
      notice = fly ? "Mode libre" : "Mode orbite";
      break;
    case Vsync:
      vsync = !vsync;
      dirty = true;
      break;
    case Fullscreen:
      toggleFullscreen();
      break;
    case RollLeft:
      camera.roll(-.1f);
      changed();
      break;
    case RollRight:
      camera.roll(.1f);
      changed();
      break;
    case ResetRoll:
      camera.up =
          std::abs(dot(camera.forward(), Vec3{0, 0, 1})) > .99f ? Vec3{0, 1, 0} : Vec3{0, 0, 1};
      changed();
      break;
    case Top:
    case Front:
    case Side: {
      float d = camera.distance();
      Vec3 axis = id == Top ? Vec3{0, 0, 1} : id == Front ? Vec3{0, -1, 0} : Vec3{1, 0, 0};
      camera.eye = camera.pivot + axis * d;
      camera.up = id == Top ? Vec3{0, 1, 0} : Vec3{0, 0, 1};
      changed();
      break;
    }
    case ExposureDown:
      exposure = std::max(.125f, exposure / 1.189207f);
      dirty = true;
      break;
    case ExposureUp:
      exposure = std::min(8.f, exposure * 1.189207f);
      dirty = true;
      break;
    case ExposureReset:
      exposure = 1;
      dirty = true;
      break;
    case Help:
      MessageBoxW(window,
                  L"Glisser gauche : orbite autour du pivot\n"
                  L"Glisser milieu / Maj + gauche : deplacement lateral\n"
                  L"Glisser droit : regarder autour de soi\n"
                  L"Molette : avancer / reculer vers le pivot\n"
                  L"Ctrl + molette : focale (champ de vision)\n"
                  L"Double-clic gauche : pivot sur la surface visible\n\n"
                  L"ZQSD / WASD / fleches : deplacement (touches physiques)\n"
                  L"R / F ou Page haut / bas : monter, descendre\n"
                  L"A / E (AZERTY), Q / E (QWERTY), pav. 7 / 9 : roulis\n"
                  L"Maj : vitesse x4 ; Ctrl : vitesse x0,2\n"
                  L"Espace : basculer orbite / libre\n"
                  L"Origine : cadrer ; 1 / 2 / 3 : face / cote / dessus\n"
                  L"Ctrl+O : ouvrir ; F11 : plein ecran ; Echap : sortir\n\n"
                  L"Menus Qualite : budget GPU et erreur LOD en pixels.\n"
                  L"Affichage : degres SH couleur et opacite independants (0 a 3).\n"
                  L"Degre 0 / SH opacite desactives : conserver la valeur de base.\n"
                  L"Le titre indique le temps GPU et le nombre de gaussiennes.\n"
                  L"Le pivot est le centre de la gaussienne visible la plus proche\n"
                  L"dont la contribution alpha au clic atteint 0,1.",
                  L"GSTile Native - navigation", MB_OK | MB_ICONINFORMATION);
      break;
    case Exit:
      DestroyWindow(window);
      break;
    }
  }
  void tick(float dt) {
    for (auto &result : loader.poll()) {
      if (result.generation != generation)
        continue;
      if (result.prefetch) {
        haloLoading = false;
        if (!result.error.empty()) {
          haloErrors++;
          continue;
        }
        renderer->prefetch(result.scene);
        continue;
      }
      if (!result.error.empty()) {
        notice = "Erreur de chargement";
        MessageBoxW(window, wide(result.error).c_str(), L"GSTile - chargement impossible",
                    MB_OK | MB_ICONERROR);
        continue;
      }
      if (renderer->uploadPending()) {
        // Opening a different bundle can supersede a bounded GPU transfer.
        while (renderer->uploadPending())
          renderer->advanceUpload();
      }
      renderer->upload(result.scene);
      lodDirty = true;
      bundle = result.bundle;
      if (result.fit) {
        camera = result.camera;
        try {
          restoreHomeCamera(bundle->id, camera);
        } catch (const std::exception &e) {
          notice = e.what();
        }
      }
      dirty = true;
      notice = result.scene->selection.limited ? "Budget atteint : proxies actifs" : "Pret";
    }
    if (GetForegroundWindow() == window) {
      auto key = [](int k) { return (GetAsyncKeyState(k) & 0x8000) != 0; };
      auto physical = [&](UINT scan) { return key(int(MapVirtualKeyW(scan, MAPVK_VSC_TO_VK_EX))); };
      float speed = camera.distance() * .6f * std::min(dt, .05f) *
                    (key(VK_SHIFT)     ? 4.f
                     : key(VK_CONTROL) ? .2f
                                       : 1.f);
      Vec3 move{};
      if (physical(0x11) || key(VK_UP))
        move = move + camera.forward() * speed;
      if (physical(0x1f) || key(VK_DOWN))
        move = move - camera.forward() * speed;
      if (physical(0x1e) || key(VK_LEFT))
        move = move - camera.right() * speed;
      if (physical(0x20) || key(VK_RIGHT))
        move = move + camera.right() * speed;
      if (key('R') || key(VK_PRIOR))
        move = move + camera.vertical() * speed;
      if (key('F') || key(VK_NEXT))
        move = move - camera.vertical() * speed;
      if (length(move) > 0) {
        camera.move(move);
        changed();
      }
      if (key(VK_NUMPAD7) || physical(0x10)) {
        camera.roll(-dt);
        changed();
      }
      if (key(VK_NUMPAD9) || physical(0x12)) {
        camera.roll(dt);
        changed();
      }
    }
    // A completely warm target needs neither disk/decode work nor intermediate
    // proxies. Publish its cached pages at once, bypassing the motion throttle.
    if (lodDirty && bundle && !loader.busy && !renderer->uploadPending() && renderer->scene) {
      const auto &target = targetSelection();
      if (!(target == renderer->scene->selection) &&
          std::all_of(target.nodes.begin(), target.nodes.end(),
                      [&](size_t id) { return renderer->cached(id, true); })) {
        if (auto ready = bundle->cachedTiles(target, budget, renderer->scene->pages)) {
          renderer->upload(std::move(ready));
          lodDirty = false;
          notice = target.limited ? "Budget atteint : proxies actifs" : "Pret";
        }
      }
    }
    if (lodDirty && bundle && !loader.busy && !renderer->uploadPending() &&
        Clock::now() - lastLodRequest >
            std::chrono::milliseconds(
                Clock::now() - lastMotion > std::chrono::milliseconds(200) ? 0 : 100)) {
      const auto &cut = targetSelection();
      const bool missingSh =
          (colorShDegree > 0 || (opacityShEnabled && opacityShDegree > 0)) && renderer->scene &&
          std::any_of(renderer->scene->pages.begin(), renderer->scene->pages.end(),
                      [](const auto &p) { return !p->fullSh; });
      if (!renderer->scene || !(cut == renderer->scene->selection) ||
          (missingSh && Clock::now() - lastMotion > std::chrono::milliseconds(200)))
        requestLoad();
      else
        lodDirty = missingSh;
    }
    prefetchHalo();
  }
  void title() {
    const bool refining = bundle && renderer->scene && renderer->scene->bundleId == bundle->id &&
                          ((renderer->uploadPending() && !renderer->prefetchPending()) ||
                           !(targetSelection() == renderer->scene->selection));
    std::string text = "GSTile Native | " +
                       (loader.busy && !haloLoading ? std::string("Chargement / affinage...")
                        : refining                  ? std::string("Affinage LOD...")
                                                    : notice) +
                       " | ";
    if (renderer->scene) {
      char part[256];
      sprintf_s(part, "%llu / %llu splats | GPU %.2f ms | LOD %.1f px%s | ",
                static_cast<unsigned long long>(renderer->scene->selection.count),
                static_cast<unsigned long long>(bundle->sourceCount), renderer->gpuMs,
                renderer->scene->selection.maxError,
                renderer->scene->selection.limited ? " (budget)" : "");
      text += part;
    }
    text += "SH couleur " + std::to_string(colorShDegree) + " / opacite " +
            (opacityShEnabled ? std::to_string(opacityShDegree) : "OFF") + " | ";
    text += "Bords " + std::to_string(int(std::round(edgeOpacity * 100))) + "% | ";
    text += renderer->adapterName + " | F1 : aide";
    SetWindowTextW(window, wide(text).c_str());
    HMENU menu = GetMenu(window);
    for (int i = 0; i < 8; i++)
      CheckMenuItem(menu, EdgeBase + i,
                    MF_BYCOMMAND | (std::abs(edgeOpacity - .25f * (i + 1)) < .001f ? MF_CHECKED
                                                                                   : MF_UNCHECKED));
    CheckMenuItem(menu, HaloCache, MF_BYCOMMAND | (haloEnabled ? MF_CHECKED : MF_UNCHECKED));
    CheckMenuRadioItem(menu, ColorShBase, ColorShBase + 3, ColorShBase + colorShDegree,
                       MF_BYCOMMAND);
    CheckMenuRadioItem(menu, OpacityShBase, OpacityShBase + 3, OpacityShBase + opacityShDegree,
                       MF_BYCOMMAND);
    CheckMenuItem(menu, OpacityShEnabled,
                  MF_BYCOMMAND | (opacityShEnabled ? MF_CHECKED : MF_UNCHECKED));
    CheckMenuItem(menu, Fly, MF_BYCOMMAND | (fly ? MF_CHECKED : MF_UNCHECKED));
    CheckMenuItem(menu, Vsync, MF_BYCOMMAND | (vsync ? MF_CHECKED : MF_UNCHECKED));
    for (int i = 0; i < 6; i++)
      CheckMenuItem(menu, BudgetBase + i,
                    MF_BYCOMMAND | (budget == budgets[i] ? MF_CHECKED : MF_UNCHECKED));
    for (int i = 0; i < 4; i++)
      CheckMenuItem(menu, QualityBase + i,
                    MF_BYCOMMAND | (quality == qualities[i] ? MF_CHECKED : MF_UNCHECKED));
  }
};
LRESULT CALLBACK windowProc(HWND window, UINT message, WPARAM w, LPARAM l) {
  auto *app = reinterpret_cast<App *>(GetWindowLongPtr(window, GWLP_USERDATA));
  if (message == WM_NCCREATE) {
    app = static_cast<App *>(reinterpret_cast<CREATESTRUCT *>(l)->lpCreateParams);
    SetWindowLongPtr(window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(app));
  }
  if (!app)
    return DefWindowProc(window, message, w, l);
  try {
    switch (message) {
    case WM_SIZE:
      app->minimized = w == SIZE_MINIMIZED;
      app->width = std::max(1, int(LOWORD(l)));
      app->height = std::max(1, int(HIWORD(l)));
      if (app->renderer && !app->minimized)
        app->renderer->resize(app->width, app->height);
      app->changed();
      return 0;
    case WM_COMMAND:
      app->command(LOWORD(w));
      return 0;
    case WM_KEYDOWN:
      if (l & (1ll << 30))
        return 0;
      if (w == 'O' && (GetKeyState(VK_CONTROL) & 0x8000))
        app->command(Open);
      if (w == VK_F1)
        app->command(Help);
      if (w == VK_HOME)
        app->command(Fit);
      if (w == VK_F11)
        app->command(Fullscreen);
      if (w == VK_SPACE)
        app->command(Fly);
      if (w == VK_ESCAPE && app->fullscreen)
        app->toggleFullscreen();
      if (w == '1')
        app->command(Front);
      if (w == '2')
        app->command(Side);
      if (w == '3')
        app->command(Top);
      return 0;
    case WM_LBUTTONDOWN:
    case WM_MBUTTONDOWN:
    case WM_RBUTTONDOWN:
      app->button = message == WM_LBUTTONDOWN ? 1 : message == WM_MBUTTONDOWN ? 2 : 3;
      app->lastX = GET_X_LPARAM(l);
      app->lastY = GET_Y_LPARAM(l);
      SetCapture(window);
      return 0;
    case WM_LBUTTONUP:
    case WM_MBUTTONUP:
    case WM_RBUTTONUP:
      app->button = 0;
      ReleaseCapture();
      return 0;
    case WM_CAPTURECHANGED:
      app->button = 0;
      return 0;
    case WM_LBUTTONDBLCLK:
      if (app->renderer) {
        auto p = app->renderer->pick(GET_X_LPARAM(l), GET_Y_LPARAM(l));
        if (p) {
          app->camera.pivot = *p;
          app->notice = "Pivot sur la surface";
          app->changed();
        } else
          app->notice = "Aucune surface sous le curseur";
      }
      app->button = 0;
      ReleaseCapture();
      return 0;
    case WM_MOUSEMOVE: {
      int x = GET_X_LPARAM(l), y = GET_Y_LPARAM(l);
      float dx = float(x - app->lastX), dy = float(y - app->lastY);
      app->lastX = x;
      app->lastY = y;
      if (!app->button)
        return 0;
      if (app->button == 2 || (app->button == 1 && (w & MK_SHIFT))) {
        float scale = 2 * app->camera.distance() * std::tan(app->camera.fov * .5f) / app->height;
        app->camera.pan(-dx * scale, dy * scale);
      } else if (app->button == 3 || app->fly)
        app->camera.look(dx * .004f, dy * .004f);
      else
        app->camera.orbit(dx * .004f, dy * .004f);
      app->changed();
      return 0;
    }
    case WM_MOUSEWHEEL: {
      float delta = GET_WHEEL_DELTA_WPARAM(w) / 120.f;
      if (GET_KEYSTATE_WPARAM(w) & MK_CONTROL)
        app->camera.fov =
            std::clamp(app->camera.fov * std::exp(-delta * .08f), .174533f, 2.094395f);
      else
        app->camera.dolly(-delta * .12f);
      app->changed();
      return 0;
    }
    case WM_DPICHANGED: {
      auto *r = reinterpret_cast<RECT *>(l);
      SetWindowPos(window, nullptr, r->left, r->top, r->right - r->left, r->bottom - r->top,
                   SWP_NOZORDER);
      return 0;
    }
    case WM_ERASEBKGND:
      return 1;
    case WM_PAINT: {
      PAINTSTRUCT p;
      BeginPaint(window, &p);
      EndPaint(window, &p);
      app->dirty = true;
      return 0;
    }
    case WM_DESTROY:
      app->running = false;
      PostQuitMessage(0);
      return 0;
    }
  } catch (const std::exception &e) {
    MessageBoxW(window, wide(e.what()).c_str(), L"GSTile Viewer", MB_OK | MB_ICONERROR);
  }
  return DefWindowProc(window, message, w, l);
}
HMENU menus() {
  HMENU bar = CreateMenu(), file = CreatePopupMenu(), nav = CreatePopupMenu(),
        quality = CreatePopupMenu(), budget = CreatePopupMenu(), error = CreatePopupMenu(),
        render = CreatePopupMenu(), colorSh = CreatePopupMenu(), opacitySh = CreatePopupMenu();
  AppendMenuW(file, MF_STRING, Open, L"&Ouvrir un dossier...\tCtrl+O");
  AppendMenuW(file, MF_STRING, Exit, L"&Quitter");
  for (auto [id, label] : {std::pair{Fit, L"Cadrer\tOrigine"},
                           {Fly, L"Mode libre\tEspace"},
                           {Front, L"Face\t1"},
                           {Side, L"Cote\t2"},
                           {Top, L"Dessus\t3"},
                           {RollLeft, L"Roulis gauche"},
                           {RollRight, L"Roulis droit"},
                           {ResetRoll, L"Retablir la verticale Z"}})
    AppendMenuW(nav, MF_STRING, id, label);
  AppendMenuW(nav, MF_SEPARATOR, 0, nullptr);
  AppendMenuW(nav, MF_STRING, SaveHome, L"Enregistrer cette vue comme accueil");
  AppendMenuW(nav, MF_STRING, RestoreHome, L"Vue d'accueil");
  const wchar_t *names[] = {L"250 000",    L"500 000",    L"1 million",
                            L"2 millions", L"4 millions", L"8 millions"};
  for (int i = 0; i < 6; i++)
    AppendMenuW(budget, MF_STRING, BudgetBase + i, names[i]);
  const wchar_t *pixels[] = {L"0,5 px - fin", L"1 px", L"2 px - equilibre", L"4 px - rapide"};
  for (int i = 0; i < 4; i++)
    AppendMenuW(error, MF_STRING, QualityBase + i, pixels[i]);
  AppendMenuW(quality, MF_POPUP, reinterpret_cast<UINT_PTR>(budget), L"Budget de gaussiennes");
  AppendMenuW(quality, MF_POPUP, reinterpret_cast<UINT_PTR>(error), L"Erreur de niveau de detail");
  AppendMenuW(quality, MF_STRING, HaloCache, L"Precharger les alentours en RAM / VRAM");
  for (auto [id, label] : {std::pair{Vsync, L"Synchronisation verticale"},
                           {Fullscreen, L"Plein ecran\tF11"},
                           {ExposureDown, L"Exposition -0,25 EV"},
                           {ExposureUp, L"Exposition +0,25 EV"},
                           {ExposureReset, L"Exposition neutre"}})
    AppendMenuW(render, MF_STRING, id, label);
  HMENU edges = CreatePopupMenu();
  AppendMenuW(edges, MF_STRING, EdgeDown, L"Diminuer de 5 %");
  AppendMenuW(edges, MF_STRING, EdgeUp, L"Augmenter de 5 %");
  AppendMenuW(edges, MF_SEPARATOR, 0, nullptr);
  for (int i = 0; i < 8; i++) {
    auto label = std::to_wstring((i + 1) * 25) + (i == 3 ? L" % - rendu d'origine" : L" %");
    AppendMenuW(edges, MF_STRING, EdgeBase + i, label.c_str());
  }
  AppendMenuW(render, MF_POPUP, reinterpret_cast<UINT_PTR>(edges), L"Opacite des bords de splats");
  const wchar_t *degrees[] = {L"0 - valeur de base", L"1", L"2", L"3 - complet"};
  AppendMenuW(opacitySh, MF_STRING, OpacityShEnabled, L"Activer les SH d'opacite");
  AppendMenuW(opacitySh, MF_SEPARATOR, 0, nullptr);
  for (int degree = 0; degree <= 3; degree++) {
    AppendMenuW(colorSh, MF_STRING, ColorShBase + degree, degrees[degree]);
    AppendMenuW(opacitySh, MF_STRING, OpacityShBase + degree, degrees[degree]);
  }
  AppendMenuW(render, MF_SEPARATOR, 0, nullptr);
  AppendMenuW(render, MF_POPUP, reinterpret_cast<UINT_PTR>(colorSh), L"SH de couleur");
  AppendMenuW(render, MF_POPUP, reinterpret_cast<UINT_PTR>(opacitySh), L"SH d'opacite");
  AppendMenuW(bar, MF_POPUP, reinterpret_cast<UINT_PTR>(file), L"&Fichier");
  AppendMenuW(bar, MF_POPUP, reinterpret_cast<UINT_PTR>(nav), L"&Navigation");
  AppendMenuW(bar, MF_POPUP, reinterpret_cast<UINT_PTR>(quality), L"&Qualite");
  AppendMenuW(bar, MF_POPUP, reinterpret_cast<UINT_PTR>(render), L"&Affichage");
  AppendMenuW(bar, MF_STRING, Help, L"&Aide");
  return bar;
}
double percentile(std::vector<double> v, double p) {
  if (v.empty())
    return 0;
  std::sort(v.begin(), v.end());
  return v[size_t(p * (v.size() - 1))];
}
void runStreamingBenchmark(App &app, const std::filesystem::path &initial,
                           const std::filesystem::path &output,
                           const std::filesystem::path &capture, int frames,
                           const std::filesystem::path &cameraFile) {
  app.renderer->prefetchedGaussians = 0;
  auto opened = Clock::now();
  app.requestLoad(initial);
  auto pump = [&] {
    app.tick(.016f);
    bool committed = app.renderer->advanceUpload();
    if (committed) {
      app.dirty = true;
      app.lodDirty = true;
    }
    if (app.dirty) {
      app.renderer->render(app.camera, true, false);
      app.dirty = false;
    }
    return committed;
  };
  while (!app.bundle || !app.renderer->scene || app.renderer->scene->bundleId != app.bundle->id) {
    pump();
    if (Clock::now() - opened > std::chrono::seconds(60))
      throw std::runtime_error("Streaming startup timed out");
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
  if (!cameraFile.empty()) {
    std::ifstream file(cameraFile);
    if (!file)
      throw std::runtime_error("Cannot open benchmark camera");
    app.camera = decodeHomeCamera(Json::parse(file));
    app.changed();
  }
  const Camera initialCamera = app.camera;
  const double firstImageMs =
      std::chrono::duration<double, std::milli>(Clock::now() - opened).count();
  Json samples = Json::array(), commits = Json::array();
  std::vector<double> cpu;
  for (int i = 0; i < frames; i++) {
    auto begin = Clock::now();
    if (i < frames * 2 / 3) {
      app.camera = initialCamera;
      app.camera.orbit(.9f * std::sin(i * .04f), .15f * std::sin(i * .07f));
      app.camera.dolly(-1.2f * (.5f + .5f * std::sin(i * .08f)));
      app.camera.pan(.25f * initialCamera.distance() * std::sin(i * .06f), 0);
      app.changed();
    }
    bool committed = pump();
    double ms = std::chrono::duration<double, std::milli>(Clock::now() - begin).count();
    cpu.push_back(ms);
    samples.push_back({{"frame", i},
                       {"cpuMs", ms},
                       {"gpuMs", app.renderer->gpuMs},
                       {"resident", app.renderer->scene->selection.count},
                       {"loading", bool(app.loader.busy)},
                       {"uploadPending", app.renderer->uploadPending()}});
    if (committed)
      commits.push_back({{"frame", i},
                         {"uploadBytes", app.renderer->lastUploadBytes},
                         {"reused", app.renderer->lastReusedGaussians},
                         {"nodes", app.renderer->scene->selection.nodes}});
    std::this_thread::sleep_until(begin + std::chrono::milliseconds(16));
  }
  const auto drainStart = Clock::now();
  while ((app.loader.busy && !app.haloLoading) ||
         (app.renderer->uploadPending() && !app.renderer->prefetchPending()) || app.lodDirty) {
    auto begin = Clock::now();
    if (pump())
      commits.push_back({{"frame", frames},
                         {"uploadBytes", app.renderer->lastUploadBytes},
                         {"reused", app.renderer->lastReusedGaussians},
                         {"nodes", app.renderer->scene->selection.nodes}});
    if (Clock::now() - drainStart > std::chrono::seconds(30))
      throw std::runtime_error("LOD did not converge after motion stopped");
    std::this_thread::sleep_until(begin + std::chrono::milliseconds(16));
  }
  const double settleMs =
      std::chrono::duration<double, std::milli>(Clock::now() - app.lastMotion).count();
  app.cancelHalo();
  app.renderer->verifySort();
  if (!capture.empty())
    app.renderer->render(app.camera, true, false, 1, capture);
  size_t basePages = 0;
  for (auto &p : app.renderer->scene->pages)
    if (!p->fullSh)
      basePages++;
  if (basePages && (app.colorShDegree > 0 || (app.opacityShEnabled && app.opacityShDegree > 0)))
    throw std::runtime_error("Streaming finished before required SH arrived");
  if (!(app.renderer->scene->selection == app.targetSelection()))
    throw std::runtime_error("Streaming finished before the target LOD cut arrived");
  Json report = {{"schema", "droneai-streaming-benchmark-v1"},
                 {"initialCamera", cameraJson(initialCamera)},
                 {"finalCamera", cameraJson(app.camera)},
                 {"selectedNodes", app.renderer->scene->selection.nodes},
                 {"targetNodes", app.targetSelection().nodes},
                 {"adapter", app.renderer->adapterName},
                 {"bundleId", app.bundle->id},
                 {"budget", app.budget},
                 {"frames", frames},
                 {"firstImageMs", firstImageMs},
                 {"cpuMedianMs", percentile(cpu, .5)},
                 {"cpuP95Ms", percentile(cpu, .95)},
                 {"cpuMaximumMs", percentile(cpu, 1)},
                 {"remainingBasePages", basePages},
                 {"prefetchedGaussians", app.renderer->prefetchedGaussians},
                 {"cacheActivations", app.renderer->cacheActivations},
                 {"haloErrors", app.haloErrors},
                 {"haloEnabled", app.haloEnabled},
                 {"settledAfterMotionMs", settleMs},
                 {"finalResident", app.renderer->scene->selection.count},
                 {"converged", true},
                 {"gpuContractsVerified", true},
                 {"gpuStreamingContractsVerified", true},
                 {"trajectory", "orbit-pan-dolly-sin-v1; final third stationary; 16ms pacing"},
                 {"commits", commits},
                 {"samples", samples}};
  std::ofstream file(output);
  file << report.dump(2);
  if (!file)
    throw std::runtime_error("Cannot write streaming report");
}
void runCacheBenchmark(App &app, const std::filesystem::path &initial,
                       const std::filesystem::path &output,
                       const std::filesystem::path &cameraFile) {
  app.renderer->prefetchedGaussians = 0;
  auto pump = [&] {
    app.tick(.016f);
    if (app.renderer->advanceUpload())
      app.dirty = app.lodDirty = true;
    if (app.dirty) {
      app.renderer->render(app.camera, true, false);
      app.dirty = false;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(8));
  };
  auto wait = [&](const auto &ready) {
    const auto start = Clock::now();
    do {
      pump();
      if (Clock::now() - start > std::chrono::seconds(90))
        throw std::runtime_error("Cache benchmark timed out");
    } while (!ready());
  };
  app.requestLoad(initial);
  wait([&] { return app.bundle && app.renderer->scene; });
  if (!cameraFile.empty()) {
    std::ifstream file(cameraFile);
    app.camera = decodeHomeCamera(Json::parse(file));
    app.changed();
  }
  const Camera home = app.camera;
  Json phases = Json::array();
  auto foregroundReady = [&] {
    return !app.lodDirty && !(app.loader.busy && !app.haloLoading) &&
           !(app.renderer->uploadPending() && !app.renderer->prefetchPending()) &&
           app.renderer->scene->selection == app.targetSelection() &&
           std::none_of(app.renderer->scene->pages.begin(), app.renderer->scene->pages.end(),
                        [](const auto &p) { return !p->fullSh; });
  };
  for (int phase = 0; phase < 3; phase++) {
    app.camera = home;
    if (phase == 1)
      app.camera.pan(home.distance() * .35f, 0);
    app.changed();
    const auto start = Clock::now();
    const uint64_t reads = app.bundle->fileReadBytes, uploads = app.renderer->totalUploadBytes,
                   reuse = app.renderer->cacheActivations;
    wait(foregroundReady);
    app.renderer->verifySort();
    phases.push_back(
        {{"phase", phase == 0   ? "A"
                   : phase == 1 ? "B"
                                : "A-return"},
         {"readyMs", std::chrono::duration<double, std::milli>(Clock::now() - start).count()},
         {"fileReadBytes", uint64_t(app.bundle->fileReadBytes) - reads},
         {"uploadBytes", app.renderer->totalUploadBytes - uploads},
         {"reusedGaussians", app.renderer->cacheActivations - reuse},
         {"resident", app.renderer->scene->selection.count}});
    if (app.haloEnabled && phase < 2)
      wait([&] {
        return foregroundReady() && app.haloMotion == app.lastMotion &&
               app.haloCursor == app.haloNodes.size() && !app.loader.busy &&
               !app.renderer->uploadPending();
      });
  }
  app.cancelHalo();
  Json report = {{"schema", "droneai-halo-cache-benchmark-v1"},
                 {"adapter", app.renderer->adapterName},
                 {"bundleId", app.bundle->id},
                 {"initialCamera", cameraJson(home)},
                 {"budget", app.budget},
                 {"haloEnabled", app.haloEnabled},
                 {"haloErrors", app.haloErrors},
                 {"prefetchedGaussians", app.renderer->prefetchedGaussians},
                 {"cacheReserveBytes", app.renderer->cacheReserveGaussians() * sizeof(Raw)},
                 {"phases", phases},
                 {"gpuContractsVerified", true}};
  std::ofstream file(output);
  file << report.dump(2);
  if (!file)
    throw std::runtime_error("Cannot write halo cache benchmark");
}
} // namespace
int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int show) {
  SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
  HRESULT com = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
  std::filesystem::path output;
  bool benchmark = false;
  try {
    int argc{};
    auto argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    std::filesystem::path initial, capture, cameraFile;
    uint64_t budget = 2000000;
    int frames = 180;
    float edgeOpacity = 1;
    bool orbit = false, opacityShEnabled = true, streaming = false, haloEnabled = true,
         cacheCycle = false;
    uint32_t colorShDegree = 3, opacityShDegree = 3;
    auto shDegree = [](const std::wstring &value) -> uint32_t {
      if (value.size() != 1 || value[0] < L'0' || value[0] > L'3')
        throw std::runtime_error("SH degree must be 0, 1, 2 or 3");
      return uint32_t(value[0] - L'0');
    };
    for (int i = 1; i < argc; i++) {
      std::wstring arg = argv[i];
      if (arg == L"--benchmark")
        benchmark = true;
      else if (arg == L"--cache-cycle")
        cacheCycle = true;
      else if (arg == L"--no-prefetch")
        haloEnabled = false;
      else if (arg == L"--streaming")
        streaming = true;
      else if (arg == L"--orbit")
        orbit = true;
      else if (arg == L"--edge-opacity" && i + 1 < argc) {
        const std::wstring value = argv[++i];
        size_t consumed = 0;
        edgeOpacity = std::stof(value, &consumed);
        if (consumed != value.size() || !std::isfinite(edgeOpacity) || edgeOpacity < .25f ||
            edgeOpacity > 2.f)
          throw std::runtime_error("--edge-opacity requires a number between 0.25 and 2");
      } else if (arg == L"--no-opacity-sh")
        opacityShEnabled = false;
      else if (arg == L"--color-sh-degree" && i + 1 < argc)
        colorShDegree = shDegree(argv[++i]);
      else if (arg == L"--opacity-sh-degree" && i + 1 < argc)
        opacityShDegree = shDegree(argv[++i]);
      else if (arg == L"--output" && i + 1 < argc)
        output = argv[++i];
      else if (arg == L"--camera" && i + 1 < argc)
        cameraFile = argv[++i];
      else if (arg == L"--screenshot" && i + 1 < argc)
        capture = argv[++i];
      else if (arg == L"--budget" && i + 1 < argc)
        budget = std::stoull(argv[++i]);
      else if (arg == L"--frames" && i + 1 < argc)
        frames = std::stoi(argv[++i]);
      else if (arg.starts_with(L"--"))
        throw std::runtime_error("Unknown or incomplete argument: " + utf8(arg));
      else
        initial = arg;
    }
    LocalFree(argv);
    if (budget < 16384 || budget > 8000000 || frames < 1 || frames > 100000)
      throw std::runtime_error("Invalid budget or frame count");
    App app;
    app.budget = budget;
    app.haloEnabled = haloEnabled;
    app.edgeOpacity = edgeOpacity;
    app.colorShDegree = colorShDegree;
    app.opacityShDegree = opacityShDegree;
    app.opacityShEnabled = opacityShEnabled;
    WNDCLASSEXW wc{sizeof(wc)};
    wc.style = CS_DBLCLKS;
    wc.lpfnWndProc = windowProc;
    wc.hInstance = instance;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.hIcon = LoadIcon(nullptr, IDI_APPLICATION);
    wc.lpszClassName = L"DroneAI.GSTile.Native";
    if (!RegisterClassExW(&wc))
      throw std::runtime_error("Window class registration failed");
    RECT rect{0, 0, 1440, 900};
    AdjustWindowRectEx(&rect, WS_OVERLAPPEDWINDOW, TRUE, 0);
    app.window =
        CreateWindowExW(0, wc.lpszClassName, L"GSTile Native - initialisation GPU",
                        WS_OVERLAPPEDWINDOW, CW_USEDEFAULT, CW_USEDEFAULT, rect.right - rect.left,
                        rect.bottom - rect.top, nullptr, menus(), instance, &app);
    if (!app.window)
      throw std::runtime_error("Cannot create viewer window");
    app.renderer = std::make_unique<Renderer>(app.window, app.width, app.height, benchmark);
    app.renderer->setShOptions(colorShDegree, opacityShDegree, opacityShEnabled);
    app.renderer->setEdgeOpacity(edgeOpacity);
    app.title();
    if (benchmark) {
      if (initial.empty() || output.empty())
        throw std::runtime_error("--benchmark requires a bundle path and --output report.json");
      app.renderer->verifyGpuContracts();
      if (cacheCycle) {
        runCacheBenchmark(app, initial, output, cameraFile);
      } else if (streaming) {
        runStreamingBenchmark(app, initial, output, capture, frames, cameraFile);
      } else {
        auto start = Clock::now();
        auto bundle = std::make_shared<Bundle>(initial);
        Camera cam;
        cam.fit(bundle->nodes[bundle->root].bounds, float(app.width) / app.height);
        if (!cameraFile.empty()) {
          std::ifstream file(cameraFile);
          if (!file)
            throw std::runtime_error("Cannot open benchmark camera");
          cam = decodeHomeCamera(Json::parse(file));
        }
        Camera initialCamera = cam;
        auto cut = bundle->select(cam, app.width, app.height, budget, 2.f);
        std::atomic_bool cancel{};
        auto scene = bundle->load(cut, cancel);
        app.renderer->upload(scene);
        double startup = std::chrono::duration<double, std::milli>(Clock::now() - start).count();
        Json samples = Json::array();
        std::vector<double> gpu, cpu;
        uint64_t lastSample = 0;
        for (int frame = 0; frame < frames + 32; frame++) {
          if (orbit)
            cam.orbit(.0025f, 0);
          auto begin = Clock::now();
          app.renderer->render(cam, true, false, 1,
                               frame == frames + 31 ? capture : std::filesystem::path{});
          double cpuMs = std::chrono::duration<double, std::milli>(Clock::now() - begin).count();
          if (frame >= 32) {
            cpu.push_back(cpuMs);
            if (app.renderer->gpuSamples != lastSample) {
              gpu.push_back(app.renderer->gpuMs);
              samples.push_back(
                  {{"frame", frame - 32}, {"gpuMs", app.renderer->gpuMs}, {"cpuFrameMs", cpuMs}});
            }
          }
          lastSample = app.renderer->gpuSamples;
        }
        app.renderer->verifySort();
        auto pivot = app.renderer->pick(app.width / 2, app.height / 2);
        auto v = [](Vec3 p) { return Json::array({p.x, p.y, p.z}); };
        Json report = {{"schema", "droneai-native-viewer-benchmark-v1"},
                       {"adapter", app.renderer->adapterName},
                       {"vramBytes", app.renderer->adapterMemory},
                       {"bundleId", bundle->id},
                       {"bundlePath", utf8(initial.wstring())},
                       {"sourceGaussians", bundle->sourceCount},
                       {"residentGaussians", cut.count},
                       {"selectedNodes", cut.nodes},
                       {"budget", budget},
                       {"budgetLimited", cut.limited},
                       {"maximumSelectedErrorPixels", cut.maxError},
                       {"width", app.width},
                       {"height", app.height},
                       {"fovRadians", cam.fov},
                       {"cameraEye", v(cam.eye)},
                       {"cameraPivot", v(cam.pivot)},
                       {"cameraUp", v(cam.up)},
                       {"initialCameraEye", v(initialCamera.eye)},
                       {"initialCameraPivot", v(initialCamera.pivot)},
                       {"initialCameraUp", v(initialCamera.up)},
                       {"trajectory", orbit ? "orbit-0.0025-radians-per-frame"
                                            : "stationary-reproject-sort-every-frame"},
                       {"warmupFrames", 32},
                       {"frames", frames},
                       {"startupMs", startup},
                       {"loadMs", scene->loadMs},
                       {"gpuMedianMs", percentile(gpu, .5)},
                       {"gpuP95Ms", percentile(gpu, .95)},
                       {"cpuFrameMedianMs", percentile(cpu, .5)},
                       {"gpuSortVerified", true},
                       {"gpuContractsVerified", true},
                       {"gpuShControlCasesVerified", 20},
                       {"edgeOpacity", edgeOpacity},
                       {"colorShDegree", colorShDegree},
                       {"opacityShDegree", opacityShDegree},
                       {"opacityShEnabled", opacityShEnabled},
                       {"centerPickHit", pivot.has_value()},
                       {"samples", samples}};
        std::ofstream file(output);
        file << report.dump(2);
        if (!file)
          throw std::runtime_error("Cannot write benchmark report");
      }
    } else {
      ShowWindow(app.window, show);
      UpdateWindow(app.window);
      if (!initial.empty())
        app.requestLoad(initial);
      else
        PostMessage(app.window, WM_COMMAND, Open, 0);
      auto last = Clock::now(), title = last;
      while (app.running) {
        MSG message;
        while (PeekMessage(&message, nullptr, 0, 0, PM_REMOVE)) {
          if (message.message == WM_QUIT) {
            app.running = false;
            break;
          }
          TranslateMessage(&message);
          DispatchMessage(&message);
        }
        if (!app.running)
          break;
        auto now = Clock::now();
        float dt = std::chrono::duration<float>(now - last).count();
        last = now;
        app.tick(dt);
        if (!app.minimized && app.renderer->uploadPending()) {
          if (app.renderer->advanceUpload()) {
            app.dirty = true;
            app.lodDirty = true;
          }
        }
        if (!app.minimized && app.dirty) {
          app.renderer->render(app.camera, true, app.vsync, app.exposure);
          app.dirty = false;
        }
        if (now - title > std::chrono::milliseconds(300)) {
          app.title();
          title = now;
        }
        MsgWaitForMultipleObjectsEx(0, nullptr, app.button ? 1 : 8, QS_ALLINPUT,
                                    MWMO_INPUTAVAILABLE);
      }
    }
    app.renderer.reset();
    if (IsWindow(app.window))
      DestroyWindow(app.window);
    if (SUCCEEDED(com))
      CoUninitialize();
    return 0;
  } catch (const std::exception &e) {
    if (benchmark && !output.empty()) {
      std::ofstream file(output);
      file << Json({{"error", e.what()}}).dump(2);
    } else
      MessageBoxW(nullptr, wide(e.what()).c_str(), L"GSTile Viewer - erreur", MB_OK | MB_ICONERROR);
    if (SUCCEEDED(com))
      CoUninitialize();
    return 1;
  }
}
