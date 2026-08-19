# REFERENCIA HISTÓRICA — CAMBIOS DE LA BASE R4 240X96

> Este documento describe la base R4 recibida. Para la versión activa R3 consulte `CAMBIOS_R3_240X96.md`.

# Cambios respecto a la base 240x48

1. Se agregó el perfil activo 240X96 y reconocimiento de 6096 x 2438/2438.4 mm.
2. Se agregó normalización del nesting antes de entradas y mapa E1/J2.
3. Se reemplazó la agrupación de columnas por bounding-box con anchors y banda fija de 100 mm.
4. Se deshabilitó la agrupación compartida de barrenos; cada barreno conserva E1 fijo propio.
5. Se cambió el orden de barrenos a X_DXF -> Y_DXF -> distancia.
6. Se integró el mapa A/B/C/D/F con preferencia de 65% para C y F.
7. Se cambió el cálculo de E1 a nodo inferior más cercano + desplazamiento X_DXF.
8. Se agregó auditoría J2: riesgo desde 55°, límite 65°.
9. Se corrigió C4 a J2=9.682°; B4 y F permanecen sin corrección.
10. Se conserva la entrada interna de 2 mm x 2 mm y la entrada de barreno +3 mm X_DXF.
