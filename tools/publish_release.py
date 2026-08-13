"""Publica un release del ANS al canal (GitHub Releases o carpeta UNC).

Uso típico (después de `python tools/build_arga_exe.py --release`):

  # GitHub Releases (requiere `gh auth login` una vez):
  python tools/publish_release.py --github --repo joserome6020/Nesting-Pruebas-de-Mejora

  # Carpeta compartida en la LAN (fileserver):
  python tools/publish_release.py --unc "\\\\fileserver\\ANS\\channel"

Ambos modos:
  1) Localizan `dist/releases/latest.json` + su `.zip`.
  2) Verifican sha256 (asegura que nadie tocó el zip después del build).
  3) Suben el zip al destino.
  4) Escriben en `latest.json`:
       - `url`               : URL/UNC del zip publicado.
       - `published_at_utc`  : ISO-8601 UTC del momento de subida.
  5) Suben también `latest.json` (para que las PCs lo consulten primero).

`latest.json` NUNCA cambia el `sha256`; el updater lo usa para validar la
descarga antes de aplicar la versión nueva.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RELEASES_DIR = ROOT / "dist" / "releases"
DEFAULT_LATEST_JSON = DEFAULT_RELEASES_DIR / "latest.json"


def _sha256_of_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _load_latest(latest_path: Path) -> dict:
    if not latest_path.is_file():
        raise SystemExit(
            f"No existe {latest_path}. Corre primero `python tools/build_arga_exe.py --release`."
        )
    try:
        return json.loads(latest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"latest.json corrupto: {exc}")


def _save_latest(latest_path: Path, data: dict) -> None:
    latest_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _resolve_zip(latest_path: Path, latest: dict) -> Path:
    filename = str(latest.get("filename") or "").strip()
    if not filename:
        raise SystemExit("latest.json no tiene 'filename'; regenera con --release.")
    zip_path = latest_path.parent / filename
    if not zip_path.is_file():
        raise SystemExit(f"No se encuentra el zip: {zip_path}")
    return zip_path


def _verify_zip(zip_path: Path, latest: dict) -> None:
    expected = str(latest.get("sha256") or "").lower().strip()
    if not expected:
        raise SystemExit("latest.json no trae sha256; regenera el release.")
    actual = _sha256_of_file(zip_path)
    if actual.lower() != expected:
        raise SystemExit(
            "sha256 no coincide entre latest.json y el zip.\n"
            f"  esperado: {expected}\n"
            f"  actual  : {actual}\n"
            "Regenera con --release o no publiques este artefacto."
        )
    print(f"[OK] sha256 verificado: {actual}")


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- GitHub

def _gh_available() -> bool:
    return shutil.which("gh") is not None


def _github_tag(latest: dict) -> str:
    version = str(latest.get("version") or "").strip()
    commit_short = str(latest.get("commit_short") or "").strip()
    if not version:
        raise SystemExit("latest.json sin 'version'; regenera el release.")
    return f"v{version}" + (f"-{commit_short}" if commit_short else "")


def _github_release_exists(repo: str, tag: str) -> bool:
    try:
        proc = subprocess.run(
            ["gh", "release", "view", tag, "--repo", repo],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _github_publish(
    repo: str,
    tag: str,
    zip_path: Path,
    latest_path: Path,
    latest: dict,
    prerelease: bool,
    make_latest: bool,
) -> str:
    if not _gh_available():
        raise SystemExit(
            "Falta la CLI de GitHub `gh`. Instala https://cli.github.com/ "
            "y corre `gh auth login` en esta PC."
        )
    notes = str(latest.get("notes") or "").strip() or "ANS release"
    if not _github_release_exists(repo, tag):
        cmd = [
            "gh",
            "release",
            "create",
            tag,
            "--repo",
            repo,
            "--title",
            f"ARGA NESTING SUITE {latest.get('version')}",
            "--notes",
            notes,
        ]
        if prerelease:
            cmd.append("--prerelease")
        if make_latest:
            cmd.append("--latest")
        print(f"[RUN] {' '.join(cmd)}")
        subprocess.check_call(cmd)
    else:
        print(f"[INFO] El tag {tag} ya existía; se agregarán/actualizarán los assets.")

    for asset in (zip_path, latest_path):
        cmd = [
            "gh",
            "release",
            "upload",
            tag,
            str(asset),
            "--repo",
            repo,
            "--clobber",
        ]
        print(f"[RUN] {' '.join(cmd)}")
        subprocess.check_call(cmd)

    # URL directa al asset del zip: /releases/download/<tag>/<filename>
    url = f"https://github.com/{repo}/releases/download/{tag}/{zip_path.name}"
    return url


# ---------------------------------------------------------------- UNC

def _unc_publish(unc_dir: str, zip_path: Path, latest_path: Path) -> str:
    dest = Path(unc_dir)
    dest.mkdir(parents=True, exist_ok=True)
    dst_zip = dest / zip_path.name
    dst_latest = dest / "latest.json"
    print(f"[RUN] copy {zip_path} -> {dst_zip}")
    shutil.copy2(zip_path, dst_zip)
    print(f"[RUN] copy {latest_path} -> {dst_latest}")
    shutil.copy2(latest_path, dst_latest)
    # UNC absoluto es la "url" para el updater cuando se usa canal LAN.
    return str(dst_zip)


# ---------------------------------------------------------------- CLI

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publica el release del ANS a GitHub Releases o UNC."
    )
    parser.add_argument(
        "--latest",
        default=str(DEFAULT_LATEST_JSON),
        help="Ruta al latest.json generado por `build_arga_exe.py --release`.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--github",
        action="store_true",
        help="Publica a GitHub Releases usando la CLI `gh` (auth previo requerido).",
    )
    group.add_argument(
        "--unc",
        default="",
        help="Publica a una carpeta UNC/local (por ejemplo \\\\fileserver\\ANS\\channel).",
    )
    parser.add_argument(
        "--repo",
        default="",
        help="Owner/repo para GitHub (obligatorio con --github). Ej: usuario/ans.",
    )
    parser.add_argument(
        "--prerelease",
        action="store_true",
        help="Marca el release como prerelease en GitHub.",
    )
    parser.add_argument(
        "--no-latest",
        action="store_true",
        help="No marcar como 'latest' en GitHub (útil para canal beta paralelo).",
    )
    args = parser.parse_args()

    latest_path = Path(args.latest).resolve()
    latest = _load_latest(latest_path)
    zip_path = _resolve_zip(latest_path, latest)
    _verify_zip(zip_path, latest)

    if args.github:
        if not args.repo.strip():
            raise SystemExit("--github requiere --repo owner/name.")
        tag = _github_tag(latest)
        url = _github_publish(
            repo=args.repo.strip(),
            tag=tag,
            zip_path=zip_path,
            latest_path=latest_path,
            latest=latest,
            prerelease=args.prerelease,
            make_latest=not args.no_latest,
        )
    else:
        if not args.unc.strip():
            raise SystemExit("--unc requiere ruta destino.")
        url = _unc_publish(args.unc.strip(), zip_path, latest_path)

    latest["url"] = url
    latest["published_at_utc"] = _now_utc_iso()
    _save_latest(latest_path, latest)
    print(f"[OK] Publicado. url={url}")
    print(f"[OK] latest.json actualizado: {latest_path}")

    if args.github:
        # Publicar de nuevo el latest.json con la URL final ya escrita.
        cmd = [
            "gh",
            "release",
            "upload",
            _github_tag(latest),
            str(latest_path),
            "--repo",
            args.repo.strip(),
            "--clobber",
        ]
        print(f"[RUN] {' '.join(cmd)}")
        subprocess.check_call(cmd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
