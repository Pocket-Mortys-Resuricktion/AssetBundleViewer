#!/usr/bin/env python3
"""
Pocket Mortys Asset Bundle Catalog Builder
==========================================
Scans a directory of Unity asset bundles and writes catalog.json for the
viewer: what every bundle is called, what is inside it, and how it is
categorised.

Usage:
    python catalog_builder.py                        # prompts for a directory
    python catalog_builder.py AssetBundles
    python catalog_builder.py AssetBundles -o catalog.json -j 8
    python catalog_builder.py AssetBundles --fresh   # ignore saved progress

The scan is resumable: progress is saved periodically, so an interrupted run
picks up where it left off. Pass --fresh to start over.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

try:
    import bundle_io
except ImportError as exc:  # pragma: no cover - import guard
    print(f"ERROR: could not import bundle_io: {exc}")
    print("Run this script from the folder it lives in.")
    sys.exit(1)

try:
    import UnityPy  # noqa: F401  (imported for the clearer error message below)
except ImportError as exc:  # pragma: no cover - import guard
    print(f"ERROR: could not import UnityPy: {exc}")
    print()
    print("Install the dependencies with:")
    print(f"    {sys.executable} -m pip install -r requirements.txt")
    sys.exit(1)

DEFAULT_OUTPUT = "catalog.json"
PROGRESS_NAME = "catalog_progress.json"
SAVE_EVERY = 100


def _scan_one(path):
    """Worker body. Returns (filename, entry). Runs in a separate process."""
    try:
        return os.path.basename(path), bundle_io.describe(path)
    except Exception as exc:  # a worker must never take the pool down
        return os.path.basename(path), {
            "filename": os.path.basename(path),
            "valid": False,
            "error": f"{type(exc).__name__}: {exc}",
            "size": 0, "size_human": "0.0 B", "assets": [], "asset_count": 0,
            "asset_types": [], "type_counts": {}, "all_asset_names": [],
            "category": "Unknown", "derived_name": "", "original_name": "",
        }


def print_summary(catalog: list) -> None:
    valid = [b for b in catalog if b.get("valid")]
    invalid = [b for b in catalog if not b.get("valid")]
    named = [b for b in valid if b.get("derived_name")]

    print()
    print("=" * 58)
    print("  CATALOG SUMMARY")
    print("=" * 58)
    print(f"  Files scanned       : {len(catalog)}")
    print(f"  Valid bundles       : {len(valid)}")
    print(f"  Invalid / skipped   : {len(invalid)}")
    print(f"  Named bundles       : {len(named)} / {len(valid)}")

    sources = {}
    for bundle in valid:
        key = bundle.get("name_source") or "(none)"
        sources[key] = sources.get(key, 0) + 1
    if sources:
        print("\n  Where the names came from:")
        for key, count in sorted(sources.items(), key=lambda kv: -kv[1]):
            print(f"    {key:<32} {count:>6}")

    categories = {}
    for bundle in valid:
        key = bundle.get("category", "Unknown")
        categories[key] = categories.get(key, 0) + 1
    if categories:
        biggest = max(categories.values())
        print("\n  Category breakdown:")
        for key, count in sorted(categories.items(), key=lambda kv: -kv[1]):
            bar = "#" * max(1, round(30 * count / biggest))
            print(f"    {key:<20} {count:>6}  {bar}")

    total_assets = sum(b.get("asset_count", 0) for b in valid)
    print(f"\n  Assets found        : {total_assets:,}")

    if invalid:
        print(f"\n  First few that could not be read:")
        for bundle in invalid[:5]:
            print(f"    {bundle['filename'][:52]:52}  {bundle.get('error','')[:50]}")
    print("=" * 58)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan Unity asset bundles and write catalog.json.")
    parser.add_argument("directory", nargs="?",
                        help="folder holding the asset bundles")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT,
                        help=f"where to write the catalog (default {DEFAULT_OUTPUT})")
    parser.add_argument("-j", "--jobs", type=int, default=0,
                        help="worker processes (default: one per CPU core, max 8)")
    parser.add_argument("--fresh", action="store_true",
                        help="ignore any saved progress and rescan everything")
    parser.add_argument("--pretty", action="store_true",
                        help="write indented JSON (larger, easier to read)")
    args = parser.parse_args()

    raw = args.directory
    if not raw:
        raw = input("Path to the bundle directory (Enter for ./AssetBundles): ").strip()
        raw = raw.strip('"') or "AssetBundles"

    directory = Path(raw).expanduser()
    if not directory.is_dir():
        print(f"ERROR: not a directory: {directory}")
        return 1
    directory = directory.resolve()

    files = sorted(
        (entry for entry in directory.iterdir()
         if entry.is_file()
         and entry.name not in bundle_io.SIDECAR_FILES
         # .resource / .resS hold the bulk data for audio and textures. They
         # belong to a bundle rather than being one, so they are read through
         # their owner and never listed on their own.
         and entry.suffix.lower() not in (".resource", ".ress")),
        key=lambda p: p.name.lower(),
    )
    total = len(files)
    if not total:
        print(f"ERROR: no files in {directory}")
        return 1

    output_path = Path(args.output).expanduser().resolve()
    progress_path = directory / PROGRESS_NAME

    print(f"Scanning {total} files in {directory}")
    print(f"Catalog  -> {output_path}")

    done = {}
    if progress_path.exists() and not args.fresh:
        try:
            with open(progress_path, "r", encoding="utf-8") as handle:
                done = json.load(handle)
            print(f"Resuming: {len(done)} files already scanned "
                  f"(use --fresh to start over)")
        except Exception as exc:
            print(f"Could not read saved progress ({exc}); starting fresh")
            done = {}

    todo = [entry for entry in files if entry.name not in done]
    jobs = args.jobs if args.jobs > 0 else min(8, os.cpu_count() or 1)
    jobs = max(1, min(jobs, len(todo) or 1))

    if todo:
        print(f"{len(todo)} to scan with {jobs} worker"
              f"{'s' if jobs > 1 else ''}\n")
    start = time.time()
    completed = 0

    def save_progress() -> bool:
        """
        Checkpoint the scan. Never raises.

        Losing a checkpoint costs a rescan; letting it throw would throw away
        the scan that has already finished, which is far worse. Windows in
        particular denies the rename while something else still holds the file
        open, so a failure here is reported and otherwise ignored.
        """
        tmp = progress_path.with_name(progress_path.name + f".{os.getpid()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(done, handle)
            os.replace(tmp, progress_path)
            return True
        except Exception as exc:
            print(f"\n  (could not save progress: {exc})")
            try:
                tmp.unlink()
            except OSError:
                pass
            return False

    try:
        if jobs == 1:
            for entry in todo:
                done[entry.name] = bundle_io.describe(str(entry))
                completed += 1
                _progress(entry.name, completed, len(todo), start)
                if completed % SAVE_EVERY == 0:
                    save_progress()
        else:
            with ProcessPoolExecutor(max_workers=jobs) as pool:
                futures = [pool.submit(_scan_one, str(entry)) for entry in todo]
                for future in as_completed(futures):
                    name, entry = future.result()
                    done[name] = entry
                    completed += 1
                    _progress(name, completed, len(todo), start)
                    if completed % SAVE_EVERY == 0:
                        save_progress()
    except KeyboardInterrupt:
        print("\n\nInterrupted. Saving what was scanned so far...")
        save_progress()
        print(f"Progress saved to {progress_path}. Run again to continue.")
        return 130

    if todo:
        print()

    # The catalog is the thing worth having, so write it before the
    # checkpoint: a problem saving progress must never cost the whole scan.
    # Keep it in a stable order so the file diffs cleanly in git.
    catalog = [done[entry.name] for entry in files if entry.name in done]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        if args.pretty:
            json.dump(catalog, handle, indent=1)
        else:
            json.dump(catalog, handle, separators=(",", ":"))

    save_progress()

    size = bundle_io.format_size(output_path.stat().st_size)
    elapsed = time.time() - start
    print(f"Wrote {output_path} ({size}) in {elapsed:.1f}s")
    print_summary(catalog)
    print("\nOpen the viewer and load this catalog, or run:")
    print(f"    python catalog_server.py \"{directory}\"")
    return 0


def _progress(name: str, position: int, total: int, start: float) -> None:
    elapsed = time.time() - start
    rate = position / elapsed if elapsed > 0 else 0
    remaining = (total - position) / rate if rate > 0 else 0
    eta = f"{remaining / 60:.1f}m" if remaining > 90 else f"{remaining:.0f}s"
    line = f"[{position:>5}/{total}] {name[:48]:<48} ETA {eta}"
    print(f"\r{line[:100]:<100}", end="", flush=True)


if __name__ == "__main__":
    sys.exit(main())
