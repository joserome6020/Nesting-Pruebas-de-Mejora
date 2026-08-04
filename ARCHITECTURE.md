# ArgaNestCore — Arquitectura (ANS C++)

## Visión

ANS C++ es la línea de evolución profesional de Arga Nesting Suite.
El producto sigue siendo usable en Python/Qt, pero el **cómputo de nesting y CAD crítico** migra a un núcleo nativo versionado: **ArgaNestCore**.

```
┌──────────────────────────────────────────────────────────┐
│  PySide6 UI · FastAPI · Herinox · Reportes PDF           │  Python (negocio)
├──────────────────────────────────────────────────────────┤
│  manager.py · export orchestration · WO/ERP rules        │  Python (orquestación)
├──────────────────────────────────────────────────────────┤
│  arga_nest_core_bridge.py  ←→  algorithm_bridge.py       │  Fachada (compat)
├──────────────────────────────────────────────────────────┤
│  arga_nest_core.pyd / ArgaNestCore.dll                   │  ★ NÚCLEO PRODUCTO
│    · IPackerEngine (Ultra, FORCE, Burke, …)              │
│    · NFP cache · LOD · CUDA filter · certifier           │
│    · (futuro) CuStripPacker · multi-plate policy         │
├──────────────────────────────────────────────────────────┤
│  ArgaNestWorker.exe (IPC)                                │  Proceso aislado
└──────────────────────────────────────────────────────────┘
```

## ABI estable (C)

Header: `native/ArgaNestCore/include/arga_nest/abi.h`

Principios:

- API C plana (`extern "C"`) para poder consumir desde Python, C#, o un worker.
- Versionado semántico en `arga_nest_version_*`.
- Entrada/salida en buffers JSON UTF-8 (Fase A) → más adelante MessagePack/binario.
- Códigos de error numéricos documentados (`ARGA_NEST_OK`, `ARGA_NEST_E_*`).

### Funciones mínimas Fase A

| Función | Rol |
|---------|-----|
| `arga_nest_version_string` | Identidad del build |
| `arga_nest_pack_sheet_json` | Empacar una hoja (JSON in/out) |
| `arga_nest_last_error` | Mensaje del último fallo |
| `arga_nest_free` | Liberar buffers del core |

## Motores internos (C++)

Namespace `arga::core`:

- `IPackerEngine` — interfaz común
- `UltraEngine` — envuelve `empaquetar_una_hoja_svgnest_ultra`
- `ForceEngine` — envuelve `empaquetar_una_hoja_base` (FORCE)
- `EngineRegistry` — selecciona por id (`svgnest_ultra`, `arga_force`, …)

Las implementaciones **reutilizan** el código en `modules/nesting_engine/cpp/` (no duplicar packers).
`cpp_v2` se fusiona por detrás cuando pase benches.

## Bridge Python

`modules/nesting_engine/arga_nest_core_bridge.py`:

1. Intenta importar `arga_nest_core` (módulo nuevo).
2. Si `ARGA_NEST_CORE=1` y el módulo existe → usa core nuevo.
3. Si no → delega a `algorithm_bridge` / `algorithm_cpp` (comportamiento ANS clásico).

`engine_registry` y la UI no deben romperse: el default sigue siendo el path legacy hasta promoción explícita.

## Worker

`native/ArgaNestWorker/`:

- Fase A: stub CLI (`--version`, `--ping`).
- Fase D: proceso hijo con stdin/stdout JSON o named pipe; la UI no carga el packer in-process.

## Relación con el ANS original

| | Original | ANS C++ |
|--|----------|---------|
| Path | `C:\Proyectos\New Arga Nesting Suite` | `C:\Proyectos\ANS C++` |
| Rol | Producción / línea estable | Evolución nativa |
| Motor | `algorithm_cpp.pyd` | `algorithm_cpp` + `arga_nest_core` |
| Sync | Independientes | Copiar fixes puntuales a mano si hace falta |

## Decisiones ya tomadas

1. **No** reescribir UI/ERP/API en C++ en Fases A–C.
2. Binding: **pybind11** (igual que legacy); nanobind evaluable después.
3. Geometría: **Clipper2** (ya vendored bajo `modules/nesting_engine/cpp/third_party`).
4. CUDA: opcional; CPU siempre como fallback.
5. Cobre: motor hermano dentro del mismo DLL a medio plazo (`CuStripPacker`), no script paralelo eterno.

## Referencias

- Plan original de producto: conversación Cursor 2026-07-31 (Fases A–D).
- `PROPOSED_NESTING_ENGINE.md` — hipótesis NFP/GPU/LOD/ML.
- `modules/nesting_engine/cpp/README.md` — motores acero actuales.
- `benchmarks/DECISION_NEXT.md` — gates de `cpp_v2`.
- `docs/cpp_migration/AGENT_TRACKING.md` — estado vivo.
