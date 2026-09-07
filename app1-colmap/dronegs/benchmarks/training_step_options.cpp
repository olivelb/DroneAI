// SPDX-License-Identifier: MIT
#include "dronegs/training_benchmark.hpp"
#include <charconv>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_set>

namespace dronegs {
TrainingStepBenchmarkOptions parse_training_step_benchmark_options(int& argc, char** argv) {
    TrainingStepBenchmarkOptions result;
    std::unordered_set<std::string_view> seen;
    int kept = 1;
    if ((argc - 1) % 2 != 0) throw std::invalid_argument("options require name/value pairs");
    for (int i = 1; i < argc; i += 2) {
        const std::string_view key(argv[i]);
        std::uint32_t* destination = key == "--benchmark-warmups" ? &result.warmups :
            key == "--benchmark-repeats" ? &result.repeats :
            key == "--benchmark-views" ? &result.views : nullptr;
        if (!destination) {
            argv[kept++] = argv[i];
            argv[kept++] = argv[i + 1];
            continue;
        }
        const std::string_view value(argv[i + 1]);
        const auto [end, error] = std::from_chars(value.data(), value.data() + value.size(), *destination);
        if (!seen.insert(key).second || error != std::errc{} ||
            end != value.data() + value.size() || *destination > 100U ||
            (*destination == 0U && key != "--benchmark-warmups")) {
            throw std::invalid_argument(std::string(key) + " must be unique and between 1 and 100 (warmups may be zero)");
        }
    }
    argc = kept;
    return result;
}
} // namespace dronegs
