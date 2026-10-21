#!/usr/bin/env python3
"""
Pocket Mortys Bundle Catalog Preview Server
============================================
Serves asset previews from actual bundle files to the catalog viewer.

Requirements:
    pip install flask

Usage:
    python catalog_server.py "C:/path/to/Internal Asset Bundles"

Then open catalog_viewer.html in your browser.
The viewer will auto-detect the server and enable live previews.
"""

import sys
import os
import io
import json
import struct
from pathlib import Path

try:
    from flask import Flask, jsonify, request, Response, send_file
except ImportError:
    print("ERROR: Flask not installed.")
    print("Install it with:  python -m pip install flask")
    sys.exit(1)

try:
    import UnityPy
except ImportError:
    print("ERROR: UnityPy not installed.")
    print("Install it with:  python -m pip install unitypy")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────
PORT       = 7777
BUNDLE_DIR = ""
CATALOG    = []

app = Flask(__name__)

# ── CORS (so the HTML file opened locally can talk to us) ─────
@app.after_request
def cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

# ── Routes ────────────────────────────────────────────────────
@app.route("/api/status")
def status():
    return jsonify({
        "ok":         True,
        "bundle_dir": BUNDLE_DIR,
        "catalog_len": len(CATALOG)
    })

@app.route("/api/catalog")
def get_catalog():
    return Response(
        json.dumps(CATALOG, separators=(",", ":")),
        mimetype="application/json"
    )

@app.route("/api/set-dir", methods=["POST", "OPTIONS"])
def set_dir():
    global BUNDLE_DIR
    if request.method == "OPTIONS":
        return "", 204
    data = request.get_json(silent=True) or {}
    path = data.get("path", "").strip()
    if path and os.path.isdir(path):
        BUNDLE_DIR = path
        return jsonify({"ok": True, "bundle_dir": BUNDLE_DIR})
    return jsonify({"ok": False, "error": "Directory not found"}), 400

@app.route("/api/preview")
def preview():
    """
    Extract and return an asset preview from a bundle file.
    Query params:
        bundle    — bundle filename (hash)
        type      — Unity asset type (Texture2D, AudioClip, TextAsset, …)
        name      — asset name
        path_id   — (optional) path_id fallback
    """
    bundle_fn  = request.args.get("bundle", "")
    asset_type = request.args.get("type", "")
    asset_name = request.args.get("name", "")
    path_id    = request.args.get("path_id", "")

    if not BUNDLE_DIR or not bundle_fn:
        return jsonify({"error": "No bundle directory set"}), 400

    filepath = os.path.join(BUNDLE_DIR, bundle_fn)
    if not os.path.exists(filepath):
        return jsonify({"error": "Bundle file not found"}), 404

    try:
        env = UnityPy.load(filepath)
    except Exception as e:
        return jsonify({"error": f"Could not load bundle: {e}"}), 500

    for obj in env.objects:
        try:
            obj_type = obj.type.name if hasattr(obj.type, "name") else str(obj.type)
        except Exception:
            continue

        if obj_type != asset_type:
            continue

        # Match by name or path_id
        try:
            data = obj.read()
        except Exception:
            continue

        obj_name = ""
        for attr in ("name", "m_Name"):
            v = getattr(data, attr, None)
            if v and isinstance(v, str):
                obj_name = v
                break

        if obj_name != asset_name and str(obj.path_id) != path_id:
            continue

        # ── Texture2D / Sprite → PNG ──────────────────────────
        if asset_type in ("Texture2D", "Sprite"):
            try:
                img = data.image
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                buf.seek(0)
                return send_file(buf, mimetype="image/png")
            except Exception as e:
                return jsonify({"error": f"Image decode failed: {e}"}), 500

        # ── AudioClip → audio bytes ───────────────────────────
        elif asset_type == "AudioClip":
            try:
                samples = data.samples  # dict of {name: bytes}
                if not samples:
                    return jsonify({"error": "No audio samples extracted"}), 404
                audio_bytes = list(samples.values())[0]
                # Detect format from magic bytes
                if audio_bytes[:4] == b"OggS":
                    mime = "audio/ogg"
                elif audio_bytes[:4] == b"RIFF":
                    mime = "audio/wav"
                elif audio_bytes[:3] == b"ID3" or audio_bytes[:2] == b"\xff\xfb":
                    mime = "audio/mpeg"
                else:
                    mime = "audio/ogg"  # Pocket Mortys uses Ogg Vorbis on Android
                return Response(audio_bytes, mimetype=mime)
            except Exception as e:
                return jsonify({"error": f"Audio extraction failed: {e}"}), 500

        # ── TextAsset → plain text ────────────────────────────
        elif asset_type == "TextAsset":
            try:
                script = getattr(data, "script", None) or getattr(data, "m_Script", b"")
                if isinstance(script, (bytes, bytearray)):
                    try:
                        text = script.decode("utf-8")
                    except UnicodeDecodeError:
                        text = script.decode("latin-1")
                else:
                    text = str(script)
                return Response(text, mimetype="text/plain; charset=utf-8")
            except Exception as e:
                return jsonify({"error": f"Text extraction failed: {e}"}), 500

        # ── MonoBehaviour → JSON dump ─────────────────────────
        elif asset_type == "MonoBehaviour":
            try:
                info = {
                    "name":    obj_name,
                    "path_id": obj.path_id,
                    "note":    "MonoBehaviour — raw field dump",
                }
                try:
                    fields = {k: str(v) for k, v in vars(data).items()
                              if not k.startswith("_") and k != "assets_file"}
                    info["fields"] = fields
                except Exception:
                    pass
                return Response(
                    json.dumps(info, indent=2, default=str),
                    mimetype="application/json"
                )
            except Exception as e:
                return jsonify({"error": f"MonoBehaviour dump failed: {e}"}), 500

        # ── Anything else → metadata only ────────────────────
        else:
            return jsonify({
                "type":    asset_type,
                "name":    obj_name,
                "path_id": obj.path_id,
                "note":    f"{asset_type} preview not yet supported"
            })

    return jsonify({"error": "Asset not found in bundle"}), 404


# ── Main ──────────────────────────────────────────────────────
def load_catalog_from_disk():
    global CATALOG
    # Look for catalog.json next to this script
    script_dir = Path(__file__).parent
    candidates = [
        script_dir / "catalog.json",
        Path.cwd() / "catalog.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    CATALOG = json.load(f)
                print(f"  Catalog loaded: {path} ({len(CATALOG)} bundles)")
                return
            except Exception as e:
                print(f"  Warning: Could not load catalog: {e}")
    print("  No catalog.json found — viewer will load it separately.")


if __name__ == "__main__":
    print("=" * 55)
    print("  Pocket Mortys Bundle Preview Server")
    print("=" * 55)

    if len(sys.argv) > 1:
        BUNDLE_DIR = sys.argv[1].strip().strip('"')
        if not os.path.isdir(BUNDLE_DIR):
            print(f"ERROR: Directory not found: {BUNDLE_DIR}")
            sys.exit(1)
        print(f"  Bundle dir : {BUNDLE_DIR}")
    else:
        print("  Bundle dir : (not set — use the viewer to set it)")
        print("  Tip: python catalog_server.py \"C:/path/to/Internal Asset Bundles\"")

    load_catalog_from_disk()

    print(f"\n  Server running at:  http://localhost:{PORT}")
    print("  Open catalog_viewer.html in your browser.")
    print("  Press Ctrl+C to stop.\n")
    print("=" * 55)

    app.run(host="127.0.0.1", port=PORT, debug=False)
