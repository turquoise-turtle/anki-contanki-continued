"""
Builds installable packages into dist/:

- contanki-test.ankiaddon: Contanki from this checkout, its version labelled with the commit.
- contanki-diagnostics.ankiaddon: the controller diagnostics add-on.

Run: uv run --no-project build.py

For a GitHub release, `uv run --no-project build.py release` instead names the add-on
contanki-continued-<version>.ankiaddon, using the version in contanki/manifest.json as it
is. It refuses to build from uncommitted changes or from a commit without a matching tag,
so the attached package is always the tagged source.
"""

import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
SKIP_DIRS = {"__pycache__"}
SKIP_FILES = {"meta.json", ".DS_Store"}


def commit() -> str:
    result = subprocess.run(
        ["git", "describe", "--always", "--dirty"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def package(source: Path, target: Path, manifest_changes: dict | None = None) -> None:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_dir() or path.name in SKIP_FILES or SKIP_DIRS & set(path.parts):
                continue
            name = path.relative_to(source).as_posix()
            if name == "manifest.json" and manifest_changes:
                manifest = json.loads(path.read_text(encoding="utf8"))
                manifest.update(manifest_changes)
                archive.writestr(name, json.dumps(manifest, indent=4))
            else:
                archive.write(path, name)
    print(f"Built {target.relative_to(ROOT)} ({target.stat().st_size // 1024} KB)")


def manifest_version() -> str:
    manifest = json.loads((ROOT / "contanki" / "manifest.json").read_text(encoding="utf8"))
    return manifest["human_version"]


def release() -> None:
    version = manifest_version()
    if commit() != version:
        sys.exit(
            f"A release build needs a clean checkout of tag {version}; "
            f"this one is {commit()}. Commit, then: git tag {version}"
        )
    package(ROOT / "contanki", DIST / f"contanki-continued-{version}.ankiaddon")
    package(ROOT / "diagnostics" / "contanki_diagnostics", DIST / "contanki-diagnostics.ankiaddon")


def main() -> None:
    DIST.mkdir(exist_ok=True)
    if sys.argv[1:] == ["release"]:
        release()
        return
    version = commit()
    if version.endswith("-dirty"):
        print("Warning: uncommitted changes are included in the build.", file=sys.stderr)
    package(
        ROOT / "contanki",
        DIST / "contanki-test.ankiaddon",
        {"human_version": f"{manifest_version()} + ({version})"},
    )
    package(ROOT / "diagnostics" / "contanki_diagnostics", DIST / "contanki-diagnostics.ankiaddon")


if __name__ == "__main__":
    main()
