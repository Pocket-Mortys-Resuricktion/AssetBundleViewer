#!/usr/bin/env python3
"""
bundle_io - shared Unity asset bundle I/O for the Pocket Mortys Bundle Catalog.

Everything that actually touches a bundle lives here so the catalog builder,
the preview/export server and the command line tool all behave identically.

What it provides
----------------
FileIndex      Maps the names Unity uses internally to the files on disk, so
               cross-bundle references still resolve after the files have been
               renamed (Pocket Mortys' downloaded bundles are plain 32 hex
               hashes; this repo ships them as "Name-<hash>.assetbundle").
open_bundle    Loads one bundle with its dependencies registered.
describe       Builds one catalog entry for a bundle.
export_asset   Turns an asset into a file you can save (PNG, WAV, TXT, JSON, OBJ).
import_asset   Replaces an asset inside a loaded bundle from a file you supply.
repack         Writes the modified bundle back out.

Requires UnityPy (>= 1.20). Pillow comes with UnityPy and is used for images.
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Iterable, List, Optional, Tuple

import UnityPy
from UnityPy.environment import Environment
from UnityPy.files import BundleFile, ObjectReader, SerializedFile

__all__ = [
    "FileIndex",
    "BundleCache",
    "open_bundle",
    "describe",
    "export_asset",
    "export_bundle_zip",
    "write_assets",
    "import_asset",
    "invalidate_caches",
    "load_typetrees",
    "repack",
    "Exported",
    "ImportResult",
    "format_size",
    "PACKERS",
    "EXPORTABLE",
    "IMPORTABLE",
    "CATEGORY_MAP",
]

# -- Constants ---------------------------------------------------------------

#: Compression modes accepted by :func:`repack`.
PACKERS = ("none", "lz4", "lzma")

#: Unity types whose contents we can turn into a real file.
EXPORTABLE = {
    "Texture2D", "Sprite", "AudioClip", "TextAsset", "Mesh",
    "MonoBehaviour", "Shader", "Font", "VideoClip",
}

#: Unity types we can write back into a bundle.
IMPORTABLE = {"Texture2D", "Sprite", "TextAsset", "MonoBehaviour"}

#: Types that say nothing about what a bundle is for.
META_TYPES = {
    "AssetBundle", "PreloadData", "AssetBundleManifest",
    "BuildSettings", "ResourceManager",
}

CATEGORY_MAP = {
    "Texture2D": "Texture",
    "Sprite": "Sprite / UI",
    "AudioClip": "Audio",
    "Mesh": "3D Mesh",
    "MeshFilter": "3D Mesh",
    "SkinnedMeshRenderer": "3D Mesh",
    "Shader": "Shader",
    "Material": "Material",
    "AnimationClip": "Animation",
    "Animator": "Animation",
    "AnimatorController": "Animation",
    "TextAsset": "Text / Data",
    "MonoBehaviour": "Script Data",
    "MonoScript": "Script Data",
    "ScriptableObject": "Script Data",
    "GameObject": "Scene Object",
    "Font": "Font",
    "VideoClip": "Video",
    "ComputeShader": "Shader",
    "Cubemap": "Texture",
    "RenderTexture": "Texture",
    "LightingDataAsset": "Lighting",
    "NavMeshData": "Navigation",
    "PhysicMaterial": "Physics",
    "TerrainData": "Terrain",
}

# -- What a bundle is about --------------------------------------------------
#
# Categorising a bundle by the commonest type of thing inside it put 45% of
# the game in one pile called "Texture", which says only that Pocket Mortys is
# a 2D game. It also duplicated the asset-type filter, which already answers
# "show me the Texture2Ds" exactly rather than by guess.
#
# So a bundle is filed by what it is *for*, read from its name and, where the
# game gave us one, its path in the game's own asset tree. That is a guess,
# and a name like "Background" could fairly sit in two piles — but a wrong
# guess among fifteen useful piles beats a right one among four useless ones.
# The exact type of every asset is still recorded in `dominant_type` and
# `asset_types`, so nothing is lost by filing it this way.

#: Checked first, against container paths. Only the 157 downloaded bundles
#: have them, but a folder name from the game's own tree beats any guess.
BUNDLE_PATH_RULES = [
    ("Audio",             r"/audio|\.wav|\.mp3|\.ogg|/sfx/|/music/"),
    ("Characters",        r"/morties/|/characters/|/npcs?/|/enemies/|/bosses/"),
    ("World & buildings", r"/environment|/buildings?/|/tiles?/|/props?/|/world/|/levels?/"),
    ("UI",                r"/ui/|/hud/|/interface/"),
]

#: Checked against the bundle's own name, and only the name. Two-letter
#: language codes make that essential: " it " appears inside paths such as
#: .../audio_sfx/... and would file a bundle of sound effects under
#: Localisation.
BUNDLE_NAME_RULES = [
    ("Sprite atlas",      r"^sactx-|spriteatlas"),
    ("Localisation",      r"(?:^|[_+\- ])(?:en|de|es|fr|it|jp|ja|ko|zh|cn|tw|pt|br|ru|tr|nl|pl)"
                          r"(?:[_+\- ](?:tw|cn|br))?$|locali[sz]|language|translat"),
    # Scenery named after a character is still scenery, so the words that can
    # only mean built environment are checked before the character names.
    # Without this, every one of the Morty Lab's walls and signs filed itself
    # under Characters.
    ("World & buildings", r"building|terrain|tileset|city|bridge|fence|sidewalk|street|rooftop"
                          r"|\broof\b|\bwall|\bfloor|setdressing"),
    # The direction rule needs the frame number that a walk cycle always has:
    # plain "_Front" also ends names like Bridge_End_Front.
    ("Characters",        r"morty|rick|summer|beth|jerry|character|anime|npc|trainer|salesman|boss|enemy"
                          r"|antagonist|alien|bird ?person|squanch|meeseeks|gazorpazorp|snowball|xenon"
                          r"|[_+\- ](?:down|up|side|back|front)[_+\- ]\d+$"
                          r"|^\d+_\d+[_+\- ]"),
    ("Multiplayer",       r"^mp[a-z0-9]|multiplayer|tourn|arena|raid|leaderboard|pvp|versus|season|guild|clan"),
    ("Shop & crafting",   r"gacha|coupon|purchase|iap|shop|store|sale|price|coin|ticket|wallet|currency"
                          r"|offer|craft|recipe|blueprint|reward|chest|loot|pack\b"),
    ("Battle & attacks",  r"attack|battle|combat|damage|crit|\bko\b|heal|revive|debuff|buff|status|faint"
                          r"|capture|catch|absorb|move\b|skill|ability|turn\b"),
    # The rest of the scenery, after the character names have had their turn:
    # these words are ordinary enough to appear in a character's name.
    ("World & buildings", r"tree|rock|grass|tile|cliff|water|house|door|lab\b|dimension|portal|sign"
                          r"|bush|dirt|window|spawn|\bmap\b|world|ground|\bforest\b|\bcave\b"
                          r"|\bsnow\b|\bsand\b|\blava\b|\bpark\b"),
    ("Effects",           r"\bfx\b|effect|particle|glow|spark|smoke|explosion|trail|flash|shine|shadow"),
    ("UI",                r"btn|button|cell|\bbar\b|icon|overlay|popup|panel|screen|title|holder|\btab\b"
                          r"|toggle|slider|menu|hud|dialog|tooltip|badge|banner|background|header|footer"
                          r"|scroll|cursor|arrow|frame|mask|alert|pause|box\b|list\b|grid\b|page\b"),
    ("Animation",         r"^anim[_\- ]?\d*$|animation|\banim\b"),
]

#: Last resort, when the name says nothing at all: fall back to what is in it.
TYPE_CATEGORY = {
    "AudioClip": "Audio",
    "Shader": "Shaders & materials",
    "ComputeShader": "Shaders & materials",
    "Material": "Shaders & materials",
    "Font": "Fonts",
    "AnimationClip": "Animation",
    "AnimatorController": "Animation",
    "Animator": "Animation",
    "Mesh": "3D models",
    "MeshFilter": "3D models",
    "SkinnedMeshRenderer": "3D models",
    "TextAsset": "Text & data",
    "MonoBehaviour": "Script data",
    "MonoScript": "Script data",
    "ScriptableObject": "Script data",
    "GameObject": "Prefabs",
    "Sprite": "Sprites",
    "Texture2D": "Textures",
    "Cubemap": "Textures",
    "RenderTexture": "Textures",
    "PreloadData": "Engine",
    "PlayerSettings": "Engine",
    "AssetBundle": "Engine",
}

_PATH_RULES = [(label, re.compile(pat)) for label, pat in BUNDLE_PATH_RULES]
_NAME_RULES = [(label, re.compile(pat)) for label, pat in BUNDLE_NAME_RULES]


def categorise(derived_name: str, container_paths: Iterable[str],
               dominant_type: str) -> str:
    """What this bundle is for, as opposed to what file types are in it."""
    joined = " ".join(container_paths or []).lower()
    if joined:
        for label, pattern in _PATH_RULES:
            if pattern.search(joined):
                return label
    name = (derived_name or "").lower()
    for label, pattern in _NAME_RULES:
        if pattern.search(name):
            return label
    return TYPE_CATEGORY.get(dominant_type or "", "Other")

#: Files this toolkit writes next to the bundles, which are not bundles.
SIDECAR_FILES = {"catalog_progress.json", "catalog.json", "Thumbs.db", ".DS_Store"}

#: Unity's own fixed file names inside an APK's assets/bin/Data.
_UNITY_SPECIAL = re.compile(
    r"^(globalgamemanagers(\.assets)?|level\d+|sharedassets\d+\.assets"
    r"|resources\.assets|unity default resources|unity_builtin_extra)$",
    re.IGNORECASE,
)

#: Where each kind of bundle comes from, and goes back to.
INTERNAL = "internal"   # shipped inside the APK
EXTERNAL = "external"   # downloaded from the game's server while you played


def bundle_origin(filename: str, file_kind: str = "") -> str:
    """
    Whether a bundle shipped in the APK or was downloaded.

    The container format is the giveaway and never disagrees with the naming:
    downloaded content is a real UnityFS bundle, while everything inside the
    APK is a bare serialized file.
    """
    if file_kind:
        return EXTERNAL if file_kind == "UnityFS bundle" else INTERNAL
    # Falling back on the name: only the APK files carry a hash suffix.
    stem = os.path.splitext(os.path.basename(filename))[0]
    return INTERNAL if (_HASH32.search(stem) or "-" in stem or "." in stem) else EXTERNAL


def source_name(filename: str, file_kind: str = "") -> str:
    """
    The name this file has in the game, which is not the name it has here.

    The collection renames everything to something readable. Put a file back
    under that readable name and the game will not find it, so anything that
    writes a bundle out has to undo the renaming:

    ``ASGLogo-7a475521...assetbundle``        -> ``7a475521...``  (no extension)
    ``namefailed.globalgamemanagers.assets``  -> ``globalgamemanagers.assets``
    ``App-level0.assetbundle``                -> ``level0``
    ``v1.0.0.assetbundle``                    -> ``__data``       (see below)
    """
    base = os.path.basename(filename)
    stem = base[:-len(".assetbundle")] if base.lower().endswith(".assetbundle") else base

    if bundle_origin(filename, file_kind) == EXTERNAL:
        # Downloaded bundles are always stored as a file literally called
        # __data, inside a folder named after the bundle.
        return "__data"

    if stem.lower().startswith("namefailed."):
        return stem[len("namefailed."):]

    label, sep, rest = stem.rpartition("-")
    if sep and rest and (_HASH32.fullmatch(rest) or _UNITY_SPECIAL.match(rest)):
        return rest
    return stem


def source_location(filename: str, file_kind: str = "") -> str:
    """Where the game reads this file from, for anyone putting a mod back."""
    if bundle_origin(filename, file_kind) == EXTERNAL:
        stem = os.path.splitext(os.path.basename(filename))[0]
        return (f"Android/data/com.turner.pocketmorties/files/UnityCache/"
                f"Shared/{stem}/<version id>/__data")
    return f"assets/bin/Data/{source_name(filename, file_kind)}"

_HASH32 = re.compile(r"([0-9a-f]{32})", re.IGNORECASE)
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def format_size(num: float) -> str:
    """Human readable byte count."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def safe_filename(name: str, fallback: str = "asset") -> str:
    """Strip anything a filesystem would object to."""
    name = _UNSAFE.sub("_", (name or "").strip()).strip(". ")
    return name[:120] or fallback


