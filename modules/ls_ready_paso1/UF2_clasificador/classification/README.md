# Módulos de clasificación

La estructura proviene del clasificador LS-ready v3 estable de 240x48.

- `classifier.py`: flujo raw JSON -> piezas -> LS-ready.
- `nesting_normalization.py`: mueve el área útil completa antes de entradas/mapa.
- `ls_ready_v3.py`: orden, entradas, E1/J2 y contratos del generador.
- `piece_plan.py`: plan local; para 240x96 cada barreno es un path individual.
- `figure_components.py` y `stroke_optimizer.py`: reconstrucción/optimización de figuras.
- `text_block.py`: clasificación y bloque continuo de texto.

El perfil activo es `config/plate_profiles/profile_240x96.py`.
