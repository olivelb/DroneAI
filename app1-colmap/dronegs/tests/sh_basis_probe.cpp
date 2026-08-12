// SPDX-License-Identifier: MIT
#include <array>
#include <charconv>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string_view>

#include "dronegs/sh.hpp"

namespace {

float parse_float(std::string_view value) {
    float result = 0.0F;
    const auto parsed = std::from_chars(
        value.data(), value.data() + value.size(), result);
    if (parsed.ec != std::errc{} || parsed.ptr != value.data() + value.size()) {
        throw std::invalid_argument("invalid direction component");
    }
    return result;
}

std::uint32_t parse_degree(std::string_view value) {
    std::uint32_t result = 0U;
    const auto parsed = std::from_chars(
        value.data(), value.data() + value.size(), result);
    if (parsed.ec != std::errc{} || parsed.ptr != value.data() + value.size()) {
        throw std::invalid_argument("invalid SH degree");
    }
    return result;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc != 5) {
            throw std::invalid_argument("usage: probe DEGREE X Y Z");
        }
        const auto degree = parse_degree(argv[1]);
        const auto basis = dronegs::evaluate_sh_basis(
            {parse_float(argv[2]), parse_float(argv[3]), parse_float(argv[4])},
            degree);
        const auto count = (degree + 1U) * (degree + 1U);
        std::cout << std::setprecision(9);
        for (std::uint32_t index = 0U; index < count; ++index) {
            if (index != 0U) {
                std::cout << ',';
            }
            std::cout << basis[index];
        }
        std::cout << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