# -- File index --------------------------------------------------------------

class FileIndex:
    """
    Every file in the bundle directory, reachable by any name Unity might use.

    A bundle refers to its dependencies by the *original* file name, which for
    Pocket Mortys' downloaded content is a bare 32 character hash. Once those
    files have been renamed to something readable the reference no longer
    matches anything on disk and UnityPy gives up, so sprites that live in a
    shared atlas fail to decode. Indexing by hash as well as by name fixes that.
    """

    def __init__(self, directory: str | os.PathLike):
        self.directory = str(directory)
        self._by_key: Dict[str, str] = {}
        self.files: List[str] = []
        self.refresh()

    def refresh(self) -> None:
        self._by_key.clear()
        self.files.clear()
        try:
            entries = sorted(os.listdir(self.directory))
        except OSError:
            return
        for name in entries:
            if name in SIDECAR_FILES:
                continue
            path = os.path.join(self.directory, name)
            if not os.path.isfile(path):
                continue
            self.files.append(name)
            self._add(name, path)
            stem = os.path.splitext(name)[0]
            self._add(stem, path)
            match = _HASH32.search(name)
            if match:
                self._add(match.group(1), path)
            # Also under the name the game knows it by, so a reference to
            # "globalgamemanagers.assets" finds the renamed copy of it.
            self._add(source_name(name), path)

    def _add(self, key: str, path: str) -> None:
        key = key.strip().lower()
        # First writer wins, so an exact file name is never shadowed by a stem
        # or hash that several files happen to share.
        if key and key not in self._by_key:
            self._by_key[key] = path

    def lookup(self, name: str) -> Optional[str]:
        """Resolve a name Unity used internally to a path on disk."""
        if not name:
            return None
        base = os.path.basename(name.replace("\\", "/")).strip().lower()
        for key in (base, os.path.splitext(base)[0]):
            hit = self._by_key.get(key)
            if hit:
                return hit
        match = _HASH32.search(base)
        if match:
            return self._by_key.get(match.group(1).lower())
        return None

    def resolve_in_dir(self, filename: str) -> Optional[str]:
        """
        Turn a catalog filename into a real path inside this directory.

        Only the basename is honoured, so a request can never reach outside the
        bundle directory, and the file has to be one we actually indexed.
        """
        base = os.path.basename((filename or "").replace("\\", "/"))
        if not base:
            return None
        path = self._by_key.get(base.lower())
        if not path:
            return None
        root = os.path.abspath(self.directory)
        full = os.path.abspath(path)
        if os.path.commonpath([full, root]) != root:
            return None
        return full

    def __len__(self) -> int:
        return len(self.files)


