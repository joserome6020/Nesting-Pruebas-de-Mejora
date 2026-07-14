cmake_minimum_required(VERSION 3.18)
project(arga_nesting_lab LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)

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

set(CLIPPER2_CPP_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../../modules/nesting_engine/cpp/third_party/Clipper2/CPP")
if(NOT EXISTS "${CLIPPER2_CPP_DIR}/CMakeLists.txt")
    message(FATAL_ERROR "Clipper2 no encontrado. Ejecuta build_lab_engine.ps1")
endif()

add_subdirectory("${CLIPPER2_CPP_DIR}" "${CMAKE_BINARY_DIR}/clipper2_cpp")

pybind11_add_module(algorithm_cpp_lab MODULE bindings_lab.cpp packer_lab.cpp)

target_include_directories(
    algorithm_cpp_lab PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CLIPPER2_CPP_DIR}/Clipper2Lib/include
)

target_link_libraries(algorithm_cpp_lab PRIVATE Clipper2)
target_compile_definitions(algorithm_cpp_lab PRIVATE ARGA_LAB_SIMULATOR=1)

if(MSVC)
    target_compile_options(algorithm_cpp_lab PRIVATE /O2 /EHsc /W3)
else()
    target_compile_options(algorithm_cpp_lab PRIVATE -O3)
endif()
