#!/usr/bin/env python3
"""
Pocket Mortys Asset Bundle Catalog Builder
==========================================
Scans all asset bundles in a directory using UnityPy, extracts maximum info
from each bundle, and outputs catalog.json for the HTML viewer.

Requirements:
    pip install unitypy

Usage:
    python catalog_builder.py                     # prompts for directory
    python catalog_builder.py "C:/path/to/bundles"

Features:
    - Recovers original bundle names from bundle headers
    - Lists every asset (name + type) inside each bundle
    - Auto-categorizes bundles by dominant asset type
    - Resumable: saves progress every 50 files
    - Skips non-bundle files gracefully
"""

import os
import json
import sys
import time
from pathlib import Path

try:
    import UnityPy
except ImportError as _e:
    print(f"ERROR: Could not import UnityPy: {_e}")
    print()
    print("UnityPy may be installed under a different Python version.")
    print("Try running with the Python Launcher instead:")
    print("    py -3.13 catalog_builder.py <your_bundle_dir>")
    print("Or install directly into this interpreter:")
    print(f"    {sys.executable} -m pip install unitypy")
    sys.exit(1)

OUTPUT_FILE   = "catalog.json"
PROGRESS_FILE = "catalog_progress.json"
SAVE_EVERY    = 50   # save progress every N files

# Types to exclude from "dominant type" calculation (meta/overhead types)
META_TYPES = {"AssetBundle", "PreloadData", "AssetBundleManifest", "BuildSettings", "ResourceManager"}

# Human-readable category mapping
CATEGORY_MAP = {
    "Texture2D":        "Texture",
    "Sprite":           "Sprite / UI",
    "AudioClip":        "Audio",
    "Mesh":             "3D Mesh",
    "MeshFilter":       "3D Mesh",
    "SkinnedMeshRenderer": "3D Mesh",
    "Shader":           "Shader",
    "Material":         "Material",
    "AnimationClip":    "Animation",
    "Animator":         "Animation",
    "TextAsset":        "Text / Data",
    "MonoBehaviour":    "Script Data",
    "GameObject":       "Scene Object",
    "Font":             "Font",
    "VideoClip":        "Video",
    "ComputeShader":    "Shader",
    "ScriptableObject": "Script Data",
    "Cubemap":          "Texture",
    "RenderTexture":    "Texture",
    "LightingDataAsset":"Lighting",
    "NavMeshData":      "Navigation",
    "PhysicMaterial":   "Physics",
    "TerrainData":      "Terrain",
}


def format_size(bytes_val):
    """Human-readable file size."""
    for unit in ("B", "KB", "MB", "GB"):
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} GB"


def safe_read_name(obj):
    """Try to read an asset's name without crashing."""
    try:
        data = obj.read()
        for attr in ("name", "m_Name", "m_AssetBundleName"):
            val = getattr(data, attr, None)
            if val and isinstance(val, str):
                return val.strip()
    except Exception:
        pass
    return ""


def get_unity_version(obj):
    """Try to extract Unity version from an object's reader."""
    try:
        reader = getattr(obj, "reader", None) or getattr(obj, "assets_file", None)
        if reader:
            v = getattr(reader, "unity_version", "") or getattr(reader, "unityVersion", "")
            if v:
                return str(v)
    except Exception:
        pass
    return ""


def extract_container_paths(obj):
    """Extract asset paths from an AssetBundle object's m_Container."""
    paths = []
    try:
        data = obj.read()
        container = getattr(data, "m_Container", None)
        if container:
            # m_Container is a list of (path, asset_info) pairs
            if isinstance(container, (list, tuple)):
                for item in container:
                    if isinstance(item, (list, tuple)) and len(item) >= 1:
                        p = item[0]
                        if p and isinstance(p, str):
                            paths.append(p)
            elif isinstance(container, dict):
                paths.extend(k for k in container.keys() if k)
    except Exception:
        pass
    return paths


def scan_bundle(filepath: str) -> dict:
    """
    Open one file and extract everything we can from it.
    Returns a dict with all discovered information.
    """
    fname = os.path.basename(filepath)
    size  = os.path.getsize(filepath)

    result = {
        "filename":       fname,
        "size":           size,
        "size_human":     format_size(size),
        "valid":          False,
        "original_name":  "",
        "category":       "Unknown",
        "dominant_type":  "",
        "unity_version":  "",
        "internal_paths": [],
        "container_paths":[],
        "asset_types":    [],
        "type_counts":    {},
        "assets":         [],
        "asset_count":    0,
        "error":          ""
    }

    try:
        env = UnityPy.load(filepath)
        result["valid"] = True

        # Internal paths (keys of env.files) often reveal the original bundle path
        internal_paths = list(env.files.keys())
        result["internal_paths"] = internal_paths

        # Attempt to infer original name from internal paths
        for path in internal_paths:
            p = path.strip()
            p_lower = p.lower()
            # Skip empty, dots, and raw CAB hashes (which are just content-addressable IDs)
            if p and p != "." and not p_lower.startswith("cab-"):
                result["original_name"] = p
                break

        assets      = []
        type_counts = {}
        unity_ver   = ""
        container_paths = []

        for obj in env.objects:
            try:
                type_name = obj.type.name if hasattr(obj.type, "name") else str(obj.type)
            except Exception:
                type_name = "Unknown"

            # Get unity version once
            if not unity_ver:
                unity_ver = get_unity_version(obj)

            # Special handling for AssetBundle metadata object
            if type_name == "AssetBundle":
                # Try to get the authoritative original bundle name
                try:
                    data = obj.read()
                    ab_name = getattr(data, "m_AssetBundleName", "") or ""
                    if ab_name and not result["original_name"]:
                        result["original_name"] = ab_name.strip()
                except Exception:
                    pass

                # Extract container paths (asset paths within the bundle)
                cp = extract_container_paths(obj)
                container_paths.extend(cp)

                # We skip adding AssetBundle itself to the assets list
                # (it's metadata, not a real asset)
                type_counts[type_name] = type_counts.get(type_name, 0) + 1
                continue

            name = safe_read_name(obj)
            asset_entry = {
                "type":    type_name,
                "name":    name,
                "path_id": obj.path_id
            }
            assets.append(asset_entry)
            type_counts[type_name] = type_counts.get(type_name, 0) + 1

        # If we still don't have an original name, try container paths
        if not result["original_name"] and container_paths:
            # Container paths look like "assets/audio/sfx_blaster.wav"
            # The first segment often matches the bundle name
            result["original_name"] = container_paths[0].split("/")[0] if container_paths else ""

        result["unity_version"]   = unity_ver
        result["container_paths"] = container_paths
        result["assets"]          = assets
        result["asset_count"]     = len(assets)
        result["type_counts"]     = type_counts
        result["asset_types"]     = sorted(type_counts.keys())

        # Dominant type: most common non-meta type
        real_types = {k: v for k, v in type_counts.items() if k not in META_TYPES}
        if real_types:
            dom = max(real_types, key=real_types.get)
            result["dominant_type"] = dom
            result["category"]      = CATEGORY_MAP.get(dom, dom)
        elif type_counts:
            dom = max(type_counts, key=type_counts.get)
            result["dominant_type"] = dom
            result["category"]      = CATEGORY_MAP.get(dom, dom)

    except Exception as e:
        result["error"] = str(e)

    return result


