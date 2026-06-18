"""Audita placas duplicadas en un workspace .arganest"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "interface"))

from nesting_workspace import cargar_workspace_desde_archivo
from modules.nesting_engine.sheet_integrity import (
    fingerprint_layout_hoja,
    _bloques_madre_rtz,
    deduplicar_hojas_grupo,
)


def _piezas_reales(madre):
    out = []
    for p in madre.get("piezas") or []:
        n = str(p.get("nombre") or "")
        if n.startswith(("REF__", "TATUAJE__", "REMANENTE__", "RETAZO_GUILLOTINA__")):
            continue
        out.append(n)
    return out


def main(path: str):
    payload = cargar_workspace_desde_archivo(path)
    res = payload.get("resultados_nesting") or payload.get("resultados") or {}
    if not res and payload.get("resultados_multilote"):
        lot0 = payload["resultados_multilote"][0] or {}
        res = lot0.get("resultados") or lot0.get("resultados_nesting") or lot0.get("data") or {}
    ui = payload.get("ui_state") or {}
    print("global_kerf:", payload.get("global_kerf_val", ui.get("global_kerf_val")), 
          "margin:", payload.get("global_margin_val", ui.get("global_margin_val")))

    for clave, grp in sorted(res.items()):
        if not isinstance(grp, dict):
            continue
        hojas = grp.get("hojas") or []
        print("\n===", clave, "hojas=", len(hojas))
        bloques = _bloques_madre_rtz(hojas)
        for bi, (madre, rtzs) in enumerate(bloques):
            pid = madre.get("placa_id")
            kerf = madre.get("kerf_usado")
            piezas = _piezas_reales(madre)
            fp = fingerprint_layout_hoja(madre)
            efi = float(madre.get("eficiencia_directa", madre.get("eficiencia", 0)) or 0)
            print(
                f"  M{bi + 1} {pid} kerf={kerf} n={len(piezas)} efi={efi:.1f}% "
                f"fp_len={len(fp)} rtz={len(rtzs)}"
            )
            print("    ", piezas)

        for i, (ma, _) in enumerate(bloques):
            for j, (mb, _) in enumerate(bloques):
                if j <= i:
                    continue
                if str(ma.get("placa_id")) != str(mb.get("placa_id")):
                    continue
                fpa, fpb = fingerprint_layout_hoja(ma), fingerprint_layout_hoja(mb)
                if not fpa or not fpb:
                    continue
                seta, setb = set(fpa), set(fpb)
                inter = len(seta & setb)
                thr = min(len(seta), len(setb)) * 0.75
                if inter >= thr:
                    print(
                        f"  SIMILAR {ma.get('placa_id')} M{i + 1} vs M{j + 1}: "
                        f"|a|={len(fpa)} |b|={len(fpb)} inter={inter}"
                    )
                    print("    a:", _piezas_reales(ma))
                    print("    b:", _piezas_reales(mb))

        ded = deduplicar_hojas_grupo(hojas)
        if len(ded) != len(hojas):
            print(f"  DEDUP would remove {len(hojas) - len(ded)} hojas")


if __name__ == "__main__":
    p = sys.argv[1] if len(sys.argv) > 1 else (
        r"C:\Users\jose_rosales\OneDrive - grupoarga.com\Escritorio\Respaldo W.O\1000 kva de prueba.arganest"
    )
    main(p)
