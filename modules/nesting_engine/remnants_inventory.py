"""Inventario de remanentes de placa → PlateSpec para pack_job.

Fuentes (ARGA_NEST_REMNANTS_SOURCE=auto|postgres|csv):
  - postgres: tabla public.nesting_remanentes_placa (stock vivo)
  - csv: inventario_remanentes.csv (fallback / sync)
  - auto: intenta Postgres; si vacío o error → CSV

IDs internos de inventario (NO son SKU Herinox): PL-CARB-0.313-40-48
En UI/nest se exponen como REM-{calibre}-{W}x{H} para no confundir con catálogo.
Inyección al nest: opt-in ARGA_NEST_REMNANTS_IN_NEST=1 (default OFF).
"""
from __future__ import annotations

import csv
import os
import re
from pathlib import Path
from typing import Any

IN_TO_MM = 25.4
_ID_RE = re.compile(
    r"^PL-([A-Za-z0-9]+)-([\d.]+)-([\d.]+)-([\d.]+)$",
    re.IGNORECASE,
)

# Códigos cortos en ID de inventario → nombre de material de planta (no SKU Herinox).
_MATERIAL_ALIASES = {
    "CARB": "CARBONO",
    "CARBON": "CARBONO",
    "CARBONO": "CARBONO",
    "SS": "INOX",
    "INOX": "INOX",
    "AL": "ALUMINIO",
    "ALU": "ALUMINIO",
    "CU": "COBRE",
    "COBRE": "COBRE",
}

# ui_placa_id → plate_id inventario (PL-…)
_UI_TO_INVENTORY_ID: dict[str, str] = {}


def normalize_remnant_material(raw: str) -> str:
    m = (raw or "").strip().upper()
    if not m:
        return ""
    if m in _MATERIAL_ALIASES:
        return _MATERIAL_ALIASES[m]
    if m.startswith("CARB"):
        return "CARBONO"
    return m


def remnant_ui_placa_id(*, calibre: str, w_in: float, h_in: float) -> str:
    """Etiqueta de placa para UI/nest — nunca PL-CARB (no es catálogo Herinox)."""
    cal = str(calibre or "").strip() or "?"
    return f"REM-{cal}-{w_in:g}x{h_in:g}"


def register_remnant_ui_id(ui_id: str, inventory_id: str) -> None:
    if ui_id and inventory_id:
        _UI_TO_INVENTORY_ID[str(ui_id)] = str(inventory_id)


def resolve_inventory_plate_id(placa_id: str) -> str:
    pid = (placa_id or "").strip()
    if not pid:
        return ""
    return _UI_TO_INVENTORY_ID.get(pid, pid)


def is_remnant_placa_id(placa_id: str) -> bool:
    p = (placa_id or "").strip().upper()
    return p.startswith("REM-") or p.startswith("PL-") or p in _UI_TO_INVENTORY_ID


ENSURE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.nesting_remanentes_placa (
    rem_uid SERIAL PRIMARY KEY,
    plate_id VARCHAR(120) NOT NULL,
    material VARCHAR(80) NOT NULL DEFAULT '',
    calibre VARCHAR(40) NOT NULL DEFAULT '',
    w_in NUMERIC(12,4) NOT NULL,
    h_in NUMERIC(12,4) NOT NULL,
    area_in2 NUMERIC(14,4) NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'DISPONIBLE',
    source VARCHAR(40) NOT NULL DEFAULT 'csv',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_nest_rem_placa_status CHECK (
        status IN ('DISPONIBLE', 'RESERVADO', 'USADO', 'DESCARTADO')
    )
);
CREATE INDEX IF NOT EXISTS idx_nest_rem_placa_avail
    ON public.nesting_remanentes_placa (material, calibre, status, area_in2);
CREATE INDEX IF NOT EXISTS idx_nest_rem_placa_id
    ON public.nesting_remanentes_placa (plate_id);
