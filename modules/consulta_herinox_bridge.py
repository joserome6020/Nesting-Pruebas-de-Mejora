#!/usr/bin/env python3
"""
Puente Herinox para AutoDXF 2.0 y Lista de largos 2.0:
- Materiales de placas (inventoryType PLATE).
- Catálogo de largos/perfiles (inventoryType LARGO).
Escribe/actualiza herinox_sync.local.json sin borrar credenciales existentes.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Set

_CORPORATE_AUTODXF20 = (
    r"Z:\♦♦GRUPO ARGA CARPETAS COMPARTIDAS♦♦\BIENVENIDO\Departamentos _antes TIK"
    r"\21. Desarrollo y Tecnologia\2.- Códigos Desarrollo y Tecnología"
    r"\7.- Configuración para equipos de Computo\AutoDXF 2.0"
)
_VENDOR_DIR: str | None = None
_LARGOS_ESPESOR_CATALOG: Dict[str, dict] | None = None
_LARGO_PROFILE_LABELS = frozenset(
    {"PTR", "SOLERA", "CANAL", "TUBO", "VARILLA", "VIGA IPR", "VIGA", "PERFIL"}
)


def _python_vendor_tag() -> str:
    return f"Python{sys.version_info.major}{sys.version_info.minor}"


def _vendor_roots() -> List[str]:
    """Carpetas base donde puede existir _vendor (misma carpeta AutoDXF 2.0 en red)."""
    roots: List[str] = []
    seen: Set[str] = set()

    def add(path: str) -> None:
        p = os.path.abspath(path or "")
        if not p or p in seen:
            return
        seen.add(p)
        roots.append(p)

    try:
        add(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        pass
    env_dir = (os.environ.get("ARGA_AUTODXF20_DIR") or "").strip()
    if env_dir:
        add(env_dir)
    add(_CORPORATE_AUTODXF20)
    return roots


def _bootstrap_vendor() -> str:
    """
    Carga psycopg2 desde _vendor/Python312|313|314 sin pip en cada PC.
    Devuelve la ruta usada o cadena vacía.
    """
    global _VENDOR_DIR
    if _VENDOR_DIR is not None:
        return _VENDOR_DIR

    tag = _python_vendor_tag()
    for base in _vendor_roots():
        for sub in (os.path.join("_vendor", tag), "_vendor"):
            vendor_dir = os.path.join(base, sub)
            if not os.path.isdir(vendor_dir):
                continue
            marker = os.path.join(vendor_dir, "psycopg2")
            if not os.path.isdir(marker) and not os.path.isfile(marker + ".py"):
                continue
            if vendor_dir not in sys.path:
                sys.path.insert(0, vendor_dir)
            _VENDOR_DIR = vendor_dir
            return vendor_dir

    _VENDOR_DIR = ""
    return ""


DEFAULT_DB = {
    "host": "192.168.2.80",
    "port": 5439,
    "name": "herinox",
    "user": "herinox",
    "password": "herinox_password_2024",
}

_CREDENTIAL_KEYS = (
    "api_base_url",
    "email",
    "password",
    "timeout_seconds",
    "db_host",
    "db_port",
    "db_name",
    "db_user",
    "db_password",
    "db_enabled",
)


def normalize_material(raw: str) -> str:
    m = (raw or "").strip().upper().replace("_", " ")
    m = re.sub(r"\s+", " ", m)
    if not m:
        return ""
    if "A 36" in m or "A36" in m:
        return "A 36"
    if "CARBON" in m or ("ACERO" in m and "CARBONO" in m) or "STEEL, CARBON" in m or "STEEL CARBON" in m:
        return "A 36"
    if ("A 36" in m and "GALV" in m) or "GALVAN" in m:
        return "A 36 GALV"
    if ("SSTL" in m and "304" in m) or "INOX" in m or "INOXIDABLE" in m or "STAINLESS" in m:
        return "SSTL 304"
    if "ALUMIN" in m:
        return "Aluminio"
    return m.title()


def _sanitize_csv_token(value: str) -> str:
    return str(value or "").replace("|", "/").strip()


def _tsv_cell(value: str) -> str:
    """Celda TSV: conserva ' | ' del combo Herinox; solo quita tab/saltos de línea."""
    return str(value or "").replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        txt = str(value).strip().replace(",", ".")
        if not txt or txt.upper() in {"N/D", "ND", "PERFIL"}:
            return None
        return float(txt)
    except Exception:
        return None


def _fmt_num(value: Any) -> str:
    num = _to_float(value)
    if num is None:
        return ""
    if abs(num - round(num)) < 1e-6:
        return str(int(round(num)))
    txt = f"{num:.4f}".rstrip("0").rstrip(".")
    return txt


def _parse_fractional(text: Any) -> float | None:
    t = str(text or "").strip().replace(",", ".")
    if not t or t.upper() in {"N/D", "ND", "PERFIL"}:
        return None
    try:
        if " " in t and "/" in t:
            whole, frac = t.split(" ", 1)
            num, den = frac.split("/", 1)
            return float(whole) + (float(num) / float(den))
        if "/" in t:
            num, den = t.split("/", 1)
            return float(num) / float(den)
        return float(t)
    except Exception:
        return None


def _load_largos_espesor_catalog() -> Dict[str, dict]:
    global _LARGOS_ESPESOR_CATALOG
    if _LARGOS_ESPESOR_CATALOG is not None:
        return _LARGOS_ESPESOR_CATALOG

    merged: Dict[str, dict] = {}
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "largos_espesor_catalog.json"),
        os.path.join(_CORPORATE_AUTODXF20, "largos_espesor_catalog.json"),
    ]
    env_dir = (os.environ.get("ARGA_AUTODXF20_DIR") or "").strip()
    if env_dir:
        candidates.insert(0, os.path.join(env_dir, "largos_espesor_catalog.json"))

    for path in candidates:
        try:
            if not os.path.isfile(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                continue
            for code, meta in raw.items():
                key = str(code or "").strip().upper()
                if key and key not in merged and isinstance(meta, dict):
                    merged[key] = meta
        except Exception:
            continue

    _LARGOS_ESPESOR_CATALOG = merged
    return merged


def _espesor_from_clasificacion(clasif: str, perfil: str) -> str:
    texto = str(clasif or "").strip()
    if not texto:
        return ""
    partes = re.split(r"\s+x\s+", texto, flags=re.IGNORECASE)
    if len(partes) >= 2:
        candidato = partes[-1].strip()
        esp = _parse_fractional(candidato)
        if esp is not None and esp > 0:
            return _fmt_num(esp)
    return ""


def _resolve_largo_espesor(row: dict) -> str:
    """
    Resuelve espesor en pulgadas para inventoryType LARGO.
    - thickness numérico en BD -> directo
    - thickness='perfil' -> catálogo Herinox (misma fuente que React) + texto clasificación
    - thickness='N/D' o sin dato -> vacío (combo muestra esp N/D)
    """
    thickness = row.get("thickness")
    thk = row.get("thk")
    mode = str(thickness or "").strip().lower()

    esp = _to_float(thickness)
    if esp is not None and esp > 0:
        return _fmt_num(esp)

    if mode == "cal":
        esp_cal = _parse_fractional(thk)
        if esp_cal is not None and esp_cal > 0:
            return _fmt_num(esp_cal)

    codigo = str(row.get("codigo") or "").strip().upper()
    perfil = str(row.get("perfil") or "").strip()
    cat = _load_largos_espesor_catalog().get(codigo, {})

    esp_cat = _to_float(cat.get("espesor"))
    if esp_cat is not None and esp_cat > 0:
        return _fmt_num(esp_cat)

    esp_txt = _espesor_from_clasificacion(str(cat.get("clasificacion") or ""), perfil)
    if esp_txt:
        return esp_txt

    thk_txt = str(thk or "").strip().upper()
    if thk_txt and thk_txt not in _LARGO_PROFILE_LABELS:
        esp_thk = _parse_fractional(thk)
        if esp_thk is not None and esp_thk > 0:
            return _fmt_num(esp_thk)

    return ""


def _enrich_largos_rows(rows: List[dict]) -> List[dict]:
    enriched: List[dict] = []
    for row in rows:
        item = dict(row)
        item["espesor_resuelto"] = _resolve_largo_espesor(item)
        codigo = str(item.get("codigo") or "").strip().upper()
        cat = _load_largos_espesor_catalog().get(codigo, {})
        if cat.get("clasificacion"):
            item["clasificacion_catalogo"] = str(cat["clasificacion"])
        enriched.append(item)
    return enriched


def _connect_db():
    vendor_dir = _bootstrap_vendor()
    try:
        import psycopg2  # type: ignore
    except ImportError as exc:
        hint = (
            f" Ejecute bootstrap_herinox_vendor.bat en la carpeta AutoDXF 2.0 "
            f"(falta _vendor/{_python_vendor_tag()})"
            if not vendor_dir
            else f" Revise _vendor en {vendor_dir} para {_python_vendor_tag()}"
        )
        raise ImportError(f"No module named 'psycopg2'.{hint}") from exc

    return psycopg2.connect(
        host=DEFAULT_DB["host"],
        port=DEFAULT_DB["port"],
        dbname=DEFAULT_DB["name"],
        user=DEFAULT_DB["user"],
        password=DEFAULT_DB["password"],
        connect_timeout=6,
    )


def fetch_materials_from_db() -> List[str]:
    sql = """
        SELECT DISTINCT p."material"
        FROM "Plate" p
        WHERE p."material" IS NOT NULL
          AND TRIM(p."material") <> ''
          AND p."codigo" IS NOT NULL
          AND TRIM(p."codigo") <> ''
          AND COALESCE(p."inventoryType", 'PLATE') IN ('PLATE', 'PLACA', 'LAMINA');
    """
    conn = None
    cur = None
    try:
        conn = _connect_db()
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall() or []
        return [str(r[0]).strip() for r in rows if r and str(r[0]).strip()]
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def fetch_largos_from_db() -> List[dict]:
    sql = """
        SELECT
            p."codigo",
            p."material",
            p."perfilEstructural",
            p."thickness",
            p."thk",
            p."width",
            p."length",
            p."lbCalculadas",
            p."disponible"
        FROM "Plate" p
        WHERE COALESCE(p."inventoryType", 'PLATE') = 'LARGO'
          AND p."codigo" IS NOT NULL
          AND TRIM(p."codigo") <> ''
        ORDER BY p."codigo";
    """
    conn = None
    cur = None
    try:
        conn = _connect_db()
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall() or []
        out: List[dict] = []
        for row in rows:
            out.append(
                {
                    "codigo": str(row[0] or "").strip(),
                    "material": str(row[1] or "").strip(),
                    "perfil": str(row[2] or "").strip(),
                    "thickness": row[3],
                    "thk": row[4],
                    "width": row[5],
                    "length": row[6],
                    "lb": row[7],
                    "disponible": bool(row[8]),
                }
            )
        return _enrich_largos_rows(out)
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def _clasificacion_largo(row: dict) -> str:
    catalogo = str(row.get("clasificacion_catalogo") or "").strip()
    if catalogo:
        return _sanitize_csv_token(catalogo)

    codigo = _sanitize_csv_token(row.get("codigo", ""))
    perfil = _sanitize_csv_token(row.get("perfil", ""))
    width = _fmt_num(row.get("width"))
    esp = str(row.get("espesor_resuelto") or _resolve_largo_espesor(row))
    if perfil and width and esp:
        if perfil.upper() == "SOLERA" and esp != width:
            return f"Solera {width} x {esp}"
        if perfil.upper() == "PTR":
            return f"{perfil} {width} x {width} x {esp}"
        return f"{perfil} {width} x {esp}"
    if perfil and codigo:
        return f"{perfil} {codigo}"
    return codigo or perfil or "LARGO"


def _texto_combo_largo(row: dict) -> str:
    codigo = _tsv_cell(row.get("codigo", ""))
    perfil = _tsv_cell(row.get("perfil", ""))
    material = _tsv_cell(row.get("material", ""))
    width = _fmt_num(row.get("width")) or "1"
    length_ft = _fmt_num(row.get("length")) or "20"
    esp = str(row.get("espesor_resuelto") or _resolve_largo_espesor(row))
    lb = _fmt_num(row.get("lb")) or "0"
    disp = "DISP" if row.get("disponible") else "NO DISP"
    esp_txt = f"esp {esp} in" if esp else "esp N/D"
    return (
        f"{codigo} | {perfil} | {material} | {width} in x {length_ft} ft | "
        f"{esp_txt} | {lb} lb | {disp}"
    )


def build_largos_catalog_tsv(rows: List[dict]) -> str:
    lines: List[str] = []
    for row in rows:
        codigo = _sanitize_csv_token(row.get("codigo", ""))
        if not codigo:
            continue
        clasif = _tsv_cell(_clasificacion_largo(row))
        perfil = _tsv_cell(row.get("perfil", ""))
        material = _tsv_cell(row.get("material", ""))
        width = _fmt_num(row.get("width"))
        esp = str(row.get("espesor_resuelto") or _resolve_largo_espesor(row))
        length_ft = _fmt_num(row.get("length"))
        lb = _fmt_num(row.get("lb"))
        stock = "1" if row.get("disponible") else "0"
        combo = _texto_combo_largo(row)
        lines.append(
            "\t".join(
                [
                    _tsv_cell(codigo),
                    clasif,
                    perfil,
                    material,
                    width,
                    esp,
                    length_ft,
                    lb,
                    stock,
                    combo,
                ]
            )
        )
    return "\n".join(lines)


def build_materials_payload(materials: List[str], source: str, detail: str) -> dict:
    raw_unique: Set[str] = set()
    canon: Set[str] = set()
    for m in materials:
        raw = _sanitize_csv_token(str(m or "").strip())
        if raw:
            raw_unique.add(raw)
        nm = normalize_material(m)
        if nm:
            canon.add(nm)

    sorted_raw = sorted(raw_unique)
    sorted_canon = sorted(canon)
    return {
        "enabled": True,
        "raw_materials_csv": "|".join(sorted_raw),
        "canonical_materials_csv": "|".join(sorted_canon),
        "raw_materials_count": len(sorted_raw),
        "canonical_materials_count": len(sorted_canon),
        "bridge_source": source,
        "bridge_detail": detail,
        "bridge_updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def build_largos_payload(rows: List[dict], source: str, detail: str) -> dict:
    tsv = build_largos_catalog_tsv(rows)
    b64 = base64.b64encode(tsv.encode("utf-8")).decode("ascii") if tsv else ""
    combo_lines = [_texto_combo_largo(row) for row in rows if _texto_combo_largo(row)]
    return {
        "largos_enabled": bool(rows),
        "largos_count": len(rows),
        "largos_bridge_source": source,
        "largos_bridge_detail": detail,
        "largos_catalog_b64": b64,
        # Separador de registro improbable en texto de combo (Inventor usa b64 TSV).
        "largos_combo_lines_csv": "\x1e".join(combo_lines),
        "largos_updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def load_existing_json(path: str) -> dict:
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def merge_payload(existing: dict, new_data: dict) -> dict:
    merged = dict(existing or {})
    preserved = {k: merged[k] for k in _CREDENTIAL_KEYS if k in merged}
    merged.update(new_data)
    merged.update(preserved)
    merged["_nota"] = (
        "Auto-generado por Consulta_Herinox.py (materiales + catalogo largos para AutoDXF / Lista de largos)."
    )
    return merged


def build_full_payload(
    materials: List[str],
    largos: List[dict],
    source: str,
    mats_detail: str,
    largos_detail: str,
) -> dict:
    payload = build_materials_payload(materials, source, mats_detail)
    payload.update(build_largos_payload(largos, source, largos_detail))
    return payload


def refresh_herinox_bridge_json(out_path: str | None = None) -> dict:
    """Consulta Herinox y escribe JSON unificado. Devuelve el payload escrito."""
    _bootstrap_vendor()
    if out_path is None:
        try:
            import config

            out_path = str(getattr(config, "HERINOX_SYNC_SETTINGS_FILE", "herinox_sync.local.json"))
        except Exception:
            out_path = os.path.join(os.path.dirname(__file__), "..", "herinox_sync.local.json")
    out_path = os.path.abspath(out_path)
    out_dir = os.path.dirname(out_path) or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)

    source = "FALLBACK"
    mats: List[str] = []
    largos: List[dict] = []
    mats_detail = ""
    largos_detail = ""

    try:
        mats = fetch_materials_from_db()
        largos = fetch_largos_from_db()
        source = "POSTGRES"
        mats_detail = f"Materiales leidos: {len(mats)}"
        largos_detail = f"Largos leidos: {len(largos)}"
    except Exception as exc:
        mats_detail = f"DB error materiales: {exc}"
        largos_detail = f"DB error largos: {exc}"
        if not mats:
            mats = ["A 36", "A 36 GALV", "SSTL 304", "Aluminio"]

    payload = build_full_payload(mats, largos, source, mats_detail, largos_detail)
    merged = merge_payload(load_existing_json(out_path), payload)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    return merged


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "herinox_sync.local.json")
    try:
        payload = refresh_herinox_bridge_json(out_path)
        print(
            "[Consulta_Herinox] OK -> "
            f"{os.path.basename(out_path)} | "
            f"source={payload.get('bridge_source')} | "
            f"raw={payload.get('raw_materials_count', 0)} | "
            f"canonical={payload.get('canonical_materials_count', 0)} | "
            f"largos={payload.get('largos_count', 0)}",
            flush=True,
        )
        return 0
    except Exception as exc:
        emergency_mats = ["A 36", "A 36 GALV", "SSTL 304", "Aluminio"]
        emergency = build_full_payload(emergency_mats, [], "FALLBACK", f"Bridge fatal: {exc}", "Sin largos")
        try:
            merged = merge_payload(load_existing_json(out_path), emergency)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2, ensure_ascii=False)
            print(f"[Consulta_Herinox] WARN -> fallback escrito en {out_path}: {exc}")
            return 0
        except Exception as exc2:
            print(f"[Consulta_Herinox] ERROR -> {exc2}; original: {exc}")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
