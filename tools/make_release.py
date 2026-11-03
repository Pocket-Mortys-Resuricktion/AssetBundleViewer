#!/usr/bin/env python3
"""
Build the archives that go on a GitHub release.

Two of them, because two kinds of person download this:

  ...-tools.zip      the programs only, about 1.7 MB. For anyone who already
                     has the bundles, or who cloned the repository.
  ...-complete.zip   the programs and every bundle: 180 MB zipped, about
                     1.1 GB once unzipped. Download, unzip, double-click
                     run.bat. Nothing else needed.

Usage:
    python tools/make_release.py --version v1.1
    python tools/make_release.py --version v1.1 --tools-only
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

#: Everything the desktop version needs to run.
TOOLS = [
    "bundle_io.py",
    "catalog_builder.py",
    "catalog_server.py",
    "catalog_viewer.html",
    "catalog.json",
    "requirements.txt",
    "run.bat",
    "run.sh",
    "README.md",
    "LICENSE",
]
TOOL_DIRS = ["typetrees", "tools", "tests"]

#: Never ship these: they are generated, or they are someone's own work.
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".git",
             "site", "previews", "exports", ".venv", "venv"}


def add(zf: zipfile.ZipFile, path: Path, arcname: str) -> int:
    """Add one file, keeping the executable bit so run.sh works on Unix."""
    info = zipfile.ZipInfo.from_file(path, arcname)
    info.compress_type = zipfile.ZIP_DEFLATED
    mode = stat.S_IMODE(path.stat().st_mode)
    if path.suffix == ".sh":
        mode |= 0o755
    info.external_attr = (mode & 0xFFFF) << 16
    with open(path, "rb") as handle:
        zf.writestr(info, handle.read())
    return path.stat().st_size


def add_tree(zf: zipfile.ZipFile, folder: Path, prefix: str) -> tuple[int, int]:
    total = count = 0
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in sorted(files):
            path = Path(root) / name
            arc = f"{prefix}/{path.relative_to(folder).as_posix()}"
            total += add(zf, path, arc)
            count += 1
    return total, count


def human(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def build(out: Path, name: str, with_bundles: bool) -> None:
    missing = [f for f in TOOLS if not (ROOT / f).is_file()]
    if missing:
        print(f"  WARNING: not found, skipping: {', '.join(missing)}")

    raw = count = 0
    print(f"  building {out.name} ...")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel in TOOLS:
            path = ROOT / rel
            if path.is_file():
                raw += add(zf, path, f"{name}/{rel}")
                count += 1
        for folder in TOOL_DIRS:
            path = ROOT / folder
            if path.is_dir():
                size, n = add_tree(zf, path, f"{name}/{folder}")
                raw += size
                count += n
        if with_bundles:
            bundles = ROOT / "AssetBundles"
            if not bundles.is_dir():
                print("  ERROR: no AssetBundles folder to include")
                return
            print("  adding the bundles, this takes a few minutes ...")
            size, n = add_tree(zf, bundles, f"{name}/AssetBundles")
            raw += size
            count += n

    print(f"  {out.name}: {count:,} files, {human(raw)} in, "
          f"{human(out.stat().st_size)} zipped")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build release archives.")
    parser.add_argument("--version", default="v1.1")
    parser.add_argument("-o", "--output", default="dist")
    parser.add_argument("--tools-only", action="store_true",
                        help="skip the 180 MB archive")
    args = parser.parse_args()

    out_dir = Path(args.output).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"PocketMortysBundleCatalog-{args.version}"

    print(f"Building into {out_dir}\n")
    build(out_dir / f"{stem}-tools.zip", stem, with_bundles=False)
    if not args.tools_only:
        build(out_dir / f"{stem}-complete.zip", stem, with_bundles=True)

    print("\nUpload these as release assets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
