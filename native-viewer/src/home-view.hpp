#pragma once
#include "bundle.hpp"
#include <fstream>
#include <windows.h>
namespace gs {
inline std::filesystem::path homeViewPath(const std::string &bundleId) {
  if (bundleId.size() != 71 || !bundleId.starts_with("sha256:") ||
      bundleId.substr(7).find_first_not_of("0123456789abcdef") != std::string::npos)
    throw std::runtime_error("Invalid home view identity");
  wchar_t buffer[32768];
  DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", buffer, 32768);
  if (!n || n >= 32768)
    throw std::runtime_error("LocalAppData is unavailable");
  return std::filesystem::path(buffer) / L"DroneAI" / L"GSTileViewer" / L"views" /
         (bundleId.substr(7) + ".json");
}
inline Json cameraJson(const Camera &c) {
  auto v = [](Vec3 p) { return Json::array({p.x, p.y, p.z}); };
  return {
      {"version", 1}, {"eye", v(c.eye)}, {"pivot", v(c.pivot)}, {"up", v(c.up)}, {"fov", c.fov}};
}
inline Camera decodeHomeCamera(const Json &j) {
  if (j.at("version") != 1)
    throw std::runtime_error("Unsupported home view");
  auto vec = [&](const char *name) {
    const auto &a = j.at(name);
    if (!a.is_array() || a.size() != 3)
      throw std::runtime_error("Invalid home view vector");
    Vec3 p;
    for (int i = 0; i < 3; i++) {
      p[i] = a[i].get<float>();
      if (!std::isfinite(p[i]))
        throw std::runtime_error("Nonfinite home view");
    }
    return p;
  };
  Camera c;
  c.eye = vec("eye");
  c.pivot = vec("pivot");
  c.up = vec("up");
  c.fov = j.at("fov");
  if (!std::isfinite(c.fov) || c.fov < .174532f || c.fov > 2.094396f ||
      length(c.eye - c.pivot) < 1e-5 || length(c.eye - c.pivot) > 1e10 || length(c.up) < 1e-5 ||
      length(cross(c.forward(), c.up)) < 1e-5)
    throw std::runtime_error("Invalid home view camera");
  c.up = normalized(c.up);
  return c;
}
inline bool restoreHomeCamera(const std::string &id, Camera &camera) {
  auto path = homeViewPath(id);
  if (!std::filesystem::exists(path))
    return false;
  if (std::filesystem::file_size(path) > 65536)
    throw std::runtime_error("Home view too large");
  std::ifstream input(path);
  camera = decodeHomeCamera(Json::parse(input));
  return true;
}
inline void saveHomeCamera(const std::string &id, const Camera &camera) {
  auto j = cameraJson(camera);
  decodeHomeCamera(j);
  auto path = homeViewPath(id);
  std::filesystem::create_directories(path.parent_path());
  auto temporary = path;
  temporary += L".tmp";
  {
    std::ofstream out(temporary, std::ios::binary);
    out << j.dump(2);
    if (!out)
      throw std::runtime_error("Cannot save home view");
  }
  if (!MoveFileExW(temporary.c_str(), path.c_str(),
                   MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
    throw std::runtime_error("Cannot publish home view");
}
} // namespace gs
