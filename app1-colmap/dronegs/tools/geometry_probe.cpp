// SPDX-License-Identifier: MIT
#include <bit>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include "dronegs/geometry_diagnostics.hpp"
#include "dronegs/ply.hpp"
#include "dronegs/colmap.hpp"

namespace {
struct View { std::uint32_t id; dronegs::RasterCamera camera; };
void export_colmap(const std::filesystem::path& data,const std::filesystem::path& output) {
    if (std::filesystem::exists(output)) throw std::invalid_argument("camera export must not exist");
    const auto scene=dronegs::load_colmap_camera_scene(data);
    std::ostringstream rows; rows << std::setprecision(17);
    for (const auto& image:scene.images) {
        const dronegs::Camera* camera=nullptr;
        for (const auto& candidate:scene.cameras) if (candidate.id==image.camera_id) camera=&candidate;
        if (camera==nullptr || (camera->model_id!=0 && camera->model_id!=1))
            throw std::invalid_argument("use the undistorted PINHOLE/SIMPLE_PINHOLE COLMAP model");
        const auto& p=camera->parameters;
        if (p.size()!=(camera->model_id==0?3U:4U)) throw std::invalid_argument("invalid COLMAP calibration");
        const double fx=p[0],fy=p[camera->model_id==0?0:1];
        const double cx=p[camera->model_id==0?1:2],cy=p[camera->model_id==0?2:3];
        const auto& q=image.qvec;
        const double norm=std::sqrt(q[0]*q[0]+q[1]*q[1]+q[2]*q[2]+q[3]*q[3]);
        if (!std::isfinite(norm) || norm<=1e-12) throw std::invalid_argument("invalid COLMAP quaternion");
        const double w=q[0]/norm,x=q[1]/norm,y=q[2]/norm,z=q[3]/norm;
        const double r[9]{1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w),
            2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w),
            2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)};
        rows << image.id << ' ' << camera->width << ' ' << camera->height
             << ' ' << fx << ' ' << fy << ' ' << cx << ' ' << cy;
        for (auto v:r) rows << ' ' << v;
        for (auto v:image.tvec) rows << ' ' << v;
        rows << '\n';
    }
    std::ofstream stream(output); stream << rows.str();
    if (!stream) throw std::runtime_error("failed writing COLMAP camera export");
}
std::vector<View> read_views(const std::filesystem::path& path) {
    std::ifstream input(path);
    if (!input) throw std::runtime_error("cannot read camera manifest");
    std::vector<View> views;
    std::set<std::uint32_t> ids;
    std::string line;
    while (std::getline(input,line)) {
        std::istringstream row(line);
        View v{}; auto& c=v.camera;
        row >> v.id >> c.width >> c.height >> c.fx >> c.fy >> c.cx >> c.cy;
        for (auto& value:c.rotation) row >> value;
        for (auto& value:c.translation) row >> value;
        if (!row || (row >> std::ws, !row.eof()))
            throw std::invalid_argument("each camera row must contain 19 numeric fields");
        if (!ids.insert(v.id).second || c.width==0 || c.height==0 ||
            static_cast<std::uint64_t>(c.width)*c.height>16777216ULL ||
            !std::isfinite(c.fx) || !std::isfinite(c.fy) || c.fx<=0 || c.fy<=0 ||
            !std::isfinite(c.cx) || !std::isfinite(c.cy))
            throw std::invalid_argument("invalid/duplicate camera or more than 16M pixels");
        for (auto t:c.translation) if (!std::isfinite(t))
            throw std::invalid_argument("nonfinite camera translation");
        for (int a=0;a<3;++a) for (int b=0;b<3;++b) {
            float dot=0;
            for (int k=0;k<3;++k) dot+=c.rotation[a*3+k]*c.rotation[b*3+k];
            if (!std::isfinite(dot) || std::abs(dot-(a==b?1.0F:0.0F))>1e-4F)
                throw std::invalid_argument("camera rotation is not orthonormal");
        }
        const auto& r=c.rotation;
        const float det=r[0]*(r[4]*r[8]-r[5]*r[7])-r[1]*(r[3]*r[8]-r[5]*r[6])+r[2]*(r[3]*r[7]-r[4]*r[6]);
        if (det<0) throw std::invalid_argument("camera rotation must be right handed");
        views.push_back(v);
        if (views.size()>32) throw std::invalid_argument("at most 32 views per diagnostic run");
    }
    if (views.empty()) throw std::invalid_argument("camera manifest is empty");
    return views;
}
void write_floats(const std::filesystem::path& path,const std::vector<float>& data) {
    static_assert(std::endian::native==std::endian::little);
    std::ofstream stream(path,std::ios::binary);
    stream.write(reinterpret_cast<const char*>(data.data()),
        static_cast<std::streamsize>(data.size()*sizeof(float)));
    if (!stream) throw std::runtime_error("failed writing diagnostic array");
}
void write_normals(const std::filesystem::path& path,const std::vector<dronegs::Gaussian>& gaussians) {
    // Sidecar rows follow the immutable PLY vertex order; no new trainable state.
    std::ofstream stream(path,std::ios::binary);
    for (const auto& g:gaussians) {
        double norm=0; for (auto q:g.rotation) norm+=static_cast<double>(q)*q;
        norm=std::sqrt(norm);
        if (!std::isfinite(norm) || norm<=1e-12) throw std::invalid_argument("invalid Gaussian rotation");
        const double w=g.rotation[0]/norm,x=g.rotation[1]/norm,y=g.rotation[2]/norm,z=g.rotation[3]/norm;
        const double r[9]{1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w),
            2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w),
            2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)};
        int axis=0; for (int a=1;a<3;++a) if (g.log_scale[a]<g.log_scale[axis]) axis=a;
        const double smallest=std::exp(g.log_scale[axis]);
        const double middle=std::exp(std::min(g.log_scale[(axis+1)%3],g.log_scale[(axis+2)%3]));
        const double confidence=1-(smallest/middle)*(smallest/middle);
        float row[6]{static_cast<float>(r[axis]),static_cast<float>(r[3+axis]),static_cast<float>(r[6+axis]),
            static_cast<float>(confidence),static_cast<float>(smallest),static_cast<float>(middle)};
        // Canonical sign only. Outward orientation is not observable from an
        // ellipsoid; angular comparisons must be unoriented unless oriented later.
        int major=0; for (int a=1;a<3;++a) if (std::abs(row[a])>std::abs(row[major])) major=a;
        if (row[major]<0) for (int a=0;a<3;++a) row[a]=-row[a];
        if (!(middle>0) || !std::isfinite(middle) || !(smallest>0) || !std::isfinite(smallest))
            throw std::invalid_argument("invalid Gaussian scale");
        if (confidence==0) row[0]=row[1]=row[2]=0;
        for (auto value:row) if (!std::isfinite(value)) throw std::invalid_argument("nonfinite Gaussian normal record");
        stream.write(reinterpret_cast<const char*>(row),sizeof(row));
    }
    if (!stream) throw std::runtime_error("failed writing Gaussian normals");
}
}
int main(int argc,char** argv) {
    try {
        if (argc==4 && std::string(argv[1])=="--export-colmap") {
            export_colmap(argv[2],argv[3]); return 0;
        }
        if (argc!=5) throw std::invalid_argument(
            "usage: dronegs_geometry_probe MODEL.ply CAMERAS.txt NEW_OUTPUT reference|fastgs");
        const std::string profile=argv[4];
        if (profile!="reference" && profile!="fastgs") throw std::invalid_argument("unknown raster profile");
        const auto views=read_views(argv[2]);
        const std::filesystem::path output=argv[3];
        if (std::filesystem::exists(output)) throw std::invalid_argument("output must not exist");
        const auto model=dronegs::read_gaussian_ply(argv[1]);
        // PLY carries directional opacity; enable it when present/nonzero.
        bool opacity_sh=false;
        for (const auto& g:model.gaussians) for (const auto coefficient:g.opacity_sh)
            opacity_sh=opacity_sh || coefficient!=0.0F;
        std::filesystem::create_directories(output);
        write_normals(output/"gaussian_normals.f32",model.gaussians);
        for (const auto& view:views) {
            const auto result=dronegs::render_geometry_tiled_cuda(model.gaussians,view.camera,
                {.fastgs=profile=="fastgs",.opacity_sh=opacity_sh,.sh_degree=model.sh_degree});
            const auto prefix=std::to_string(view.id);
            write_floats(output/(prefix+".geometry.f32"),result.channels);
            write_floats(output/(prefix+".rgb.f32"),result.appearance.rgb);
            std::cout << "view " << view.id << " complete\n" << std::flush;
        }
        std::ofstream manifest(output/"complete.json");
        manifest << "{\"schema_version\":1,\"channels\":12,\"gaussian_normal_channels\":6,\"gaussians\":" << model.gaussians.size()
                 << ",\"sh_degree\":" << model.sh_degree << ",\"opacity_sh\":" << (opacity_sh?"true":"false")
                 << ",\"views\":" << views.size() << ",\"raster_profile\":\"" << profile << "\"}\n";
        if (!manifest) throw std::runtime_error("failed writing completion manifest");
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "geometry diagnostic: " << error.what() << '\n'; return 1;
    }
}
