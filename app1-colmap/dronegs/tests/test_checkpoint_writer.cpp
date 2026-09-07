// SPDX-License-Identifier: GPL-3.0-or-later
#include "dronegs/ordered_training.hpp"
#include "dronegs/checkpoint_writer.hpp"
#include <cmath>
#include <cstring>
#include <iostream>
#include <iterator>
#include <limits>
#include "fixtures/checkpoint_writer_v5_reference.inc"

namespace fs = std::filesystem;
namespace io = dronegs::checkpoint_io;
using Snapshot = writer_reference::CheckpointSnapshotFixture;
using Mode = io::ChecksumMode;

void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
std::vector<char> bytes(const fs::path& p) {
    std::ifstream f(p, std::ios::binary);
    require(bool(f), "missing fixture output");
    return {std::istreambuf_iterator<char>(f), {}};
}
void put(const fs::path& p, const std::vector<char>& b) {
    std::ofstream f(p, std::ios::binary); f.write(b.data(), b.size());
    require(bool(f), "fixture write failed");
}
template<class F> void rejects(F f, const char* needle) {
    try { f(); }
    catch (const std::exception& e) {
        require(std::string(e.what()).find(needle) != std::string::npos,
                "wrong failure reason"); return;
    }
    throw std::runtime_error("expected rejection did not occur");
}
Snapshot fixture(std::size_t n, bool optional) {
    Snapshot s;
    s.dataset_fingerprint = std::string("CPU\0dataset", 11);
    s.configuration_fingerprint = "CPU-fixture-config";
    s.gaussian_count = n; s.optimizer_steps = 256; s.maximum_steps = 30000;
    s.progress.completed_iteration = 256; s.noise_seed = 42;
    s.maximum_active_sh_degree = 3; s.active_sh_degree = 3;
    s.sh_degree_interval = 1000;
    s.progress.initial_loss = .375F;
    s.progress.topology_refinements = 7; s.progress.gaussians_added = 3;
    s.progress.gaussians_pruned = 2; s.progress.gaussian_slots_reused = 1;
    s.progress.topology_compactions = 4;
    if (optional) {
        s.progress.initial_held_out_psnr = 12.5F;
        s.progress.initial_held_out_ssim = .5F;
        s.progress.initial_pixel_weighted_psnr = 13.5F;
        s.progress.initial_pixel_weighted_ssim = .625F;
    }
    s.gaussians.resize(n);
    for (std::size_t i=0;i<n;++i) {
        auto& g=s.gaussians[i]; g.xyz={float(i%37)*.25F, -.5F, 2.F};
        g.dc={.25F,.5F,.75F}; g.log_scale={-1.F,-2.F,-3.F};
        g.opacity_logit=-.125F;
        for (std::size_t k=0;k<g.sh_rest.size();++k) g.sh_rest[k]=float((i+k)%19)*.03125F;
        for (std::size_t k=0;k<g.opacity_sh.size();++k) g.opacity_sh[k]=-float((i+k)%17)*.0625F;
    }
    auto floats=[](auto& v,std::size_t count) {
        v.resize(count);
        for (std::size_t i=0;i<count;++i) v[i]=float(int(i%257)-128)*.0078125F;
    };
    auto pairs=[](auto& v,std::size_t count) {
        v.resize(count);
        for (std::size_t i=0;i<count;++i) v[i]={float(i%127)*.015625F,-float(i%131)*.03125F};
    };
    floats(s.first_dc,n*3); floats(s.second_dc,n*3);
    pairs(s.sh_rest_moments,n*45);
    floats(s.first_opacity,n); floats(s.second_opacity,n);
    pairs(s.opacity_sh_moments,n*15);
    floats(s.first_xyz,n*3); floats(s.second_xyz,n*3);
    floats(s.first_log_scale,n*3); floats(s.second_log_scale,n*3);
    floats(s.first_rotation,n*4); floats(s.second_rotation,n*4);
    floats(s.refine_weight_max,n); floats(s.visibility_count,n);
    floats(s.edge_weight_sum,n); floats(s.absgrad_sum,n);
    floats(s.absgrad_observation_count,n);
    return s;
}
void checksum_valid(const fs::path& p) {
    const auto n=fs::file_size(p); require(n>=8,"checksum trailer missing");
    std::uint64_t trailer=0;
    std::ifstream f(p,std::ios::binary);f.seekg(n-8);
    f.read(reinterpret_cast<char*>(&trailer),8);
    require(bool(f) && writer_reference::checkpoint_checksum(p,n-8)==trailer,
            "checksum mismatch");
}
void timings(const char* mode, std::size_t n, const io::WriteTimings& t) {
    for (auto v:{t.serialization_seconds,t.checksum_seconds,t.payload_flush_close_seconds,
        t.trailer_seconds,t.file_sync_seconds,t.publication_seconds,
        t.directory_sync_seconds,t.total_seconds}) require(std::isfinite(v)&&v>=0,"invalid phase timing");
    require(t.payload_bytes>0 && t.total_seconds>=t.serialization_seconds,"inconsistent timing");
    std::cout << "{\"case\":\"phase_telemetry_fixture_only\",\"mode\":\"" << mode
        << "\",\"gaussians\":" << n << ",\"payload_bytes\":" << t.payload_bytes
        << ",\"checksum_inside_serialization\":" << (t.checksum_inside_serialization?"true":"false")
        << ",\"serialization_seconds\":" << t.serialization_seconds
        << ",\"checksum_seconds\":" << t.checksum_seconds
        << ",\"payload_flush_close_seconds\":" << t.payload_flush_close_seconds
        << ",\"trailer_seconds\":" << t.trailer_seconds
        << ",\"file_sync_seconds\":" << t.file_sync_seconds
        << ",\"publication_seconds\":" << t.publication_seconds
        << ",\"directory_sync_seconds\":" << t.directory_sync_seconds
        << ",\"total_seconds\":" << t.total_seconds << "}\n";
}
void parity(const fs::path& root,std::size_t n,bool optional) {
    auto s=fixture(n,optional);auto base=root/("n"+std::to_string(n));fs::create_directory(base);
    auto old=base/"original.ckpt";
    writer_reference::write_original(s,old);
    auto expected=bytes(old);checksum_valid(old);
    for (auto mode:{Mode::reread_file,Mode::streaming}) {
        auto name=mode==Mode::streaming?"streaming":"reread";
        auto p=base/(std::string(name)+".ckpt"); io::WriteTimings t;
        io::write_checkpoint(s,p,mode,&t);
        require(bytes(p)==expected,"original V5 bytes differ");checksum_valid(p);
        require(t.checksum_inside_serialization==(mode==Mode::streaming),"checksum phase label differs");
        require(!fs::exists(p.string()+".tmp"),"successful temporary was not published");
        timings(name,n,t);
    }
    if(n==3) {
        const auto p=base/"default.ckpt";io::write_checkpoint(s,p);
        require(bytes(p)==expected,"default mode differs");
        auto b=expected;b[b.size()/2]^=1;put(base/"corrupted.ckpt",b);
        rejects([&]{checksum_valid(base/"corrupted.ckpt");},"checksum mismatch");
        b=expected;b.resize(b.size()-9);put(base/"truncated.ckpt",b);
        rejects([&]{checksum_valid(base/"truncated.ckpt");},"checksum mismatch");
        s.progress.completed_iteration=257;
        io::write_checkpoint(s,p,Mode::streaming);
        require(bytes(p)!=expected,"replacement did not publish new snapshot");checksum_valid(p);
    }
}
void local_failures(const fs::path& root) {
    auto s=fixture(3,true);
    rejects([&]{io::write_checkpoint(s,root/"missing"/"out.ckpt");},"cannot create checkpoint");
    for(auto mode:{Mode::reread_file,Mode::streaming}) {
        std::string name=mode==Mode::streaming?"streaming":"reread";
        auto p=root/("full-"+name+".ckpt"); const std::vector<char> old={'o','l','d'};put(p,old);
        fs::create_symlink("/dev/full",p.string()+".tmp");
        rejects([&]{io::write_checkpoint(s,p,mode);},"failed to write checkpoint");
        require(bytes(p)==old,"write failure changed published checkpoint");
        p=root/("truncated-snapshot-"+name+".ckpt");put(p,old);
        auto broken=s;broken.sh_rest_moments.pop_back();
        rejects([&]{io::write_checkpoint(broken,p,mode);},"snapshot is truncated");
        require(bytes(p)==old,"snapshot failure changed published checkpoint");
    }
}
void injected_failures(const fs::path& root,const std::string& fault) {
    auto s=fixture(3,true);
    for(auto mode:{Mode::reread_file,Mode::streaming}) {
        auto p=root/(std::string("fault-")+(mode==Mode::streaming?"streaming":"reread")+".ckpt");
        const std::vector<char> old={'p','r','e','v','i','o','u','s'};put(p,old);
        rejects([&]{io::write_checkpoint(s,p,mode);},fault=="rename"?"cannot publish checkpoint":"cannot fsync checkpoint");
        require(bytes(p)==old,"failure lost previous checkpoint");
        require(fs::is_regular_file(p.string()+".tmp"),"failure did not retain temporary");
        require(!fs::exists(p.string()+".previous"),"previous checkpoint was not restored");
    }
}
int main(int argc,char** argv) {
    try {
        require(argc>=2,"need new output directory");fs::path root=argv[1];
        require(!fs::exists(root),"test output must be new");fs::create_directory(root);
        if(argc==3) injected_failures(root,argv[2]);
        else {
            io::StreamingChecksum known; require(known.value()==14695981039346656037ULL,"FNV empty vector");
            known.update("hel",3);known.update("lo",2);
            require(known.value()==0xa430d84680aabd0bULL && known.bytes()==5,"FNV hello vector");
            parity(root,0,false);parity(root,1,false);parity(root,3,true);
            parity(root,23301,true);parity(root,23302,true); // straddle 4MiB SH component chunk
            local_failures(root);
        }
        std::cout << "{\"status\":\"PASS\",\"default_streaming\":"
            << (io::default_checksum_mode==Mode::streaming?"true":"false") << "}\n";
    } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
