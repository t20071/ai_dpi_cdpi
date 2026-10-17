"""Mirror the project into dpi_agent_project/ so both locations stay identical.

The repository root is the working copy. dpi_agent_project/ is kept as an
exact copy so that older links to that folder keep working.

    python scripts/sync_copy.py           # update the copy
    python scripts/sync_copy.py --check   # only report differences (used by CI)

Only files git would commit are mirrored: tracked files plus new files that
are not ignored. .env, .venv/ and data/runtime/ are never copied.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COPY_NAME = "dpi_agent_project"
COPY = ROOT / COPY_NAME
# Never mirrored: GitHub only reads workflows from the root, and the copy cannot contain itself
SKIP_PREFIXES = (".github/", f"{COPY_NAME}/")


def project_files(folder: Path) -> set[str]:
    """Paths (relative, with /) that git would commit in this folder."""
    output = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=folder,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    return {path for path in output.split("\0") if path and (folder / path).is_file()}


def differences() -> tuple[list[str], list[str]]:
    """Return (files to copy, files to delete) to make the copy match the root."""
    source = {p for p in project_files(ROOT) if not p.startswith(SKIP_PREFIXES)}
    copied = project_files(COPY) if COPY.is_dir() else set()
    changed = sorted(
        p for p in source if p not in copied or (ROOT / p).read_bytes() != (COPY / p).read_bytes()
    )
    extra = sorted(copied - source)
    return changed, extra


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report differences, change nothing")
    args = parser.parse_args()

    if (ROOT.parent / "pyproject.toml").is_file():
        print(f"This is the {COPY_NAME}/ copy. Run the script from the repository root instead.")
        return 1

    changed, extra = differences()
    if not changed and not extra:
        print(f"{COPY_NAME}/ matches the repository root.")
        return 0

    if args.check:
        for path in changed:
            print(f"differs: {path}")
        for path in extra:
            print(f"only in {COPY_NAME}/: {path}")
        print(f"\n{COPY_NAME}/ is out of date. Run: python scripts/sync_copy.py")
        return 1

    for path in changed:
        target = COPY / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / path, target)
    for path in extra:
        (COPY / path).unlink()
        # Remove folders the deletion left empty
        for parent in (COPY / path).parents:
            if parent == COPY or any(parent.iterdir()):
                break
            parent.rmdir()
    print(f"Updated {COPY_NAME}/: {len(changed)} copied, {len(extra)} removed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
