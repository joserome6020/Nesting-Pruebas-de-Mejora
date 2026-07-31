"""Reanuda VSM/ContPAQ de un Job ya exportado, sin ejecutar CAD ni MRL."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config
from interface.export_checkpoint_service import (
    checkpoint_export_ok,
    guardar_checkpoint_export,
    nuevo_export_run_id,
)
from modules.nesting_engine.api_client import avanzar_job_centralizado


def _db_config() -> dict:
    return {
        "host": getattr(config, "NESTING_DB_HOST", "192.168.2.80"),
        "database": getattr(config, "NESTING_DB_NAME", "nestingpro_db"),
        "user": getattr(config, "NESTING_DB_USER", "postgres"),
        "password": getattr(config, "NESTING_DB_PASSWORD", "nesting123"),
        "port": getattr(config, "NESTING_DB_PORT", "5433"),
    }


def _guardar(job: str, stage: str, result, run_id: str, db_conf: dict) -> None:
    guardar_checkpoint_export(
        job,
        "JOB",
        stage,
        status="OK" if result else "FAILED",
        run_id=run_id,
        detail=result.summary(),
        http_status=result.http_status,
        metadata={
            "api_operation": result.operation,
            "api_target": result.target,
            "api_response": result.response or {},
            "manual_resume": True,
        },
        db_config=db_conf,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True, help="Número de Job a reanudar.")
    args = parser.parse_args()
    job = str(args.job or "").strip()
    db_conf = _db_config()
    run_id = nuevo_export_run_id()

    stage_vsm = f"VSM_JOB:{job}"
    if checkpoint_export_ok(job, "JOB", stage_vsm, db_config=db_conf):
        print(f"{stage_vsm}: ya confirmado; no se reintenta.")
    else:
        vsm = avanzar_job_centralizado(job)
        _guardar(job, stage_vsm, vsm, run_id, db_conf)
        print(vsm.summary())
        if not vsm:
            return 2

    guardar_checkpoint_export(
        job,
        "JOB",
        "CONTPAQ",
        status="OK",
        run_id=run_id,
        detail=(
            "PO ContPAQ omitida intencionalmente: las WO normales no generan "
            "pedido; la compra se consolida únicamente en la SWO."
        ),
        metadata={"manual_resume": True, "po_scope": "SWO_ONLY"},
        db_config=db_conf,
    )
    print("CONTPAQ: omitido para WO normal; solo las SWO generan PO.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
