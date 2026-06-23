"""
Catálogo de largos — alineación Lista de Largos (nesting) ↔ Gestión Herinox.

Fuente maestra de códigos, costos y dimensiones comerciales:
  PostgreSQL herinox, tabla "Plate" (inventoryType = 'LARGO').

Uso:
  - Normalizar clasificación del CSV/job (lista_largos_job)
  - Enriquecer material_requerido_ldg (codigo, costo)
"""

from __future__ import annotations

import os
import re
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

# ---------------------------------------------------------------------------
# Conexión Herinox (Gestión de Placas / Largos — docker placas-gestion-db)
# ---------------------------------------------------------------------------
HERINOX_DB_CONFIG = {
    "host": os.getenv("HERINOX_DB_HOST", "192.168.2.80"),
    "port": os.getenv("HERINOX_DB_PORT", "5439"),
    "database": os.getenv("HERINOX_DB_NAME", "herinox"),
    "user": os.getenv("HERINOX_DB_USER", "herinox"),
    "password": os.getenv("HERINOX_DB_PASSWORD", "herinox_password_2024"),
    "connect_timeout": int(os.getenv("HERINOX_DB_CONNECT_TIMEOUT", "5")),
}

PERFILES_ESTRUCTURALES = (
    "SOLERA",
    "PTR",
    "CANAL",
    "VIGA IPR",
    "VIGA",
    "ANGULO",
    "TUBULAR",
    "TUBO",
    "VARILLA",
)

ALIAS_PERFIL = {
    "SOL": "SOLERA",
    "SOLERA": "SOLERA",
    "PTR": "PTR",
    "TUBULAR": "TUBULAR",
    "TUBO": "TUBO",
    "CANAL": "CANAL",
    "VIGA": "VIGA",
    "VIGA IPR": "VIGA IPR",
    "IPR": "VIGA IPR",
    "ANGULO": "ANGULO",
    "ÁNGULO": "ANGULO",
    "VARILLA": "VARILLA",
}

CODIGO_HERINOX_RE = re.compile(r"^[A-Z]{2,5}\d{2,4}$", re.IGNORECASE)

ALIAS_MATERIAL_GRADE = {
    "A36": "A 36",
    "A 36": "A 36",
    "A36GALV": "A 36 GALV",
    "A 36 GALV": "A 36 GALV",
    "A1018": "A 1018",
    "A 1018": "A 1018",
    "A514": "A 514",
    "A 514": "A 514",
    "A572G50": "A 572 G50",
    "A 572 G50": "A 572 G50",
}


def _parse_numero(valor: str) -> Optional[float]:
    texto = str(valor or "").strip().replace(",", ".")
    if not texto:
        return None
    try:
        if "/" in texto:
            return float(Fraction(texto))
        return float(texto)
    except Exception:
        return None


def normalizar_perfil(raw: str) -> Optional[str]:
    t = re.sub(r"\s+", " ", str(raw or "").strip().upper())
    if not t:
        return None
    if t in ALIAS_PERFIL:
        return ALIAS_PERFIL[t]
    for perfil in PERFILES_ESTRUCTURALES:
        if t == perfil or t.startswith(perfil + " "):
            return perfil
    return t


def normalizar_material_grade(raw: str) -> Optional[str]:
    t = re.sub(r"\s+", " ", str(raw or "").strip().upper())
    if not t:
        return None
    compacto = t.replace(" ", "")
    return ALIAS_MATERIAL_GRADE.get(compacto) or ALIAS_MATERIAL_GRADE.get(t) or t


def _normalizar_texto_combo(texto: str) -> str:
    return re.sub(r"\s+", " ", str(texto or "").strip()).upper()


def _es_formato_combo_herinox(texto: str) -> bool:
    partes = [p.strip() for p in str(texto or "").split("|") if p.strip()]
    return len(partes) >= 3 and bool(CODIGO_HERINOX_RE.match(partes[0].replace(" ", "")))


