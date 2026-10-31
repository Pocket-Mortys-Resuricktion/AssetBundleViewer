#!/usr/bin/env python3
"""
Render every asset ahead of time, so the web version can show them.

Why this exists
---------------
Reading a Unity bundle needs code that cannot run in a browser: UnityPy
depends on four compiled packages, and the texture formats this game uses
(ASTC and ETC2) need a real decoder. Rather than ask everyone to install
Python to look at a sprite, we decode everything once, here, and publish the
results as ordinary files. The web viewer is then just a page that shows
images and offers downloads, which works on any device.

What it writes
--------------
    site/index.html            the viewer
    site/catalog.json          what is in every bundle
    site/thumbs/<b>/<id>.webp  192px previews, for browsing
    site/assets/<b>/<id>.<ext> the real exported asset, for downloading
    site/previews.json         which assets have a preview, and of what kind

<b> is the bundle's position in the catalog, so the viewer can work out any
URL without a lookup table.

Usage
-----
    python tools/prerender.py AssetBundles -o site -j 8
    python tools/prerender.py AssetBundles --only Texture2D,Sprite
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import bundle_io  # noqa: E402

#: Types worth publishing. The rest (GameObject, Transform, colliders and so
#: on) describe scene structure rather than content: there is nothing to look
#: at and nothing anyone would export, and including them would quadruple the
#: file count for no benefit.
DEFAULT_TYPES = [
    "Texture2D", "Sprite", "AudioClip", "TextAsset",
    "MonoBehaviour", "Mesh", "Shader", "Material", "Font", "SpriteAtlas",
]

IMAGE_TYPES = {"Texture2D", "Sprite"}
THUMB_SIZE = 192


def render_bundle(job: Tuple[int, str, str, str, List[str], bool]) -> dict:
    """
    Render one bundle's assets. Runs in a worker process.

    Returns a small report; the files themselves are written as we go so
    nothing large has to travel between processes.
    """
    position, bundle_dir, filename, out_dir, types, skip_existing = job
    from PIL import Image

    result = {"position": position, "filename": filename,
              "previews": {}, "written": 0, "bytes": 0, "errors": []}

    index = _worker_index(bundle_dir)
    path = index.resolve_in_dir(filename)
    if not path:
        result["errors"].append("file not found")
        return result

    try:
        env = bundle_io.open_bundle(path, index)
    except Exception as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
        return result

    wanted = set(types)
    asset_dir = Path(out_dir) / "assets" / str(position)
    thumb_dir = Path(out_dir) / "thumbs" / str(position)

    for obj in env.objects:
        type_name = bundle_io.type_name(obj)
        if type_name not in wanted:
            continue
        key = str(obj.path_id)
        try:
            item = bundle_io.export_asset(obj)
        except Exception as exc:
            # One unreadable asset must not cost us the other 500 in the file.
            result["errors"].append(f"{key} {type_name}: {str(exc)[:120]}")
            continue

        extension = os.path.splitext(item.filename)[1].lstrip(".").lower() or "bin"
        asset_dir.mkdir(parents=True, exist_ok=True)
        target = asset_dir / f"{key}.{extension}"

        if skip_existing and target.exists():
            # Counted but not rewritten, so the size report stays honest
            # across a resumed run.
            result["bytes"] += target.stat().st_size
        else:
            target.write_bytes(item.data)
            result["written"] += 1
            result["bytes"] += len(item.data)

        entry = {"e": extension, "t": type_name}

        if type_name in IMAGE_TYPES and item.kind == "image":
            thumb = thumb_dir / f"{key}.webp"
            if skip_existing and thumb.exists():
                result["bytes"] += thumb.stat().st_size
            else:
                try:
                    image = Image.open(io.BytesIO(item.data))
                    image.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
                    thumb_dir.mkdir(parents=True, exist_ok=True)
                    image.save(thumb, "WEBP", quality=80, method=4)
                    result["written"] += 1
                    result["bytes"] += thumb.stat().st_size
                except Exception as exc:
                    result["errors"].append(f"{key} thumb: {str(exc)[:80]}")
            if thumb.exists():
                entry["w"] = 1

        result["previews"][key] = entry

    return result


_INDEX_CACHE: Dict[str, bundle_io.FileIndex] = {}


def _worker_index(directory: str) -> bundle_io.FileIndex:
    """One index per worker process, built once and reused."""
    index = _INDEX_CACHE.get(directory)
    if index is None:
        index = bundle_io.FileIndex(directory)
        _INDEX_CACHE[directory] = index
    return index


def human(num: float) -> str:
    return bundle_io.format_size(num)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prerender every asset for the web viewer.")
    parser.add_argument("directory", nargs="?", default="AssetBundles")
    parser.add_argument("-o", "--output", default="site")
    parser.add_argument("-c", "--catalog", default="catalog.json")
    parser.add_argument("-j", "--jobs", type=int, default=0)
    parser.add_argument("--only", default="",
                        help="comma separated Unity types, instead of the default set")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after this many bundles (for a quick trial run)")
    parser.add_argument("--resume", action="store_true",
                        help="keep files that are already rendered")
    args = parser.parse_args()

    bundle_dir = Path(args.directory).expanduser()
    if not bundle_dir.is_dir():
        print(f"ERROR: not a directory: {bundle_dir}")
        return 1

    catalog_path = Path(args.catalog).expanduser()
    if not catalog_path.is_file():
        print(f"ERROR: no catalog at {catalog_path}. Run catalog_builder.py first.")
        return 1
    with open(catalog_path, "r", encoding="utf-8") as handle:
        catalog = json.load(handle)

    types = [t.strip() for t in args.only.split(",") if t.strip()] or DEFAULT_TYPES
    out_dir = Path(args.output).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Only bundles that hold something we publish are worth opening.
    jobs = []
    for position, bundle in enumerate(catalog):
        if not bundle.get("valid"):
            continue
        if not any(t in types for t in bundle.get("asset_types", [])):
            continue
        jobs.append((position, str(bundle_dir), bundle["filename"],
                     str(out_dir), types, args.resume))
    if args.limit:
        jobs = jobs[:args.limit]

    workers = args.jobs if args.jobs > 0 else min(8, os.cpu_count() or 1)
    workers = max(1, min(workers, len(jobs) or 1))
    print(f"Rendering {len(jobs)} of {len(catalog)} bundles with {workers} workers")
    print(f"Types: {', '.join(types)}")
    print(f"Output: {out_dir}\n")

    previews: Dict[str, Dict[str, dict]] = {}
    errors: List[str] = []
    written = total_bytes = 0
    start = time.time()

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(render_bundle, job) for job in jobs]
        for done, future in enumerate(as_completed(futures), 1):
            report = future.result()
            if report["previews"]:
                previews[str(report["position"])] = report["previews"]
            written += report["written"]
            total_bytes += report["bytes"]
            errors.extend(f"{report['filename']}: {e}" for e in report["errors"][:3])

            elapsed = time.time() - start
            rate = done / elapsed if elapsed else 0
            eta = (len(jobs) - done) / rate if rate else 0
            print(f"\r[{done:>5}/{len(jobs)}] {written:>7} files  "
                  f"{human(total_bytes):>9}  ETA {eta/60:4.1f}m   ", end="", flush=True)
    print()

    with open(out_dir / "previews.json", "w", encoding="utf-8") as handle:
        json.dump({"format": 1, "thumb": THUMB_SIZE, "types": types,
                   "bundles": previews}, handle, separators=(",", ":"))

    shutil.copy(ROOT / "catalog_viewer.html", out_dir / "index.html")
    shutil.copy(catalog_path, out_dir / "catalog.json")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    # Add up what we wrote rather than walking the tree: on a slow disk,
    # stat()ing 75,000 files afterwards takes longer than the render did.
    extras = sum(f.stat().st_size for f in
                 (out_dir / "previews.json", out_dir / "index.html",
                  out_dir / "catalog.json") if f.is_file())
    print(f"\nWrote {written:,} files, {human(total_bytes)} of assets")
    print(f"Whole site: about {human(total_bytes + extras)} in {out_dir}")
    print(f"Took {(time.time() - start)/60:.1f} minutes")
    if errors:
        print(f"\n{len(errors)} assets could not be rendered, first few:")
        for line in errors[:5]:
            print(f"   {line[:110]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
