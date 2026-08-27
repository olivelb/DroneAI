cmake_minimum_required(VERSION 3.28)

set(
    DRONEGS_CUDA_ARCHITECTURES
    "native"
    CACHE STRING
    "CUDA target preset: native, portable, or a CMake architecture list")
set_property(
    CACHE DRONEGS_CUDA_ARCHITECTURES
    PROPERTY STRINGS native portable)

if(DRONEGS_CUDA_ARCHITECTURES STREQUAL "native")
    # CMake asks nvcc to detect every GPU visible while configuring.
    set(_dronegs_cuda_architectures_resolved "native")
elseif(DRONEGS_CUDA_ARCHITECTURES STREQUAL "portable")
    # CUDA 12.9 cubins for Turing through Blackwell, including datacenter
    # and embedded variants.
    set(
        _dronegs_cuda_architectures_resolved
        "75-real;80-real;86-real;87-real;89-real;90-real;100-real;101-real;120-real")
else()
    set(
        _dronegs_cuda_architectures_resolved
        "${DRONEGS_CUDA_ARCHITECTURES}")
endif()

# Keep a preset-managed cache responsive to later preset changes, while
# preserving the precedence of an explicit CMAKE_CUDA_ARCHITECTURES value.
if(NOT DEFINED CMAKE_CUDA_ARCHITECTURES OR
   (DEFINED DRONEGS_CUDA_ARCHITECTURES_LAST_APPLIED AND
    CMAKE_CUDA_ARCHITECTURES STREQUAL
        DRONEGS_CUDA_ARCHITECTURES_LAST_APPLIED))
    set(
        CMAKE_CUDA_ARCHITECTURES
        "${_dronegs_cuda_architectures_resolved}"
        CACHE STRING "" FORCE)
    set(
        DRONEGS_CUDA_ARCHITECTURES_LAST_APPLIED
        "${_dronegs_cuda_architectures_resolved}"
        CACHE INTERNAL "Last DroneGS-managed CUDA architecture list" FORCE)
endif()
unset(_dronegs_cuda_architectures_resolved)

project(DroneGS VERSION 0.5.0 LANGUAGES CXX CUDA)

string(REGEX MATCH "^([0-9]+)\\.([0-9]+)" _dronegs_cuda_version "${CMAKE_CUDA_COMPILER_VERSION}")
if(NOT _dronegs_cuda_version)
    message(FATAL_ERROR "Cannot derive the CUDA runtime version from ${CMAKE_CUDA_COMPILER_VERSION}")
endif()
set(DRONEGS_CUDA_RUNTIME_VERSION "${CMAKE_MATCH_1}.${CMAKE_MATCH_2}")
unset(_dronegs_cuda_version)

option(DRONEGS_BUILD_TESTS "Build DroneGS native tests" ON)
option(DRONEGS_BUILD_BENCHMARKS "Build DroneGS native benchmarks" OFF)

find_package(JPEG REQUIRED)
find_package(Threads REQUIRED)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_CUDA_STANDARD 20)
set(CMAKE_CUDA_STANDARD_REQUIRED ON)

