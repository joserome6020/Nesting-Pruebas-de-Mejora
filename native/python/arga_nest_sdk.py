"""SDK headless para ArgaNestCore / ArgaNestWorker (ANS C++)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]


def _ensure_path():
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))


class ArgaNestSDK:
    """Cliente in-process (arga_nest_core) o out-of-process (ArgaNestWorker)."""

    def __init__(self, *, use_worker: bool = False, worker_exe: str | None = None):
        _ensure_path()
        self.use_worker = use_worker
        self.worker_exe = worker_exe or str(_ROOT / "native" / "bin" / "ArgaNestWorker.exe")
        self._proc: subprocess.Popen[str] | None = None
        self._core = None
        if not use_worker:
            from modules.nesting_engine import arga_nest_core_bridge as bridge

            if not bridge.core_available():
                raise RuntimeError("arga_nest_core no disponible; compila el core o use_worker=True")
            self._core = bridge

    def _ensure_worker(self):
        if self._proc and self._proc.poll() is None:
            return
        if not Path(self.worker_exe).is_file():
            raise FileNotFoundError(self.worker_exe)
        self._proc = subprocess.Popen(
            [self.worker_exe],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def _worker_cmd(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_worker()
        assert self._proc and self._proc.stdin and self._proc.stdout
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        return json.loads(line)

    def version(self) -> str:
        if self.use_worker:
            return str(self._worker_cmd({"cmd": "version"}).get("version") or "")
        return self._core.core_status().get("version") or ""

    def pack_sheet(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.use_worker:
            resp = self._worker_cmd({"cmd": "pack_sheet", "request": request})
            if not resp.get("ok"):
                raise RuntimeError(resp.get("error") or "worker pack failed")
            return resp["result"]
        return self._core.pack_sheet_json(request)

    def pack_job(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.use_worker:
            resp = self._worker_cmd({"cmd": "pack_job", "request": request})
            return resp["result"]
        from modules.nesting_engine import arga_nest_core as core

        return json.loads(core.pack_job_json(json.dumps(request)))

    def pack_cu(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.use_worker:
            return self._worker_cmd({"cmd": "pack_cu", "request": request})["result"]
        from modules.nesting_engine import arga_nest_core as core

        return json.loads(core.pack_cu_strip_json(json.dumps(request)))

    def export_dxf(self, request: dict[str, Any]) -> str:
        if self.use_worker:
            return str(self._worker_cmd({"cmd": "export_dxf", "request": request}).get("dxf") or "")
        from modules.nesting_engine import arga_nest_core as core

        return core.export_dxf_json(json.dumps(request))

    def export_step(self, request: dict[str, Any], *, prefer_occt: bool = False, out_path: str | None = None) -> str:
        if prefer_occt:
            from occt_step_upgrade import export_request_via_core_then_occt
            import tempfile
            from pathlib import Path

            path = Path(out_path) if out_path else Path(tempfile.gettempdir()) / "arga_nest_occt.step"
            export_request_via_core_then_occt(request, path)
            return path.read_text(encoding="utf-8", errors="ignore")
        if self.use_worker:
            return str(self._worker_cmd({"cmd": "export_step", "request": request}).get("step") or "")
        from modules.nesting_engine import arga_nest_core as core

        return core.export_step_json(json.dumps(request))

    def cuda_status(self) -> dict[str, Any]:
        from modules.nesting_engine import arga_nest_core as core

        return dict(core.cuda_status())

    def close(self):
        if self._proc and self._proc.poll() is None:
            try:
                self._worker_cmd({"cmd": "shutdown"})
            except Exception:
                pass
            self._proc.terminate()
            self._proc = None
