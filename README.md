I'm lazy so I had Claude explain, I hope you don't mind 🥹
# Pocket Mortys Bundle Catalog

A set of tools for indexing, searching, and previewing the Unity asset bundles from *Pocket Mortys* (Android/iOS).

The bundles ship as hashed filenames with no readable structure. These tools scan every bundle, record what is inside, and give you a searchable browser UI with live previews of textures, audio, and text data.

Built as part of a fan preservation effort for the game.

## Components

| File | Purpose |
|---|---|
| `catalog_builder.py` | Scans a directory of bundles with [UnityPy](https://github.com/K0lb3/UnityPy) and writes `catalog.json`. |
| `catalog_viewer.html` | Standalone browser UI for searching, filtering, bookmarking, and annotating the catalog. No build step. |
| `catalog_server.py` | Optional local Flask server that extracts assets from the bundles on demand so the viewer can preview them. |
| `catalog.json` | Catalog output. |

## Features

**Builder**
- Lists every asset in every bundle (name, Unity type, path ID)
- Records per-bundle type counts, internal paths, and container paths
- Auto-categorizes each bundle by its dominant asset type (Texture, Sprite / UI, Audio, 3D Mesh, Shader, Animation, Text / Data, Script Data, and so on)
- Attempts to recover original bundle names from bundle headers
- Resumable: progress is saved every 50 files, so an interrupted scan picks up where it left off
- Skips files that are not valid bundles and records the error instead of crashing

**Viewer**
- Two views: **Bundles** (one row per bundle) and **All Assets** (flat list across every bundle)
- Search by bundle name, hash, asset name, or path
- Filter by category, asset type, identified-only, bookmarked, or has-notes
- Sort by name, size, asset count, category, or hash
- Bookmarks and per-bundle notes (stored in your browser's `localStorage`)
- Export the current filtered view as JSON
- Live previews when the server is running:

| Asset type | Preview |
|---|---|
| `Texture2D`, `Sprite` | Rendered as PNG |
| `AudioClip` | Playable audio (Ogg / WAV / MP3 detected automatically) |
| `TextAsset` | Plain text |
| `MonoBehaviour` | JSON dump of its fields |
| Anything else | Metadata only |

## Requirements

- Python 3.8+
- [UnityPy](https://pypi.org/project/UnityPy/) (builder and server)
- [Flask](https://pypi.org/project/Flask/) (server only)
- A modern browser

```bash
python -m pip install unitypy flask
```

## Setup

### 1. Get the bundles

You need your own copy of the game's asset bundles, the internal asset bundles are inside a folder named something like `assets` extracted from the APK or downloaded game data. To get the external asset bundles, you would've needed to have installed Pocket Mortys and played before the game shutdown. If you did go to Android/Data/com.turner.pocketmorties/files/UnityCache/Shared and all those folders are the external asset bundles. You'd want to import the files named `__Data`. **This repository does not distribute game assets.**...

Or you can go to the AssetBundles folder on this repo and download them all there. 

### 2. Build the catalog (optional if using the prebuilt one)

```bash
python catalog_builder.py "C:/path/to/Internal Asset Bundles"
```

If you omit the path, the script prompts for one.

Output:
- `catalog.json` is written to the current working directory.
- `catalog_progress.json` is written inside the bundle directory. It is the resume file; delete it to force a full rescan.

A summary (valid bundles, category breakdown, total asset count) prints when the scan finishes.

### 3. Open the viewer

Open `catalog_viewer.html` in your browser and click **Load catalog.json**.

This is enough for searching and filtering. Previews need the server.

### 4. Start the preview server (optional)

```bash
python catalog_server.py "C:/path/to/Internal Asset Bundles"
```

The server listens on `http://localhost:7777`. The viewer detects it automatically and the status pill in the top bar switches from **Server offline** to connected.

If you start the server without a path, you can set the bundle directory afterward through the viewer.

Keep `catalog.json` next to `catalog_server.py` or run the server from the folder containing it; the server also loads it on startup.

## API

The server exposes a small HTTP API on `127.0.0.1:7777`.

| Endpoint | Description |
|---|---|
| `GET /api/status` | Server status, current bundle directory, loaded catalog size |
| `GET /api/catalog` | The catalog the server loaded from disk |
| `POST /api/set-dir` | Body `{"path": "..."}` sets the bundle directory |
| `GET /api/preview` | Extracts an asset. Params: `bundle`, `type`, `name`, optional `path_id` |

## Catalog format

`catalog.json` is a JSON array with one object per file:

```json
{
  "filename": "0000000000000000f000000000000000",
  "size": 336572,
  "size_human": "328.7 KB",
  "valid": true,
  "original_name": "",
  "category": "Shader",
  "dominant_type": "Shader",
  "unity_version": "",
  "internal_paths": [],
  "container_paths": [],
  "asset_types": ["Material", "Shader", "Sprite", "Texture2D"],
  "type_counts": {"Material": 4, "Texture2D": 7, "Shader": 8, "Sprite": 6},
  "assets": [{"type": "Material", "name": "Default-Diffuse", "path_id": 1}],
  "asset_count": 25,
  "error": ""
}
```

## Limitations

- Many bundles have no recoverable original name. They are identified only by hash and by their contents.
- Many assets, shaders in particular, have empty names.
- The preview endpoint matches by asset type plus name (or path ID). If several assets in one bundle share a name, you may get the first match.
- Each preview request loads the whole bundle with UnityPy, so large bundles preview slowly.
- Only the preview types listed above are supported. Meshes, animations, and materials show metadata only.
- Bundles must be readable by UnityPy. Newer or unusual Unity versions or encrypted bundles may fail and be recorded as invalid.

## Security notes

- The server binds to `127.0.0.1` only, but it sends `Access-Control-Allow-Origin: *` so a locally opened HTML file can reach it. Do not expose it beyond localhost.
- `/api/set-dir` lets any page that can reach the server change the bundle directory. Run it only while you are using the viewer.

## Disclaimer

This is an unofficial, non-commercial fan project and is not affiliated with or endorsed by the rights holders of *Pocket Mortys* or *Rick and Morty*. All game assets, names, and characters belong to their respective owners. These tools contain no game assets; you must supply your own legitimately obtained copy of the data.

## License

Licensed under the [GNU General Public License v3.0](LICENSE). This covers the tools only, not any game content.