# -- Loading -----------------------------------------------------------------

def serialized_files(env_or_file: Any) -> List[SerializedFile]:
    """All SerializedFiles inside an Environment, BundleFile or SerializedFile."""
    found: List[SerializedFile] = []

    def walk(item: Any) -> None:
        if isinstance(item, SerializedFile):
            found.append(item)
        elif isinstance(item, BundleFile):
            for sub in (item.files or {}).values():
                walk(sub)

    if isinstance(env_or_file, Environment):
        for sub in env_or_file.files.values():
            walk(sub)
    else:
        walk(env_or_file)
    return found


#: How many dependency files one bundle may pull in. Atlases reference each
#: other in long chains, and following all of them would load half the game.
MAX_DEPENDENCIES = 24


def resolve_dependencies(env: Environment, index: Optional[FileIndex],
                         passes: int = 1) -> List[str]:
    """
    Load and register the files this bundle depends on.

    UnityPy registers a loaded file under its own path, so a file renamed on
    disk never matches the name the bundle asks for and the reference dies.
    We load it ourselves and register it under the name Unity expects, which
    is what makes sprites packed into a shared atlas decode at all.

    Returns the dependency names that were resolved.
    """
    if index is None:
        return []

    # The bundle we were asked about must stay the main file. Dependencies are
    # excluded from env.objects, and atlases routinely point back at the bundle
    # that referenced them, so reloading one as a dependency would empty the
    # environment.
    main_keys = list(env.files.keys())
    main_paths = {os.path.abspath(k) for k in main_keys}

    resolved: List[str] = []
    seen: set = set()
    for _ in range(max(1, passes)):
        pending = []
        for sfile in serialized_files(env):
            for external in getattr(sfile, "externals", []) or []:
                raw = getattr(external, "path", "") or ""
                name = os.path.basename(str(raw).replace("\\", "/"))
                if name and name not in seen:
                    pending.append(name)
        if not pending:
            break
        for name in pending:
            seen.add(name)
            if len(resolved) >= MAX_DEPENDENCIES:
                break
            if env.get_cab(name) is not None:
                continue
            path = index.lookup(name)
            if not path or os.path.abspath(path) in main_paths:
                continue
            try:
                loaded = env.load_file(path, is_dependency=True)
            except Exception:
                continue
            subs = serialized_files(loaded) if loaded is not None else []
            if subs:
                env.register_cab(name, subs[0])
                resolved.append(name)

    for key in main_keys:
        main = env.files.get(key)
        if main is not None and getattr(main, "is_dependency", False):
            main.is_dependency = False
    return resolved


#: Where the committed MonoBehaviour type definitions live.
TYPETREE_FILE = Path(__file__).resolve().parent / "typetrees" / "pocketmortys.json"

_typetrees: Optional["TypeTreeDump"] = None
_typetrees_loaded = False


class TypeTreeDump:
    """
    The game's MonoBehaviour field layouts, read from a JSON dump.

    Unity strips this information from IL2CPP builds, which is why a
    MonoBehaviour normally reads as an unusable blob. UnityPy can regenerate
    it from the game's metadata, but only with Il2CppDumper and a .NET
    runtime. We do that once (see tools/make_typetrees.py) and ship the
    result, so this stands in for the real generator with nothing installed.

    It only has to provide get_nodes_up, which is all UnityPy asks of a
    generator when it meets a MonoBehaviour.
    """

    def __init__(self, payload: dict):
        self.unity_version: str = payload.get("unity_version", "")
        self.game_version: str = payload.get("game_version", "")
        self._trees: Dict[str, Any] = payload.get("trees", {})
        self._cache: Dict[str, Any] = {}

    def __len__(self) -> int:
        return len(self._trees)

    def get_nodes_up(self, assembly: str, fullname: str):
        from UnityPy.helpers.TypeTreeNode import TypeTreeNode

        # Builds disagree about whether the assembly carries its .dll
        # suffix, so both the dump and the lookup drop it.
        if assembly.lower().endswith(".dll"):
            assembly = assembly[:-4]
        key = f"{assembly}|{fullname}"
        if key in self._cache:
            return self._cache[key]
        rows = self._trees.get(key)
        node = TypeTreeNode.from_list(rows) if rows else None
        self._cache[key] = node
        return node

    # UnityPy calls this one on some paths; same answer either way.
    get_nodes = get_nodes_up


