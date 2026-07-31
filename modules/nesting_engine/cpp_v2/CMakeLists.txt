cmake_minimum_required(VERSION 3.18)
project(arga_nesting_cpp_v2 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)
include(CheckLanguage)

option(ARGA_CPP_V2_ENABLE_CUDA "Construye el filtro raster CUDA opcional" ON)
set(ARGA_CPP_V2_HAS_CUDA 0)
if(ARGA_CPP_V2_ENABLE_CUDA)
    # winget instala CUDA pero no siempre refresca PATH de la sesión actual.
    # Localizamos nvcc explícitamente para que el build siga siendo opcional.
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
            # CUDA 13 vía winget no siempre escribe InstallDir en el registro.
            # MSBuild necesita esta propiedad incluso cuando nvcc fue localizado.
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
        set(ARGA_CPP_V2_HAS_CUDA 1)
        message(STATUS "ARGA cpp_v2: CUDA raster filter enabled")
    else()
        message(STATUS "ARGA cpp_v2: CUDA Toolkit not found; using CPU fallback")
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

# Reutiliza Clipper2 de producción (mismo patrón que LAB SIMULATOR).
set(CLIPPER2_CPP_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../cpp/third_party/Clipper2/CPP")
if(NOT EXISTS "${CLIPPER2_CPP_DIR}/CMakeLists.txt")
    message(FATAL_ERROR "Clipper2 no encontrado en ${CLIPPER2_CPP_DIR}. Ejecuta build_cpp_v2_engine.ps1 o build_cpp_engine.ps1")
endif()

add_subdirectory("${CLIPPER2_CPP_DIR}" "${CMAKE_BINARY_DIR}/clipper2_cpp")

set(
    ARGA_CPP_V2_SOURCES
    bindings_v2.cpp
    packer_v2.cpp
    cuda/raster_filter.cpp
)
if(ARGA_CPP_V2_HAS_CUDA)
    # Compilar el kernel directamente dentro del .pyd evita el device-link
    # incompleto que produce una librería CUDA estática en MSBuild.
    list(APPEND ARGA_CPP_V2_SOURCES cuda/raster_filter.cu)
endif()

pybind11_add_module(algorithm_cpp_v2 MODULE ${ARGA_CPP_V2_SOURCES})

target_include_directories(
    algorithm_cpp_v2 PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CLIPPER2_CPP_DIR}/Clipper2Lib/include
)

target_link_libraries(algorithm_cpp_v2 PRIVATE Clipper2)
target_compile_definitions(
    algorithm_cpp_v2 PRIVATE
    ARGA_CPP_V2_POC=1
    ARGA_CPP_V2_HAS_CUDA=${ARGA_CPP_V2_HAS_CUDA}
)
if(ARGA_CPP_V2_HAS_CUDA)
    # El kernel no llama funciones device de otros objetos: RDC no es necesario.
    target_link_libraries(algorithm_cpp_v2 PRIVATE CUDA::cudart)
    set_target_properties(
        algorithm_cpp_v2 PROPERTIES
        CUDA_STANDARD 17
        CUDA_STANDARD_REQUIRED ON
        CUDA_SEPARABLE_COMPILATION OFF
        # Evita requerir cudart64_130.dll en equipos que compilen o ejecuten
        # el PoC sin Toolkit; el runtime devuelve fallback CPU si no hay driver.
        CUDA_RUNTIME_LIBRARY Static
        # Fat binary para GPUs NVIDIA comunes; PCs no CUDA usan el fallback CPU.
        CUDA_ARCHITECTURES "75;86;89"
    )
endif()

if(MSVC)
    # No pasar flags MSVC como entradas crudas a nvcc; CUDA recibe sus propios
    # flags del toolset de Visual Studio.
    target_compile_options(
        algorithm_cpp_v2 PRIVATE
        $<$<COMPILE_LANGUAGE:CXX>:/O2>
        $<$<COMPILE_LANGUAGE:CXX>:/EHsc>
        $<$<COMPILE_LANGUAGE:CXX>:/W3>
    )
    target_compile_definitions(algorithm_cpp_v2 PRIVATE _CRT_SECURE_NO_WARNINGS)
else()
    target_compile_options(algorithm_cpp_v2 PRIVATE -O3)
endif()
