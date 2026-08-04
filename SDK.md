# ArgaNestCore SDK (headless) — 0.3.0

## CUDA
```python
sdk.cuda_status()  # build_has_cuda / runtime_available
# Opt-in filtro GPU en motores: set ARGA_NEST_CUDA=1
```

## STEP OCCT
```python
sdk.export_step(request, prefer_occt=True, out_path=r"out.step")
```

## Benches
```powershell
python benchmarks\arga_nest_core_bench.py --json benchmarks\baselines\arga_nest_core_last.json
```

## Auto-update
```powershell
python native\python\auto_update.py
```
Ver `native\update_manifest.example.json` para codesign.
