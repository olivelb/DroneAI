// SPDX-License-Identifier: MIT
// GPU qualification fixture. Builds/runs only in the coordinator's qualified GPU environment.
#include <array>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <iterator>
#include <sstream>
#include <stdexcept>
#include <jpeglib.h>
#include "dronegs/checkpoint_evaluation.hpp"
#include "dronegs/model.hpp"
#include "dronegs/training.hpp"
namespace {
void check(bool condition, const char* message) { if (!condition) throw std::runtime_error(message); }
std::string bytes(const std::filesystem::path& path) {
    std::ifstream in(path,std::ios::binary); check(bool(in),"missing fixture artifact");
    return {std::istreambuf_iterator<char>(in),std::istreambuf_iterator<char>()};
}
void write_image(const std::filesystem::path& path) {
    auto* file=std::fopen(path.string().c_str(),"wb"); check(file!=nullptr,"cannot create JPEG fixture");
    jpeg_compress_struct c{}; jpeg_error_mgr error{};
    c.err=jpeg_std_error(&error); jpeg_create_compress(&c); jpeg_stdio_dest(&c,file);
    c.image_width=32; c.image_height=32; c.input_components=3; c.in_color_space=JCS_RGB;
    jpeg_set_defaults(&c); jpeg_set_quality(&c,95,TRUE); jpeg_start_compress(&c,TRUE);
    std::array<JSAMPLE,96> row{};
    for(std::size_t x=0;x<32;++x) { row[x*3]=x*7; row[x*3+1]=180; row[x*3+2]=255-x*7; }
    while(c.next_scanline<c.image_height) { auto* p=row.data(); jpeg_write_scanlines(&c,&p,1); }
    jpeg_finish_compress(&c); jpeg_destroy_compress(&c); std::fclose(file);
}
std::vector<std::vector<std::string>> csv_stage(const std::filesystem::path& path,const std::string& stage) {
    std::istringstream input(bytes(path)); std::string line;
    std::vector<std::vector<std::string>> rows;
    while(std::getline(input,line)) {
        if(!line.starts_with(stage+",")) continue;
        std::istringstream row(line); std::string cell; std::vector<std::string> cells;
        while(std::getline(row,cell,',')) cells.push_back(cell);
        check(cells.size()==13,"unexpected native metrics shape"); rows.push_back(cells);
    }
    return rows;
}
}
int main(int argc,char** argv) {
    try {
        if(argc!=2) throw std::invalid_argument("fixture requires a NEW scratch directory");
        const std::filesystem::path root=argv[1];
        check(std::filesystem::create_directory(root),"scratch must be new");
        std::filesystem::create_directories(root/"data"/"images");
        dronegs::Scene scene;
        scene.cameras.push_back({.id=1,.model_id=1,.width=32,.height=32,.parameters={30.,30.,16.,16.}});
        for(std::uint32_t i=0;i<4;++i) {
            const auto name="fixture-"+std::to_string(i)+".jpg";
            write_image(root/"data"/"images"/name);
            scene.images.push_back({.id=i+1,.camera_id=1,.name=name,.qvec={1.,0.,0.,0.},
                .tvec={double(i)*0.015,0.,0.},.source_x=4,.source_y=4,.source_width=24,.source_height=24});
        }
        std::uint64_t point_id=1;
        for(int y=-3;y<=3;++y) for(int x=-3;x<=3;++x)
            scene.points.push_back({.id=point_id++,.xyz={double(x)*0.15,double(y)*0.15,2.},.rgb={128,180,128}});
        auto initial=dronegs::initialize_fixed_topology(scene);
        const auto original_initial=initial;
        dronegs::Options options;
        options.data_path=root/"data"; options.output_path=root/"training";
        options.run_manifest=options.output_path/"unused.json";
        options.iterations=8; options.strategy="mrnf"; options.max_cap=128; options.sh_degree=0;
        options.resize_factor=1; options.max_width=32; options.tile_mode=1; options.seed=42;
        options.test_every=2; options.dataset_fingerprint="checkpoint-evaluator-synthetic-v1";
        options.eval_every=4; options.eval_start=4; options.stop_after=4;
        options.checkpoint_path=root/"source.ckpt";
        const auto trained=dronegs::train_ordered_mrnf(options,scene,initial);
        check(!trained.completed && trained.completed_iterations==4,"fixture did not stop at checkpoint step");
        const auto checkpoint_before=bytes(options.checkpoint_path);
        const auto reference=csv_stage(options.output_path/"evaluation"/"metrics.csv","iteration_4");
        check(!reference.empty(),"fixture has no held-out reference");
        options.resume_from=options.checkpoint_path; options.checkpoint_path.clear();
        options.stop_after=0; options.eval_every=0; options.eval_start=0;
        options.output_path=root/"diagnostic";
        dronegs::CheckpointEvaluationOptions diagnostic;
        diagnostic.expected_completed_iteration=4; diagnostic.expected_held_out_frames=reference.size();
        diagnostic.repeats=2; diagnostic.export_all_predictions=true;
        diagnostic.supplied_binary_sha256=std::string(64,'0'); // Explicitly supplied fixture label, never computed.
        initial=original_initial;
        dronegs::evaluate_checkpoint_read_only(options,scene,initial,diagnostic);
        check(bytes(options.resume_from)==checkpoint_before,"read-only evaluator changed checkpoint source");
        for(int repeat=0;repeat<2;++repeat) {
            const auto dir=options.output_path/("repeat-"+std::to_string(repeat));
            const auto actual=csv_stage(dir/"evaluation"/"metrics.csv","checkpoint_4");
            check(actual.size()==reference.size(),"held-out identity count differs");
            for(std::size_t row=0;row<reference.size();++row) {
                for(std::size_t column=1;column<9;++column)
                    check(actual[row][column]==reference[row][column],"held-out identity or crop dimensions changed");
                for(std::size_t column=9;column<13;++column) {
                    const double expected=std::stod(reference[row][column]), value=std::stod(actual[row][column]);
                    check(std::isfinite(value) && std::abs(value-expected)<=1e-6*std::max(1.,std::abs(expected)),
                        "native checkpoint metric parity failed at float32 fixture tolerance");
                }
                const auto path=dir/("frame-"+actual[row][2]+".rgb.f32");
                check(std::filesystem::file_size(path)==std::stoull(actual[row][8])*12,"float32 export size differs");
                if (repeat == 1) {
                    const auto first = options.output_path / "repeat-0";
                    check(bytes(path) == bytes(first / path.filename()),
                        "same-state RGB float32 repeats differ; diagnose GPU nondeterminism before changing this oracle");
                    const auto target_name = "frame-" + actual[row][2] + ".target.ppm";
                    check(bytes(dir / target_name) == bytes(first / target_name),
                        "same-state target PPM repeats differ");
                }
            }
        }
        for(const auto& entry:std::filesystem::recursive_directory_iterator(options.output_path))
            check(entry.path().extension()!=".ckpt" && entry.path().extension()!=".ply" &&
                entry.path().filename()!="trainer_run.json","diagnostic wrote a training/model artifact");
        check(bytes(options.output_path/"diagnostic-complete.json").find("\"training_steps_executed\":0")!=std::string::npos,
            "diagnostic completion marker missing");
        int rejected_count=0;
        const auto expect_rejection=[&](dronegs::Options request,dronegs::CheckpointEvaluationOptions settings) {
            request.output_path=root/("rejected-"+std::to_string(rejected_count++));
            auto model=original_initial; bool failed=false;
            try { dronegs::evaluate_checkpoint_read_only(request,scene,model,settings); }
            catch(const std::exception&) { failed=true; }
            check(failed && !std::filesystem::exists(request.output_path),"invalid checkpoint was accepted or emitted artifacts");
        };
        { auto request=options; request.dataset_fingerprint+="-wrong"; expect_rejection(request,diagnostic); }
        { auto request=options; request.iterations=9; expect_rejection(request,diagnostic); }
        { auto settings=diagnostic; settings.expected_completed_iteration=3; expect_rejection(options,settings); }
        { auto settings=diagnostic; settings.expected_held_out_frames=reference.size()+1; expect_rejection(options,settings); }
        {
            auto settings = diagnostic;
            settings.export_all_predictions = false;
            // Four supported one-tile images: select an existing frame that is
            // absent from the held-out reference, rather than an out-of-range ID.
            std::size_t training_frame = 0;
            while (training_frame < scene.images.size() && std::any_of(reference.begin(), reference.end(),
                [training_frame](const auto& row) { return std::stoull(row[2]) == training_frame; }))
                ++training_frame;
            check(training_frame < scene.images.size(), "fixture has no non-held-out frame to test");
            settings.export_frame_indices = {training_frame};
            expect_rejection(options, settings);
        }
        const auto corrupt=root/"corrupt.ckpt";
        auto damaged=checkpoint_before; damaged[damaged.size()/2]^=1;
        {std::ofstream out(corrupt,std::ios::binary);out.write(damaged.data(),damaged.size());}
        { auto request=options; request.resume_from=corrupt; expect_rejection(request,diagnostic); }
        check(bytes(options.resume_from)==checkpoint_before,"failure tests changed checkpoint source");
        std::cout<<"checkpoint evaluator GPU fixture passed\n";
        return 0;
    } catch(const std::exception& error) { std::cerr<<error.what()<<'\n'; return 1; }
}
