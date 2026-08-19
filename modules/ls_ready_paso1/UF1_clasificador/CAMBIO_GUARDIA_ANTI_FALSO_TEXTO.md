# Guardia anti-falso-texto

Se agregó un guardia post-armado en `classification/piece_plan.py`.

- Texto real compacto, con dimensión larga `<= TEXT_COMPONENT_MAX_LONG_DIM_MM` (700 mm): conserva `mark_text` y E1 fijo.
- Bloques dispersos detectados erróneamente como texto, con dimensión larga `> 700 mm`: se regresan a figuras y usan la política de figuras (`follow_y`).
- No se modificó la lógica de barrenos C+C, normalización, orden de piezas ni mapas E1/J2.