"""


def default_csv_path() -> Path:
    try:
        import config as app_config

        return Path(app_config.asegurar_archivo_persistente("inventario_remanentes.csv"))
    except Exception:
        root = Path(__file__).resolve().parents[2]
        return root / "inventario_remanentes.csv"


def _db_config() -> dict[str, Any]:
    try:
        import config as app_config

        return {
            "host": getattr(app_config, "NESTING_DB_HOST", "192.168.2.80"),
            "database": getattr(app_config, "NESTING_DB_NAME", "nestingpro_db"),
            "user": getattr(app_config, "NESTING_DB_USER", "postgres"),
            "password": getattr(app_config, "NESTING_DB_PASSWORD", "nesting123"),
            "port": getattr(app_config, "NESTING_DB_PORT", "5433"),
            "connect_timeout": int(getattr(app_config, "NESTING_DB_CONNECT_TIMEOUT", 3)),
        }
    except Exception:
        return {
            "host": os.getenv("NESTING_DB_HOST", "192.168.2.80"),
            "database": os.getenv("NESTING_DB_NAME", "nestingpro_db"),
            "user": os.getenv("NESTING_DB_USER", "postgres"),
            "password": os.getenv("NESTING_DB_PASSWORD", "nesting123"),
            "port": os.getenv("NESTING_DB_PORT", "5433"),
            "connect_timeout": int(os.getenv("NESTING_DB_CONNECT_TIMEOUT", "3")),
        }


def _source_mode() -> str:
    return (os.environ.get("ARGA_NEST_REMNANTS_SOURCE") or "auto").strip().lower()


def parse_plate_id(plate_id: str) -> dict[str, Any] | None:
    m = _ID_RE.match((plate_id or "").strip())
    if not m:
        return None
    mat, cal, w_in, h_in = m.groups()
    return {
        "material": normalize_remnant_material(mat),
        "calibre": cal,
        "w_in": float(w_in),
        "h_in": float(h_in),
        "w_mm": float(w_in) * IN_TO_MM,
        "h_mm": float(h_in) * IN_TO_MM,
        "inventory_id": (plate_id or "").strip(),
    }


def _plate_dict(
    *,
    pid: str,
    w_in: float,
    h_in: float,
    area: float,
    material: str,
    calibre: str,
    units: str,
    source: str,
) -> dict[str, Any]:
    w = w_in * IN_TO_MM if units == "mm" else w_in
    h = h_in * IN_TO_MM if units == "mm" else h_in
    mat = normalize_remnant_material(material)
    ui_id = remnant_ui_placa_id(calibre=calibre, w_in=w_in, h_in=h_in)
    register_remnant_ui_id(ui_id, pid)
    return {
        "id": ui_id,
        "inventory_id": pid,
        "w": w,
        "h": h,
        "cost": area,
        "is_remnant": True,
        "material": mat,
        "calibre": calibre,
        "area_in2": area,
        "source": source,
    }


def load_remnant_plates_from_csv(
    csv_path: str | Path | None = None,
    *,
    material: str | None = None,
    calibre: str | None = None,
    min_area_in2: float = 400.0,
    max_plates: int = 64,
    units: str = "mm",
) -> list[dict[str, Any]]:
    path = Path(csv_path) if csv_path else default_csv_path()
    if not path.is_file():
        return []

    mat_f = (material or "").strip().upper() or None
    cal_f = (calibre or "").strip() or None
    seen: set[tuple[str, float]] = set()
    out: list[dict[str, Any]] = []

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = (row.get("ID") or "").strip()
            parsed = parse_plate_id(pid)
            if not parsed:
                continue
            try:
                area = float(row.get("Area_in2") or 0.0)
            except (TypeError, ValueError):
                continue
            if area < min_area_in2:
                continue
            mat = normalize_remnant_material(
                (row.get("Material") or parsed["material"] or "").strip().upper()
            )
            cal = (row.get("Calibre") or parsed["calibre"] or "").strip()
            if mat_f and mat != mat_f and mat_f not in mat and normalize_remnant_material(mat_f) != mat:
                continue
            if cal_f and abs(float(cal) - float(cal_f)) > 1e-6:
                continue
            key = (pid, round(area, 2))
            if key in seen:
                continue
            seen.add(key)
            out.append(
                _plate_dict(
                    pid=pid,
                    w_in=parsed["w_in"],
                    h_in=parsed["h_in"],
                    area=area,
                    material=mat,
                    calibre=cal,
                    units=units,
                    source="csv",
                )
            )
            if len(out) >= max_plates:
                break

    out.sort(key=lambda p: float(p.get("area_in2") or 0.0))
    return out


def ensure_remnants_table(conn) -> None:
    with conn.cursor() as cur:
        for stmt in ENSURE_TABLE_SQL.strip().split(";"):
            s = stmt.strip()
            if s:
                cur.execute(s)
    conn.commit()


def sync_csv_to_postgres(
    csv_path: str | Path | None = None,
    *,
    db_config: dict[str, Any] | None = None,
    max_rows: int = 5000,
) -> dict[str, Any]:
    """Upsert CSV → Postgres (solo filas nuevas por plate_id+area)."""
    import psycopg2

    plates = load_remnant_plates_from_csv(csv_path, min_area_in2=0.0, max_plates=max_rows)
    cfg = dict(db_config or _db_config())
    inserted = 0
    with psycopg2.connect(**cfg) as conn:
        ensure_remnants_table(conn)
        with conn.cursor() as cur:
            for p in plates:
                parsed = parse_plate_id(p["id"])
                if not parsed:
                    continue
                cur.execute(
                    """
                    SELECT 1 FROM public.nesting_remanentes_placa
                    WHERE plate_id = %s AND ABS(area_in2 - %s) < 0.05
                      AND status = 'DISPONIBLE'
                    LIMIT 1
                    """,
                    (p["id"], float(p["area_in2"])),
                )
                if cur.fetchone():
                    continue
                cur.execute(
                    """
                    INSERT INTO public.nesting_remanentes_placa
                        (plate_id, material, calibre, w_in, h_in, area_in2, status, source)
                    VALUES (%s, %s, %s, %s, %s, %s, 'DISPONIBLE', 'csv_sync')
                    """,
                    (
                        p["id"],
                        p.get("material") or "",
                        p.get("calibre") or "",
                        parsed["w_in"],
                        parsed["h_in"],
                        float(p["area_in2"]),
                    ),
                )
                inserted += 1
        conn.commit()
    return {"ok": True, "csv_rows": len(plates), "inserted": inserted}


def load_remnant_plates_from_postgres(
    *,
    material: str | None = None,
    calibre: str | None = None,
    min_area_in2: float = 400.0,
    max_plates: int = 64,
    units: str = "mm",
    db_config: dict[str, Any] | None = None,
    ensure_table: bool = True,
) -> list[dict[str, Any]]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    cfg = dict(db_config or _db_config())
    mat_f = (material or "").strip().upper() or None
    cal_f = (calibre or "").strip() or None
    out: list[dict[str, Any]] = []

    with psycopg2.connect(**cfg) as conn:
        if ensure_table:
            ensure_remnants_table(conn)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT plate_id, material, calibre, w_in, h_in, area_in2
                FROM public.nesting_remanentes_placa
                WHERE status = 'DISPONIBLE' AND area_in2 >= %s
                ORDER BY area_in2 ASC
                LIMIT %s
                """,
                (float(min_area_in2), int(max_plates) * 4),
            )
            rows = cur.fetchall() or []

    for row in rows:
        pid = str(row.get("plate_id") or "")
        mat = normalize_remnant_material(str(row.get("material") or "").strip().upper())
        cal = str(row.get("calibre") or "").strip()
        if mat_f and mat and mat != mat_f and mat_f not in mat and normalize_remnant_material(mat_f) != mat:
            continue
        if cal_f:
            try:
                if abs(float(cal) - float(cal_f)) > 1e-6:
                    continue
            except (TypeError, ValueError):
                continue
        try:
            w_in = float(row["w_in"])
            h_in = float(row["h_in"])
            area = float(row.get("area_in2") or (w_in * h_in))
        except (TypeError, ValueError, KeyError):
            continue
        out.append(
            _plate_dict(
                pid=pid,
                w_in=w_in,
                h_in=h_in,
                area=area,
                material=mat,
                calibre=cal,
                units=units,
                source="postgres",
            )
        )
        if len(out) >= max_plates:
            break
    return out


