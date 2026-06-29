import json
import os
import re
import socket
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

import config


@dataclass
class HerinoxSyncResult:
    ok: bool
    updated_rows: int = 0
    matched_codes: int = 0
    sheet_count: int = 0
    source: str = "none"
    dof_rate: float = 18.5
    dof_source: str = "FALLBACK"
    message: str = ""
    updated_items: List[dict] = field(default_factory=list)
    nominal_by_code: Dict[str, str] = field(default_factory=dict)


class HerinoxPlateSync:
    """Carga placas desde react-Herinox (API o PostgreSQL) para nesting y pestaña SHEETS."""

    SHEET_EMPRESA = "STOCK GRUPO ARGA"
    SHEET_PROVEEDOR = "STOCK PROVEEDOR"

    LOGIN_PATH = "/api/auth/login"
    PLATES_PATH = "/api/plates"
    STEEL_GAUGE_TO_INCHES = {
        3: 0.2391, 4: 0.2242, 5: 0.2092, 6: 0.1943, 7: 0.1793, 8: 0.1644,
        9: 0.1495, 10: 0.1345, 11: 0.1196, 12: 0.1046, 13: 0.0897, 14: 0.0747,
        15: 0.0673, 16: 0.0598, 18: 0.0478, 20: 0.0359,
    }
    STAINLESS_GAUGE_TO_INCHES = {
        10: 0.1406, 11: 0.125, 12: 0.1094, 14: 0.0781, 16: 0.0625, 18: 0.05, 20: 0.0375,
    }
    ALUMINUM_GAUGE_TO_INCHES = {
        8: 0.1285, 9: 0.1144, 10: 0.1019, 11: 0.0907, 12: 0.0808,
        13: 0.072, 14: 0.0641, 15: 0.0571, 16: 0.0508, 17: 0.0453,
        18: 0.0403, 19: 0.0359, 20: 0.032,
    }


    def __init__(self):
        self.settings_file = str(
            getattr(config, "HERINOX_SYNC_SETTINGS_FILE", config.ruta_persistente("herinox_sync.local.json"))
        )
        settings = self._load_settings()

        self.base_url = str(settings.get("api_base_url", "")).rstrip("/")
        self.email = str(settings.get("email", "")).strip()
        self.password = str(settings.get("password", "")).strip()
        self.timeout = int(settings.get("timeout_seconds", 8))
        self.enabled = bool(settings.get("enabled", False))
        self.db_enabled = bool(settings.get("db_enabled", True))
        self.db_config = {
            "host": str(settings.get("db_host", "")).strip(),
            "port": int(settings.get("db_port", 5439)),
            "database": str(settings.get("db_name", "")).strip(),
            "user": str(settings.get("db_user", "")).strip(),
            "password": str(settings.get("db_password", "")).strip(),
            "connect_timeout": int(settings.get("db_connect_timeout", 5)),
        }
        self._rows_empresa: List[list] = []
        self._rows_proveedor: List[list] = []
        self._prev_snapshot: Dict[str, list] = {}

    def get_sheet_rows(self) -> tuple[List[list], List[list]]:
        """Devuelve (datos_empresa, datos_proveedor) en formato de filas del nesting."""
        return list(self._rows_empresa), list(self._rows_proveedor)

    def _cargar_respaldo_local(self) -> Optional[HerinoxSyncResult]:
        try:
            from modules.herinox_catalog_cache import cargar_respaldo_placas

            emp, prov, meta = cargar_respaldo_placas()
            if not emp and not prov:
                return None
            self._rows_empresa = emp
            self._rows_proveedor = prov
            self._prev_snapshot = {
                str(row[2]).strip().upper(): row for row in emp + prov if len(row) > 2
            }
            dof_rate = float(meta.get("dof_rate") or 18.5)
            dof_source = str(meta.get("dof_source") or "CACHE")
            codes = {
                str(row[2]).strip().upper()
                for row in emp + prov
                if len(row) > 2 and str(row[2]).strip()
            }
            return HerinoxSyncResult(
                ok=True,
                matched_codes=len(codes),
                sheet_count=2,
                source=str(meta.get("source") or "cache"),
                dof_rate=dof_rate,
                dof_source=dof_source,
                message=(
                    f"Inventario desde respaldo local ({meta.get('updated_at', '?')}) — "
                    "Herinox no disponible."
                ),
                nominal_by_code={},
            )
        except Exception as exc:
            print(f"[HERINOX CACHE] No se pudo cargar respaldo local: {exc}")
            return None

    def run(self, plates_xlsx_path: str | None = None) -> HerinoxSyncResult:
        """Carga/refresca inventario de placas desde Herinox. plates_xlsx_path se ignora (legacy)."""
        _ = plates_xlsx_path
        return self.refresh()

    def refresh(self) -> HerinoxSyncResult:
        api_error = ""
        plates_by_code: Dict[str, dict] = {}
        source = "none"

        if self.base_url and self.email and self.password:
            try:
                token = self._login()
                plates_by_code = self._fetch_plates(token)
                source = "api"
            except Exception as exc:
                api_error = str(exc)
        elif self.enabled:
            api_error = f"Faltan credenciales API en {self.settings_file}"

        if not plates_by_code and self.db_enabled:
            db_error = ""
            host = str(self.db_config.get("host") or "").strip()
            port = int(self.db_config.get("port") or 5439)
            probe_s = min(2, max(1, int(self.db_config.get("connect_timeout", 5))))
            if host and not self._host_reachable(host, port, probe_s):
                db_error = f"sin conexion a {host}:{port} (red/VPN)"
            else:
                try:
                    plates_by_code = self._fetch_plates_from_db()
                    source = "postgres"
                except Exception as exc:
                    db_error = str(exc)
            if not plates_by_code and not db_error:
                db_error = "sin datos desde PostgreSQL"
            if not plates_by_code and db_error:
                fallback = self._cargar_respaldo_local()
                if fallback:
                    if api_error:
                        fallback.message = (
                            f"{fallback.message} (API falló: {api_error}; DB: {db_error})"
                        )
                    else:
                        fallback.message = f"{fallback.message} (DB: {db_error})"
                    return fallback
                if api_error:
                    return HerinoxSyncResult(
                        ok=False,
                        source="none",
                        message=f"Fallo API ({api_error}) y DB ({db_error}).",
                        updated_items=[],
                        nominal_by_code={},
                    )
                return HerinoxSyncResult(
                    ok=False,
                    source="none",
                    message=f"Fallo DB Herinox: {db_error}",
                    updated_items=[],
                    nominal_by_code={},
                )

        if not plates_by_code:
            detalle = f" API: {api_error}" if api_error else ""
            fallback = self._cargar_respaldo_local()
            if fallback:
                return fallback
            return HerinoxSyncResult(
                ok=False,
                source="none",
                message=f"No se obtuvieron placas desde Herinox.{detalle}",
                updated_items=[],
                nominal_by_code={},
            )

        dof_rate, dof_source = self._obtener_tipo_cambio_dof()
        if not dof_rate or dof_rate <= 0:
            dof_rate, dof_source = 18.5, "FALLBACK"

        nominal_by_code = {
            code: self._extract_nominal(remote)
            for code, remote in plates_by_code.items()
            if code
        }
        empresa_rows, proveedor_rows, updated_items, updated_rows = self._build_sheet_rows(
            plates_by_code, dof_rate, self._prev_snapshot
        )
        self._rows_empresa = empresa_rows
        self._rows_proveedor = proveedor_rows
        self._prev_snapshot = {
            str(row[2]).strip().upper(): row for row in empresa_rows + proveedor_rows if len(row) > 2
        }

        try:
            from modules.herinox_catalog_cache import guardar_snapshot_placas

            paths = guardar_snapshot_placas(
                empresa_rows,
                proveedor_rows,
                source=source,
                dof_rate=float(dof_rate),
                dof_source=dof_source,
            )
            print(
                f"[HERINOX CACHE] Respaldo placas guardado | json={paths.get('json', '')} | "
                f"xlsx={paths.get('xlsx', '')}"
            )
        except Exception as cache_exc:
            print(f"[HERINOX CACHE] WARN: no se pudo guardar respaldo placas: {cache_exc}")

        suffix = ""
        if not self.enabled:
            suffix = " (auto-sync desactivada; datos cargados directo desde Herinox)"

        return HerinoxSyncResult(
            ok=True,
            updated_rows=updated_rows,
            matched_codes=len(plates_by_code),
            sheet_count=2,
            source=source,
            dof_rate=float(dof_rate),
            dof_source=dof_source,
            message=f"Inventario cargado desde Herinox via {source} (TC {dof_source}: {dof_rate:.4f}).{suffix}",
            updated_items=updated_items,
            nominal_by_code=nominal_by_code,
        )

    def _load_settings(self) -> dict:
        settings = {
            "enabled": bool(getattr(config, "HERINOX_SYNC_ENABLED", False)),
            "api_base_url": str(getattr(config, "HERINOX_API_BASE_URL", "")).strip(),
            "email": str(getattr(config, "HERINOX_SYNC_EMAIL", "")).strip(),
            "password": str(getattr(config, "HERINOX_SYNC_PASSWORD", "")).strip(),
            "timeout_seconds": int(getattr(config, "HERINOX_SYNC_TIMEOUT_SECONDS", 8)),
            "db_enabled": True,
            "db_host": str(getattr(config, "HERINOX_DB_HOST", "")).strip(),
            "db_port": int(getattr(config, "HERINOX_DB_PORT", 5439)),
            "db_name": str(getattr(config, "HERINOX_DB_NAME", "")).strip(),
            "db_user": str(getattr(config, "HERINOX_DB_USER", "")).strip(),
            "db_password": str(getattr(config, "HERINOX_DB_PASSWORD", "")).strip(),
            "db_connect_timeout": int(getattr(config, "HERINOX_DB_CONNECT_TIMEOUT", 5)),
        }

        if not os.path.exists(self.settings_file):
            self._write_settings_template(settings)
            return settings

        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                settings["enabled"] = bool(loaded.get("enabled", settings["enabled"]))
                settings["api_base_url"] = str(loaded.get("api_base_url", settings["api_base_url"])).strip()
                settings["email"] = str(loaded.get("email", settings["email"])).strip()
                settings["password"] = str(loaded.get("password", settings["password"])).strip()
                settings["timeout_seconds"] = int(loaded.get("timeout_seconds", settings["timeout_seconds"]))
                settings["db_enabled"] = bool(loaded.get("db_enabled", settings["db_enabled"]))
                settings["db_host"] = str(loaded.get("db_host", settings["db_host"])).strip()
                settings["db_port"] = int(loaded.get("db_port", settings["db_port"]))
                settings["db_name"] = str(loaded.get("db_name", settings["db_name"])).strip()
                settings["db_user"] = str(loaded.get("db_user", settings["db_user"])).strip()
                settings["db_password"] = str(loaded.get("db_password", settings["db_password"])).strip()
                settings["db_connect_timeout"] = int(
                    loaded.get("db_connect_timeout", settings["db_connect_timeout"])
                )
        except Exception:
            # Si el archivo local está dañado, seguimos con fallback para no romper el arranque.
            pass

        return settings

    def _write_settings_template(self, defaults: dict) -> None:
        try:
            parent = os.path.dirname(self.settings_file)
            if parent:
                os.makedirs(parent, exist_ok=True)
            payload = {
                "enabled": defaults.get("enabled", True),
                "api_base_url": defaults.get("api_base_url", "http://192.168.2.80:4000"),
                "email": defaults.get("email", ""),
                "password": defaults.get("password", ""),
                "timeout_seconds": defaults.get("timeout_seconds", 8),
                "db_enabled": defaults.get("db_enabled", True),
                "db_host": defaults.get("db_host", "192.168.2.80"),
                "db_port": defaults.get("db_port", 5439),
                "db_name": defaults.get("db_name", "herinox"),
                "db_user": defaults.get("db_user", "herinox"),
                "db_password": defaults.get("db_password", "herinox_password_2024"),
                "db_connect_timeout": defaults.get("db_connect_timeout", 5),
                "_nota": "Sync intenta API primero; si falla, usa PostgreSQL directo.",
            }
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _login(self) -> str:
        login_url = f"{self.base_url}{self.LOGIN_PATH}"
        payload = json.dumps({"email": self.email, "password": self.password}).encode("utf-8")
        req = urllib.request.Request(login_url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Login HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"No se pudo conectar al login de Herinox: {exc.reason}") from exc

        token = str(data.get("token", "")).strip()
        if not token:
            raise RuntimeError("Login sin token en la respuesta.")
        return token

    def _fetch_plates(self, token: str) -> Dict[str, dict]:
        plates_url = f"{self.base_url}{self.PLATES_PATH}"
        req = urllib.request.Request(plates_url, method="GET")
        req.add_header("Authorization", f"Bearer {token}")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                rows = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Plates HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"No se pudo consultar /api/plates: {exc.reason}") from exc

        if not isinstance(rows, list):
            raise RuntimeError("La respuesta de /api/plates no es una lista.")

        by_code: Dict[str, dict] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            inv_type = str(row.get("inventoryType", "PLATE")).upper()
            if inv_type not in {"PLATE", "PLACA", "LAMINA"}:
                continue
            code = self._norm_code(row.get("codigo"))
            if not code:
                continue
            by_code[code] = row
        return by_code

    @staticmethod
    def _host_reachable(host: str, port: int, timeout_s: float = 1.5) -> bool:
        try:
            with socket.create_connection((host, int(port)), timeout=max(0.5, float(timeout_s))):
                return True
        except OSError:
            return False

    def _fetch_plates_from_db(self) -> Dict[str, dict]:
        conn = None
        cur = None
        try:
            conn = psycopg2.connect(**self.db_config)
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """
                SELECT
                    p."codigo",
                    p."material",
                    p."thickness",
                    p."thk",
                    p."length",
                    p."width",
                    p."lbCalculadas",
                    p."costoActual",
                    p."costoActualUsd",
                    p."costoPorLbUsd",
                    p."disponible",
                    ph."newPrice" AS precio_hist,
                    ph."newPriceUsd" AS precio_hist_usd,
                    ph."pricePerLbUsd" AS precio_lb_hist_usd
                FROM "Plate" p
                LEFT JOIN LATERAL (
                    SELECT "newPrice", "newPriceUsd", "pricePerLbUsd"
                    FROM "PriceHistory"
                    WHERE "plateId" = p."id"
                      AND COALESCE("newPrice", 0) > 0
                      AND "approvalStatus" IN ('APPROVED', 'PENDING')
                    ORDER BY
                      CASE WHEN "approvalStatus" = 'APPROVED' THEN 0 ELSE 1 END,
                      "changedAt" DESC NULLS LAST
                    LIMIT 1
                ) ph ON TRUE
                WHERE p."codigo" IS NOT NULL
                  AND TRIM(p."codigo") <> ''
                  AND COALESCE(p."inventoryType", 'PLATE') IN ('PLATE', 'PLACA', 'LAMINA')
                """
            )
            rows = cur.fetchall() or []
            by_code: Dict[str, dict] = {}
            for row in rows:
                code = self._norm_code(row.get("codigo"))
                if not code:
                    continue
                costo = self._first_positive(row.get("costoActual"), row.get("precio_hist"))
                costo_usd = self._first_positive(row.get("costoActualUsd"), row.get("precio_hist_usd"))
                usd_lb = self._first_positive(row.get("costoPorLbUsd"), row.get("precio_lb_hist_usd"))
                by_code[code] = {
                    "codigo": code,
                    "material": row.get("material"),
                    "thickness": row.get("thickness"),
                    "thk": row.get("thk"),
                    "length": row.get("length"),
                    "width": row.get("width"),
                    "lbCalculadas": row.get("lbCalculadas"),
                    "costoActual": costo,
                    "costoActualUsd": costo_usd,
                    "costoPorLbUsd": usd_lb,
                    "disponible": row.get("disponible"),
                }
            return by_code
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

    _ROW_FIELDS = (
        ("Thickness", 0),
        ("Material", 1),
        ("Arga Code", 2),
        ("Length", 3),
        ("Width", 4),
        ("LB", 5),
        ("MXN", 6),
        ("$$/LB", 7),
        ("Stock", 8),
    )

    def _build_sheet_rows(
        self,
        plates_by_code: Dict[str, dict],
        dof_rate: float,
        prev_snapshot: Dict[str, list],
    ) -> tuple[List[list], List[list], List[dict], int]:
        empresa_rows: List[list] = []
        proveedor_rows: List[list] = []
        updated_items: List[dict] = []
        updated_rows = 0

        for code in sorted(plates_by_code.keys()):
            remote = plates_by_code[code]
            origen = self._origen_placa(remote)
            row = self._remote_to_placa_row(remote, dof_rate, origen)
            if origen == "PROVEEDOR":
                proveedor_rows.append(row)
                sheet_name = self.SHEET_PROVEEDOR
            else:
                empresa_rows.append(row)
                sheet_name = self.SHEET_EMPRESA

            prev = prev_snapshot.get(code)
            if not prev:
                continue
            changed_fields, changes_detail = self._diff_placa_row(prev, row)
            if changed_fields:
                updated_rows += 1
                updated_items.append(
                    {
                        "sheet": sheet_name,
                        "arga_code": code,
                        "fields": changed_fields,
                        "changes": changes_detail,
                    }
                )

        return empresa_rows, proveedor_rows, updated_items, updated_rows

    @staticmethod
    def _origen_placa(remote: dict) -> str:
        """Clasifica placa en STOCK EMPRESA o STOCK PROVEEDOR."""
        for key in ("origen", "proveedor", "stockSource", "stockType"):
            txt = str(remote.get(key) or "").strip().upper()
            if "PROVEEDOR" in txt or txt in {"PROV", "SUPPLIER", "EXTERNO"}:
                return "PROVEEDOR"
        return "EMPRESA"

    def _remote_to_placa_row(self, remote: dict, dof_rate: float, origen: str) -> list:
        fields = self._build_herinox_field_values(remote, dof_rate)
        code = self._norm_code(remote.get("codigo"))
        row = [
            fields.get("Thickness", ""),
            fields.get("Material", ""),
            code,
            fields.get("Length", ""),
            fields.get("Width", ""),
            fields.get("LB", ""),
            fields.get("MXN", ""),
            fields.get("$$/LB", ""),
            fields.get("Stock", ""),
        ]
        precio_usd = self._to_float_safe(fields.get("$$/LB", "")) or 0.0
        row.append(origen)
        row.append(precio_usd)
        return row

    def _diff_placa_row(self, prev: list, new: list) -> tuple[List[str], List[dict]]:
        changed_fields: List[str] = []
        changes_detail: List[dict] = []
        for field_name, idx in self._ROW_FIELDS:
            old_val = prev[idx] if len(prev) > idx else ""
            new_val = new[idx] if len(new) > idx else ""
            if self._values_equivalent(old_val, new_val):
                continue
            changed_fields.append(field_name)
            changes_detail.append(
                {
                    "field": field_name,
                    "before": self._format_for_compare(old_val),
                    "after": self._format_for_compare(new_val),
                }
            )
        return changed_fields, changes_detail

    @staticmethod
    def _norm_code(value: Optional[object]) -> str:
        if value is None:
            return ""
        return str(value).strip().upper().replace(" ", "")

    @staticmethod
    def _clean_number(value: Optional[object], decimals: Optional[int] = None):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return ""
        if decimals is None:
            if number.is_integer():
                return int(number)
            return number
        return round(number, decimals)

    def _resolve_thickness_decimal(self, remote: dict):
        thickness_mode = str(remote.get("thickness") or "").strip().lower()
        thk_raw = str(remote.get("thk") or "").strip()
        material = str(remote.get("material") or "").upper()
        thk_num = self._parse_fractional(thk_raw)
        if thk_num is None:
            return ""

        if thickness_mode == "cal":
            gauge = int(round(thk_num))
            if "SSTL" in material or "INOX" in material:
                val = self.STAINLESS_GAUGE_TO_INCHES.get(gauge)
            elif "AL " in material or "ALUMIN" in material:
                val = self.ALUMINUM_GAUGE_TO_INCHES.get(gauge)
            else:
                val = self.STEEL_GAUGE_TO_INCHES.get(gauge)
            if val is not None:
                return self._fmt_decimal(val)
            # En modo CAL, si no hay mapa y el número parece calibre (ej. 8, 11, 14),
            # evitamos escribir ese calibre nominal como espesor decimal.
            if thk_num > 2:
                return ""

        return self._fmt_decimal(thk_num)

    @staticmethod
    def _parse_fractional(text: str) -> Optional[float]:
        t = str(text or "").strip()
        if not t:
            return None
        t = t.replace(",", ".")
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

    @staticmethod
    def _fmt_decimal(value: float) -> str:
        return f"{float(value):.4f}".rstrip("0").rstrip(".")

    def _build_herinox_field_values(self, remote: dict, dof_rate: float) -> Dict[str, object]:
        thickness_val = self._resolve_thickness_decimal(remote)
        lb_val = self._clean_number(remote.get("lbCalculadas"), decimals=2)
        mxn_val = self._clean_number(remote.get("costoActual"), decimals=2)
        usd_total_herinox = self._clean_number(
            self._first_positive(remote.get("costoActualUsd"), remote.get("newPriceUsd")),
            decimals=4,
        )
        usd_per_lb = self._clean_number(
            self._first_positive(remote.get("costoPorLbUsd"), remote.get("pricePerLbUsd")),
            decimals=6,
        )
        if usd_per_lb == "" and usd_total_herinox != "" and lb_val != "":
            usd_per_lb = self._calcular_usd_por_lb_desde_total(usd_total_herinox, lb_val)
        if usd_per_lb == "":
            usd_per_lb = self._calcular_usd_por_lb(mxn_val, lb_val, dof_rate)
        stock_status = self._to_disponibilidad(remote.get("disponible")) or "NO DISPONIBLE"

        values: Dict[str, object] = {
            "Material": remote.get("material"),
            "Length": self._clean_number(remote.get("length")),
            "Width": self._clean_number(remote.get("width")),
            "LB": lb_val,
            "MXN": mxn_val,
            "$$/LB": usd_per_lb,
            "Stock": stock_status,
        }
        if str(thickness_val).strip() != "":
            values["Thickness"] = thickness_val
        return values

    @staticmethod
    def _to_float_safe(value):
        try:
            txt = str(value).strip()
            if not txt:
                return None
            txt = txt.replace(",", "")
            return float(txt)
        except Exception:
            return None

    @staticmethod
    def _format_for_compare(value) -> str:
        if value is None:
            return ""
        txt = str(value).strip()
        if not txt or txt.lower() == "nan":
            return ""
        num = HerinoxPlateSync._to_float_safe(txt)
        if num is not None:
            return f"{num:.6f}".rstrip("0").rstrip(".")
        return txt.upper()

    @staticmethod
    def _values_equivalent(a, b) -> bool:
        a_txt = HerinoxPlateSync._format_for_compare(a)
        b_txt = HerinoxPlateSync._format_for_compare(b)
        if a_txt == b_txt:
            return True
        a_num = HerinoxPlateSync._to_float_safe(a_txt)
        b_num = HerinoxPlateSync._to_float_safe(b_txt)
        if a_num is not None and b_num is not None:
            return abs(a_num - b_num) < 1e-9
        return False

    @staticmethod
    def _first_positive(*values):
        for value in values:
            try:
                number = float(value)
                if number > 0:
                    return round(number, 2)
            except (TypeError, ValueError):
                continue
        return ""

    @staticmethod
    def _to_disponibilidad(value) -> str:
        if isinstance(value, bool):
            return "DISPONIBLE" if value else "NO DISPONIBLE"
        txt = str(value or "").strip().lower()
        if txt in {"1", "true", "si", "yes", "disponible"}:
            return "DISPONIBLE"
        if txt in {"0", "false", "no", "no disponible"}:
            return "NO DISPONIBLE"
        return ""

    @staticmethod
    def _calcular_usd_por_lb(mxn, lb, dof_rate: float):
        try:
            mxn_val = float(mxn)
            lb_val = float(lb)
            tc = float(dof_rate)
            if mxn_val > 0 and lb_val > 0 and tc > 0:
                return round((mxn_val / tc) / lb_val, 6)
        except Exception:
            pass
        return ""

    @staticmethod
    def _calcular_usd_por_lb_desde_total(usd_total, lb):
        try:
            usd_val = float(usd_total)
            lb_val = float(lb)
            if usd_val > 0 and lb_val > 0:
                return round(usd_val / lb_val, 6)
        except Exception:
            pass
        return ""

    @staticmethod
    def _extract_nominal(remote: dict) -> str:
        # "thk" en Herinox representa el valor nominal antes de convertir a decimal.
        nominal = str(remote.get("thk") or "").strip()
        return nominal or "N/A"

    def _obtener_tipo_cambio_dof(self):
        hoy = datetime.now().strftime("%d/%m/%Y")
        fecha_q = urllib.parse.quote(hoy, safe="")
        urls = [
            f"https://www.dof.gob.mx/indicadores_detalle.php?cod_tipo_indicador=158&dfecha={fecha_q}",
            "https://www.dof.gob.mx/indicadores_detalle.php?cod_tipo_indicador=158",
        ]
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        }
        patrones = [
            r"Tipo de cambio[^0-9]{0,40}([0-9]{1,2}\.[0-9]{2,6})",
            r"\bFIX\b[^0-9]{0,40}([0-9]{1,2}\.[0-9]{2,6})",
            r"([0-9]{1,2}\.[0-9]{2,6})",
        ]
        for url in urls:
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                for pat in patrones:
                    m = re.search(pat, html or "", flags=re.IGNORECASE | re.DOTALL)
                    if not m:
                        continue
                    tc = float(m.group(1))
                    if 10.0 <= tc <= 30.0:
                        return tc, "DOF"
            except Exception:
                continue
        return None, "DOF_NO_DISPONIBLE"
