"""Candado: post-export ANS dispara CypTube auto-nest (Modo B)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_build_cmd_incluye_skip_wait_y_dry_run() -> None:
    from modules.dxf_export.cyptube_bridge import build_auto_nest_cmd

    prefs = {
        "cyptube_main": r"C:\fake\CypTube\main.py",
        "python_exe": r"C:\fake\python.exe",
        "skip_wait": True,
        "dry_run": True,
    }
    # python_exe inexistente → cae a sys.executable; forzamos path en resolve vía archivo temp
    with tempfile.TemporaryDirectory() as tmp:
        py = Path(tmp) / "python.exe"
        py.write_bytes(b"")
        main = Path(tmp) / "main.py"
        main.write_text("#", encoding="utf-8")
        prefs["python_exe"] = str(py)
        prefs["cyptube_main"] = str(main)
        nest = Path(tmp) / "NESTEOS DE COBRE"
        nest.mkdir()
        cmd = build_auto_nest_cmd(nest, prefs=prefs)
    assert cmd[0] == str(py)
    assert cmd[1] == str(main)
    assert cmd[2] == "auto-nest"
    assert "--nesteos-dir" in cmd
    assert "--skip-wait" in cmd
    assert "--dry-run" in cmd


def test_launch_disabled_no_popen() -> None:
    from modules.dxf_export.cyptube_bridge import launch_cyptube_auto_nest

    calls: list = []

    def fake_popen(*_a, **_k):
        calls.append(True)
        raise AssertionError("no debe lanzar")

    logs: list[str] = []
    result = launch_cyptube_auto_nest(
        r"C:\no\existe",
        log_fn=logs.append,
        prefs={"enabled": False, "cyptube_main": r"C:\x\main.py"},
        popen=fake_popen,
    )
    assert result.skipped and not result.launched
    assert not calls
    assert any("deshabilitado" in m for m in logs)


def test_launch_ok_tras_json() -> None:
    from modules.dxf_export.cyptube_bridge import launch_cyptube_auto_nest

    with tempfile.TemporaryDirectory() as tmp:
        nest = Path(tmp) / "NESTEOS DE COBRE"
        nest.mkdir()
        (nest / "cyptube_verticales.json").write_text("{}", encoding="utf-8")
        main = Path(tmp) / "main.py"
        main.write_text("# cyptube", encoding="utf-8")
        py = Path(tmp) / "python.exe"
        py.write_bytes(b"")

        captured: dict = {}

        class FakeProc:
            pid = 4242

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["kwargs"] = kwargs
            return FakeProc()

        logs: list[str] = []
        result = launch_cyptube_auto_nest(
            nest,
            log_fn=logs.append,
            prefs={
                "enabled": True,
                "cyptube_main": str(main),
                "python_exe": str(py),
                "skip_wait": True,
                "dry_run": False,
                "new_console": False,
            },
            popen=fake_popen,
        )
        assert result.launched and result.pid == 4242
        assert captured["cmd"][2] == "auto-nest"
        assert str(nest.resolve()) in captured["cmd"] or os.path.abspath(str(nest)) in captured["cmd"]
        assert "--skip-wait" in captured["cmd"]
        assert any("auto-nest lanzado" in m for m in logs)


def test_servidor_unc_pasa_al_cmd_sin_romper() -> None:
    from modules.dxf_export.cyptube_bridge import (
        build_auto_nest_cmd,
        classify_nesteos_destino,
        resolve_nesteos_dir_for_rpa,
    )

    unc = (
        r"\\192.168.2.80\Users\Administrator\Desktop\Grupo Arga Metals"
        r"\ARGA METALS CORPORATE SYSTEM\TANKS\CLIENTE\JOB\NESTING\NESTEOS DE COBRE"
    )
    assert classify_nesteos_destino(unc) == "servidor"
    mapped, dest = resolve_nesteos_dir_for_rpa(unc, prefs={"path_maps": []})
    assert dest == "servidor"
    assert mapped.startswith("\\\\192.168.2.80")
    assert "NESTEOS DE COBRE" in mapped

    with tempfile.TemporaryDirectory() as tmp:
        py = Path(tmp) / "python.exe"
        py.write_bytes(b"")
        main = Path(tmp) / "main.py"
        main.write_text("#", encoding="utf-8")
        cmd = build_auto_nest_cmd(
            unc,
            prefs={
                "cyptube_main": str(main),
                "python_exe": str(py),
                "skip_wait": True,
                "dry_run": False,
                "path_maps": [],
            },
        )
    assert "--nesteos-dir" in cmd
    idx = cmd.index("--nesteos-dir")
    assert cmd[idx + 1].startswith("\\\\192.168.2.80")
    assert classify_nesteos_destino(r"C:\Users\x\Desktop\Nesteos Locales\A\NESTEOS DE COBRE") == "local"


def test_path_maps_remap_servidor() -> None:
    from modules.dxf_export.cyptube_bridge import apply_path_maps

    src = r"\\192.168.2.80\Users\Administrator\Desktop\Grupo Arga Metals"
    path = src + r"\ARGA METALS CORPORATE SYSTEM\TANKS\X\NESTEOS DE COBRE"
    out = apply_path_maps(
        path,
        prefs={
            "path_maps": [
                {"from": src, "to": r"Z:\Grupo Arga Metals"},
            ]
        },
    )
    assert out.lower().startswith(r"z:\grupo arga metals".lower())
    assert out.lower().endswith(r"nesteos de cobre")


def test_config_default_file_existe() -> None:
    cfg = ROOT / "_config" / "cyptube_bridge.json"
    assert cfg.is_file(), cfg
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "enabled" in data
    assert "cyptube_main" in data


if __name__ == "__main__":
    test_build_cmd_incluye_skip_wait_y_dry_run()
    test_launch_disabled_no_popen()
    test_launch_ok_tras_json()
    test_servidor_unc_pasa_al_cmd_sin_romper()
    test_path_maps_remap_servidor()
    test_config_default_file_existe()
    print("[OK] CyPTube bridge auto-nest")
