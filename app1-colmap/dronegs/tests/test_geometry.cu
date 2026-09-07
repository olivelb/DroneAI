// SPDX-License-Identifier: MIT
#include <cmath>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include "dronegs/geometry_diagnostics.hpp"
#include "dronegs/ordered_training.hpp"

void near(float actual,float expected,float tolerance,const char* label) {
    if (!std::isfinite(actual) || std::abs(actual-expected)>tolerance)
        throw std::runtime_error(std::string(label)+": "+std::to_string(actual)+" != "+std::to_string(expected));
}
dronegs::Gaussian gaussian(float depth,float sx,float sy,float sz,float opacity) {
    dronegs::Gaussian g; g.xyz={0,0,depth};
    g.log_scale={std::log(sx),std::log(sy),std::log(sz)};
    g.opacity_logit=std::log(opacity/(1-opacity)); return g;
}
int main() {
    try {
        using namespace dronegs;
        RasterCamera c{.fx=30,.fy=30,.cx=16.5F,.cy=16.5F,.width=33,.height=33};
        const auto p=(16U*33U+16U)*geometry_channel_count;
        const GeometryRenderOptions reference{.fastgs=false,.opacity_sh=false,.sh_degree=0};
        auto front=gaussian(4,0.7F,0.7F,0.01F,0.5F);
        auto back=gaussian(6,1.05F,1.05F,0.01F,0.5F);
        const std::vector<Gaussian> layers{front,back};
        const auto frozen=layers;
        auto d=render_geometry_tiled_cuda(layers,c,reference);
        if (std::memcmp(layers.data(),frozen.data(),layers.size()*sizeof(Gaussian))!=0)
            throw std::runtime_error("diagnostic changed input model");
        near(d.channels[p],0.75F,1e-6F,"coverage");
        near(d.channels[p+1],14.0F/3,2e-6F,"between-layer mean");
        near(d.channels[p+2],std::sqrt(8.0F/9),2e-6F,"layer dispersion");
        near(d.channels[p+3],0.01F,1e-6F,"covariance depth sigma");
        near(d.channels[p+4],0.01F,1e-6F,"minimum axis sigma");
        near(d.channels[p+7],-1,1e-6F,"camera facing normal");
        if (d.channels[p+10]<0.9F) throw std::runtime_error("plane depth hid separated layers");
        const auto rgb=render_alpha_tiled_cuda(layers,c,{0,0,0},0);
        for (std::size_t i=0;i<rgb.rgb.size();++i) near(d.appearance.rgb[i],rgb.rgb[i],1e-7F,"reference RGB parity");
        for (std::size_t i=0;i<rgb.transmittance.size();++i) near(d.channels[i*12],1-rgb.transmittance[i],1e-7F,"coverage parity");

        auto sphere=render_geometry_tiled_cuda({gaussian(4,0.7F,0.7F,0.7F,0.9F)},c,reference);
        near(sphere.channels[p+8],0,1e-7F,"sphere normal unsupported");
        near(sphere.channels[p+11],0,1e-7F,"sphere plane unsupported");
        auto empty=render_geometry_tiled_cuda({},c,reference);
        for (auto v:empty.channels) near(v,0,0,"empty geometry");
        auto behind=render_geometry_tiled_cuda({gaussian(-4,1,1,0.01F,0.9F)},c,reference);
        for (auto v:behind.channels) near(v,0,0,"behind camera");

        // Known inclined plane: its ray intersections vary across pixels even
        // though the only primitive has a constant center Z.
        auto inclined=front;
        const float angle=0.6F;
        inclined.rotation={std::cos(angle/2),0,std::sin(angle/2),0};
        auto plane=render_geometry_tiled_cuda({inclined},c,reference);
        for (unsigned x=11;x<=21;++x) {
            const auto q=(16U*33U+x)*12U;
            const float expected=4*std::cos(angle)/(std::sin(angle)*(static_cast<float>(x)-16)/30+std::cos(angle));
            near(plane.channels[q+9],expected,3e-6F,"inclined plane ray intersection");
        }
        // Unit/scale equivariance; screen-space footprint and support unchanged.
        auto scaled=inclined;
        for (auto& x:scaled.xyz) x*=10;
        for (auto& s:scaled.log_scale) s+=std::log(10.0F);
        auto scale_result=render_geometry_tiled_cuda({scaled},c,reference);
        for (std::size_t i=0;i<plane.channels.size();i+=12) {
            near(scale_result.channels[i],plane.channels[i],2e-6F,"scale coverage");
            for (auto channel:{1U,2U,3U,4U,9U,10U})
                near(scale_result.channels[i+channel]/10,plane.channels[i+channel],5e-6F,"scale geometry");
        }
        // Rotate model and camera together: camera-frame outputs must agree.
        auto rotated=front; rotated.xyz={4,0,0};
        rotated.rotation={std::sqrt(0.5F),0,std::sqrt(0.5F),0};
        auto rotated_camera=c; rotated_camera.rotation={0,0,-1,0,1,0,1,0,0};
        auto baseline=render_geometry_tiled_cuda({front},c,reference);
        auto invariant=render_geometry_tiled_cuda({rotated},rotated_camera,reference);
        for (std::size_t i=0;i<baseline.channels.size();++i)
            near(invariant.channels[i],baseline.channels[i],5e-6F,"rotation equivariance");

        // The FastGS diagnostic uses the actual persistent training renderer's
        // footprint/alpha convention, not just the standalone reference profile.
        std::vector<std::uint8_t> target(33U*33U*3U,0);
        OrderedAlphaTrainingContext context(layers,33U*33U,10,2,
            MrnfOptimizerProfile::reference_absolute,0,1000,0,true);
        std::vector<float> prediction;
        context.evaluate_quality(c,target.data(),target.size(),&prediction);
        auto fast=render_geometry_tiled_cuda(layers,c,{.fastgs=true,.opacity_sh=false,.sh_degree=0});
        for (std::size_t i=0;i<prediction.size();++i) near(fast.appearance.rgb[i],prediction[i],2e-6F,"FastGS training RGB parity");
        std::cout << "geometry CUDA: analytic layers, covariance, inclined plane, degenerate normals, empty views, scale/rotation and renderer parity passed\n";
        return 0;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
