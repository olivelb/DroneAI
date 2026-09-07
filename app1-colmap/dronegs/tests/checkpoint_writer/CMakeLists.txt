cmake_minimum_required(VERSION 3.28)

# The same CPU-only targets work standalone or under the CUDA project.
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    project(DroneGSCheckpointWriterTests LANGUAGES CXX)
    enable_testing()
endif()

# /dev/full, POSIX signals and LD_PRELOAD fault injection are Linux-specific.
if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    return()
endif()
enable_language(C)
find_package(Python3 COMPONENTS Interpreter REQUIRED)

add_library(dronegs_checkpoint_faults SHARED ../checkpoint_faults.c)
target_link_libraries(dronegs_checkpoint_faults PRIVATE ${CMAKE_DL_LIBS})
target_compile_options(dronegs_checkpoint_faults PRIVATE -Wall -Wextra -Werror)

foreach(mode IN ITEMS 0 1)
    set(target dronegs_checkpoint_writer_tests_${mode})
    add_executable(${target} ../test_checkpoint_writer.cpp)
    target_include_directories(${target} PRIVATE ../../include)
    target_compile_features(${target} PRIVATE cxx_std_20)
    target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic -Werror)
    target_compile_definitions(${target} PRIVATE
        DRONEGS_EXPERIMENTAL_STREAMING_CHECKPOINT_CHECKSUM=${mode}
        DRONEGS_PROFILE_CHECKPOINT_WRITE_PHASES=1)
    foreach(test_case IN ITEMS parity fsync rename)
        set(arguments
            --binary $<TARGET_FILE:${target}>
            --artifacts-root ${CMAKE_CURRENT_BINARY_DIR}/artifacts)
        if(NOT test_case STREQUAL "parity")
            list(APPEND arguments --fault ${test_case}
                --fault-library $<TARGET_FILE:dronegs_checkpoint_faults>)
        endif()
        add_test(NAME ${target}_${test_case}
            COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/run_test.py ${arguments})
        set_tests_properties(${target}_${test_case} PROPERTIES
            LABELS "cpu;checkpoint-writer" TIMEOUT 90 RUN_SERIAL TRUE)
    endforeach()
endforeach()
