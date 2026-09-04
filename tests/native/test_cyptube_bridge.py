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
                "launch_delay_s": 0,
            },
            popen=fake_popen,
        )
        assert result.launched and result.pid == 4242
        assert captured["cmd"][2] == "auto-nest"
        assert str(nest.resolve()) in captured["cmd"] or os.path.abspath(str(nest)) in captured["cmd"]
        assert "--skip-wait" in captured["cmd"]
        assert any("auto-nest lanzado" in m for m in logs)


def test_launch_delay_programa_hilo() -> None:
    from modules.dxf_export.cyptube_bridge import launch_cyptube_auto_nest

    with tempfile.TemporaryDirectory() as tmp:
        nest = Path(tmp) / "NESTEOS DE COBRE"
        nest.mkdir()
        (nest / "cyptube_verticales.json").write_text("{}", encoding="utf-8")
        main = Path(tmp) / "main.py"
        main.write_text("#", encoding="utf-8")
        py = Path(tmp) / "python.exe"
        py.write_bytes(b"")

        calls: list = []

        class FakeProc:
            pid = 99

        def fake_popen(cmd, **kwargs):
            calls.append(cmd)
            return FakeProc()

        logs: list[str] = []
        result = launch_cyptube_auto_nest(
            nest,
            log_fn=logs.append,
            prefs={
                "enabled": True,
                "cyptube_main": str(main),
                "python_exe": str(py),
                "launch_delay_s": 0.05,
                "new_console": False,
            },
            popen=fake_popen,
        )
        assert result.launched and result.reason == "scheduled_0s"
        assert not calls
        assert any("programado" in m for m in logs)
        import time as _time

        _time.sleep(0.15)
        assert calls, "debe lanzar tras el delay"


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


def test_resolve_python_prefiere_venv_junto_a_main() -> None:
    from modules.dxf_export.cyptube_bridge import resolve_python_exe

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "CypTube"
        root.mkdir()
        main = root / "main.py"
        main.write_text("#", encoding="utf-8")
        venv_py = root / ".venv" / "Scripts" / "python.exe"
        venv_py.parent.mkdir(parents=True)
        venv_py.write_bytes(b"")
        resolved = resolve_python_exe({"cyptube_main": str(main), "python_exe": ""})
    assert Path(resolved).resolve() == venv_py.resolve()


def test_resolve_python_no_usa_ans_exe_cuando_frozen(monkeypatch) -> None:
    from modules.dxf_export import cyptube_bridge as bridge

    fake_ans = r"C:\Program Files\Arga\Arga Nesting Suite.exe"
    monkeypatch.setattr(bridge.sys, "frozen", True, raising=False)
    monkeypatch.setattr(bridge.sys, "executable", fake_ans)
    monkeypatch.setattr(bridge.shutil, "which", lambda _name: None)

    with tempfile.TemporaryDirectory() as tmp:
        main = Path(tmp) / "main.py"
        main.write_text("#", encoding="utf-8")
        resolved = bridge.resolve_python_exe(
            {"cyptube_main": str(main), "python_exe": ""}
        )
    assert resolved == "python"
    assert not bridge._same_exe(resolved, fake_ans)


def test_launch_skip_si_python_invalido_en_frozen(monkeypatch) -> None:
    from modules.dxf_export import cyptube_bridge as bridge

    fake_ans = r"C:\Program Files\Arga\Arga Nesting Suite.exe"
    monkeypatch.setattr(bridge.sys, "frozen", True, raising=False)
    monkeypatch.setattr(bridge.sys, "executable", fake_ans)
    monkeypatch.setattr(bridge.shutil, "which", lambda _name: None)

    with tempfile.TemporaryDirectory() as tmp:
        nest = Path(tmp) / "NESTEOS DE COBRE"
        nest.mkdir()
        (nest / "cyptube_verticales.json").write_text("{}", encoding="utf-8")
        main = Path(tmp) / "main.py"
        main.write_text("#", encoding="utf-8")

        logs: list[str] = []
        result = bridge.launch_cyptube_auto_nest(
            nest,
            log_fn=logs.append,
            prefs={
                "enabled": True,
                "cyptube_main": str(main),
                "python_exe": "",
                "skip_wait": True,
                "new_console": False,
            },
        )
    assert result.skipped and not result.launched
    assert any("python inválido" in m.lower() or "python_exe" in m for m in logs)


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
    test_launch_delay_programa_hilo()
    test_servidor_unc_pasa_al_cmd_sin_romper()
    test_path_maps_remap_servidor()
    test_resolve_python_prefiere_venv_junto_a_main()
    test_config_default_file_existe()
    print("[OK] CyPTube bridge auto-nest")
