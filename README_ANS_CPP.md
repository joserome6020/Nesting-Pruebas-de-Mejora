# ANS C++ (Arga Nesting Suite — Native Core Line)

**Core:** ArgaNestCore **0.3.0** · ABI **1.2.0** · **CUDA enabled**  
**Estado:** Fases A–D + pendientes de profundidad verificados

## Verificar

```powershell
cd "C:\Proyectos\ANS C++"
python tests\native\test_suite_ans_cpp.py
python benchmarks\arga_nest_core_bench.py
$env:ARGA_NEST_CORE="1"
$env:ARGA_NEST_CUDA="1"
python main.py
```

Docs: `AGENT_TRACKING.md` · `SDK.md` · `AGENTS.md`
