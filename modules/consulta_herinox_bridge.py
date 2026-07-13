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
import shutil
import sys
from datetime import datetime
from typing import Any, Dict, List, Set

AUTODXF20_REL = (
    r"♦♦GRUPO ARGA CARPETAS COMPARTIDAS♦♦\BIENVENIDO\Departamentos _antes TIK"
    r"\21. Desarrollo y Tecnologia\2.- Códigos Desarrollo y Tecnología"
    r"\7.- Configuración para equipos de Computo\AutoDXF 2.0"
)
UNC_SHARE_ROOTS = (r"\\192.168.2.47\arga",)


def _join_unc(root: str, rel: str) -> str:
    return root.rstrip("\\") + "\\" + rel.lstrip("\\")


def autodxf20_candidate_paths() -> List[str]:
    out: List[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        p = os.path.normpath(str(path or "").strip())
        if not p:
            return
        key = p.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(p)

    env_dir = (os.environ.get("ARGA_AUTODXF20_DIR") or "").strip()
    if env_dir:
        add(env_dir)
    for root in UNC_SHARE_ROOTS:
        add(_join_unc(root, AUTODXF20_REL))
    add(_join_unc("Z:", AUTODXF20_REL))
    return out


def resolve_autodxf20_dir(*, prefer_existing: bool = True) -> str:
    for path in autodxf20_candidate_paths():
        if not prefer_existing or os.path.isdir(path):
            return path
    paths = autodxf20_candidate_paths()
    return paths[0] if paths else ""


_CORPORATE_AUTODXF20 = resolve_autodxf20_dir(prefer_existing=False) or _join_unc(
    UNC_SHARE_ROOTS[0], AUTODXF20_REL
)
_VENDOR_DIR: str | None = None
_LARGOS_ESPESOR_CATALOG: Dict[str, dict] | None = None
_LARGO_PROFILE_LABELS = frozenset(
    {"PTR", "SOLERA", "CANAL", "TUBO", "VARILLA", "VIGA IPR", "VIGA", "PERFIL"}
)


def _python_vendor_tag() -> str:
    return f"Python{sys.version_info.major}{sys.version_info.minor}"


def _is_unc_or_remote(path: str) -> bool:
    p = os.path.abspath(path or "")
    if p.startswith("\\\\"):
        return True
    if len(p) >= 2 and p[1] == ":":
        drive = p[:2] + "\\"
        try:
            import ctypes

            if int(ctypes.windll.kernel32.GetDriveTypeW(drive)) == 4:
                return True
        except Exception:
            pass
    return False


def _needs_local_vendor_mirror(vendor_dir: str) -> bool:
    p = os.path.abspath(vendor_dir or "")
    if not p:
        return False
    if len(p) >= 200:
        return True
    return _is_unc_or_remote(p)


def _local_vendor_cache_dir(tag: str) -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Arga", "AutoDXF20_vendor", tag)


def _mirror_vendor_to_local(source: str, tag: str) -> str:
    """Copia _vendor de red a ruta local corta (psycopg2 no carga DLL en UNC largo)."""
    dest = _local_vendor_cache_dir(tag)
    stamp_src = os.path.join(source, ".vendor_ok")
    stamp_dst = os.path.join(dest, ".vendor_ok")
    psyc_src = os.path.join(source, "psycopg2")
    psyc_dst = os.path.join(dest, "psycopg2")

    need = not os.path.isdir(psyc_dst)
    if not need and os.path.isfile(stamp_src):
        try:
            need = (not os.path.isfile(stamp_dst)) or (
                os.path.getmtime(stamp_src) > os.path.getmtime(stamp_dst)
            )
        except OSError:
            need = True
    if not need and os.path.isdir(psyc_src):
        try:
            need = os.path.getmtime(psyc_src) > os.path.getmtime(psyc_dst)
        except OSError:
            need = True

    if need:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.isdir(dest):
            shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(source, dest)
    return dest


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
    for corp in autodxf20_candidate_paths():
        add(corp)
    return roots


def _bootstrap_vendor() -> str:
    """
    Carga psycopg2 desde _vendor/Python312|313|314 sin pip en cada PC.
    Si _vendor esta en red con ruta larga, lo espeja a %LOCALAPPDATA%\\Arga\\...
    Devuelve la ruta usada o cadena vacía.
    """
    global _VENDOR_DIR
    if _VENDOR_DIR is not None:
        return _VENDOR_DIR

    tag = _python_vendor_tag()
    candidates: List[str] = []
    for base in _vendor_roots():
        for sub in (os.path.join("_vendor", tag), "_vendor"):
            vendor_dir = os.path.join(base, sub)
            if not os.path.isdir(vendor_dir):
                continue
            marker = os.path.join(vendor_dir, "psycopg2")
            if not os.path.isdir(marker) and not os.path.isfile(marker + ".py"):
                continue
            candidates.append(vendor_dir)

    for vendor_dir in candidates:
        attempt_dirs = [vendor_dir]
        if _needs_local_vendor_mirror(vendor_dir):
            try:
                attempt_dirs.append(_mirror_vendor_to_local(vendor_dir, tag))
            except Exception:
                pass

        for attempt_dir in attempt_dirs:
            if attempt_dir not in sys.path:
                sys.path.insert(0, attempt_dir)
            try:
                import psycopg2  # type: ignore  # noqa: F401
            except ImportError:
                if attempt_dir in sys.path:
                    sys.path.remove(attempt_dir)
                continue
            _VENDOR_DIR = attempt_dir
            return attempt_dir

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
    # Galvanizado antes que A 36 genérico (A 36 GALV también contiene "A 36").
    if ("A 36" in m and "GALV" in m) or "GALVAN" in m:
        return "A 36 GALV"
    if "A 36" in m or "A36" in m:
        return "A 36"
    if "CARBON" in m or ("ACERO" in m and "CARBONO" in m) or "STEEL, CARBON" in m or "STEEL CARBON" in m:
        return "A 36"
    # Inoxidable: piezas DXF (INOXIDABLE), Herinox (SSTL / SSTL 304) y variantes comunes.
    if "SSTL" in m:
        if "316" in m:
            return "SSTL 316"
        return "SSTL 304"
    if "INOX" in m or "INOXIDABLE" in m or "STAINLESS" in m:
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
    ]
    for corp in autodxf20_candidate_paths():
        candidates.append(os.path.join(corp, "largos_espesor_catalog.json"))

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
        msg = str(exc).lower()
        if "demasiado largo" in msg or "too long" in msg:
            hint = (
                f" psycopg2 no carga desde ruta de red larga; "
                f"revise %LOCALAPPDATA%\\Arga\\AutoDXF20_vendor\\{_python_vendor_tag()}"
            )
        elif not vendor_dir:
            hint = (
                f" Ejecute bootstrap_herinox_vendor.bat en la carpeta AutoDXF 2.0 "
                f"(falta _vendor/{_python_vendor_tag()})"
            )
        else:
            hint = f" Revise _vendor en {vendor_dir} para {_python_vendor_tag()}"
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