def print_summary(catalog: list):
    """Print a breakdown of the catalog after scanning."""
    valid   = [b for b in catalog if b["valid"]]
    invalid = [b for b in catalog if not b["valid"]]

    print(f"\n{'='*55}")
    print(f"  CATALOG SUMMARY")
    print(f"{'='*55}")
    print(f"  Total files scanned : {len(catalog)}")
    print(f"  Valid asset bundles : {len(valid)}")
    print(f"  Invalid / skipped   : {len(invalid)}")

    named = [b for b in valid if b["original_name"]]
    print(f"  Recovered names     : {len(named)} / {len(valid)}")

    print(f"\n  Category breakdown:")
    cats = {}
    for b in valid:
        c = b["category"]
        cats[c] = cats.get(c, 0) + 1
    for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
        bar = "█" * min(30, count // max(1, len(valid) // 30))
        print(f"    {cat:<20} {count:>5}  {bar}")

    total_assets = sum(b["asset_count"] for b in valid)
    print(f"\n  Total assets found  : {total_assets:,}")

    if invalid:
        print(f"\n  First few invalid files:")
        for b in invalid[:5]:
            print(f"    {b['filename'][:60]}  — {b['error'][:60]}")
    print(f"{'='*55}")


def main():
    # --- Determine bundle directory ---
    if len(sys.argv) > 1:
        bundle_dir = Path(sys.argv[1])
    else:
        raw = input("Enter path to bundle directory (press Enter for current dir): ").strip()
        bundle_dir = Path(raw) if raw else Path(".")

    if not bundle_dir.exists() or not bundle_dir.is_dir():
        print(f"ERROR: Directory not found: {bundle_dir}")
        sys.exit(1)

    all_files = [f for f in bundle_dir.iterdir() if f.is_file()]
    total     = len(all_files)
    print(f"Found {total} files in: {bundle_dir.resolve()}")
    print(f"Output will be saved to: {Path(OUTPUT_FILE).resolve()}\n")

    # --- Load existing progress ---
    catalog_dict = {}  # keyed by filename for deduplication
    progress_path = bundle_dir / PROGRESS_FILE
    if progress_path.exists():
        try:
            with open(progress_path, "r", encoding="utf-8") as f:
                catalog_dict = json.load(f)
            print(f"Resuming from saved progress ({len(catalog_dict)} files already done)\n")
        except Exception as e:
            print(f"Could not load progress file: {e} — starting fresh\n")

    # --- Scan ---
    start_time = time.time()
    newly_scanned = 0

    for i, filepath in enumerate(all_files):
        fname = filepath.name
        if fname in catalog_dict:
            continue  # already done

        elapsed  = time.time() - start_time
        rate     = newly_scanned / elapsed if elapsed > 0 and newly_scanned > 0 else 0
        remaining = (total - i) / rate if rate > 0 else 0
        eta_str  = f"{remaining/60:.1f}m" if remaining > 60 else f"{remaining:.0f}s"

        print(f"[{i+1:>4}/{total}]  {fname[:55]:<55}  ETA: {eta_str}", end="\r")

        result = scan_bundle(str(filepath))
        catalog_dict[fname] = result
        newly_scanned += 1

        # Save progress periodically
        if newly_scanned % SAVE_EVERY == 0:
            with open(progress_path, "w", encoding="utf-8") as f:
                json.dump(catalog_dict, f)
            print(f"\n  → Progress saved ({len(catalog_dict)}/{total} files done)")

    print()  # newline after progress line

    # --- Write final catalog.json ---
    catalog_list = list(catalog_dict.values())

    output_path = Path(OUTPUT_FILE)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(catalog_list, f, separators=(",", ":"))  # compact for smaller file

    print(f"\nCatalog saved → {output_path.resolve()}")

    # Save final progress too
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(catalog_dict, f)

    print_summary(catalog_list)
    print(f"\nOpen catalog_viewer.html and load catalog.json to explore your bundles.\n")


if __name__ == "__main__":
    main()