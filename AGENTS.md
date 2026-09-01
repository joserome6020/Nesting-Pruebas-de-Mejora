# Instrucciones para agentes de IA — New Arga Nesting Suite

1. Lee primero [`AGENT_TRACKING.md`](AGENT_TRACKING.md) (estado vivo + checklist).
2. Lee [`ARCHITECTURE.md`](ARCHITECTURE.md) antes de tocar el núcleo nativo.
3. **Único árbol de trabajo:** `C:\Proyectos\New Arga Nesting Suite`.
   **No** copies ni sincronices a `ANS C++` ni a otro clon. Esa regla está
   **eliminada**; no la reintroduzcas.
4. Default runtime = motor legacy (`algorithm_cpp`). Core nuevo con `ARGA_NEST_CORE=1`.
5. Tras cambios en C++: recompilar y correr `python tests/native/smoke_arga_nest_core.py --require-core`.
6. **Todo bug corregido deja un candado en `tests/native/run_regresiones.py`.**
   Escribe el test con el caso real que lo motivó, comprueba que *falla* con el
   código viejo y agrégalo a la lista. Un bug sin candado vuelve.
7. Corre `python tests/native/run_regresiones.py` (sin BD ni core) antes de
   cerrar sesión o publicar build. Debe salir `REGRESIONES PASS`.
8. Antes de reimplementar una regla de negocio (cantidades, factores, WO/SWO,
   costos), busca si ya existe en `api/legacy_core.py` y **reúsala**. Los bugs
   de este tipo han venido de tener dos caminos que calculan lo mismo distinto.
9. **Paridad del .exe con cada mejora:** al modificar el ANS, valida si
    [`tools/build_arga_exe.py`](tools/build_arga_exe.py) empaqueta/compila ese
    cambio (imports, `.pyd`/Worker, assets, `_config`, defaults de `main.py`).
    Si no, **arregla el build en el mismo cambio** para que no se quede atrás.
    Ver `.cursor/rules/build-exe-parity.mdc`.
9b. **Release = build + publicar + versionar:** si el usuario pide un release /
    zip de Release, además del `--release` local hay que: (1) commit+push de
    lo que entra, (2) **actualizar `main` en remoto** (merge/FF desde la
    rama de trabajo), (3) publicar el artefacto con tag apuntando al
    **commit del build** (`publish_release.py` usa `--target`),
    (`python tools/publish_release.py --github --repo joserome6020/Nesting-Pruebas-de-Mejora`
    o el UNC indicado) y devolver la URL del tag nuevo. No dejar el zip solo
    en `dist/releases/` ni dejar `main` congelada. Ver
    `.cursor/rules/release-publish.mdc`.
9c. **Release = ANS cerrado + empaquetado 100%:** cerrar el Suite antes del
    build; smoke sin `--skip-smoke` debe cubrir módulos de runtime (p. ej.
    `engine.step_paths` / Crear STEPs, buzón). Si falta algo en el bundle,
    arreglar `tools/build_arga_exe.py` y **repetir** hasta checklist OK.
    Ver `.cursor/rules/release-complete-packaging.mdc`.
10. **Tras corregir un bug: commit local + push remoto obligatorio** en
    este repo (GitHub). No dejes el fix solo en disco: otras PCs y builds se
    actualizan desde el remoto. Incluye el candado, `AGENTS.md` /
    `AGENT_TRACKING.md` si cambiaron, y verifica `git status -sb` que HEAD =
    `origin/<rama>`.
11. Actualiza el **Changelog** de `AGENT_TRACKING.md` al cerrar la sesión.
