# Instrucciones para agentes de IA — ANS C++

1. Lee primero [`AGENT_TRACKING.md`](AGENT_TRACKING.md) (estado vivo + checklist).
2. Lee [`ARCHITECTURE.md`](ARCHITECTURE.md) antes de tocar el núcleo nativo.
3. Trabaja **solo** en `C:\Proyectos\ANS C++`. No modifiques `C:\Proyectos\New Arga Nesting Suite`.
4. Default runtime = motor legacy (`algorithm_cpp`). Core nuevo con `ARGA_NEST_CORE=1`.
5. Tras cambios en C++: recompilar y correr `python tests/native/smoke_arga_nest_core.py --require-core`.
6. Actualiza el **Changelog** de `AGENT_TRACKING.md` al cerrar la sesión.
