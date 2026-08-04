/* ArgaNestCore — ABI C estable
 * Consumible desde Python (pybind), C#, o ArgaNestWorker.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef _WIN32
#  ifdef ARGA_NEST_CORE_EXPORTS
#    define ARGA_NEST_API __declspec(dllexport)
#  else
#    define ARGA_NEST_API __declspec(dllimport)
#  endif
#else
#  define ARGA_NEST_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

enum ArgaNestStatus {
    ARGA_NEST_OK = 0,
    ARGA_NEST_E_INVALID_ARG = 1,
    ARGA_NEST_E_PARSE = 2,
    ARGA_NEST_E_ENGINE = 3,
    ARGA_NEST_E_NO_MEMORY = 4,
    ARGA_NEST_E_INTERNAL = 5,
    ARGA_NEST_E_CERTIFY = 6
};

ARGA_NEST_API int arga_nest_version_major(void);
ARGA_NEST_API int arga_nest_version_minor(void);
ARGA_NEST_API int arga_nest_version_patch(void);
ARGA_NEST_API int arga_nest_version_string(char** out_utf8);

/** Empaca una hoja (JSON). Incluye certify fail-closed en la respuesta. */
ARGA_NEST_API int arga_nest_pack_sheet_json(
    const char* request_json,
    char** out_response_json);

/** Multi-placa (JSON in/out). */
ARGA_NEST_API int arga_nest_pack_job_json(
    const char* request_json,
    char** out_response_json);

/** Nesting de tira de cobre. */
ARGA_NEST_API int arga_nest_pack_cu_strip_json(
    const char* request_json,
    char** out_response_json);

/** Export DXF ASCII desde un pack response o request+pack interno. */
ARGA_NEST_API int arga_nest_export_dxf_json(
    const char* request_json,
    char** out_dxf_utf8);

/**
 * Certifica DXF ASCII post-export.
 * request_json: {"dxf":"..."}  → JSON {ok, entity_count, closed_outers, common_cut_segments, issues}
 */
ARGA_NEST_API int arga_nest_certify_dxf_json(
    const char* request_json,
    char** out_json);

/** Export STEP ASCII mínimo. */
ARGA_NEST_API int arga_nest_export_step_json(
    const char* request_json,
    char** out_step_utf8);

/** Stats caché NFP L1. */
ARGA_NEST_API int arga_nest_nfp_cache_stats_json(char** out_json);
ARGA_NEST_API void arga_nest_nfp_cache_reset(void);

/** Estado CUDA del build/runtime (JSON). */
ARGA_NEST_API int arga_nest_cuda_status_json(char** out_json);

ARGA_NEST_API const char* arga_nest_last_error(void);
ARGA_NEST_API void arga_nest_free(void* p);

#ifdef __cplusplus
}
#endif