def load_remnant_plates(
    csv_path: str | Path | None = None,
    *,
    material: str | None = None,
    calibre: str | None = None,
    min_area_in2: float = 400.0,
    max_plates: int = 64,
    units: str = "mm",
    db_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Carga remanentes: Postgres (si aplica) con fallback CSV."""
    mode = _source_mode()
    kwargs = dict(
        material=material,
        calibre=calibre,
        min_area_in2=min_area_in2,
        max_plates=max_plates,
        units=units,
    )

    if mode in ("postgres", "pg", "db", "auto"):
        try:
            plates = load_remnant_plates_from_postgres(db_config=db_config, **kwargs)
            if plates:
                return plates
            if mode != "auto":
                return []
        except Exception as ex:
            if mode != "auto":
                raise
            # auto: silencioso → CSV
            _ = ex

    return load_remnant_plates_from_csv(csv_path, **kwargs)


def plates_for_job(
    *,
    full_plates: list[dict[str, Any]] | None = None,
    csv_path: str | Path | None = None,
    material: str | None = None,
    calibre: str | None = None,
    prefer_remnants: bool = True,
    max_remnants: int = 32,
) -> list[dict[str, Any]]:
    rem = load_remnant_plates(
        csv_path,
        material=material,
        calibre=calibre,
        max_plates=max_remnants,
    )
    full = []
    for p in full_plates or []:
        q = dict(p)
        q.setdefault("is_remnant", False)
        full.append(q)
    if prefer_remnants:
        return rem + full
    return full + rem


def remnants_in_nest_enabled() -> bool:
    # Default OFF: no inyectar remanentes ni IDs tipo PL-CARB sin opt-in explícito.
    v = (os.environ.get("ARGA_NEST_REMNANTS_IN_NEST") or "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def as_datos_placas_rows(
    *,
    material: str | None = None,
    calibre: str | None = None,
    min_area_in2: float = 400.0,
    max_plates: int = 48,
    exclude_ids: set[str] | None = None,
) -> list[list[Any]]:
    """Filas compatibles con Herinox/sheets_manager:
    [cal, mat, id, L_in, W_in, lb, mxn, usd_lb, stock, origen, usd_lb2]

    ``id`` es etiqueta UI ``REM-…`` (nunca PL-CARB como SKU de catálogo).
    """
    rem = load_remnant_plates(
        material=material,
        calibre=calibre,
        min_area_in2=min_area_in2,
        max_plates=max_plates,
        units="in",
    )
    skip = {str(x) for x in (exclude_ids or set())}
    rows: list[list[Any]] = []
    for p in rem:
        pid_ui = str(p.get("id") or "")
        pid_inv = str(p.get("inventory_id") or pid_ui)
        if not pid_ui or pid_ui in skip or pid_inv in skip:
            continue
        w_in = float(p.get("w") or 0.0)
        h_in = float(p.get("h") or 0.0)
        area = float(p.get("area_in2") or (w_in * h_in))
        cost = max(1.0, area * 0.01)
        mat = normalize_remnant_material(str(p.get("material") or ""))
        cal = str(p.get("calibre") or "")
        register_remnant_ui_id(pid_ui, pid_inv)
        rows.append(
            [
                cal,
                mat,
                pid_ui,
                w_in,
                h_in,
                0.0,
                cost,
                0.0,
                "DISPONIBLE",
                "REMANENTE",
                0.0,
            ]
        )
    return rows


def inject_remnants_into_datos_placas(
    datos_placas: list | None,
    *,
    max_plates: int = 48,
    exclude_ids: set[str] | None = None,
) -> list:
    """Antepone remanentes al inventario Herinox (opt-in ARGA_NEST_REMNANTS_IN_NEST=1)."""
    base = list(datos_placas or [])
    if not remnants_in_nest_enabled():
        return base
    try:
        rem_rows = as_datos_placas_rows(max_plates=max_plates, exclude_ids=exclude_ids)
    except Exception as ex:
        print(f"[REMNANTS] inject skip: {ex}", flush=True)
        return base
    if not rem_rows:
        return base
    print(
        f"[REMNANTS] inject {len(rem_rows)} remanentes (UI=REM-…; PL-… solo inventario interno)",
        flush=True,
    )
    return rem_rows + base


def mark_remnant_used(
    plate_id: str,
    *,
    area_in2: float | None = None,
    db_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Marca remanente como USADO en Postgres (no-op si no hay fila / sin DB)."""
    pid = resolve_inventory_plate_id(plate_id)
    if not pid:
        return {"ok": False, "reason": "empty_id"}
    try:
        import psycopg2
    except Exception as ex:
        return {"ok": False, "reason": f"no_psycopg2:{ex}"}

    cfg = dict(db_config or _db_config())
    try:
        with psycopg2.connect(**cfg) as conn:
            ensure_remnants_table(conn)
            with conn.cursor() as cur:
                if area_in2 is not None:
                    cur.execute(
                        """
                        UPDATE public.nesting_remanentes_placa
                        SET status = 'USADO', updated_at = NOW()
                        WHERE plate_id = %s AND status = 'DISPONIBLE'
                          AND ABS(area_in2 - %s) < 0.5
                        """,
                        (pid, float(area_in2)),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE public.nesting_remanentes_placa
                        SET status = 'USADO', updated_at = NOW()
                        WHERE rem_uid = (
                            SELECT rem_uid FROM public.nesting_remanentes_placa
                            WHERE plate_id = %s AND status = 'DISPONIBLE'
                            ORDER BY area_in2 ASC
                            LIMIT 1
                        )
                        """,
                        (pid,),
                    )
                n = cur.rowcount
            conn.commit()
        return {"ok": True, "updated": int(n or 0), "plate_id": pid}
    except Exception as ex:
        return {"ok": False, "reason": str(ex), "plate_id": pid}
