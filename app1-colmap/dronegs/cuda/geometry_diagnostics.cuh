// SPDX-License-Identifier: MIT
// Included inside rasterization.cu's anonymous namespace. Diagnostic-only data;
// the normal training specialization has no metadata allocation or accumulation.
struct DeviceGeometryRecord {
    float variance_z = 0.0F;
    float minimum_variance = 0.0F;
    float normal[3]{};
    float planarity = 0.0F;
    float plane_distance = 0.0F;
};

__global__ void build_geometry_records_kernel(
    const Gaussian* gaussians, const std::uint64_t* sorted_keys,
    std::uint32_t count, DeviceRasterCamera camera,
    DeviceGeometryRecord* output) {
    const auto i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= count) return;
    DeviceGeometryRecord result{};
    if (sorted_keys[i] == std::numeric_limits<std::uint64_t>::max()) {
        output[i] = result;
        return;
    }
    const auto& g = gaussians[static_cast<std::uint32_t>(sorted_keys[i])];
    float s[3]{};
    if (!load_gaussian_scale_device(g, s)) { output[i] = result; return; }
    const float qnorm = sqrtf(g.rotation[0]*g.rotation[0] +
        g.rotation[1]*g.rotation[1] + g.rotation[2]*g.rotation[2] +
        g.rotation[3]*g.rotation[3]);
    const float w=g.rotation[0]/qnorm, x=g.rotation[1]/qnorm;
    const float y=g.rotation[2]/qnorm, z=g.rotation[3]/qnorm;
    const float r[9]{1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w),
        2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w),
        2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)};
    float cr[9]{};
    for (int a=0;a<3;++a) for (int b=0;b<3;++b)
        for (int k=0;k<3;++k) cr[a*3+b]+=camera.rotation[a*3+k]*r[k*3+b];
    int smallest=0;
    for (int a=1;a<3;++a) if (s[a]<s[smallest]) smallest=a;
    const float middle=fminf(s[(smallest+1)%3],s[(smallest+2)%3]);
    result.minimum_variance=s[smallest]*s[smallest];
    // Continuous eigenvalue-gap support: spheres and line-like splats supply
    // no unique normal. No scene-specific flatness threshold is used.
    result.planarity=fmaxf(0.0F,1.0F-result.minimum_variance/(middle*middle));
    float center[3]{};
    for (int a=0;a<3;++a) {
        center[a]=camera.translation[a];
        for (int k=0;k<3;++k) center[a]+=camera.rotation[a*3+k]*g.xyz[k];
        result.normal[a]=cr[a*3+smallest];
        result.plane_distance+=result.normal[a]*center[a];
        result.variance_z+=cr[6+a]*cr[6+a]*s[a]*s[a];
    }
    if (result.plane_distance>0.0F) {
        result.plane_distance=-result.plane_distance;
        for (int a=0;a<3;++a) result.normal[a]=-result.normal[a];
    }
    output[i]=result;
}

struct GeometryMoments {
    double mass=0, mean=0, m2=0, covariance=0, minimum=0;
    double plane_mass=0, plane_mean=0, plane_m2=0;
    double normal[3]{};
    __device__ void add(float weight, float depth, const DeviceGeometryRecord& g,
                        float ray_x, float ray_y) {
        const double next=mass+weight;
        const double delta=static_cast<double>(depth)-mean;
        mean+=weight*delta/next;
        m2+=weight*delta*(static_cast<double>(depth)-mean);
        mass=next;
        covariance+=weight*static_cast<double>(g.variance_z);
        minimum+=weight*static_cast<double>(g.minimum_variance);
        const double supported=weight*static_cast<double>(g.planarity);
        for (int a=0;a<3;++a) normal[a]+=supported*g.normal[a];
        const float denominator=g.normal[0]*ray_x+g.normal[1]*ray_y+g.normal[2];
        // Angular numerical guard only; grazing views retain low support.
        const float ray_norm=sqrtf(ray_x*ray_x+ray_y*ray_y+1.0F);
        if (supported>0 && fabsf(denominator)>1.0e-4F*ray_norm) {
            const float plane=g.plane_distance/denominator;
            if (isfinite(plane) && plane>minimum_depth) {
                const double next_plane=plane_mass+supported;
                const double d=static_cast<double>(plane)-plane_mean;
                plane_mean+=supported*d/next_plane;
                plane_m2+=supported*d*(static_cast<double>(plane)-plane_mean);
                plane_mass=next_plane;
            }
        }
    }
    __device__ void write(float* out, float coverage) const {
        for (int a=0;a<12;++a) out[a]=0;
        out[0]=coverage;
        if (mass<=0) return;
        out[1]=static_cast<float>(mean);
        out[2]=static_cast<float>(sqrt(fmax(0.0,m2/mass)));
        out[3]=static_cast<float>(sqrt(fmax(0.0,covariance/mass)));
        out[4]=static_cast<float>(sqrt(fmax(0.0,minimum/mass)));
        const double norm=sqrt(normal[0]*normal[0]+normal[1]*normal[1]+normal[2]*normal[2]);
        if (norm>0) for (int a=0;a<3;++a) out[5+a]=static_cast<float>(normal[a]/norm);
        out[8]=static_cast<float>(norm/mass);
        if (plane_mass>0) {
            out[9]=static_cast<float>(plane_mean);
            out[10]=static_cast<float>(sqrt(fmax(0.0,plane_m2/plane_mass)));
            out[11]=static_cast<float>(plane_mass);
        }
    }
};