def _perfil_estructural_tarjeton(perfil: str) -> str:
    """Etiqueta del tarjetón Herinox (ej. SOLERA -> SOLERA perfil)."""
    p = str(perfil or "").strip()
    if not p:
        return ""
    if re.search(r"\bperfil\b", p, re.IGNORECASE):
        return p
    return f"{p} perfil"


def _descripcion_tarjeton(row: dict) -> str:
    """Descripción mostrada en el tarjetón (columna descripcion de Plate)."""
    desc = str(row.get("descripcion") or "").strip()
    if desc:
        return desc
    return _clasificacion_largo(row)


def _tarjeton_largo(row: dict) -> dict:
    """Campos visibles en el tarjetón de Lista de largos / Herinox."""
    return {
        "codigo": str(row.get("codigo") or "").strip(),
        "perfil_estructural": _perfil_estructural_tarjeton(row.get("perfil", "")),
        "tipo_material": str(row.get("material") or "").strip(),
        "descripcion": _descripcion_tarjeton(row),
    }


def fetch_largos_from_db() -> List[dict]:
    sql = """
        SELECT
            p."codigo",
            p."material",
            p."perfilEstructural",
            p."descripcion",
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
                    "descripcion": str(row[3] or "").strip(),
                    "thickness": row[4],
                    "thk": row[5],
                    "width": row[6],
                    "length": row[7],
                    "lb": row[8],
                    "disponible": bool(row[9]),
                }
            )
        return _enrich_largos_rows(out)
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def _clasificacion_largo(row: dict) -> str:
    desc = str(row.get("descripcion") or "").strip()
    if desc:
        return _sanitize_csv_token(desc)

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
    """Texto del combo Inventor: mismos 4 campos visibles en el tarjetón Herinox."""
    codigo = _tsv_cell(row.get("codigo", ""))
    perfil = _tsv_cell(_perfil_estructural_tarjeton(row.get("perfil", "")))
    material = _tsv_cell(row.get("material", ""))
    descripcion = _tsv_cell(_descripcion_tarjeton(row))
    return f"{codigo} | {perfil} | {material} | {descripcion}"


def build_largos_catalog_tsv(rows: List[dict]) -> str:
    lines: List[str] = []
    for row in rows:
        codigo = _sanitize_csv_token(row.get("codigo", ""))
        if not codigo:
            continue
        clasif = _tsv_cell(_descripcion_tarjeton(row))
        perfil = _tsv_cell(_perfil_estructural_tarjeton(row.get("perfil", "")))
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
    largos_items = [
        _tarjeton_largo(row)
        for row in rows
        if str(row.get("codigo") or "").strip()
    ]
    return {
        "largos_enabled": bool(rows),
        "largos_count": len(rows),
        "largos_bridge_source": source,
        "largos_bridge_detail": detail,
        "largos_catalog_b64": b64,
        # Separador de registro improbable en texto de combo (Inventor usa b64 TSV).
        "largos_combo_lines_csv": "\x1e".join(combo_lines),
        # Tarjetones Herinox: codigo, perfil estructural, tipo material, descripcion.
        "largos_items": largos_items,
        "largos_items_json": json.dumps(largos_items, ensure_ascii=False),
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
