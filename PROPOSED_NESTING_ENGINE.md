# Propuestas de Arquitectura para el Nuevo Motor de Nesting (C++)

Este documento detalla las propuestas teóricas, algoritmos y la arquitectura técnica sugerida para desarrollar el **nuevo motor de nesting en C++**. 
Está diseñado para que Cursor (y cualquier desarrollador del equipo) entienda los conceptos clave antes de empezar a programar el núcleo matemático.

## Objetivo
Superar la velocidad y calidad de motores open-source actuales (libnest2d, Deepnest) resolviendo el principal cuello de botella: **El cálculo y evaluación de polígonos irregulares (No-Fit Polygon o NFP).**

---

## 1. El Problema Actual (Motores Clásicos)
Los motores clásicos sufren de complejidad $O(n^2 m^2)$ al calcular intersecciones exactas (flotantes) en la CPU usando librerías como Clipper o Boost.Geometry. En piezas con cientos de vértices, y probando miles de rotaciones mediante Algoritmos Genéticos (GA), la CPU se ahoga.

---

## 2. Propuestas Técnicas y Soluciones (Las 4 Hipótesis)

### Hipótesis 1: Aceleración por GPU y Rasterización (El "Game Changer")
En lugar de matemáticas poligonales, usar la GPU para operaciones lógicas sobre mapas de bits.

- **Concepto:** Convertir las piezas (polígonos) en grillas 2D de píxeles (Rasterización) en una matriz booleana. `1` = material, `0` = vacío.
- **Implementación (OpenCL o CUDA):**
  - Cargar las piezas como texturas o matrices booleanas en la memoria de la GPU.
  - Para saber si dos piezas colisionan, la GPU ejecuta una operación `AND` a nivel de bits (Bitwise AND) entre las dos matrices superpuestas. Si el resultado es `> 0`, hay colisión.
  - Las GPUs están diseñadas para hacer millones de estas operaciones matemáticas simples por segundo.
- **Flujo:** 
  1. Rasterizar piezas.
  2. GPU busca las zonas viables rápidamente y devuelve una "nube de puntos" válida.
  3. CPU realiza el ajuste poligonal exacto (flotantes) **solo en las posiciones viables que devolvió la GPU**.

### Hipótesis 2: Simplificación Jerárquica Dinámica (Level of Detail - LOD)
No procesar piezas a máxima resolución si no es necesario.
- **Fase 1 (AABB - Axis-Aligned Bounding Box):** Rectángulo perfecto. Filtro hiper-rápido para descartar choques lejanos.
- **Fase 2 (Convex Hull):** Envoltura convexa. Elimina las concavidades. Útil para ubicar piezas rápidamente antes de meter otras en sus huecos.
- **Fase 3 (Simplificación de Douglas-Peucker):** Reducir vértices de un polígono cóncavo (ej. de 1000 vértices a 30) manteniendo la forma general tolerando 0.5mm de error. **El 90% de las iteraciones del algoritmo genético deben correr con esta versión.**
- **Fase 4 (Full Detail):** Solo se usa el polígono original con resolución real cuando el algoritmo ha decidido la posición cuasi-final.

### Hipótesis 3: Sembrado Heurístico con Inteligencia Artificial
El Algoritmo Genético (GA) clásico empieza "a ciegas" (poblaciones con orden y rotación aleatoria).
- **Solución:** Implementar un clasificador ligero (ML, KNN, o Red Neuronal pequeña) que analice las áreas y las "Bounding Boxes" de las piezas de la orden.
- **Reglas Heurísticas Base:** 
  1. Ordenar por Área (Descendente) o Perímetro.
  2. Emparejar piezas con concavidades grandes con piezas pequeñas que quepan en ellas antes de empezar el anidado.
- El modelo ML "sugiere" las primeras 5 poblaciones iniciales del Algoritmo Genético, garantizando que el punto de partida ya tenga un 80% de eficiencia, ahorrando miles de ciclos muertos.

### Hipótesis 4: Pre-cálculo y Caché de NFP
La mayoría de las fábricas cortan perfiles y formas estandarizadas.
- **Estructura:** Tabla Hash o SQLite local en memoria (`std::unordered_map` en C++).
- **Llave:** `Hash(GeometriaPiezaA) + Hash(GeometriaPiezaB) + AnguloRelativo`.
- **Valor:** El No-Fit Polygon resultante (el contorno de encaje).
- **Lógica:** Antes de calcular matemáticamente el choque entre dos piezas, el motor revisa si esa combinación exacta ya se calculó en este lote o en lotes pasados. Si es así, recupera el NFP en `O(1)`.

---

## 3. Pila Tecnológica Sugerida para el Motor C++
- **Lenguaje:** C++20 (Para aprovechar las últimas features de concurrencia y ranges).
- **Binding a Python:** `pybind11` o `nanobind` (más moderno y rápido) para compilar el motor como una librería importable en Python (`import new_arga_engine`).
- **Librería de Geometría (Respaldo CPU):** Clipper2 (mucho más rápida que Clipper1) o Boost.Geometry.
- **Concurrencia:** OpenMP para paralelizar la evaluación de poblaciones del Algoritmo Genético en CPU, y OpenCL/Vulkan Compute para la Fase 1 (Rasterización).

## 4. Próximos pasos para Cursor
Cuando se inicie el desarrollo de este motor, empezar con:
1. **Un prototipo de Python a C++ (Binding):** Enviar un polígono de Python a C++ y regresarlo intacto.
2. **Prueba de Concepto (PoC) de NFP usando Clipper2:** Calcular el encaje de 2 polígonos básicos.
3. **PoC de Simplificación (LOD):** Implementar el algoritmo de Douglas-Peucker en C++ para reducir vértices en la memoria de carga inicial.