get_filename_component(DRONEGS_REPOSITORY_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
set_property(
    DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${DRONEGS_REPOSITORY_ROOT}/.git/HEAD"
    "${DRONEGS_REPOSITORY_ROOT}/.git/index"
)

execute_process(
    COMMAND git -c safe.directory=${DRONEGS_REPOSITORY_ROOT} rev-parse HEAD
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
    OUTPUT_VARIABLE DRONEGS_GIT_REVISION
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET
)
execute_process(
    COMMAND git -c safe.directory=${DRONEGS_REPOSITORY_ROOT} status --porcelain
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
    OUTPUT_VARIABLE DRONEGS_GIT_DIRTY
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET
)
if(NOT DRONEGS_GIT_REVISION)
    set(DRONEGS_GIT_REVISION "unknown")
elseif(DRONEGS_GIT_DIRTY)
    string(APPEND DRONEGS_GIT_REVISION "-dirty")
endif()

add_library(dronegs_core
    src/cli.cpp
    src/colmap.cpp
    src/image.cpp
    src/model.cpp
    src/ply.cpp
    src/manifest.cpp
    src/topology_telemetry.cpp
    src/topology_percentiles.cpp
    src/sh.cpp
    src/rasterization.cpp
)
target_include_directories(dronegs_core PUBLIC include)
target_link_libraries(dronegs_core PUBLIC JPEG::JPEG Threads::Threads)
target_compile_definitions(
    dronegs_core PRIVATE
    DRONEGS_GIT_REVISION="${DRONEGS_GIT_REVISION}"
    DRONEGS_CUDA_RUNTIME_VERSION="${DRONEGS_CUDA_RUNTIME_VERSION}")
target_compile_options(dronegs_core PRIVATE
    $<$<COMPILE_LANGUAGE:CXX>:-Wall;-Wextra;-Wpedantic;-Wconversion;-Werror>
)

add_library(dronegs_cuda
    cuda/loss.cu
    cuda/rasterization.cu
    cuda/trainer.cu
)
target_include_directories(dronegs_cuda PUBLIC include)
target_link_libraries(dronegs_cuda PUBLIC dronegs_core)
set_target_properties(dronegs_cuda PROPERTIES
    CUDA_SEPARABLE_COMPILATION ON
    POSITION_INDEPENDENT_CODE ON
)
target_compile_options(dronegs_cuda PRIVATE
    $<$<COMPILE_LANGUAGE:CUDA>:--expt-relaxed-constexpr;--use_fast_math>
)

add_executable(dronegs src/main.cpp)
target_link_libraries(dronegs PRIVATE dronegs_core dronegs_cuda)

if(DRONEGS_BUILD_TESTS)
    enable_testing()
    find_package(Python3 COMPONENTS Interpreter REQUIRED)

    add_executable(dronegs_core_tests tests/test_core.cpp tests/test_topology_telemetry.cpp tests/test_topology_percentiles.cpp tests/test_refinement_workspace.cpp)
    target_link_libraries(dronegs_core_tests PRIVATE dronegs_core)
    add_test(NAME dronegs_core_tests COMMAND dronegs_core_tests)

    add_executable(dronegs_rasterization_tests tests/test_rasterization.cpp)
    target_link_libraries(dronegs_rasterization_tests PRIVATE dronegs_core)
    add_test(NAME dronegs_rasterization_tests COMMAND dronegs_rasterization_tests)

    add_executable(dronegs_sh_basis_probe tests/sh_basis_probe.cpp)
    target_link_libraries(dronegs_sh_basis_probe PRIVATE dronegs_core)
    add_test(
        NAME dronegs_sh_python_parity_tests
        COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_sh_python_parity.py
                $<TARGET_FILE:dronegs_sh_basis_probe>
                ${CMAKE_CURRENT_SOURCE_DIR}/../gaussian_ortho)

    add_executable(dronegs_cuda_tests tests/test_loss.cu)
    target_link_libraries(dronegs_cuda_tests PRIVATE dronegs_cuda)
    set_target_properties(dronegs_cuda_tests PROPERTIES CUDA_SEPARABLE_COMPILATION ON)
    add_test(NAME dronegs_cuda_tests COMMAND dronegs_cuda_tests)

    add_executable(
        dronegs_rasterization_cuda_tests tests/test_rasterization_cuda.cu)
    target_link_libraries(dronegs_rasterization_cuda_tests PRIVATE dronegs_cuda)
    set_target_properties(
        dronegs_rasterization_cuda_tests PROPERTIES CUDA_SEPARABLE_COMPILATION ON)
    add_test(
        NAME dronegs_rasterization_cuda_tests
        COMMAND dronegs_rasterization_cuda_tests)

    add_executable(dronegs_training_tests tests/test_training.cu tests/test_topology_compaction.cu tests/test_topology_snapshot.cu tests/test_refinement_workspace.cu)
    # Compile the snapshot oracle's device path with the production kernel flags.
    set_source_files_properties(tests/test_topology_snapshot.cu PROPERTIES
        COMPILE_OPTIONS "--expt-relaxed-constexpr;--use_fast_math")
    target_link_libraries(dronegs_training_tests PRIVATE dronegs_cuda)
    set_target_properties(
        dronegs_training_tests PROPERTIES CUDA_SEPARABLE_COMPILATION ON)
    add_test(NAME dronegs_training_tests COMMAND dronegs_training_tests)

    add_executable(dronegs_tile_training_tests tests/test_tile_training.cu)
    target_link_libraries(dronegs_tile_training_tests PRIVATE dronegs_cuda)
    set_target_properties(
        dronegs_tile_training_tests PROPERTIES CUDA_SEPARABLE_COMPILATION ON)
    add_test(NAME dronegs_tile_training_tests COMMAND dronegs_tile_training_tests)

    add_test(
        NAME dronegs_lpips_tool_tests
        COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_lpips_tool.py)
endif()

if(DRONEGS_BUILD_BENCHMARKS)
    add_executable(dronegs_topology_percentile_benchmark benchmarks/benchmark_topology_percentiles.cpp)
    target_link_libraries(dronegs_topology_percentile_benchmark PRIVATE dronegs_core)
    add_executable(dronegs_checkpoint_topology_benchmark benchmarks/benchmark_checkpoint_topology.cu)
    target_link_libraries(dronegs_checkpoint_topology_benchmark PRIVATE dronegs_cuda)
    set_target_properties(dronegs_checkpoint_topology_benchmark PROPERTIES CUDA_SEPARABLE_COMPILATION ON)
    add_executable(dronegs_topology_benchmark benchmarks/benchmark_topology.cu)
    target_link_libraries(dronegs_topology_benchmark PRIVATE dronegs_cuda)
    set_target_properties(dronegs_topology_benchmark PROPERTIES CUDA_SEPARABLE_COMPILATION ON)
    add_executable(
        dronegs_rasterization_cuda_benchmark
        benchmarks/benchmark_rasterization_cuda.cu)
    target_link_libraries(
        dronegs_rasterization_cuda_benchmark PRIVATE dronegs_cuda)
    set_target_properties(
        dronegs_rasterization_cuda_benchmark PROPERTIES
        CUDA_SEPARABLE_COMPILATION ON)
endif()

install(TARGETS dronegs RUNTIME DESTINATION bin)
