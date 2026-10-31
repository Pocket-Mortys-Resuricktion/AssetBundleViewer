#!/usr/bin/env python3
"""
Dump the game's MonoBehaviour type definitions to a JSON file.

Why this exists
---------------
Pocket Mortys is an IL2CPP build, so Unity strips the type information that
says what fields a MonoBehaviour has. Without it every one of the game's
~15,000 MonoBehaviours is an opaque blob: you can see that it is called
"Dimension6SpawnPoints" and nothing else.

The information can be recovered from the game's own metadata, but doing so
needs Il2CppDumper and a .NET runtime, which is far too much to ask of anyone
who just wants to look at a spawn point. So it is recovered once, here, and
the result is committed. Everyone else gets readable MonoBehaviours for free,
and bundle_io loads the dump with no extra dependencies.

Usage
-----
    python tools/make_typetrees.py <DummyDll folder> --unity 2022.3.62f1

The DummyDll folder is what Il2CppDumper produces from the APK's
libil2cpp.so and global-metadata.dat.

Needs:  pip install TypeTreeGeneratorAPI   (and a .NET runtime)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

DEFAULT_OUT = HERE.parent / "typetrees" / "pocketmortys.json"


def _key(assembly: str, fullname: str) -> str:
    """One spelling for a class, since builds disagree about the .dll suffix."""
    if assembly.lower().endswith(".dll"):
        assembly = assembly[:-4]
    return f"{assembly}|{fullname}"


def collect_script_classes(bundle_dir: str) -> set:
    """
    Every class the game's MonoScripts point at.

    This is the list that matters: it includes ScriptableObjects, which hold
    most of the game's data and which the generator's MonoBehaviour listing
    leaves out entirely.
    """
    import bundle_io

    found = set()
    folder = Path(bundle_dir).expanduser()
    if not folder.is_dir():
        print(f"  (no bundle folder at {folder}; skipping this step)")
        return found

    index = bundle_io.FileIndex(str(folder))
    for name in index.files:
        # MonoScripts live in the shared assets files, not in asset bundles.
        if "sharedassets" not in name.lower() and "globalgamemanagers" not in name.lower():
            continue
        path = index.resolve_in_dir(name)
        if not path:
            continue
        try:
            env = bundle_io.open_bundle(path, index)
        except Exception:
            continue
        for obj in env.objects:
            if bundle_io.type_name(obj) != "MonoScript":
                continue
            try:
                script = obj.read()
            except Exception:
                continue
            assembly = str(getattr(script, "m_AssemblyName", "") or "")
            class_name = str(getattr(script, "m_ClassName", "") or "")
            namespace = str(getattr(script, "m_Namespace", "") or "")
            if not assembly or not class_name:
                continue
            fullname = f"{namespace}.{class_name}" if namespace else class_name
            found.add((assembly, fullname))
    return found


def _flatten(node) -> list:
    """
    A node tree as a flat list of plain values.

    to_dict() keeps m_Children as live node objects, which JSON cannot take.
    The nesting is redundant anyway: m_Level records the depth, and
    TypeTreeNode.from_list rebuilds the tree from that alone.
    """
    rows = []
    for row in node.to_dict_list():
        rows.append({key: value for key, value in row.items()
                     if key != "m_Children" and isinstance(value, (str, int, float, bool))})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dump MonoBehaviour typetrees from a DummyDll folder.")
    parser.add_argument("dummydll", help="folder of .dll files from Il2CppDumper")
    parser.add_argument("--unity", default="2022.3.62f1",
                        help="Unity version the game was built with")
    parser.add_argument("-o", "--output", default=str(DEFAULT_OUT))
    parser.add_argument("--game-version", default="",
                        help="game version these came from, recorded in the file")
    parser.add_argument("--bundles", default="AssetBundles",
                        help="bundle folder to read the game's own script list from")
    args = parser.parse_args()

    try:
        from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator
    except ImportError as exc:
        print(f"ERROR: {exc}")
        print("Install the generator with:  python -m pip install TypeTreeGeneratorAPI")
        return 1

    folder = Path(args.dummydll).expanduser()
    if not folder.is_dir():
        print(f"ERROR: not a directory: {folder}")
        return 1

    generator = TypeTreeGenerator(args.unity)
    generator.load_local_dll_folder(str(folder))
    loaded = generator.get_loaded_dll_names()

    # Classes that derive from MonoBehaviour. This misses ScriptableObjects,
    # which Unity also serialises as MonoBehaviour objects and which is where
    # most of this game's data actually lives.
    wanted = {(str(a), str(n)) for a, n in generator.get_monobehaviour_definitions()}
    print(f"Loaded {len(loaded)} assemblies, {len(wanted)} MonoBehaviour classes")

    # So ask the game instead: every MonoScript in the bundles names a class
    # that something in the game points at.
    from_scripts = collect_script_classes(args.bundles)
    print(f"Found {len(from_scripts)} classes referenced by the game's own scripts")
    wanted |= from_scripts
    print(f"Generating {len(wanted)} definitions")

    trees: dict[str, list] = {}
    failed: list[str] = []
    for assembly, fullname in sorted(wanted):
        try:
            # get_nodes_up walks base classes too, so inherited fields are
            # included; without them the byte layout does not line up.
            node = generator.get_nodes_up(assembly, fullname)
            if node is None:
                failed.append(f"{assembly}|{fullname}")
                continue
            trees[_key(assembly, fullname)] = _flatten(node)
        except Exception as exc:
            failed.append(f"{assembly}|{fullname} ({type(exc).__name__})")

    payload = {
        "format": 1,
        "unity_version": args.unity,
        "game_version": args.game_version,
        "generated_from": folder.name,
        "count": len(trees),
        "trees": trees,
    }

    out = Path(args.output).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))

    size = out.stat().st_size
    print(f"Wrote {out} ({size / 1024 / 1024:.1f} MB) with {len(trees)} class definitions")
    if failed:
        print(f"{len(failed)} could not be generated, e.g. {failed[:3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
