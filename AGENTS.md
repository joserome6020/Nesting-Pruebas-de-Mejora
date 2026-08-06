# Instrucciones para agentes de IA — ANS C++

1. Lee primero [`AGENT_TRACKING.md`](AGENT_TRACKING.md) (estado vivo + checklist).
2. Lee [`ARCHITECTURE.md`](ARCHITECTURE.md) antes de tocar el núcleo nativo.
3. **Correcciones de bugs: aplícalas SIEMPRE en los dos proyectos**,
   `C:\Proyectos\ANS C++` y `C:\Proyectos\New Arga Nesting Suite`. Ambos están en
   producción; arreglar solo uno deja el error vivo. Verifica con
   `Compare-Object` que los archivos queden iguales.
4. Trabajo **nuevo** (features, experimentos, migración C++): solo en
   `C:\Proyectos\ANS C++`.
5. Default runtime = motor legacy (`algorithm_cpp`). Core nuevo con `ARGA_NEST_CORE=1`.
6. Tras cambios en C++: recompilar y correr `python tests/native/smoke_arga_nest_core.py --require-core`.
7. **Todo bug corregido deja un candado en `tests/native/run_regresiones.py`.**
   Escribe el test con el caso real que lo motivó, comprueba que *falla* con el
   código viejo y agrégalo a la lista. Un bug sin candado vuelve.
8. Corre `python tests/native/run_regresiones.py` (sin BD ni core) antes de
   cerrar sesión o publicar build. Debe salir `REGRESIONES PASS`.
9. Antes de reimplementar una regla de negocio (cantidades, factores, WO/SWO,
   costos), busca si ya existe en `api/legacy_core.py` y **reúsala**. Los bugs
   de este tipo han venido de tener dos caminos que calculan lo mismo distinto.
10. **Tras corregir un bug: commit local + push remoto obligatorio** en
    `New Arga Nesting Suite` (GitHub). No dejes el fix solo en disco: otras PCs
    y builds se actualizan desde el remoto. Incluye el candado, `AGENTS.md` /
    `AGENT_TRACKING.md` si cambiaron, y verifica `git status -sb` que HEAD =
    `origin/<rama>`. `ANS C++` no es repo git: copia los archivos y confirma
    igualdad con `Compare-Object`.
11. Actualiza el **Changelog** de `AGENT_TRACKING.md` al cerrar la sesión.