def load_typetrees(path: Optional[str] = None) -> Optional[TypeTreeDump]:
    """
    The shipped type definitions, or None if they are not there.

    Loaded once and reused. A missing file is not an error: everything else
    still works, MonoBehaviours just stay unreadable.
    """
    global _typetrees, _typetrees_loaded
    if path is None and _typetrees_loaded:
        return _typetrees

    target = Path(path) if path else TYPETREE_FILE
    dump = None
    if target.is_file():
        try:
            with open(target, "r", encoding="utf-8") as handle:
                dump = TypeTreeDump(json.load(handle))
        except Exception:
            dump = None
    if path is None:
        _typetrees, _typetrees_loaded = dump, True
    return dump


def open_bundle(path: str, index: Optional[FileIndex] = None,
                passes: int = 1) -> Environment:
    """Load one bundle, wire up its dependencies and its type definitions."""
    env = UnityPy.load(path)
    resolve_dependencies(env, index, passes)
    dump = load_typetrees()
    if dump is not None:
        env.typetree_generator = dump
    return env


class BundleCache:
    """
    Small LRU of loaded bundles.

    Opening a bundle costs real time (UnityPy parses the whole container), and
    the viewer asks for many assets from the same bundle in a row, so keeping
    the last few around makes browsing feel instant.
    """

    def __init__(self, index: Optional[FileIndex] = None, size: int = 6):
        self.index = index
        self.size = size
        self._items: "OrderedDict[str, Environment]" = OrderedDict()
        self._lock = RLock()

    def get(self, path: str) -> Environment:
        key = os.path.abspath(path)
        with self._lock:
            env = self._items.pop(key, None)
            if env is not None:
                self._items[key] = env
                return env
        env = open_bundle(path, self.index)
        with self._lock:
            self._items[key] = env
            while len(self._items) > self.size:
                self._items.popitem(last=False)
        return env

    def drop(self, path: str) -> None:
        with self._lock:
            self._items.pop(os.path.abspath(path), None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


# -- Reading assets ----------------------------------------------------------

def type_name(obj: ObjectReader) -> str:
    try:
        return obj.type.name if hasattr(obj.type, "name") else str(obj.type)
    except Exception:
        return "Unknown"


def asset_name(obj: ObjectReader) -> str:
    """Read an asset's name as cheaply as we can, never raising."""
    try:
        peek = obj.peek_name()
        if isinstance(peek, str) and peek.strip():
            return peek.strip()
    except Exception:
        pass
    try:
        data = obj.read()
        for attr in ("m_Name", "name", "m_AssetBundleName"):
            val = getattr(data, attr, None)
            if isinstance(val, str) and val.strip():
                return val.strip()
    except Exception:
        pass
    return ""


def find_asset(env: Environment, path_id: int) -> Optional[ObjectReader]:
    """Find one object by path_id, without reading every other object."""
    for obj in env.objects:
        if obj.path_id == path_id:
            return obj
    return None


def _strip_hash(stem: str) -> str:
    """'Name-<32 hex>' -> 'Name'. Leaves anything else alone."""
    match = re.match(r"^(.*)-[0-9a-f]{32}$", stem, re.IGNORECASE)
    return match.group(1) if match and match.group(1) else stem


def describe(path: str) -> dict:
    """
    Build one catalog entry for a file.

    Never raises: a file that is not a bundle comes back with ``valid: False``
    and the reason in ``error``.
    """
    filename = os.path.basename(path)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0

    entry: Dict[str, Any] = {
        "filename": filename,
        "size": size,
        "size_human": format_size(size),
        "valid": False,
        "file_kind": "",
        "original_name": "",
        "derived_name": "",
        "name_source": "",
        "category": "Unknown",
        "dominant_type": "",
        "unity_version": "",
        "internal_paths": [],
        "container_paths": [],
        "dependencies": [],
        "asset_types": [],
        "type_counts": {},
        "assets": [],
        "all_asset_names": [],
        "asset_count": 0,
        "error": "",
    }

    try:
        env = UnityPy.load(path)
    except Exception as exc:
        entry["error"] = f"{type(exc).__name__}: {exc}"
        return entry

    try:
        top = next(iter(env.files.values()), None)
        entry["file_kind"] = (
            "UnityFS bundle" if isinstance(top, BundleFile)
            else "SerializedFile" if isinstance(top, SerializedFile)
            else type(top).__name__ if top is not None else ""
        )

        sfiles = serialized_files(env)
        if not sfiles:
            # UnityPy happily "loads" arbitrary bytes and hands back an empty
            # environment, so this is where a file that is not a bundle at all
            # gets caught.
            entry["error"] = "not a Unity bundle or serialized file"
            return entry

        # The CAB names live on the inner SerializedFiles. env.files is keyed by
        # the path we passed in, which tells us nothing about the bundle.
        internal = []
        for sfile in sfiles:
            name = os.path.basename(str(getattr(sfile, "name", "") or ""))
            if name and name != filename:
                internal.append(name)
            version = str(getattr(sfile, "unity_version", "") or "")
            if version and not entry["unity_version"]:
                entry["unity_version"] = version
        entry["internal_paths"] = internal

        deps = []
        for sfile in sfiles:
            for external in getattr(sfile, "externals", []) or []:
                raw = os.path.basename(str(getattr(external, "path", "") or ""))
                if raw and raw not in deps:
                    deps.append(raw)
        entry["dependencies"] = deps

        container_paths: List[str] = []
        try:
            for container_path, _ in env.container.items():
                if container_path:
                    container_paths.append(container_path)
        except Exception:
            pass

        assets: List[dict] = []
        type_counts: Dict[str, int] = {}
        bundle_name = ""

        for obj in env.objects:
            tname = type_name(obj)
            type_counts[tname] = type_counts.get(tname, 0) + 1

            if tname == "AssetBundle":
                try:
                    data = obj.read()
                    candidate = getattr(data, "m_AssetBundleName", "") or ""
                    if isinstance(candidate, str) and candidate.strip():
                        bundle_name = candidate.strip()
                    container = getattr(data, "m_Container", None)
                    if isinstance(container, dict):
                        container_paths.extend(k for k in container if k)
                    elif isinstance(container, (list, tuple)):
                        for item in container:
                            if (isinstance(item, (list, tuple)) and item
                                    and isinstance(item[0], str)):
                                container_paths.append(item[0])
                except Exception:
                    pass
                continue  # metadata, not something you would export

            assets.append({
                "type": tname,
                "name": asset_name(obj),
                # A path_id is a signed 64-bit integer, which is wider than
                # the only number type JavaScript has. Left as a number, one
                # in five of them loses its last digits when the catalog is
                # parsed in a browser and no longer identifies an asset.
                "path_id": str(obj.path_id),
            })

        # Deduplicate container paths, keeping order.
        entry["container_paths"] = list(dict.fromkeys(container_paths))
        entry["valid"] = True
        entry["assets"] = assets
        entry["asset_count"] = len(assets)
        entry["type_counts"] = type_counts
        entry["asset_types"] = sorted(type_counts)
        entry["all_asset_names"] = list(dict.fromkeys(
            a["name"] for a in assets if a["name"]
        ))

        # -- Name recovery, best source first -----------------------------
        stem = os.path.splitext(filename)[0]
        stripped = _strip_hash(stem)
        candidates: List[Tuple[str, str]] = []
        if bundle_name:
            candidates.append((bundle_name, "AssetBundle.m_AssetBundleName"))
        if entry["container_paths"]:
            first = entry["container_paths"][0]
            pretty = os.path.splitext(os.path.basename(first))[0]
            if pretty:
                candidates.append((pretty, "container path"))
        if stripped and not _HASH32.fullmatch(stripped):
            candidates.append((stripped, "file name"))
        if entry["all_asset_names"]:
            candidates.append((entry["all_asset_names"][0], "first asset"))

        for value, source in candidates:
            if value:
                entry["derived_name"] = value
                entry["name_source"] = source
                break
        entry["original_name"] = entry["derived_name"]  # kept for older catalogs
        # The name the game knows this file by, which is how other bundles
        # refer to it. Recorded so the viewer can match a dependency to the
        # renamed file that holds it without re-deriving the rule.
        entry["source_name"] = source_name(filename, entry["file_kind"])
        entry["origin"] = bundle_origin(filename, entry["file_kind"])

        real = {k: v for k, v in type_counts.items() if k not in META_TYPES}
        pool = real or type_counts
        if pool:
            dominant = max(pool, key=lambda k: pool[k])
            entry["dominant_type"] = dominant
            entry["category"] = categorise(
                entry["derived_name"], entry["container_paths"], dominant)

    except Exception as exc:
        entry["error"] = f"{type(exc).__name__}: {exc}"

    return entry


# -- Exporting ---------------------------------------------------------------

@dataclass
class Exported:
    """One asset turned into bytes you can write to disk."""
    filename: str
    mimetype: str
    data: bytes
    kind: str  # image | audio | text | json | model | binary
    note: str = ""


#: Uncompressed formats that store more than 8 bits per channel. UnityPy
#: hands these straight to Pillow, which has no raw mode for them, so they
#: fail with "unknown raw mode". They are rare but they do occur here.
_WIDE_FORMATS = {
    63: ("RG16", 2, (0, 1, None, None), 8),
    72: ("RG32", 2, (0, 1, None, None), 16),
    73: ("RGB48", 3, (0, 1, 2, None), 16),
    74: ("RGBA64", 4, (0, 1, 2, 3), 16),
}


def _wide_texture_image(texture):
    """
    Decode a 16-bit-per-channel texture down to ordinary 8-bit RGBA.

    Taking the high byte of each channel is exactly what the game's own
    renderer ends up showing on an 8-bit display, so nothing visible is lost.
    """
    from PIL import Image

    spec = _WIDE_FORMATS.get(int(texture.m_TextureFormat))
    if spec is None:
        return None
    _, channels, order, bits = spec
    width, height = int(texture.m_Width), int(texture.m_Height)
    try:
        raw = bytes(texture.image_data or b"")
    except Exception:
        return None

    step = bits // 8
    pixel_size = channels * step
    if len(raw) < width * height * pixel_size:
        # The pixels usually live in a .resS stream rather than inline.
        try:
            raw = bytes(texture.get_image_data() or b"")
        except Exception:
            return None
    if len(raw) < width * height * pixel_size:
        return None

    out = bytearray(width * height * 4)
    high = step - 1  # little endian, so the most significant byte is last
    for pixel in range(width * height):
        base = pixel * pixel_size
        for slot, source in enumerate(order):
            out[pixel * 4 + slot] = (
                raw[base + source * step + high] if source is not None else 255
            )
    image = Image.frombytes("RGBA", (width, height), bytes(out))
    # Unity stores textures bottom-up.
    return image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)


