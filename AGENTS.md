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
7. Actualiza el **Changelog** de `AGENT_TRACKING.md` al cerrar la sesión.
