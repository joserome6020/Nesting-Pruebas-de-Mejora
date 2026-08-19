# Revisión de la primera V1 simplificada

La primera entrega 240x96 no debía considerarse reemplazo del clasificador 240x48 porque omitía estas bases:

- lector DXF oficial;
- perfiles de placa y registro;
- asignación de geometría a piezas;
- clasificación de contornos internos y barrenos;
- separación de texto y figuras;
- texto continuo;
- reconstrucción de figuras por endpoints;
- plan local y global;
- contratos `generator_path_dxf`;
- validaciones LS-ready;
- selector BAT, logs y pruebas.

La versión actual usa el paquete 240x48 como base directa y conserva esos módulos. Solo se adaptaron las reglas específicas de 240x96.
