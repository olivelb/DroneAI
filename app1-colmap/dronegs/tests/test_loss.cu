// SPDX-License-Identifier: MIT
#include <cuda_runtime.h>

#include <array>
#include <cmath>
#include <cstddef>
#include <iostream>
#include <stdexcept>
#include <string>

#include "dronegs/loss.hpp"

namespace {

void require_cuda(cudaError_t status, const char* operation) {
    if (status != cudaSuccess) {
        throw std::runtime_error(std::string(operation) + ": " + cudaGetErrorString(status));
    }
}

float cpu_loss(const std::array<float, 4>& prediction,
               const std::array<float, 4>& target) {
    float loss = 0.0F;
    for (std::size_t index = 0; index < prediction.size(); ++index) {
        loss += std::abs(prediction[index] - target[index]);
    }
    return loss / static_cast<float>(prediction.size());
}

void check_close(float actual, float expected, float tolerance, const char* label) {
    if (std::abs(actual - expected) > tolerance) {
        throw std::runtime_error(std::string(label) + " mismatch");
    }
}

}  // namespace

int main() {
    constexpr std::array<float, 4> prediction{0.2F, 0.7F, 0.4F, 0.9F};
    constexpr std::array<float, 4> target{0.1F, 0.9F, 0.3F, 0.5F};
    float* device_prediction = nullptr;
    float* device_target = nullptr;
    float* device_gradient = nullptr;
    try {
        require_cuda(cudaMalloc(reinterpret_cast<void**>(&device_prediction),
                                sizeof(prediction)), "allocate prediction");
        require_cuda(cudaMalloc(reinterpret_cast<void**>(&device_target),
                                sizeof(target)), "allocate target");
        require_cuda(cudaMalloc(reinterpret_cast<void**>(&device_gradient),
                                sizeof(prediction)), "allocate gradient");
        require_cuda(cudaMemcpy(device_prediction, prediction.data(), sizeof(prediction),
                                cudaMemcpyHostToDevice), "copy prediction");
        require_cuda(cudaMemcpy(device_target, target.data(), sizeof(target),
                                cudaMemcpyHostToDevice), "copy target");

        const float loss = dronegs::color_l1_loss_gradient(
            device_prediction, device_target, device_gradient, prediction.size());
        std::array<float, 4> gradient{};
        require_cuda(cudaMemcpy(gradient.data(), device_gradient, sizeof(gradient),
                                cudaMemcpyDeviceToHost), "copy gradient");
        check_close(loss, cpu_loss(prediction, target), 1.0e-6F, "L1 loss");

        constexpr float epsilon = 1.0e-3F;
        for (std::size_t index = 0; index < prediction.size(); ++index) {
            auto plus = prediction;
            auto minus = prediction;
            plus[index] += epsilon;
            minus[index] -= epsilon;
            const float finite_difference =
                (cpu_loss(plus, target) - cpu_loss(minus, target)) / (2.0F * epsilon);
            check_close(gradient[index], finite_difference, 2.0e-4F,
                        "finite-difference gradient");
        }

        bool empty_rejected = false;
        try {
            static_cast<void>(dronegs::color_l1_loss_gradient(
                device_prediction, device_target, device_gradient, 0));
        } catch (const std::invalid_argument&) {
            empty_rejected = true;
        }
        if (!empty_rejected) {
            throw std::runtime_error("zero-length loss was accepted");
        }

        cudaFree(device_gradient);
        cudaFree(device_target);
        cudaFree(device_prediction);
        std::cout << "DroneGS CUDA gradient test passed\n";
        return 0;
    } catch (const std::exception& error) {
        cudaFree(device_gradient);
        cudaFree(device_target);
        cudaFree(device_prediction);
        std::cerr << "DroneGS CUDA test failed: " << error.what() << "\n";
        return 1;
    }
}
