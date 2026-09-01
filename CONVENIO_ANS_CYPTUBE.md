# Convenio ANS ↔ CypTube — Nesteos de cobre

Espejo del convenio en `C:\Proyectos\Cobre - CypTube\CONVENIO_ANS_CYPTUBE.md`.  
Fuente de verdad del contrato de carpetas/JSON: ambos archivos deben decir lo mismo.

Objetivo: al terminar la exportación de cobre en ANS, CypTube genera `.ctds` (Corte + Marcaje) vía RPA.

---

## Verificación ANS → CypTube (2026-09-01, chat ANS)

Estado del **gatillo Modo B** revisado en código (no sustituye prueba en planta):

| Paso | Estado |
|------|--------|
| Export cobre escribe DXF Corte/Marcaje + `cyptube_verticales.json` | OK (`cyptube_vertical.py` + `exporter.py`) |
| Tras el JSON, ANS llama `launch_cyptube_auto_nest` | OK (`exporter.py` post-manifiesto) |
| Comando: `python <cyptube_main> auto-nest --nesteos-dir … --skip-wait` | OK (`cyptube_bridge.py`) |
| Espera antes del RPA (`launch_delay_s`, default **15 s**) | OK (modales ANS no estorban) |
| Config `_config/cyptube_bridge.json` (`enabled: true`, ruta main CypTube) | OK · `main.py` CypTube existe en disco |
| Local + UNC servidor 80 (`path_maps` opcional) | OK candados `test_cyptube_bridge.py` |
| CypTube CLI `auto-nest --skip-wait` / `--dry-run` | OK (`main.py` + `rpa/auto_nest.py` → RPA `completo-batch`) |

**Pendiente planta (misma PC con Friendess + CypTube elevado):**

1. Exportar W.O. cobre real (local y/o servidor).
2. En log ANS buscar: `[CyPTube] auto-nest lanzado destino=…`
3. Consola CypTube / CTDS en esa misma carpeta `NESTEOS DE COBRE`.
4. Opcional: `python main.py auto-nest --nesteos-dir "…" --dry-run` antes del RPA.

Si el RPA no arranca: confirmar `enabled`, ruta `cyptube_main`, y que Friendess esté instalado (RPA elevado).

---

## Respuestas ANS (2026-09-01)

1. **¿Ejecutar `auto-nest` al terminar el export?**  
   - [x] **Sí, en la misma PC del ANS** (Modo B).  
   - Tras escribir `cyptube_verticales.json`, ANS lanza en consola nueva:  
     `python <cyptube_main> auto-nest --nesteos-dir "<NESTEOS DE COBRE>" --skip-wait`  
   - Config: `_config/cyptube_bridge.json` · módulo `modules/dxf_export/cyptube_bridge.py`  
   - Off: `"enabled": false` o `ARGA_CYPTUBE_AUTO_NEST=0`  
   - Requiere Friendess CypTube + repo CypTube en esa PC (RPA elevado).

2. **¿Ruta final = `…/NESTEOS DE COBRE/` + `cyptube_verticales.json`?**  
   **Sí — en local y en servidor 80.** Misma estructura bajo:
   - Local: `…\Nesteos Locales\…\NESTING\NESTEOS DE COBRE\`
   - Servidor: `\\192.168.2.80\…\TANKS\…\NESTING\NESTEOS DE COBRE\`  
   El gatillo pasa **la ruta absoluta real** del export (no solo local).

3. **¿JSON incluye `A_mm` / `B_mm` / `canal`?**  
   **Sí, ya.** Por barra: `A_mm = ancho+0.2`, `B_mm = 6.0`, `canal` = `AMADA/VERTICAL` o `NESTEOS DE COBRE`.

4. **¿Primero DXF y al final el JSON?**  
   **Sí.** Luego dispara el bridge con esa carpeta.

5. **¿AMADA/VERTICAL y DXF normal en el mismo JSON con `canal` distinto?**  
   **Sí.**

6. **¿Callback ANS al terminar CTDS?**  
   **Nada por ahora.** Evidencia = `CTDS/` + VSM.

### Destinos duales (local + servidor 80) — obligatorio para CypTube

| Switch ANS | Destino | `--nesteos-dir` que recibe CypTube |
|------------|---------|-------------------------------------|
| EXPORTAR A SERVIDOR Y BD **ON** | UNC `\\192.168.2.80\…` | Esa carpeta `NESTEOS DE COBRE` en el job del server |
| Switch **OFF** | Nesteos Locales | Esa carpeta bajo OneDrive/Desktop |

- CTDS se escriben **junto** al nesteo (misma raíz local o UNC).
- Log ANS: `[CyPTube] auto-nest lanzado destino=servidor|local …`
- Remapeo opcional si el RPA ve otra letra: `path_maps` en `cyptube_bridge.json`.

### Comunicación planta

| Modo | Estado |
|------|--------|
| **B — llamada post-export** | **Activo** (local **y** servidor) |
| A — `auto-watch` | Reserva (`--modo local` o `servidor`) |

### Switch ANS: cobre sin marcaje (`cu_sin_marcaje`)

- Configuración Global → **COBRE — sin marcaje (solo corte)**.
- **OFF:** DXF con MARK + split `*_Corte` + `*_Marcaje` (RPA completo).
- **ON (default):** cobre sin MARK; JSON con `sin_marcaje: true` y solo `*_Corte.dxf`.
  CypTube `completo-batch` procesa corte y omite marcaje si no hay `marcaje_path`.

---

## Contrato (resumen acordado)

| Ítem | Valor |
|------|--------|
| Carpeta | `NESTEOS DE COBRE` (local o UNC) |
| Manifiesto | `cyptube_verticales.json` (sello al final) |
| DXF | `<base>_Corte.dxf` + `<base>_Marcaje.dxf` |
| CTDS (CypTube) | `<base>_Corte.ctds` + `<base>_Marcaje.ctds` en la misma raíz |
| Comunicación | **Modo B** (`cyptube_bridge`) |
| Código ANS | `cyptube_vertical.py` + `cyptube_bridge.py` + `exporter.py` |
| Candados | `test_cyptube_vertical_split.py`, `test_cyptube_bridge.py` |
| Config | `_config/cyptube_bridge.json` |

---

## Checklist de prueba conjunta

- [ ] Export **local** cobre → auto-nest con ruta Nesteos Locales → CTDS ahí.
- [ ] Export **servidor 80** cobre → auto-nest con UNC → CTDS bajo el job en red.
- [ ] `auto-nest --dry-run` lista pendientes en ambos destinos.
- [ ] `python main.py vsm` abre un CTDS (local y/o servidor).

---

## Comandos CypTube

| Acción | Comando |
|--------|---------|
| Disparo ANS (ambas rutas) | `auto-nest --nesteos-dir "<ruta absoluta NESTEOS DE COBRE>" --skip-wait` |
| Vigilante local | `python main.py auto-watch --modo local` |
| Vigilante servidor | `python main.py auto-watch --modo servidor` |
| Operador | `python main.py vsm` |

Repo CypTube: `https://github.com/grupoaragoncuu-beep/CypTube-Automatico`
