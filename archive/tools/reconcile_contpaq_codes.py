"""Audita y aplica equivalencias internas Herinox -> ContPAQ.

No escribe en Herinox ni ContPAQ.  Por defecto solo reporta; ``--apply-exact``
registra únicamente códigos idénticos que el endpoint de catálogo confirma.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config
from interface.material_code_mapping_service import (
    actualizar_verificacion_catalogo,
    asegurar_tabla_equivalencias,
    auditar_equivalencias_mrl,
    registrar_equivalencia,
    registrar_matches_directos_catalogo,
    sembrar_equivalencias_verificadas,
    sincronizar_codigos_contpaq_mrl,
)
from lista_largos_material_requerido import asegurar_tabla_material_requerido_ldg


def _db_config() -> dict:
    return {
        "host": getattr(config, "NESTING_DB_HOST", "192.168.2.80"),
        "database": getattr(config, "NESTING_DB_NAME", "nestingpro_db"),
        "user": getattr(config, "NESTING_DB_USER", "postgres"),
        "password": getattr(config, "NESTING_DB_PASSWORD", "nesting123"),
        "port": getattr(config, "NESTING_DB_PORT", "5433"),
    }


def _verificar_catalogo(url: str, codigos: list[str]) -> dict[str, dict]:
    if not codigos:
        return {}
    payload = json.dumps({"codigos": codigos}).encode("utf-8")
    request = urllib.request.Request(
        url.rstrip("/") + "/catalog/verify-codes",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            body = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Catálogo ContPAQ HTTP {error.code}: {raw}") from error
    resultados = body.get("results") or []
    return {
        str(item.get("codigo") or "").strip().upper(): dict(item)
        for item in resultados
        if item
    }


def _buscar_candidatos(url: str, texto: str) -> list[dict]:
    payload = json.dumps({"texto": texto, "limite": 10}).encode("utf-8")
    request = urllib.request.Request(
        url.rstrip("/") + "/catalog/search",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return list((json.loads(response.read().decode("utf-8") or "{}").get("results") or []))
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Búsqueda ContPAQ HTTP {error.code}: {raw}") from error


def _parse_mapping(valor: str) -> tuple[str, str]:
    herinox, separador, contpaq = str(valor or "").partition("=")
    if not separador or not herinox.strip() or not contpaq.strip():
        raise argparse.ArgumentTypeError("Use HERINOX=CONTPAQ, por ejemplo HR164=TUB010.")
    return herinox.strip().upper(), contpaq.strip().upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--swo", help="Limita la auditoría a una SWO.")
    parser.add_argument(
        "--catalog-url",
        default="http://192.168.2.80:8006",
        help="Base URL de InsertaPOContPaq.",
    )
    parser.add_argument(
        "--register",
        type=_parse_mapping,
        action="append",
        default=[],
        help="Equivalencia revisada HERINOX=CONTPAQ.",
    )
    parser.add_argument(
        "--verified-by",
        default="ANS_ADMIN",
        help="Usuario o fuente que revisó equivalencias manuales.",
    )
    parser.add_argument(
        "--apply-exact",
        action="store_true",
        help="Registra como VERIFIED solo códigos Herinox idénticos existentes en ContPAQ.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Permite registrar equivalencias y sincronizar snapshots MRL.",
    )
    parser.add_argument(
        "--semantic-candidates",
        action="store_true",
        help="Incluye candidatos por descripción para revisión humana; no los registra.",
    )
    args = parser.parse_args()
    if args.apply_exact and not args.apply:
        parser.error("--apply-exact requiere --apply.")
    if args.register and not args.apply:
        parser.error("--register requiere --apply.")

    import psycopg2
    from psycopg2.extras import RealDictCursor

    if args.apply:
        asegurar_tabla_material_requerido_ldg(_db_config())

    with psycopg2.connect(**_db_config()) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            if args.apply:
                asegurar_tabla_equivalencias(cursor)
                sembrar_equivalencias_verificadas(cursor)
                for herinox, contpaq in args.register:
                    registrar_equivalencia(
                        cursor,
                        herinox_codigo=herinox,
                        codigo_contpaq=contpaq,
                        estatus="VERIFIED",
                        origen="REVISION_MANUAL",
                        nota="Alta mediante reconcile_contpaq_codes.py",
                        verificado_por=args.verified_by,
                    )

            filas = auditar_equivalencias_mrl(cursor, orden_id=args.swo)
            candidatos_exactos = sorted(
                {
                    str(fila.get("herinox_codigo") or "").strip().upper()
                    for fila in filas
                    if str(fila.get("codigo_contpaq_estatus") or "").upper() != "VERIFIED"
                    and str(fila.get("herinox_codigo") or "").strip()
                }
            )
            codigos_mapeados = sorted(
                {
                    str(fila.get("codigo_contpaq") or "").strip().upper()
                    for fila in filas
                    if str(fila.get("codigo_contpaq") or "").strip()
                }
            )
            catalogo = _verificar_catalogo(
                args.catalog_url,
                sorted(set(candidatos_exactos + codigos_mapeados)),
            )

            if args.apply_exact:
                exactos = {
                    codigo: catalogo.get(codigo) or {}
                    for codigo in candidatos_exactos
                    if (catalogo.get(codigo) or {}).get("existe")
                }
                print(
                    "Matches directos:",
                    json.dumps(
                        registrar_matches_directos_catalogo(
                            cursor,
                            exactos,
                            verificado_por=args.verified_by,
                        ),
                        ensure_ascii=False,
                    ),
                )

            if args.apply:
                verificacion = actualizar_verificacion_catalogo(cursor, catalogo)
                print("Catálogo:", json.dumps(verificacion, ensure_ascii=False))
                sincronizacion = sincronizar_codigos_contpaq_mrl(
                    cursor,
                    orden_id=args.swo,
                    tipo_orden="SWO" if args.swo else None,
                    resultados_catalogo=catalogo,
                )
                print("Sincronización:", json.dumps(sincronizacion, ensure_ascii=False))
                filas = auditar_equivalencias_mrl(cursor, orden_id=args.swo)

    print("Auditoría MRL:")
    for fila in filas:
        herinox = str(fila.get("herinox_codigo") or "").strip().upper()
        candidato = catalogo.get(herinox) or {}
        candidatos = []
        if (
            args.semantic_candidates
            and str(fila.get("codigo_contpaq_estatus") or "").upper() != "VERIFIED"
        ):
            candidatos = _buscar_candidatos(
                args.catalog_url,
                str(fila.get("material") or ""),
            )
        print(
            json.dumps(
                {
                    "mrl_id": fila.get("id"),
                    "orden": fila.get("orden_id"),
                    "herinox": herinox,
                    "contpaq": fila.get("codigo_contpaq"),
                    "estatus": fila.get("codigo_contpaq_estatus"),
                    "contpaq_mismo_codigo": bool(candidato.get("existe")),
                    "descripcion_contpaq": candidato.get("descripcion"),
                    "candidatos_semanticos": candidatos,
                    "po_estatus": fila.get("po_estatus"),
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
