cmake_minimum_required(VERSION 3.18)
project(arga_nesting_cpp LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)
include(CheckLanguage)

option(ARGA_NEST_ENABLE_CUDA "Acelerador CUDA compartido para motores ANS" ON)
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
        message(STATUS "ARGA algorithm_cpp: CUDA nest accel enabled")
    else()
        message(STATUS "ARGA algorithm_cpp: CUDA Toolkit not found; CPU fallback")
    endif()
endif()

include(FetchContent)
FetchContent_Declare(
    pybind11
    GIT_REPOSITORY https://github.com/pybind/pybind11.git
    GIT_TAG v2.13.6
)
FetchContent_MakeAvailable(pybind11)

set(CLIPPER2_UTILS OFF CACHE BOOL "" FORCE)
set(CLIPPER2_EXAMPLES OFF CACHE BOOL "" FORCE)
set(CLIPPER2_TESTS OFF CACHE BOOL "" FORCE)
set(CLIPPER2_USINGZ "OFF" CACHE STRING "" FORCE)

set(CLIPPER2_CPP_DIR "${CMAKE_CURRENT_SOURCE_DIR}/third_party/Clipper2/CPP")
if(NOT EXISTS "${CLIPPER2_CPP_DIR}/CMakeLists.txt")
    message(FATAL_ERROR "Clipper2 no encontrado en ${CLIPPER2_CPP_DIR}. Ejecuta build_cpp_engine.ps1")
endif()

add_subdirectory("${CLIPPER2_CPP_DIR}" "${CMAKE_BINARY_DIR}/clipper2_cpp")

set(
    ARGA_NEST_SOURCES
    bindings.cpp
    packer.cpp
    packer_base.cpp
    packer_burke_blf.cpp
    packer_libnest2d.cpp
    packer_svgnest_ultra.cpp
    cuda/nest_accel.cpp
    cuda/nest_accel_raster.cpp
)
if(ARGA_NEST_HAS_CUDA)
    list(APPEND ARGA_NEST_SOURCES cuda/nest_accel.cu)
endif()

pybind11_add_module(algorithm_cpp MODULE ${ARGA_NEST_SOURCES})

target_include_directories(
    algorithm_cpp PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CLIPPER2_CPP_DIR}/Clipper2Lib/include
)

target_link_libraries(algorithm_cpp PRIVATE Clipper2)
target_compile_definitions(
    algorithm_cpp PRIVATE
    ARGA_NEST_HAS_CUDA=${ARGA_NEST_HAS_CUDA}
    _CRT_SECURE_NO_WARNINGS
)

if(ARGA_NEST_HAS_CUDA)
    target_link_libraries(algorithm_cpp PRIVATE CUDA::cudart)
    set_target_properties(
        algorithm_cpp PROPERTIES
        CUDA_STANDARD 17
        CUDA_STANDARD_REQUIRED ON
        CUDA_SEPARABLE_COMPILATION OFF
        CUDA_RUNTIME_LIBRARY Static
        CUDA_ARCHITECTURES "75;86;89"
    )
endif()

if(MSVC)
    target_compile_options(
        algorithm_cpp PRIVATE
        $<$<COMPILE_LANGUAGE:CXX>:/O2 /EHsc /W3>
    )
else()
    target_compile_options(
        algorithm_cpp PRIVATE
        $<$<COMPILE_LANGUAGE:CXX>:-O3>
    )
endif()

install(TARGETS algorithm_cpp LIBRARY DESTINATION modules/nesting_engine)
