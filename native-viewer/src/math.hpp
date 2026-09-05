#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
namespace gs {
struct Vec3 {
  float x{}, y{}, z{};
  float &operator[](int i) { return (&x)[i]; }
  float operator[](int i) const { return (&x)[i]; }
  Vec3 operator+(Vec3 b) const { return {x + b.x, y + b.y, z + b.z}; }
  Vec3 operator-(Vec3 b) const { return {x - b.x, y - b.y, z - b.z}; }
  Vec3 operator*(float s) const { return {x * s, y * s, z * s}; }
};
inline float dot(Vec3 a, Vec3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
inline Vec3 cross(Vec3 a, Vec3 b) {
  return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x};
}
inline float length(Vec3 a) { return std::sqrt(dot(a, a)); }
inline Vec3 normalized(Vec3 a) { return a * (1 / std::max(length(a), 1e-20f)); }
inline Vec3 rotate(Vec3 v, Vec3 axis, float angle) {
  axis = normalized(axis);
  return v * std::cos(angle) + cross(axis, v) * std::sin(angle) +
         axis * (dot(axis, v) * (1 - std::cos(angle)));
}
struct Bounds {
  Vec3 lo, hi;
  Vec3 center() const { return (lo + hi) * .5f; }
  float radius() const { return length(hi - lo) * .5f; }
  float distance(Vec3 p) const {
    Vec3 d;
    for (int i = 0; i < 3; i++)
      d[i] = std::max({lo[i] - p[i], 0.f, p[i] - hi[i]});
    return std::max(length(d), 1e-5f);
  }
};
struct Camera {
  Vec3 eye{0, -5, 3}, pivot{}, up{0, 0, 1};
  float fov = 1.04719755f;
  Vec3 forward() const { return normalized(pivot - eye); }
  Vec3 right() const { return normalized(cross(forward(), up)); }
  Vec3 vertical() const { return normalized(cross(right(), forward())); }
  float distance() const { return std::max(length(pivot - eye), 1e-5f); }
  void fit(Bounds b, float aspect) {
    pivot = b.center();
    float r = std::max(b.radius(), .001f);
    float angle = std::atan(std::tan(fov * .5f) * std::min(aspect, 1.f));
    eye = pivot + normalized(Vec3{0, -1, .45f}) * (r / std::sin(angle) * 1.12f);
    up = {0, 0, 1};
  }
  void orbit(float x, float y) {
    Vec3 offset = eye - pivot, verticalAxis = vertical(), horizontal = right();
    offset = rotate(offset, verticalAxis, -x);
    horizontal = rotate(horizontal, verticalAxis, -x);
    up = rotate(up, verticalAxis, -x);
    offset = rotate(offset, horizontal, -y);
    up = normalized(rotate(up, horizontal, -y));
    eye = pivot + offset;
  }
  void look(float x, float y) {
    Vec3 oldEye = eye;
    orbit(x, y);
    Vec3 shift = oldEye - eye;
    eye = oldEye;
    pivot = pivot + shift;
  }
  void pan(float x, float y) {
    Vec3 d = right() * x + vertical() * y;
    eye = eye + d;
    pivot = pivot + d;
  }
  void dolly(float amount) {
    float d = std::clamp(distance() * std::exp(amount), 1e-5f, 1e10f);
    eye = pivot - forward() * d;
  }
  void roll(float radians) { up = normalized(rotate(up, forward(), radians)); }
  void move(Vec3 d) {
    eye = eye + d;
    pivot = pivot + d;
  }
};
} // namespace gs
