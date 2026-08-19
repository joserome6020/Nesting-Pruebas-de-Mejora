# REVISIÓN DEL GENERADOR PARA R3

La transformación geométrica no cambia: `X_UF2 = Y_DXF` y `Y_UF2 = 6096 - X_DXF`.
No obstante, el generador R4 adjunto no es intercambiable sin cambios porque:

- fuerza `robot_id = 4`;
- rechaza un JSON cuyo `robot_programming.robot` sea 3;
- etiqueta reportes y perfil como R4;
- contiene una plantilla fija P[1]..P[6];
- P[2] es un punto joint medido para R4 (`J1..J6`), no una coordenada relativa al UF;
- el estado inicial de E1 de la escuadra también debe confirmarse físicamente para R3.

Para liberar el generador R3 se necesita confirmar la plantilla inicial de R3, especialmente P[2] y, de preferencia, los P[1]..P[6] exportados desde un programa base funcional del robot 3. Las operaciones posteriores sí pueden conservar la misma lógica y consumir directamente el nuevo mapa R3 del clasificador.