def extraer_codigo_herinox_combo(texto: str) -> str:
    partes = [p.strip() for p in str(texto or "").split("|") if p.strip()]
    if len(partes) >= 3:
        codigo = partes[0].replace(" ", "").upper()
        if CODIGO_HERINOX_RE.match(codigo):
            return codigo
    parsed = parse_clasificacion_largo(texto)
    return str(parsed.get("codigo_herinox") or "").strip().upper()


def parse_clasificacion_largo(texto: str) -> Dict[str, Any]:
    """
    Interpreta textos de inventario/nesting como:
      'Solera 1 x 1/2', 'Solera 1.5 x 1/2', 'PTR ...', etc.
    """
    original = str(texto or "").strip()
    resultado: Dict[str, Any] = {
        "texto_original": original,
        "codigo_herinox": None,
        "perfil_estructural": None,
        "material_grade": None,
        "ancho_in": None,
        "espesor_in": None,
        "largo_stock_ft": None,
        "texto_combo": None,
        "display_material": original or "SIN CLASIFICACION",
    }

    if not original:
        return resultado

    if _es_formato_combo_herinox(original):
        partes = [p.strip() for p in original.split("|")]
        codigo = partes[0].replace(" ", "").upper()
        resultado["codigo_herinox"] = codigo
        resultado["perfil_estructural"] = normalizar_perfil(partes[1])
        resultado["material_grade"] = normalizar_material_grade(partes[2])
        resultado["texto_combo"] = _normalizar_texto_combo(original)
        if len(partes) > 3:
            m_dim = re.search(
                r"(\d+(?:\.\d+)?)\s*in\s*[xX×]\s*(\d+(?:\.\d+)?)\s*ft",
                partes[3],
                re.IGNORECASE,
            )
            if m_dim:
                resultado["ancho_in"] = _parse_numero(m_dim.group(1))
                resultado["largo_stock_ft"] = _parse_numero(m_dim.group(2))
        resultado["display_material"] = original
        return resultado

    # Prefijo explícito de perfil al inicio
    perfil_detectado = None
    resto = original
    upper = original.upper()
    for perfil in sorted(PERFILES_ESTRUCTURALES, key=len, reverse=True):
        if upper.startswith(perfil):
            perfil_detectado = perfil
            resto = original[len(perfil) :].strip(" -:|")
            break

    # Solera N x M (pulgadas)
    m_solera = re.match(
        r"(?i)^(?:solera|sol)?\s*(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?|\d+\s*/\s*\d+)$",
        resto if perfil_detectado else original,
    )
    if m_solera or re.match(
        r"(?i)^(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?|\d+\s*/\s*\d+)$",
        resto if perfil_detectado else original,
    ):
        if not m_solera:
            m_solera = re.match(
                r"(?i)^(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?|\d+\s*/\s*\d+)$",
                resto,
            )
        if m_solera:
            resultado["perfil_estructural"] = perfil_detectado or "SOLERA"
            resultado["ancho_in"] = _parse_numero(m_solera.group(1))
            resultado["espesor_in"] = _parse_numero(m_solera.group(2))
            return resultado

    if perfil_detectado:
        resultado["perfil_estructural"] = normalizar_perfil(perfil_detectado)

    # Grado de material embebido (A36, A 1018, etc.)
    m_grade = re.search(
        r"(?i)\b(A\s*36\s*GALV|A\s*36|A\s*1018|A\s*514|A\s*572\s*G50|SSTL\s*304|SSTL\s*316)\b",
        original,
    )
    if m_grade:
        resultado["material_grade"] = normalizar_material_grade(m_grade.group(1))

    return resultado


def largo_pulgadas_a_pies(largo_in: float) -> float:
    return round(float(largo_in) / 12.0, 2)


