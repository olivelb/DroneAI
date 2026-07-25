cmake_minimum_required(VERSION 3.28)

project(DroneGS VERSION 0.5.0 LANGUAGES CXX CUDA)

option(DRONEGS_BUILD_TESTS "Build DroneGS native tests" ON)

find_package(JPEG REQUIRED)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_CUDA_STANDARD 20)
set(CMAKE_CUDA_STANDARD_REQUIRED ON)

if(NOT CMAKE_CUDA_ARCHITECTURES)
    set(CMAKE_CUDA_ARCHITECTURES "89-real;89-virtual")
endif()

get_filename_component(DRONEGS_REPOSITORY_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)

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
    src/rasterization.cpp
)
target_include_directories(dronegs_core PUBLIC include)
target_link_libraries(dronegs_core PUBLIC JPEG::JPEG)
target_compile_definitions(
    dronegs_core PRIVATE DRONEGS_GIT_REVISION="${DRONEGS_GIT_REVISION}")
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

    add_executable(dronegs_core_tests tests/test_core.cpp)
    target_link_libraries(dronegs_core_tests PRIVATE dronegs_core)
    add_test(NAME dronegs_core_tests COMMAND dronegs_core_tests)

    add_executable(dronegs_rasterization_tests tests/test_rasterization.cpp)
    target_link_libraries(dronegs_rasterization_tests PRIVATE dronegs_core)
    add_test(NAME dronegs_rasterization_tests COMMAND dronegs_rasterization_tests)

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

    add_executable(dronegs_training_tests tests/test_training.cu)
    target_link_libraries(dronegs_training_tests PRIVATE dronegs_cuda)
    set_target_properties(
        dronegs_training_tests PROPERTIES CUDA_SEPARABLE_COMPILATION ON)
    add_test(NAME dronegs_training_tests COMMAND dronegs_training_tests)
endif()

install(TARGETS dronegs RUNTIME DESTINATION bin)
