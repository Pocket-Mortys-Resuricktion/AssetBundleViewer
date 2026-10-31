#!/usr/bin/env python3
"""
Pocket Mortys Bundle Catalog Server
===================================
Serves the viewer and does the work the browser cannot: reading assets out of
the bundles, exporting them as real files, replacing them, and writing the
modified bundle back out.

Usage:
    python catalog_server.py AssetBundles
    python catalog_server.py AssetBundles --port 7777 --no-browser

Then open http://127.0.0.1:7777 (it opens by itself unless --no-browser).

Everything stays on your own machine: the server binds to 127.0.0.1 and never
talks to the network.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import threading
import time
import webbrowser
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional

try:
    from flask import (Flask, Response, abort, jsonify, request, send_file,
                       send_from_directory)
except ImportError:
    print("ERROR: Flask is not installed.")
    print(f"Install the dependencies with:\n    {sys.executable} -m pip install -r requirements.txt")
    sys.exit(1)

try:
    import bundle_io
except ImportError as exc:
    print(f"ERROR: could not import bundle_io: {exc}")
    print("Run this script from the folder it lives in.")
    sys.exit(1)

HERE = Path(__file__).resolve().parent
VIEWER = HERE / "catalog_viewer.html"

app = Flask(__name__, static_folder=None)


class State:
    """Everything the server knows, in one place."""

    def __init__(self) -> None:
        self.directory: str = ""
        self.index: Optional[bundle_io.FileIndex] = None
        self.cache = bundle_io.BundleCache(None, size=4)
        self.catalog: List[dict] = []
        self.catalog_path: str = ""
        self.out_dir: Path = HERE / "exports"
        self.token: str = ""
        self.require_token: bool = True
        self.allow_cors: bool = False
        self.max_zip_assets: int = 4000
        # bundle filename -> {"env":, "changes": [...], "touched": float}
        self.edits: Dict[str, dict] = {}
        self._extra: List[str] = []
        self._extra_stamp = None
        self.lock = threading.RLock()

    def set_directory(self, path: str) -> None:
        resolved = str(Path(path).expanduser().resolve())
        if not os.path.isdir(resolved):
            raise ValueError(f"not a directory: {path}")
        self.directory = resolved
        self.index = bundle_io.FileIndex(resolved)
        self.cache = bundle_io.BundleCache(self.index, size=4)
        self.edits.clear()
        self._extra_stamp = None

    def bundle_path(self, filename: str) -> str:
        """Resolve a bundle name to a path, or refuse."""
        if not self.directory or self.index is None:
            abort(400, "No bundle directory is set yet.")
        path = self.index.resolve_in_dir(filename or "")
        if not path:
            abort(404, f"No bundle called {filename!r} in the bundle directory.")
        return path

    def env_for(self, filename: str, for_edit: bool = False):
        """
        The loaded bundle to work with.

        Once a bundle has staged edits we keep using that same environment, so
        previews show the replacement rather than the original.
        """
        path = self.bundle_path(filename)
        key = os.path.basename(path)
        with self.lock:
            session = self.edits.get(key)
            if session is not None:
                session["touched"] = time.time()
                return session["env"]
            if not for_edit:
                return self.cache.get(path)
            env = bundle_io.open_bundle(path, self.index)
            self.edits[key] = {"env": env, "changes": [], "touched": time.time(),
                               "path": path}
            return env

    def extra_bundles(self) -> List[str]:
        """
        Bundles in the folder that the catalog does not know about.

        Anything you drop in yourself lands here, and the viewer marks it so
        you can tell your own files from the collection that shipped.
        """
        if self.index is None:
            return []
        stamp = (len(self.index), len(self.catalog))
        if self._extra_stamp != stamp:
            known = {str(b.get("filename", "")).lower() for b in self.catalog}
            self._extra = [name for name in self.index.files
                           if name.lower() not in known
                           and not name.lower().endswith((".resource", ".ress"))]
            self._extra_stamp = stamp
        return self._extra

    def session_for(self, filename: str) -> Optional[dict]:
        path = self.index.resolve_in_dir(filename or "") if self.index else None
        if not path:
            return None
        return self.edits.get(os.path.basename(path))


state = State()


# -- Request plumbing --------------------------------------------------------

OPEN_ROUTES = {"/", "/api/status", "/favicon.ico"}


@app.before_request
def guard():
    if request.method == "OPTIONS":
        return None
    if request.path in OPEN_ROUTES or not state.require_token:
        return None
    supplied = (request.headers.get("X-PM-Token")
                or request.args.get("token")
                or (request.form.get("token") if request.form else None)
                or "")
    if not secrets.compare_digest(str(supplied), state.token):
        return jsonify({
            "error": "Missing or wrong access token.",
            "hint": "Open the viewer at the address the server printed, or "
                    "restart with --no-token.",
        }), 403
    return None


@app.after_request
def headers(response: Response) -> Response:
    if state.allow_cors:
        # Only on request: lets a viewer opened straight from disk reach the
        # server. The token is what actually keeps other pages out.
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-PM-Token"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(500)
def as_json(err):
    return jsonify({"error": getattr(err, "description", str(err))}), \
        getattr(err, "code", 500)


def explain(exc: Exception) -> str:
    """
    A message fit to show someone.

    Our own refusals are already written as sentences; anything else gets its
    exception type, because the bare text is rarely enough on its own.
    """
    if isinstance(exc, ValueError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def wants(name: str, required: bool = True) -> str:
    value = (request.args.get(name) or "").strip()
    if required and not value:
        abort(400, f"Missing '{name}'.")
    return value


def wants_path_id() -> int:
    raw = (request.args.get("path_id") or "").strip()
    if not raw:
        abort(400, "Missing 'path_id'.")
    try:
        return int(raw)
    except ValueError:
        abort(400, f"'path_id' must be a whole number, got {raw!r}.")


def locate(filename: str, path_id: int, for_edit: bool = False):
    env = state.env_for(filename, for_edit=for_edit)
    obj = bundle_io.find_asset(env, path_id)
    if obj is None:
        abort(404, f"No asset with path_id {path_id} in {filename}.")
    return env, obj


# -- Viewer ------------------------------------------------------------------

@app.route("/")
def viewer():
    if not VIEWER.exists():
        abort(404, f"{VIEWER.name} is missing from {HERE}.")
    html = VIEWER.read_text(encoding="utf-8")
    config = json.dumps({
        "token": state.token,
        "served": True,
        "bundleDir": state.directory,
        "hasCatalog": bool(state.catalog),
    })
    # The viewer reads window.PM_CONFIG if it is there and falls back to
    # sensible defaults when opened straight from disk.
    return Response(
        html.replace("</head>", f"<script>window.PM_CONFIG={config};</script></head>", 1),
        mimetype="text/html",
    )


@app.route("/favicon.ico")
def favicon():
    return Response(b"", mimetype="image/x-icon")


# -- Status and catalog ------------------------------------------------------

@app.route("/api/status")
def status():
    return jsonify({
        "ok": True,
        "version": 2,
        "bundle_dir": state.directory,
        "bundle_count": len(state.index) if state.index else 0,
        "catalog_len": len(state.catalog),
        "catalog_path": state.catalog_path,
        "out_dir": str(state.out_dir),
        "needs_token": state.require_token,
        "pending_edits": {name: len(s["changes"]) for name, s in state.edits.items()},
        "can_import": sorted(bundle_io.IMPORTABLE),
        "packers": list(bundle_io.PACKERS),
        # Bundles you added yourself, so the viewer can mark them as yours.
        "extra_bundles": state.extra_bundles()[:500],
        "has_typetrees": bundle_io.load_typetrees() is not None,
    })


@app.route("/api/catalog")
def catalog():
    return Response(json.dumps(state.catalog, separators=(",", ":")),
                    mimetype="application/json")


@app.route("/api/set-dir", methods=["POST", "OPTIONS"])
def set_dir():
    if request.method == "OPTIONS":
        return "", 204
    payload = request.get_json(silent=True) or {}
    try:
        state.set_directory(str(payload.get("path", "")).strip().strip('"'))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "bundle_dir": state.directory,
                    "bundle_count": len(state.index or [])})


@app.route("/api/assets")
def assets():
    """
    The real contents of one bundle, read from the file.

    The viewer uses this when a bundle is opened, so what you see is what is
    in the file right now rather than whatever the catalog recorded.
    """
    filename = wants("bundle")
    env = state.env_for(filename)
    session = state.session_for(filename)
    edited = {c["path_id"] for c in session["changes"]} if session else set()
    rows = []
    for obj in env.objects:
        tname = bundle_io.type_name(obj)
        if tname == "AssetBundle":
            continue
        rows.append({
            "type": tname,
            "name": bundle_io.asset_name(obj),
            # As a string: JavaScript rounds integers this large.
            "path_id": str(obj.path_id),
            "exportable": tname in bundle_io.EXPORTABLE,
            "importable": tname in bundle_io.IMPORTABLE,
            "edited": obj.path_id in edited,
        })
    return jsonify({"bundle": filename, "count": len(rows), "assets": rows})


# -- Preview and export ------------------------------------------------------

def _deliver(item: bundle_io.Exported, as_download: bool):
    response = send_file(
        BytesIO(item.data),
        mimetype=item.mimetype,
        as_attachment=as_download,
        download_name=item.filename,
    )
    response.headers["X-PM-Filename"] = item.filename
    response.headers["X-PM-Kind"] = item.kind
    if item.note:
        response.headers["X-PM-Note"] = item.note
    return response


@app.route("/api/preview")
def preview():
    """Render one asset inline. Same bytes as /api/export, different disposition."""
    filename = wants("bundle")
    path_id = wants_path_id()
    prefer = "raw" if request.args.get("raw") else "auto"

    session = state.session_for(filename)
    staged = (session or {}).get("previews", {}).get(path_id)
    if staged and prefer != "raw":
        mime, payload = staged
        response = send_file(BytesIO(payload), mimetype=mime)
        response.headers["X-PM-Kind"] = "image" if mime.startswith("image/") else "text"
        response.headers["X-PM-Staged"] = "1"
        return response

    _, obj = locate(filename, path_id)
    try:
        return _deliver(bundle_io.export_asset(obj, prefer=prefer), False)
    except Exception as exc:
        return jsonify({
            "error": explain(exc),
            "type": bundle_io.type_name(obj),
            "name": bundle_io.asset_name(obj),
            "path_id": path_id,
        }), 422


@app.route("/api/export")
def export_one():
    filename = wants("bundle")
    path_id = wants_path_id()
    prefer = "raw" if request.args.get("raw") else "auto"
    _, obj = locate(filename, path_id)
    try:
        return _deliver(bundle_io.export_asset(obj, prefer=prefer), True)
    except Exception as exc:
        return jsonify({"error": explain(exc)}), 422


@app.route("/api/export-bundle")
def export_bundle():
    """Every asset in one bundle, as a zip."""
    filename = wants("bundle")
    types = [t for t in (request.args.get("types") or "").split(",") if t]
    prefer = "raw" if request.args.get("raw") else "auto"
    env = state.env_for(filename)

    count = sum(1 for o in env.objects
                if bundle_io.type_name(o) != "AssetBundle"
                and (not types or bundle_io.type_name(o) in types))
    if count > state.max_zip_assets:
        return jsonify({
            "error": f"{count:,} assets is more than this will export in one go "
                     f"(limit {state.max_zip_assets:,}). Filter by type and try again.",
        }), 413

    data, report = bundle_io.export_bundle_zip(env, types or None, prefer)
    failed = sum(1 for row in report if not row["ok"])
    stem = bundle_io.safe_filename(os.path.splitext(filename)[0], "bundle")
    response = send_file(BytesIO(data), mimetype="application/zip",
                         as_attachment=True, download_name=f"{stem}_assets.zip")
    response.headers["X-PM-Exported"] = str(len(report) - failed)
    response.headers["X-PM-Failed"] = str(failed)
    return response


# -- Importing and building --------------------------------------------------

@app.route("/api/import", methods=["POST", "OPTIONS"])
def import_one():
    """
    Stage a replacement for one asset.

    Nothing is written to disk here; the change lives in memory until you
    build the bundle.
    """
    if request.method == "OPTIONS":
        return "", 204

    filename = (request.form.get("bundle") or "").strip()
    raw_id = (request.form.get("path_id") or "").strip()
    if not filename or not raw_id:
        return jsonify({"error": "Both 'bundle' and 'path_id' are required."}), 400
    try:
        path_id = int(raw_id)
    except ValueError:
        return jsonify({"error": f"'path_id' must be a whole number, got {raw_id!r}."}), 400

    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "No file was uploaded."}), 400
    payload = upload.read()
    source = os.path.basename(upload.filename or "upload")

    env, obj = locate(filename, path_id, for_edit=True)
    try:
        result = bundle_io.import_asset(obj, payload, source)
    except Exception as exc:
        return jsonify({"error": explain(exc)}), 422
    # So the next preview shows the replacement rather than a cached decode.
    bundle_io.invalidate_caches(env)

    session = state.session_for(filename)
    assert session is not None
    record = result.as_dict()
    record["at"] = datetime.now().strftime("%H:%M:%S")
    # record["path_id"] is a string now, so compare like for like or the same
    # asset would stack up a new entry every time it is replaced.
    session["changes"] = [c for c in session["changes"]
                          if str(c["path_id"]) != str(result.path_id)] + [record]
    session["touched"] = time.time()
    if result.preview:
        # Reading the object again would hand back the original, so keep what
        # the rebuilt bundle will actually contain.
        session.setdefault("previews", {})[result.path_id] = (
            result.preview_mime or "application/octet-stream", result.preview)

    return jsonify({
        "ok": True,
        "change": record,
        "changes": session["changes"],
        "will_write": [name for name, _ in bundle_io.changed_files(env)],
    })


@app.route("/api/edits", methods=["GET"])
def list_edits():
    out = {}
    for name, session in state.edits.items():
        if session["changes"]:
            out[name] = {
                "changes": session["changes"],
                "will_write": [n for n, _ in bundle_io.changed_files(session["env"])],
            }
    return jsonify({"edits": out, "count": sum(len(v["changes"]) for v in out.values())})


@app.route("/api/edits", methods=["DELETE", "OPTIONS"])
def drop_edits():
    if request.method == "OPTIONS":
        return "", 204
    filename = (request.args.get("bundle") or "").strip()
    with state.lock:
        if filename:
            path = state.index.resolve_in_dir(filename) if state.index else None
            key = os.path.basename(path) if path else filename
            state.edits.pop(key, None)
            state.cache.drop(path or "")
        else:
            state.edits.clear()
            state.cache.clear()
    return jsonify({"ok": True})


@app.route("/api/build", methods=["POST", "OPTIONS"])
def build():
    """Write the edited bundle(s) to the exports folder."""
    if request.method == "OPTIONS":
        return "", 204
    payload = request.get_json(silent=True) or {}
    filename = str(payload.get("bundle", "")).strip()
    pack = str(payload.get("pack", "lz4")).strip() or "lz4"
    if pack not in bundle_io.PACKERS:
        return jsonify({"error": f"'pack' must be one of {', '.join(bundle_io.PACKERS)}."}), 400

    session = state.session_for(filename)
    if session is None or not session["changes"]:
        return jsonify({"error": "Nothing has been replaced in that bundle yet."}), 400

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = bundle_io.safe_filename(os.path.splitext(filename)[0], "bundle")
    job = f"{stamp}_{stem}"
    target = state.out_dir / job
    try:
        written = bundle_io.repack(session["env"], str(target), pack)
    except Exception as exc:
        return jsonify({"error": explain(exc)}), 422

    files = [{
        "name": os.path.basename(path),
        "size": os.path.getsize(path),
        "size_human": bundle_io.format_size(os.path.getsize(path)),
        "url": f"/api/download?job={job}&name={os.path.basename(path)}",
    } for path in written]

    (target / "_changes.json").write_text(
        json.dumps({"bundle": filename, "pack": pack,
                    "changes": session["changes"]}, indent=2),
        encoding="utf-8")

    # Replacing a packed sprite rewrites the atlas, which is often a different
    # bundle. Say so plainly, or people will copy back the wrong file.
    names = {f["name"] for f in files}
    if os.path.basename(filename) not in names:
        note = (f"The change landed in {', '.join(sorted(names))} rather than "
                f"{filename}, because the asset you replaced lives in a shared "
                f"texture. That is the file to put back into the game.")
    elif len(files) > 1:
        note = "Several files changed. You need all of them."
    else:
        note = ""

    return jsonify({
        "ok": True,
        "job": job,
        "folder": str(target),
        "pack": pack,
        "files": files,
        "note": note,
    })


@app.route("/api/download")
def download():
    job = wants("job")
    name = wants("name")
    if not re.fullmatch(r"[A-Za-z0-9._\-]+", job):
        abort(400, "Bad job id.")
    folder = (state.out_dir / job).resolve()
    if not str(folder).startswith(str(state.out_dir.resolve())) or not folder.is_dir():
        abort(404, "No such build.")
    return send_from_directory(folder, os.path.basename(name), as_attachment=True)


@app.route("/api/builds")
def builds():
    """Everything built so far this session, newest first."""
    rows = []
    if state.out_dir.is_dir():
        for folder in sorted(state.out_dir.iterdir(), reverse=True):
            if not folder.is_dir():
                continue
            files = [f for f in sorted(folder.iterdir())
                     if f.is_file() and f.name != "_changes.json"]
            if files:
                rows.append({
                    "job": folder.name,
                    "folder": str(folder),
                    "files": [{"name": f.name, "size_human":
                               bundle_io.format_size(f.stat().st_size),
                               "url": f"/api/download?job={folder.name}&name={f.name}"}
                              for f in files],
                })
    return jsonify({"out_dir": str(state.out_dir), "builds": rows[:50]})


# -- Start up ----------------------------------------------------------------

def load_catalog(explicit: str = "") -> None:
    candidates = [Path(explicit)] if explicit else [HERE / "catalog.json",
                                                    Path.cwd() / "catalog.json"]
    for path in candidates:
        if path.is_file():
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    state.catalog = json.load(handle)
                state.catalog_path = str(path.resolve())
                print(f"  Catalog    : {path} ({len(state.catalog)} bundles)")
                return
            except Exception as exc:
                print(f"  Catalog    : could not read {path} ({exc})")
    print("  Catalog    : none found. Build one with catalog_builder.py, or "
          "load one in the viewer.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Browse, export from and edit Pocket Mortys asset bundles.")
    parser.add_argument("directory", nargs="?", default="AssetBundles",
                        help="folder holding the asset bundles (default ./AssetBundles)")
    parser.add_argument("-p", "--port", type=int, default=7777)
    parser.add_argument("-c", "--catalog", default="", help="path to catalog.json")
    parser.add_argument("-o", "--out", default="", help="where built bundles go")
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open a browser window")
    parser.add_argument("--no-token", action="store_true",
                        help="skip the access token (only if nothing else runs on this machine)")
    parser.add_argument("--cors", action="store_true",
                        help="allow a viewer opened from disk to reach this server")
    args = parser.parse_args()

    print("=" * 62)
    print("  Pocket Mortys Bundle Catalog Server")
    print("=" * 62)

    directory = Path(args.directory).expanduser()
    if directory.is_dir():
        state.set_directory(str(directory))
        print(f"  Bundles    : {state.directory} ({len(state.index or [])} files)")
    else:
        print(f"  Bundles    : {directory} does not exist - set it in the viewer")

    state.out_dir = Path(args.out).expanduser().resolve() if args.out \
        else HERE / "exports"
    state.out_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Exports to : {state.out_dir}")

    load_catalog(args.catalog)

    state.require_token = not args.no_token
    state.allow_cors = args.cors
    state.token = secrets.token_urlsafe(18)

    url = f"http://127.0.0.1:{args.port}/"
    print()
    print(f"  Open: {url}")
    if not state.require_token:
        print("  Access token disabled (--no-token).")
    elif args.cors:
        print(f"  Token for a viewer opened from disk: {state.token}")
    print("  Press Ctrl+C to stop.")
    print("=" * 62)

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)
    except OSError as exc:
        print(f"\nCould not start on port {args.port}: {exc}")
        print("Something else may be using it. Try --port 7778.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