def _wide_sprite_image(sprite):
    """The same rescue for a sprite whose atlas is one of those formats."""
    from PIL import Image
    from UnityPy.enums import SpritePackingRotation
    from UnityPy.export.SpriteHelper import SpriteSettings

    render = _sprite_render_data(sprite)
    pointer = getattr(render, "texture", None)
    if pointer is None:
        return None
    try:
        texture = pointer.deref().read()
    except Exception:
        return None

    atlas = _wide_texture_image(texture)
    if atlas is None:
        return None

    rect = render.textureRect
    x, y = int(round(rect.x)), int(round(rect.y))
    w, h = int(round(rect.width)), int(round(rect.height))
    region = atlas.crop((x, atlas.height - y - h, x + w, atlas.height - y))

    settings = SpriteSettings(render.settingsRaw)
    if settings.packed == 1:
        undo = {
            SpritePackingRotation.kSPRFlipHorizontal: Image.Transpose.FLIP_LEFT_RIGHT,
            SpritePackingRotation.kSPRFlipVertical: Image.Transpose.FLIP_TOP_BOTTOM,
            SpritePackingRotation.kSPRRotate180: Image.Transpose.ROTATE_180,
            SpritePackingRotation.kSPRRotate90: Image.Transpose.ROTATE_90,
        }.get(settings.packingRotation)
        if undo is not None:
            region = region.transpose(undo)
    return region


