cmake_minimum_required(VERSION 3.18)
project(arga_nest_core LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

get_filename_component(ANS_CPP_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
set(LEGACY_CPP_DIR "${ANS_CPP_ROOT}/modules/nesting_engine/cpp")
set(WORKER_DIR "${ANS_CPP_ROOT}/native/ArgaNestWorker")

include(CheckLanguage)
option(ARGA_NEST_ENABLE_CUDA "CUDA accel para ArgaNestCore" ON)
option(ARGA_NEST_BUILD_WORKER "Build ArgaNestWorker.exe" ON)
set(ARGA_NEST_HAS_CUDA 0)

if(ARGA_NEST_ENABLE_CUDA)
    if(WIN32 AND (NOT CMAKE_CUDA_COMPILER OR CMAKE_CUDA_COMPILER MATCHES "NOTFOUND"))
        file(
            GLOB _ARGA_CUDA_COMPILERS
            "C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v*/bin/nvcc.exe"
        )
        if(_ARGA_CUDA_COMPILERS)
            list(SORT _ARGA_CUDA_COMPILERS)
            list(GET _ARGA_CUDA_COMPILERS -1 _ARGA_CUDA_COMPILER)
            get_filename_component(
                _ARGA_CUDA_TOOLKIT_DIR
                "${_ARGA_CUDA_COMPILER}/../.."
                ABSOLUTE
            )
            set(CMAKE_CUDA_COMPILER "${_ARGA_CUDA_COMPILER}" CACHE FILEPATH "")
            set(ENV{CUDA_PATH} "${_ARGA_CUDA_TOOLKIT_DIR}")
            set(ENV{CudaToolkitDir} "${_ARGA_CUDA_TOOLKIT_DIR}/")
            set(
                CMAKE_VS_GLOBALS
                "CudaToolkitDir=${_ARGA_CUDA_TOOLKIT_DIR}/"
                CACHE STRING ""
                FORCE
            )
        endif()
    endif()
    check_language(CUDA)
    if(CMAKE_CUDA_COMPILER)
        enable_language(CUDA)
        find_package(CUDAToolkit REQUIRED)
        set(ARGA_NEST_HAS_CUDA 1)
        message(STATUS "ArgaNestCore: CUDA nest accel ENABLED")
    else()
        message(STATUS "ArgaNestCore: CUDA Toolkit not found; CPU fallback")
    endif()
endif()

find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)

# Fuerza a pybind11 a reusar el Python que ya encontramos con find_package(Python).
# Sin esto, pybind11 dispara find_package(PythonInterp/PythonLibs) legacy y en PCs
# con multiples Pythons (ej. 3.13 + 3.14) engancha el "equivocado" ignorando el
# -DPython_EXECUTABLE=... que le pasa build_arga_nest_core.ps1. Regresion: crash
# 2026-08-13 (audit+render ezdxf en 3.14).
set(PYBIND11_FINDPYTHON ON CACHE BOOL "" FORCE)

include(FetchContent)
FetchContent_Declare(
    pybind11
    GIT_REPOSITORY https://github.com/pybind/pybind11.git
    GIT_TAG v2.13.6
)
FetchContent_MakeAvailable(pybind11)

FetchContent_Declare(
    nlohmann_json
    GIT_REPOSITORY https://github.com/nlohmann/json.git
    GIT_TAG v3.11.3
)
FetchContent_MakeAvailable(nlohmann_json)

set(CLIPPER2_UTILS OFF CACHE BOOL "" FORCE)
set(CLIPPER2_EXAMPLES OFF CACHE BOOL "" FORCE)
set(CLIPPER2_TESTS OFF CACHE BOOL "" FORCE)
set(CLIPPER2_USINGZ "OFF" CACHE STRING "" FORCE)

set(CLIPPER2_CPP_DIR "${LEGACY_CPP_DIR}/third_party/Clipper2/CPP")
if(NOT EXISTS "${CLIPPER2_CPP_DIR}/CMakeLists.txt")
    message(FATAL_ERROR "Clipper2 no encontrado en ${CLIPPER2_CPP_DIR}")
endif()

add_subdirectory("${CLIPPER2_CPP_DIR}" "${CMAKE_BINARY_DIR}/clipper2_cpp")

set(
    ARGA_NEST_LIB_SOURCES
    src/abi_impl.cpp
    src/engine_facade.cpp
    src/certifier.cpp
    src/nfp_cache.cpp
    src/profiles.cpp
    src/multi_plate.cpp
    src/cu_strip.cpp
    src/dxf_io.cpp
    src/mark_engine.cpp
    src/step_export.cpp
    src/cuda_status.cpp
    src/constraints.cpp
    src/lod.cpp
    src/sa_refine.cpp
    src/common_line.cpp
    src/cost_score.cpp
    src/export_cam.cpp
    src/tabu.cpp
    "${LEGACY_CPP_DIR}/packer.cpp"
    "${LEGACY_CPP_DIR}/packer_base.cpp"
    "${LEGACY_CPP_DIR}/packer_burke_blf.cpp"
    "${LEGACY_CPP_DIR}/packer_libnest2d.cpp"
    "${LEGACY_CPP_DIR}/packer_svgnest_ultra.cpp"
    "${LEGACY_CPP_DIR}/cuda/nest_accel.cpp"
    "${LEGACY_CPP_DIR}/cuda/nest_accel_raster.cpp"
)
if(ARGA_NEST_HAS_CUDA)
    list(APPEND ARGA_NEST_LIB_SOURCES "${LEGACY_CPP_DIR}/cuda/nest_accel.cu")
endif()

add_library(arga_nest_core_lib STATIC ${ARGA_NEST_LIB_SOURCES})
target_include_directories(
    arga_nest_core_lib PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${LEGACY_CPP_DIR}
    ${CLIPPER2_CPP_DIR}/Clipper2Lib/include
)
target_link_libraries(arga_nest_core_lib PUBLIC Clipper2 nlohmann_json::nlohmann_json)
target_compile_definitions(
    arga_nest_core_lib PUBLIC
    ARGA_NEST_CORE_EXPORTS
    ARGA_NEST_HAS_CUDA=${ARGA_NEST_HAS_CUDA}
    _CRT_SECURE_NO_WARNINGS
)
if(ARGA_NEST_HAS_CUDA)
    target_link_libraries(arga_nest_core_lib PUBLIC CUDA::cudart)
    set_target_properties(
        arga_nest_core_lib PROPERTIES
        CUDA_STANDARD 17
        CUDA_STANDARD_REQUIRED ON
        CUDA_SEPARABLE_COMPILATION OFF
        CUDA_RUNTIME_LIBRARY Static
        CUDA_ARCHITECTURES "75;86;89"
    )
endif()
if(MSVC)
    target_compile_options(
        arga_nest_core_lib PRIVATE
        $<$<COMPILE_LANGUAGE:CXX>:/O2 /EHsc /W3 /bigobj>
    )
endif()

pybind11_add_module(arga_nest_core MODULE src/bindings_core.cpp)
target_link_libraries(arga_nest_core PRIVATE arga_nest_core_lib)
if(MSVC)
    target_compile_options(arga_nest_core PRIVATE /O2 /EHsc /W3 /bigobj)
endif()

add_custom_command(
    TARGET arga_nest_core POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        $<TARGET_FILE:arga_nest_core>
        "${ANS_CPP_ROOT}/modules/nesting_engine/$<TARGET_FILE_NAME:arga_nest_core>"
    COMMENT "Installing arga_nest_core next to nesting_engine"
)

if(ARGA_NEST_BUILD_WORKER)
    add_executable(ArgaNestWorker "${WORKER_DIR}/main.cpp")
    target_link_libraries(ArgaNestWorker PRIVATE arga_nest_core_lib)
    target_include_directories(ArgaNestWorker PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
    add_custom_command(
        TARGET ArgaNestWorker POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E make_directory "${ANS_CPP_ROOT}/native/bin"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            $<TARGET_FILE:ArgaNestWorker>
            "${ANS_CPP_ROOT}/native/bin/ArgaNestWorker.exe"
    )
endif()
