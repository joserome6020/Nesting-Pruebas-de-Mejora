import os
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOURCE_HOOK = ROOT / ".githooks" / "pre-commit"
TARGET_HOOK = ROOT / ".git" / "hooks" / "pre-commit"


def main() -> None:
    if not SOURCE_HOOK.exists():
        raise FileNotFoundError(f"Missing source hook: {SOURCE_HOOK}")
    if not (ROOT / ".git").exists():
        raise FileNotFoundError(f"Not a git repository: {ROOT}")

    TARGET_HOOK.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE_HOOK, TARGET_HOOK)

    try:
        mode = TARGET_HOOK.stat().st_mode
        TARGET_HOOK.chmod(mode | 0o111)
    except Exception:
        pass

    print(f"[ok] Installed hook: {TARGET_HOOK}")


if __name__ == "__main__":
    main()