def _missing_stream(exc: Exception, what: str) -> str:
    """
    Explain a missing side-car file.

    Unity can keep an asset's bulk data in a companion .resource or .resS file
    next to the bundle. Collections that saved only the bundle itself lose it,
    and then there is nothing to decode - nothing we can repair from here.
    """
    match = re.search(r"([^\s/\\]+\.(?:resource|resS))", str(exc), re.IGNORECASE)
    missing = match.group(1) if match else "its companion data file"
    return (f"the {what} live in {missing}, which is not in the bundle folder. "
            f"That file was not saved alongside this bundle, so this asset "
            f"cannot be decoded.")


def _audio_mime(data: bytes) -> Tuple[str, str]:
    if data[:4] == b"OggS":
        return "audio/ogg", ".ogg"
    if data[:4] == b"RIFF":
        return "audio/wav", ".wav"
    if data[:3] == b"ID3" or data[:2] == b"\xff\xfb":
        return "audio/mpeg", ".mp3"
    if data[:4] == b"FSB5":
        return "application/octet-stream", ".fsb"
    return "audio/wav", ".wav"


def _jsonable(value: Any, depth: int = 0) -> Any:
    """Make a typetree value safe to serialise, without exploding on cycles."""
    if depth > 12:
        return "..."
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return {"__bytes__": len(value)}
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v, depth + 1) for v in value]
    if hasattr(value, "__dict__"):
        return {
            k: _jsonable(v, depth + 1)
            for k, v in vars(value).items()
            if not k.startswith("_") and k not in ("assets_file", "object_reader")
        }
    return str(value)


def _typetree_json(obj: ObjectReader) -> Tuple[bytes, str]:
    """A MonoBehaviour / generic object as readable JSON."""
    try:
        tree = obj.read_typetree()
        return json.dumps(_jsonable(tree), indent=2, default=str).encode("utf-8"), ""
    except Exception as exc:
        data = obj.read()
        note = f"typetree unavailable ({type(exc).__name__}); showing parsed fields"
        return json.dumps(_jsonable(data), indent=2, default=str).encode("utf-8"), note


def export_asset(obj: ObjectReader, prefer: str = "auto") -> Exported:
    """
    Turn one asset into a downloadable file.

    ``prefer="raw"`` always gives the undecoded object bytes, which is what you
    want if you intend to edit something we have no proper converter for.
    """
    tname = type_name(obj)
    name = safe_filename(asset_name(obj) or f"{tname}_{obj.path_id}")

    if prefer == "raw":
        return Exported(f"{name}.bin", "application/octet-stream",
                        bytes(obj.get_raw_data()), "binary",
                        "raw serialised object data")

    if tname in ("Texture2D", "Sprite"):
        data = obj.read()
        try:
            image = data.image
        except FileNotFoundError as exc:
            raise ValueError(_missing_stream(exc, "pixels")) from exc
        except Exception:
            image = (_wide_texture_image(data) if tname == "Texture2D"
                     else _wide_sprite_image(data))
            if image is None:
                raise
        if image is None:
            raise ValueError("this asset has no decodable image data")
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return Exported(f"{name}.png", "image/png", buf.getvalue(), "image")

    if tname == "AudioClip":
        data = obj.read()
        try:
            samples = data.samples or {}
        except FileNotFoundError as exc:
            raise ValueError(_missing_stream(exc, "audio")) from exc
        if not samples:
            raise ValueError("no audio samples could be decoded")
        clip_name, audio = next(iter(samples.items()))
        mime, ext = _audio_mime(audio)
        out_name = safe_filename(os.path.splitext(clip_name)[0] or name)
        return Exported(f"{out_name}{ext}", mime, audio, "audio")

    if tname == "TextAsset":
        data = obj.read()
        script = getattr(data, "m_Script", b"") or b""
        if isinstance(script, str):
            script = script.encode("utf-8", "surrogateescape")
        return Exported(f"{name}.txt", "text/plain; charset=utf-8",
                        bytes(script), "text")

    if tname == "Mesh":
        from UnityPy.export import MeshExporter
        data = obj.read()
        obj_text = MeshExporter.export_mesh_obj(data)
        if isinstance(obj_text, str):
            obj_text = obj_text.encode("utf-8")
        return Exported(f"{name}.obj", "text/plain; charset=utf-8",
                        bytes(obj_text), "model")

    if tname == "Font":
        data = obj.read()
        blob = getattr(data, "m_FontData", b"") or b""
        if blob:
            ext = ".otf" if bytes(blob[:4]) == b"OTTO" else ".ttf"
            return Exported(f"{name}{ext}", "font/ttf", bytes(blob), "binary")

    if tname == "VideoClip":
        data = obj.read()
        blob = getattr(data, "m_VideoData", b"") or b""
        if blob:
            return Exported(f"{name}.mp4", "video/mp4", bytes(blob), "binary")

    # MonoBehaviour, Shader, Material, GameObject and everything else: the
    # typetree is the useful, editable representation.
    payload, note = _typetree_json(obj)
    return Exported(f"{name}.json", "application/json", payload, "json", note)


def write_assets(zf: zipfile.ZipFile, env: Environment,
                 path_ids: Optional[Iterable[Any]] = None,
                 types: Optional[Iterable[str]] = None,
                 prefer: str = "auto", folder: str = "",
                 used: Optional[Dict[str, int]] = None) -> List[dict]:
    """
    Write assets from one bundle into an archive that is already open.

    Separate from export_bundle_zip so a single zip can span several bundles,
    which is what exporting a selection needs.

    ``path_ids`` narrows it to exactly those assets, which is how "export what
    I am looking at" works: the caller sends the ids its filters left showing
    rather than getting the whole bundle regardless.

    One asset failing never aborts the archive; the reason lands in the report.
    """
    wanted = set(types) if types else None
    chosen = {str(p) for p in path_ids} if path_ids is not None else None
    if used is None:
        used = {}
    report: List[dict] = []

    for obj in env.objects:
        tname = type_name(obj)
        if tname == "AssetBundle":
            continue
        if wanted is not None and tname not in wanted:
            continue
        if chosen is not None and str(obj.path_id) not in chosen:
            continue

        row = {"path_id": str(obj.path_id), "type": tname, "name": "",
               "file": "", "ok": False, "error": ""}
        try:
            item = export_asset(obj, prefer=prefer)
            row["name"] = os.path.splitext(item.filename)[0]
            parts = [p for p in (folder, tname) if p]
            arc = "/".join(parts + [item.filename])
            # Two assets in one bundle may share a name.
            count = used.get(arc.lower(), 0)
            used[arc.lower()] = count + 1
            if count:
                stem, ext = os.path.splitext(arc)
                arc = f"{stem}_{obj.path_id}{ext}"
            zf.writestr(arc, item.data)
            row["file"] = arc
            row["ok"] = True
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        report.append(row)
    return report


