# ANS C++ — Seguimiento (cpp_migration)

> **Versión:** ArgaNestCore **0.5.3** · ABI **1.4.0**  
> **IA:** L1+L3-lite determinista · A/B **0/6/0 success** · UI default **off**

## Estado

| Frente | Estado |
|--------|--------|
| 3 frentes Q/V/D | HECHO |
| IA L1+L3 | HECHO opt-in (Burke preserve_order + ga_seed) |

## Verificar

```powershell
py -3.14 tests\native\test_ai_determinism.py
py -3.14 benchmarks\ai_ranker_ab.py --calibrate-pack --trials 1
```

## Changelog

### 2026-08-03c

- Core 0.5.3: Burke seed + preserve_order; A/B estable 0/6/0