def _ultimo_usd_mxn(cursor) -> float:
    try:
        cursor.execute(
            """
            SELECT "usdMxn"
            FROM "ExchangeRate"
            ORDER BY "publishedAt" DESC NULLS LAST
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        if row and row.get("usdMxn"):
            return float(row["usdMxn"])
    except Exception:
        pass
    return 0.0


def _costo_unitario_mxn(
    costo_actual: float,
    lb_calculadas: float,
    precio_hist: Optional[float] = None,
    precio_por_lb_hist: Optional[float] = None,
    costo_actual_usd: float = 0.0,
    costo_por_lb_usd: float = 0.0,
    usd_mxn: float = 0.0,
) -> float:
    if costo_actual and float(costo_actual) > 0:
        return float(costo_actual)
    if precio_hist and float(precio_hist) > 0:
        return float(precio_hist)
    lb = float(lb_calculadas or 0)
    ppl = float(precio_por_lb_hist or 0)
    if lb > 0 and ppl > 0:
        return round(lb * ppl, 2)
    tc = float(usd_mxn or 0)
    if tc > 0:
        usd = float(costo_actual_usd or 0)
        if usd > 0:
            return round(usd * tc, 2)
        ppl_usd = float(costo_por_lb_usd or 0)
        if lb > 0 and ppl_usd > 0:
            return round(lb * ppl_usd * tc, 2)
    return 0.0


MATERIAL_DENSITY_LB_IN3: Dict[str, float] = {
    "A 36 GALV": 0.284,
    "A 36": 0.284,
    "A 1018": 0.284,
    "A 514": 0.284,
    "A 572 G50": 0.284,
    "SSTL 304": 0.289,
    "SSTL 316": 0.289,
    "CU": 0.323,
    "AL 3003": 0.098,
    "AL 5052": 0.097,
    "AL 6061": 0.098,
}


def _canonical_material(raw: str) -> Optional[str]:
    t = re.sub(r"\s+", " ", str(raw or "").strip().upper())
    if not t:
        return None
    return ALIAS_MATERIAL_GRADE.get(t.replace(" ", "")) or ALIAS_MATERIAL_GRADE.get(t) or t


def _espesor_display_herinox(placa: Dict[str, Any]) -> str:
    """Misma lógica que normalizeLongThicknessForDisplay en React-Herinox."""
    raw = str(placa.get("thickness_raw") or "").strip().lower()
    if raw and raw not in ("perfil", "largo", "in", "n/d"):
        parsed = _parse_numero(raw)
        if parsed and parsed > 0:
            return f"{parsed:g}"

    thk_txt = str(placa.get("thk_raw") or "").strip()
    parsed_thk = _parse_numero(thk_txt)
    if parsed_thk and parsed_thk > 0:
        return f"{parsed_thk:g}"

    material = _canonical_material(placa.get("material_grade") or "")
    density = MATERIAL_DENSITY_LB_IN3.get(material or "") if material else None
    width = float(placa.get("ancho_in") or 0)
    largo_ft = float(placa.get("largo_ft") or 0)
    lb = float(placa.get("lb_calculadas") or 0)
    perfil = placa.get("perfil_estructural") or ""

    if not density or width <= 0 or largo_ft <= 0 or lb <= 0:
        return "N/D"

    length_in = largo_ft * 12.0
    if length_in <= 0:
        return "N/D"

    if perfil == "SOLERA":
        inferred = lb / (width * length_in * density)
        if inferred > 0:
            return f"{inferred:.3f}".rstrip("0").rstrip(".")

    if perfil == "PTR":
        section_area = lb / (length_in * density)
        side = width
        if section_area > 0 and section_area < side * side:
            discriminant = side * side - section_area
            if discriminant >= 0:
                inferred = (side - (discriminant**0.5)) / 2.0
                if 0 < inferred < side / 2:
                    return f"{inferred:.3f}".rstrip("0").rstrip(".")

    return "N/D"


def _perfil_display_herinox(placa: Dict[str, Any]) -> str:
    perfil = str(placa.get("perfil_estructural") or "").strip()
    thk = str(placa.get("thk_raw") or "").strip()
    if perfil:
        return perfil
    return thk or "N/D"


def construir_dimensiones_base(placa: Dict[str, Any]) -> str:
    ancho = float(placa.get("ancho_in") or 0)
    largo_ft = float(placa.get("largo_ft") or 0)
    if ancho > 0 and largo_ft > 0:
        return f"{ancho:g} in x {largo_ft:g} ft"
    return "N/D"


def construir_texto_combo_usuario(placa: Dict[str, Any]) -> str:
    """
    Texto del combo Inventor — alineado a tarjeta React-Herinox (código, perfil, material,
    dimensiones base, espesor, lb).
    """
    codigo = str(placa.get("codigo") or "").strip()
    perfil = _perfil_display_herinox(placa)
    material = str(placa.get("material_grade") or "").strip()
    dims = construir_dimensiones_base(placa)
    esp = _espesor_display_herinox(placa)
    esp_label = "N/D" if esp == "N/D" else f"{esp} in"
    lb = float(placa.get("lb_calculadas") or 0)
    disp = " | DISP" if placa.get("disponible") else " | NO DISP"

    return (
        f"{codigo} | {perfil} | {material} | {dims} | esp {esp_label} | {lb:g} lb{disp}"
    )


def _cargar_placas_largos_desde_herinox(
    solo_disponibles: bool = False,
) -> List[Dict[str, Any]]:
    conexion = None
    cursor = None
    try:
        conexion = psycopg2.connect(**HERINOX_DB_CONFIG)
        cursor = conexion.cursor(cursor_factory=RealDictCursor)

        where_disp = 'AND p."disponible" = true' if solo_disponibles else ""
        usd_mxn = _ultimo_usd_mxn(cursor)
        cursor.execute(
            f"""
            SELECT
                p."id",
                p."codigo",
                p."material",
                p."perfilEstructural",
                p."descripcion",
                p."width",
                p."length",
                p."thickness",
                p."thk",
                p."lbCalculadas",
                p."costoActual",
                p."costoActualUsd",
                p."costoPorLbUsd",
                p."disponible",
                ph."newPrice" AS precio_hist,
                ph."pricePerLb" AS precio_por_lb_hist
            FROM "Plate" p
            LEFT JOIN LATERAL (
                SELECT "newPrice", "pricePerLb"
                FROM "PriceHistory"
                WHERE "plateId" = p."id"
                  AND (
                    COALESCE("newPrice", 0) > 0
                    OR COALESCE("pricePerLb", 0) > 0
                  )
                  AND "approvalStatus" IN ('APPROVED', 'PENDING')
                ORDER BY
                  CASE WHEN "approvalStatus" = 'APPROVED' THEN 0 ELSE 1 END,
                  "changedAt" DESC NULLS LAST
                LIMIT 1
            ) ph ON TRUE
            WHERE p."inventoryType" = 'LARGO'
            {where_disp}
            ORDER BY p."perfilEstructural", p."material", p."length", p."width"
            """
        )
        filas = cursor.fetchall() or []
        catalogo = []
        for row in filas:
            thickness_raw = str(row.get("thickness") or "").strip()
            thk_raw = str(row.get("thk") or "").strip()
            lb_calc = float(row.get("lbCalculadas") or 0)
            costo_unit = _costo_unitario_mxn(
                float(row.get("costoActual") or 0),
                lb_calc,
                row.get("precio_hist"),
                row.get("precio_por_lb_hist"),
                float(row.get("costoActualUsd") or 0),
                float(row.get("costoPorLbUsd") or 0),
                usd_mxn,
            )
            placa = {
                "id": row.get("id"),
                "codigo": str(row.get("codigo") or "").strip().upper(),
                "material_grade": str(row.get("material") or "").strip(),
                "perfil_estructural": normalizar_perfil(
                    row.get("perfilEstructural") or thk_raw or ""
                ),
                "descripcion": str(row.get("descripcion") or "").strip(),
                "ancho_in": float(row.get("width") or 0),
                "largo_ft": float(row.get("length") or 0),
                "thickness_raw": thickness_raw,
                "thk_raw": thk_raw,
                "lb_calculadas": lb_calc,
                "costo_actual": costo_unit,
                "disponible": bool(row.get("disponible")),
            }
            esp_txt = _espesor_display_herinox(placa)
            placa["espesor_in"] = (
                None if esp_txt == "N/D" else _parse_numero(esp_txt)
            )
            placa["clasificacion"] = construir_etiqueta_clasificacion(placa)
            placa["texto_combo"] = construir_texto_combo_usuario(placa)
            placa["dimensiones_base"] = construir_dimensiones_base(placa)
            placa["espesor_display"] = esp_txt
            placa["perfil_display"] = _perfil_display_herinox(placa)
            catalogo.append(placa)
        return catalogo
    except Exception as exc:
        print(f"⚠️ No se pudo cargar catálogo Herinox: {exc}")
        return []
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()


def _puntuar_match(
    parsed: Dict[str, Any],
    placa: Dict[str, Any],
    largo_in_pedido: float,
) -> float:
    perfil = parsed.get("perfil_estructural")
    if perfil and placa.get("perfil_estructural") != perfil:
        return -1.0

    largo_ft_pedido = largo_pulgadas_a_pies(largo_in_pedido)
    largo_ft_placa = float(placa.get("largo_ft") or 0)
    if largo_ft_placa <= 0:
        return -1.0

    diff_ft = abs(largo_ft_pedido - largo_ft_placa)
    if diff_ft > 1.0:
        return -1.0

    score = 100.0 - diff_ft * 10.0

    ancho = parsed.get("ancho_in")
    if ancho is not None and placa.get("ancho_in"):
        diff_ancho = abs(float(ancho) - float(placa["ancho_in"]))
        if diff_ancho > 0.25:
            return -1.0
        score -= diff_ancho * 5.0

    espesor = parsed.get("espesor_in")
    if espesor is not None and placa.get("espesor_in"):
        diff_esp = abs(float(espesor) - float(placa["espesor_in"]))
        if diff_esp > 0.2:
            score -= diff_esp * 8.0

    grade = parsed.get("material_grade")
    if grade and placa.get("material_grade"):
        g1 = grade.replace(" ", "").upper()
        g2 = str(placa["material_grade"]).replace(" ", "").upper()
        if g1 != g2 and g1 not in g2 and g2 not in g1:
            score -= 15.0

    return score


def _indices_catalogo(
    catalogo: List[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    por_codigo: Dict[str, Dict[str, Any]] = {}
    por_combo: Dict[str, Dict[str, Any]] = {}
    for placa in catalogo or []:
        codigo = str(placa.get("codigo") or "").strip().upper()
        if codigo and codigo not in por_codigo:
            por_codigo[codigo] = placa
        combo = str(placa.get("texto_combo") or "").strip()
        if combo and combo not in por_combo:
            por_combo[combo] = placa
    return por_codigo, por_combo


def buscar_placa_herinox(
    texto_material: str,
    largo_in: float,
    catalogo: Optional[List[Dict[str, Any]]] = None,
    solo_disponibles: bool = False,
    codigo_hint: str = "",
) -> Optional[Dict[str, Any]]:
    parsed = parse_clasificacion_largo(texto_material)
    if catalogo is None:
        catalogo = _cargar_placas_largos_desde_herinox(solo_disponibles=solo_disponibles)
    if not catalogo:
        return None

    por_codigo, por_combo = _indices_catalogo(catalogo)
    codigo = (
        str(codigo_hint or "").strip().upper()
        or str(parsed.get("codigo_herinox") or "").strip().upper()
        or extraer_codigo_herinox_combo(texto_material)
    )
    if codigo:
        return por_codigo.get(codigo)

    combo_key = str(parsed.get("texto_combo") or _normalizar_texto_combo(texto_material))
    if combo_key and combo_key in por_combo:
        return por_combo[combo_key]

    if parsed.get("codigo_herinox") or extraer_codigo_herinox_combo(texto_material):
        return None

    if largo_in <= 0:
        return None

    mejor: Optional[Tuple[float, Dict[str, Any]]] = None
    for placa in catalogo:
        score = _puntuar_match(parsed, placa, largo_in)
        if score < 0:
            continue
        if mejor is None or score > mejor[0]:
            mejor = (score, placa)

    return mejor[1] if mejor else None


def enriquecer_fila_pedido(
    material: str,
    largo_in: float,
    cantidad: int = 1,
    catalogo: Optional[List[Dict[str, Any]]] = None,
    codigo_hint: str = "",
) -> Dict[str, Any]:
    """
    Devuelve codigo y costo (total de línea) desde Herinox.
    Prioridad: código del combo (HR164, PTR052, …) > match difuso.
    """
    codigo = codigo_hint or extraer_codigo_herinox_combo(material)
    placa = buscar_placa_herinox(
        material,
        0.0,
        catalogo=catalogo,
        codigo_hint=codigo,
    )
    if not placa:
        codigo_fallback = str(codigo or "").strip()
        parsed = parse_clasificacion_largo(material)
        largo_ft = parsed.get("largo_stock_ft")
        return {
            "codigo": codigo_fallback,
            "costo": None,
            "herinox_plate_id": None,
            "largo_ft": largo_ft,
            "largo_stock_in": (
                round(float(largo_ft) * 12.0, 2) if largo_ft else None
            ),
        }

    costo_unit = float(placa.get("costo_actual") or 0)
    costo_total = costo_unit * max(1, int(cantidad)) if costo_unit > 0 else None
    largo_ft = float(placa.get("largo_ft") or 0)

    return {
        "codigo": placa.get("codigo") or codigo or "",
        "costo": costo_total,
        "costo_unitario": costo_unit if costo_unit > 0 else None,
        "herinox_plate_id": placa.get("id"),
        "perfil_estructural": placa.get("perfil_estructural"),
        "material_grade": placa.get("material_grade"),
        "largo_ft": largo_ft,
        "largo_stock_in": round(largo_ft * 12.0, 2) if largo_ft > 0 else None,
        "lb_calculadas": placa.get("lb_calculadas"),
    }


ETIQUETA_TIPO_PERFIL_MRL = {
    "ANGULO": "Ángulo",
    "CANAL": "Canal",
    "PTR": "PTR",
    "SOLERA": "Solera",
    "TUBO": "Tubo",
    "TUBULAR": "Tubular",
    "REDONDO": "Redondo",
    "VARILLA": "Varilla",
    "VIGA": "Viga",
    "VIGA IPR": "Viga IPR",
}

_PREFIJO_CODIGO_TIPO_MRL = (
    ("SCO", "Solera"),
    ("SLC", "Solera"),
    ("ANG", "Ángulo"),
    ("CAN", "Canal"),
    ("PTR", "PTR"),
    ("RED", "Redondo"),
    ("TUB", "Tubo"),
    ("TYA", "Tubular"),
    ("HR", "Tubo"),
    ("VAR", "Varilla"),
    ("VIG", "Viga"),
)


def etiqueta_tipo_perfil_mrl(
    material: str,
    codigo: str = "",
    catalogo: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Etiqueta corta de familia (Ángulo, PTR, Solera…) para tablas MRL."""
    parsed = parse_clasificacion_largo(material)
    perfil = parsed.get("perfil_estructural")
    cod = (
        str(codigo or "").strip().upper()
        or str(parsed.get("codigo_herinox") or "").strip().upper()
        or extraer_codigo_herinox_combo(material)
    )

    if not perfil and cod:
        placa = buscar_placa_herinox(material, 0.0, catalogo=catalogo, codigo_hint=cod)
        if placa:
            perfil = placa.get("perfil_estructural")

    if perfil:
        key = str(perfil).upper()
        return ETIQUETA_TIPO_PERFIL_MRL.get(key, str(perfil).title())

    if cod:
        for pref, etiqueta in _PREFIJO_CODIGO_TIPO_MRL:
            if cod.startswith(pref):
                return etiqueta

    return "Largo"


def descripcion_herinox_mrl(
    codigo: str,
    material: str = "",
    catalogo: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Descripción del tarjetón Herinox (columna descripcion), con fallback a clasificación."""
    cod = (
        str(codigo or "").strip().upper()
        or extraer_codigo_herinox_combo(material)
    )
    if not cod:
        return ""
    placa = buscar_placa_herinox(material, 0.0, catalogo=catalogo, codigo_hint=cod)
    if not placa:
        return ""
    desc = str(placa.get("descripcion") or "").strip()
    if desc:
        return desc
    return str(placa.get("clasificacion") or "").strip()


def datos_material_requerido_pedido(
    material: str,
    cantidad: int,
    catalogo: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Pedido Cominox: largo comercial del catálogo Herinox por código
    y costo = precio unitario × cantidad de barras estándar.
    """
    codigo = extraer_codigo_herinox_combo(material)
    info = enriquecer_fila_pedido(
        material,
        0.0,
        cantidad=max(1, int(cantidad or 1)),
        catalogo=catalogo,
        codigo_hint=codigo,
    )
    largo_in = float(info.get("largo_stock_in") or 0)
    if largo_in <= 0:
        largo_ft = float(info.get("largo_ft") or 0)
        if largo_ft > 0:
            largo_in = round(largo_ft * 12.0, 2)
    return {
        "codigo": str(info.get("codigo") or codigo or "").strip(),
        "largo": largo_in,
        "costo": info.get("costo"),
        "costo_unitario": info.get("costo_unitario"),
    }


def normalizar_fila_lista_largos(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Añade campos parseados a una fila de lista_largos_job / API lista-largos.
    """
    clasificacion = str(
        row.get("clasificacion") or row.get("material") or row.get("nombre") or ""
    ).strip()
    parsed = parse_clasificacion_largo(clasificacion)
    largo_in = float(row.get("largo_in") or row.get("largo") or 0)

    out = dict(row)
    out["clasificacion"] = clasificacion or out.get("clasificacion")
    out["material_key"] = parsed.get("display_material") or clasificacion or "SIN CLASIFICACION"
    out["perfil_estructural"] = parsed.get("perfil_estructural")
    out["material_grade"] = parsed.get("material_grade")
    out["ancho_in"] = parsed.get("ancho_in")
    out["espesor_in"] = parsed.get("espesor_in")
    out["largo_ft_catalogo"] = largo_pulgadas_a_pies(largo_in) if largo_in > 0 else None
    return out


def _formato_espesor_display(espesor: Optional[float]) -> str:
    if espesor is None or espesor <= 0:
        return "N/D"
    fracciones = {
        0.125: "1/8",
        0.1875: "3/16",
        0.25: "1/4",
        0.3125: "5/16",
        0.375: "3/8",
        0.5: "1/2",
        0.625: "5/8",
        0.75: "3/4",
        1.0: "1",
    }
    for valor, texto in fracciones.items():
        if abs(espesor - valor) < 0.02:
            return texto
    if abs(espesor - round(espesor)) < 0.001:
        return str(int(round(espesor)))
    return str(round(espesor, 3)).rstrip("0").rstrip(".")


def construir_etiqueta_clasificacion(placa: Dict[str, Any]) -> str:
    """
    Etiqueta para nesting (parse_clasificacion_largo) con espesor inferido como en Herinox.
    """
    perfil = placa.get("perfil_estructural") or ""
    ancho = placa.get("ancho_in") or 0
    esp = _formato_espesor_display(placa.get("espesor_in"))
    codigo = str(placa.get("codigo") or "").strip()

    if perfil == "SOLERA":
        if ancho > 0 and esp != "N/D":
            return f"Solera {ancho:g} x {esp}"
        return f"Solera {codigo}".strip() if codigo else "Solera (sin dimensiones)"

    if perfil == "ANGULO":
        if ancho > 0 and esp != "N/D":
            return f"Ángulo {ancho:g} x {ancho:g} x {esp}"
        return f"Ángulo {codigo}".strip() if codigo else "Ángulo (sin dimensiones)"

    if perfil == "CANAL":
        lbft = placa.get("peso_lb_ft")
        if lbft and ancho > 0:
            return f"Canal {ancho:g} x {lbft:g} lb/ft"
        return f"Canal {ancho:g}" if ancho > 0 else f"Canal {codigo}".strip()

    if perfil in ("VIGA", "VIGA IPR"):
        if ancho > 0:
            return f"Viga IPR {ancho:g}" if perfil == "VIGA IPR" else f"Viga {ancho:g}"
        return f"Viga IPR {codigo}".strip() if perfil == "VIGA IPR" else f"Viga {codigo}".strip()

    if perfil == "PTR":
        if ancho > 0 and esp != "N/D":
            return f"PTR {ancho:g} x {ancho:g} x {esp}"
        if ancho > 0:
            return f"PTR {ancho:g}"
        return f"PTR {codigo}".strip() if codigo else "PTR"

    if perfil == "TUBO":
        return f"TUBO {codigo}".strip() if codigo else "TUBO"

    if perfil == "VARILLA":
        return f"Varilla {codigo}".strip() if codigo else "Varilla Roscada"

    if codigo:
        return f"{perfil} {codigo}".strip()
    return perfil or "No Clasificado"


def exportar_catalogo_ilogic_csv(
    ruta_salida: str,
    solo_disponibles: bool = False,
) -> int:
    """
    Genera catalogo_largos_herinox.csv para la regla iLogic (delimitador |).
    Ejecutar tras actualizar precios en React-Herinox.
    """
    catalogo = _cargar_placas_largos_desde_herinox(solo_disponibles=solo_disponibles)
    lineas = [
        "codigo|clasificacion|perfil_estructural|material_grade|ancho_in|espesor_in|largo_ft|lb_calculadas|costo_mxn|disponible"
    ]

    vistos_codigo = set()
    for placa in catalogo:
        codigo = str(placa.get("codigo") or "").strip()
        if not codigo or codigo in vistos_codigo:
            continue
        vistos_codigo.add(codigo)

        etiqueta = construir_etiqueta_clasificacion(placa)
        esp = placa.get("espesor_in")
        esp_txt = "" if esp is None else str(esp)

        lineas.append(
            "|".join(
                [
                    codigo,
                    etiqueta,
                    str(placa.get("perfil_estructural") or ""),
                    str(placa.get("material_grade") or ""),
                    str(placa.get("ancho_in") or ""),
                    esp_txt,
                    str(placa.get("largo_ft") or ""),
                    str(placa.get("lb_calculadas") or ""),
                    str(placa.get("costo_actual") or ""),
                    "1" if placa.get("disponible") else "0",
                ]
            )
        )

    from pathlib import Path

    destino = Path(ruta_salida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("\n".join(lineas) + "\n", encoding="utf-8-sig")
    return len(lineas) - 1


def asegurar_columnas_lista_largos_job(cursor) -> None:
    """Columnas opcionales para cruce con Herinox en lista_largos_job."""
    alteraciones = [
        "ADD COLUMN IF NOT EXISTS perfil_estructural TEXT",
        "ADD COLUMN IF NOT EXISTS material_grade TEXT",
        "ADD COLUMN IF NOT EXISTS ancho_in NUMERIC(12,3)",
        "ADD COLUMN IF NOT EXISTS espesor_in NUMERIC(12,3)",
        "ADD COLUMN IF NOT EXISTS herinox_codigo TEXT",
        "ADD COLUMN IF NOT EXISTS material_key TEXT",
        "ADD COLUMN IF NOT EXISTS proceso TEXT",
    ]
    for col in alteraciones:
        cursor.execute(f"ALTER TABLE public.lista_largos_job {col}")
