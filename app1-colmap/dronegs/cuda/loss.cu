// SPDX-License-Identifier: MIT
#include "dronegs/loss.hpp"

#include <cuda_runtime.h>

#include <cstddef>
#include <stdexcept>
#include <string>

namespace dronegs {
namespace {

void require_cuda(cudaError_t status, const char* operation) {
    if (status != cudaSuccess) {
        throw std::runtime_error(std::string(operation) + ": " + cudaGetErrorString(status));
    }
}

__global__ void color_l1_kernel(const float* prediction, const float* target,
                                float* gradient, float* loss, std::size_t count) {
    const std::size_t index = blockIdx.x * blockDim.x + threadIdx.x;
    if (index >= count) {
        return;
    }
    const float difference = prediction[index] - target[index];
    const float normalizer = 1.0F / static_cast<float>(count);
    gradient[index] = (difference > 0.0F ? 1.0F : (difference < 0.0F ? -1.0F : 0.0F)) *
                      normalizer;
    atomicAdd(loss, fabsf(difference) * normalizer);
}

}  // namespace

float color_l1_loss_gradient(const float* device_prediction,
                             const float* device_target,
                             float* device_gradient,
                             std::size_t count) {
    if (count == 0) {
        throw std::invalid_argument("L1 loss requires at least one value");
    }
    float* device_loss = nullptr;
    require_cuda(cudaMalloc(&device_loss, sizeof(float)), "cudaMalloc loss");
    try {
        require_cuda(cudaMemset(device_loss, 0, sizeof(float)), "cudaMemset loss");
        constexpr std::size_t block_size = 256;
        const auto block_count = static_cast<unsigned int>((count + block_size - 1) / block_size);
        color_l1_kernel<<<block_count, block_size>>>(
            device_prediction, device_target, device_gradient, device_loss, count);
        require_cuda(cudaGetLastError(), "launch color_l1_kernel");
        require_cuda(cudaDeviceSynchronize(), "synchronize color_l1_kernel");
        float host_loss = 0.0F;
        require_cuda(cudaMemcpy(&host_loss, device_loss, sizeof(float), cudaMemcpyDeviceToHost),
                     "copy L1 loss");
        require_cuda(cudaFree(device_loss), "cudaFree loss");
        return host_loss;
    } catch (...) {
        cudaFree(device_loss);
        throw;
    }
}

}  // namespace dronegs