def export_bundle_zip(env: Environment, types: Optional[Iterable[str]] = None,
                      prefer: str = "auto",
                      path_ids: Optional[Iterable[Any]] = None
                      ) -> Tuple[bytes, List[dict]]:
    """Everything from one bundle as a zip, plus a per-asset report."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        report = write_assets(zf, env, path_ids=path_ids, types=types, prefer=prefer)
        zf.writestr("_export_report.json", json.dumps(report, indent=2))
    return buf.getvalue(), report


# -- Importing ---------------------------------------------------------------

@dataclass
class ImportResult:
    """
    What an import actually did, so the UI can be honest about it.

    ``preview`` holds the asset as it will appear in the rebuilt bundle,
    including any quality lost to re-encoding. It has to be captured here:
    UnityPy keeps an edit in a write buffer that reading the object again
    does not consult, so only the file written at the end reflects it.
    """
    path_id: int
    type: str
    name: str
    source: str
    detail: str = ""
    warnings: List[str] = field(default_factory=list)
    preview: Optional[bytes] = None
    preview_mime: str = ""

    def as_dict(self) -> dict:
        return {
            # A string, so the browser keeps every digit. See describe().
            "path_id": str(self.path_id), "type": self.type, "name": self.name,
            "source": self.source, "detail": self.detail,
            "warnings": list(self.warnings),
        }


def _png_bytes(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def invalidate_caches(env: Environment) -> None:
    """
    Forget decoded images.

    UnityPy caches the decoded atlas on the SerializedFile that asked for it,
    so after writing into a texture the next preview would still show the old
    pixels unless we clear it.
    """
    for sfile in serialized_files(env):
        cache = getattr(sfile, "_cache", None)
        if isinstance(cache, dict):
            cache.clear()


def _load_image(data: bytes):
    from PIL import Image
    image = Image.open(io.BytesIO(data))
    image.load()
    return image.convert("RGBA")


def _format_name(texture) -> str:
    try:
        return texture.m_TextureFormat.name
    except AttributeError:
        return str(texture.m_TextureFormat)


def _import_texture(obj: ObjectReader, data: bytes, source: str) -> ImportResult:
    from PIL import Image
    texture = obj.read()
    new = _load_image(data)
    old_w, old_h = int(texture.m_Width), int(texture.m_Height)
    warnings: List[str] = []

    if (new.width, new.height) != (old_w, old_h):
        warnings.append(
            f"resized {new.width}x{new.height} to {old_w}x{old_h} to match the "
            "original; the game expects the original dimensions"
        )
        new = new.resize((old_w, old_h), Image.LANCZOS)

    # Keeping the original format means the game loads it exactly as before.
    # Compressed formats (ASTC/ETC) are lossy, so the result will not be a
    # pixel-perfect copy of the file that went in.
    texture.set_image(new, target_format=texture.m_TextureFormat)
    texture.save()
    fmt = _format_name(texture)
    if fmt.upper().startswith(("ASTC", "ETC", "DXT", "PVRTC", "BC")):
        warnings.append(f"re-encoded as {fmt}, which is lossy")
    return ImportResult(obj.path_id, "Texture2D", texture.m_Name or "", source,
                        f"{old_w}x{old_h} {fmt}", warnings,
                        _png_bytes(texture.image), "image/png")


def _sprite_render_data(sprite):
    """
    The SpriteAtlasData that actually says where this sprite's pixels live.

    A packed sprite's rectangle is recorded on the atlas, not on the sprite,
    so reading m_RD directly gives the wrong (often empty) pointer. This is
    the same lookup UnityPy's own exporter does.
    """
    from UnityPy.enums import ClassIDType

    atlas = None
    if getattr(sprite, "m_SpriteAtlas", None):
        try:
            atlas = sprite.m_SpriteAtlas.deref_parse_as_object()
        except Exception:
            atlas = None
    if atlas is None and getattr(sprite, "m_AtlasTags", None):
        # The direct pointer is empty in some builds; match the atlas by name.
        for candidate in (sprite.assets_file.objects.values()
                          if sprite.assets_file else []):
            if candidate.type == ClassIDType.SpriteAtlas and \
                    candidate.peek_name() == sprite.m_AtlasTags[0]:
                atlas = candidate.parse_as_object()
                break

    if atlas is not None:
        for key, value in atlas.m_RenderDataMap:
            if key == sprite.m_RenderDataKey:
                return value
    return sprite.m_RD


def _import_sprite(obj: ObjectReader, data: bytes, source: str) -> ImportResult:
    """
    Replace a sprite by painting the new image back into its texture.

    A Sprite is a rectangle inside a (usually shared) atlas texture, so the
    only way to change how it looks is to write into that texture. Every other
    sprite packed into the same atlas is left untouched.

    The export side crops the rectangle, un-rotates it and flips it; this
    undoes all three so a PNG exported from here can be edited and imported
    straight back.
    """
    from PIL import Image
    from UnityPy.enums import SpritePackingMode, SpritePackingRotation
    from UnityPy.export.SpriteHelper import SpriteSettings

    sprite = obj.read()
    render = _sprite_render_data(sprite)

    pointer = getattr(render, "texture", None)
    texture = None
    if pointer is not None:
        try:
            target = pointer.deref()
            texture = target.read() if target is not None else None
        except Exception:
            texture = None
    if texture is None:
        raise ValueError(
            "this sprite's texture could not be found. It lives in another "
            "bundle that is not in the bundle directory."
        )

    warnings: List[str] = []
    if getattr(render, "alphaTexture", None):
        warnings.append(
            "this sprite has a separate alpha texture, which is not updated; "
            "transparency may not come out as expected"
        )

    rect = render.textureRect
    x, y = int(round(rect.x)), int(round(rect.y))
    w, h = int(round(rect.width)), int(round(rect.height))

    settings = SpriteSettings(render.settingsRaw)
    if settings.packingMode == SpritePackingMode.kSPMTight:
        warnings.append(
            "this sprite is tightly packed, so the exported PNG was masked to "
            "its outline; the full rectangle is written back"
        )

    new = _load_image(data)
    # The exporter reads the atlas bottom-up, crops, rotates and then flips the
    # result. Undoing that and converting back to top-left orientation works
    # out to applying the very same rotation again, because a flip either side
    # of a transform turns it into its own inverse here.
    undo = redo = None
    if settings.packed == 1:
        undo, redo = {
            SpritePackingRotation.kSPRFlipHorizontal:
                (Image.Transpose.FLIP_LEFT_RIGHT, Image.Transpose.FLIP_LEFT_RIGHT),
            SpritePackingRotation.kSPRFlipVertical:
                (Image.Transpose.FLIP_TOP_BOTTOM, Image.Transpose.FLIP_TOP_BOTTOM),
            SpritePackingRotation.kSPRRotate180:
                (Image.Transpose.ROTATE_180, Image.Transpose.ROTATE_180),
            SpritePackingRotation.kSPRRotate90:
                (Image.Transpose.ROTATE_270, Image.Transpose.ROTATE_90),
        }.get(settings.packingRotation, (None, None))
    if undo is not None:
        new = new.transpose(undo)

    if (new.width, new.height) != (w, h):
        warnings.append(f"resized {new.width}x{new.height} to the sprite's {w}x{h}")
        new = new.resize((w, h), Image.LANCZOS)

    # Texture2D.image is top-left origin; Unity's rectangle is bottom-left.
    atlas = texture.image.convert("RGBA")
    box = (x, atlas.height - y - h, x + w, atlas.height - y)
    atlas.paste(new, (box[0], box[1]))
    texture.set_image(atlas, target_format=texture.m_TextureFormat)
    texture.save()

    # Crop the region back out of the encoded atlas so the viewer can show
    # what the sprite will really look like once the bundle is rebuilt.
    region = texture.image.convert("RGBA").crop(box)
    if redo is not None:
        region = region.transpose(redo)

    fmt = _format_name(texture)
    if fmt.upper().startswith(("ASTC", "ETC", "DXT", "PVRTC", "BC")):
        warnings.append(f"the atlas is re-encoded as {fmt}, which is lossy")
    if int(texture.m_Width) != w or int(texture.m_Height) != h:
        warnings.append(
            f"written into shared texture '{texture.m_Name}' "
            f"({texture.m_Width}x{texture.m_Height}) at {x},{y}"
        )
    return ImportResult(obj.path_id, "Sprite", sprite.m_Name or "", source,
                        f"{w}x{h} in {texture.m_Name}", warnings,
                        _png_bytes(region), "image/png")


def _import_textasset(obj: ObjectReader, data: bytes, source: str) -> ImportResult:
    asset = obj.read()
    asset.m_Script = data.decode("utf-8", "surrogateescape")
    asset.save()
    return ImportResult(obj.path_id, "TextAsset", asset.m_Name or "", source,
                        f"{len(data)} bytes", [], data,
                        "text/plain; charset=utf-8")


def _import_typetree(obj: ObjectReader, data: bytes, source: str) -> ImportResult:
    tree = json.loads(data.decode("utf-8"))
    if not isinstance(tree, dict):
        raise ValueError("the JSON must be an object matching the asset's fields")
    obj.save_typetree(tree)
    return ImportResult(obj.path_id, type_name(obj), str(tree.get("m_Name", "")),
                        source, f"{len(tree)} fields", [], data, "application/json")


def import_asset(obj: ObjectReader, data: bytes, source: str = "") -> ImportResult:
    """
    Replace one asset's contents from an uploaded file.

    The bundle is changed in memory only; call :func:`repack` to write it out.
    """
    tname = type_name(obj)
    source = source or "uploaded file"

    if not data:
        raise ValueError("the replacement file is empty")

    if tname == "Texture2D":
        return _import_texture(obj, data, source)
    if tname == "Sprite":
        return _import_sprite(obj, data, source)
    if tname == "TextAsset":
        return _import_textasset(obj, data, source)
    if source.lower().endswith(".json") or tname == "MonoBehaviour":
        return _import_typetree(obj, data, source)

    raise ValueError(
        f"replacing {tname} is not supported. Supported: "
        + ", ".join(sorted(IMPORTABLE))
        + ". Any type can be edited by exporting its .json and importing it back."
    )


# -- Writing back ------------------------------------------------------------

def changed_files(env: Environment) -> List[Tuple[str, Any]]:
    """
    Every loaded file that an import touched, as (file name, file object).

    Usually one, but replacing a sprite writes into its atlas, and that atlas
    can live in a different bundle: you need that file too for the change to
    show up in the game.
    """
    out: List[Tuple[str, Any]] = []
    for key, item in env.files.items():
        if getattr(item, "is_changed", False):
            out.append((os.path.basename(str(key)), item))
    return out


def build_changed(env: Environment, pack: str = "lz4") -> List[Tuple[str, bytes]]:
    """Serialise every changed file. Returns (file name, bytes) pairs."""
    if pack not in PACKERS:
        raise ValueError(f"pack must be one of {', '.join(PACKERS)}")
    changed = changed_files(env)
    if not changed:
        raise ValueError(
            "nothing has been replaced in this bundle, so there is nothing to build"
        )
    return [(name, item.save(packer=pack)) for name, item in changed]


def repack(env: Environment, out_dir: str, pack: str = "lz4") -> List[str]:
    """
    Write every changed file into ``out_dir`` under its original name.

    Keeping the original names matters: a sprite edit changes the atlas
    bundle, not the bundle you opened, and the game looks that file up by
    name. Returns every path written.
    """
    directory = Path(os.path.abspath(out_dir))
    directory.mkdir(parents=True, exist_ok=True)

    written: List[str] = []
    for name, payload in build_changed(env, pack):
        target = directory / safe_filename(name, "modified.assetbundle")
        target.write_bytes(payload)
        written.append(str(target))
    return written
