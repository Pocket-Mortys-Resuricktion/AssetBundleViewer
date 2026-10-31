# Pocket Mortys Bundle Catalog

Look inside every asset bundle in *Pocket Mortys*, export anything you find,
and put your own version back in.

The game ships about 4,000 bundles with no readable structure. These tools
scan all of them, give you a searchable browser with real previews, and let
you swap an asset for one of your own and build a bundle you can drop back
onto your device.

A fan preservation project for a game that is no longer available.

|  | |
|---|---|
| **Just want to look around?** | [Open it in your browser](#the-online-version). Nothing to install, works on a phone. |
| **Want to mod?** | [Get the desktop version](#the-desktop-version). One command. |

---

## The online version

```
https://pocket-mortys-resuricktion.github.io/AssetBundleViewer/
```

Every sprite, texture, sound and data file is decoded ahead of time and
published, so the site can show you the actual asset and hand you the file:

- A **thumbnail grid** of all 29,000 images — scroll and search to find what
  you want
- Click anything for the full-size image, playable audio or readable data
- **Download** it as a PNG, WAV, TXT or JSON

It cannot *change* anything, because writing a bundle needs code a browser
cannot run. That is what the desktop version is for.

---

## The desktop version

### You need

- Python 3.9 or newer
- The bundles (the `AssetBundles/` folder here, or your own copy)

### Run it

Double-click **`run.bat`** on Windows, or **`./run.sh`** on macOS and Linux.
It installs what it needs, builds the catalog if there isn't one, and opens
your browser.

By hand:

```bash
python -m pip install -r requirements.txt
```

```bash
python catalog_server.py AssetBundles
```

That opens <http://127.0.0.1:7777>. Use that address rather than opening
`catalog_viewer.html` from disk — served by the server, everything works with
no extra setup.

### What it adds over the online version

- Previews read live from the bundle, including types that are not published
- **Replace** a `Texture2D`, `Sprite`, `TextAsset` or `MonoBehaviour` with a
  file of your own
- Stage as many replacements as you like, then **build** the bundle and
  download it
- Export a whole bundle's assets as a zip
- Point it at your own bundles, which are marked **yours** so you can tell
  them from the ones that shipped here

---

## Internal and external bundles

This matters, because it decides where a modified file has to go back to.
The viewer labels every bundle, and so does the catalog.

| | how many | what it is | its real name | where it lives |
|---|---|---|---|---|
| **in the APK** | 3,867 | a bare serialized file | `7a475521…` — a hash, **no extension** | `assets/bin/Data/` |
| **downloaded** | 157 | a UnityFS bundle | `__data` | `UnityCache/Shared/<name>/<version>/__data` |

The files here are renamed to something readable — `ASGLogo-7a475521….assetbundle`
rather than `7a475521…` — so you can find things. The game does not know those
names, so anything written back out is given its original name again.

Downloaded bundles live at
`Android/data/com.turner.pocketmorties/files/UnityCache/Shared/<name>/<version id>/__data`,
and the `<version id>` folder is the one already on your device.

---

## Reading the game's data

Most of what a modder actually wants — Morty stats, spawn points, shop
contents, tuning values — lives in `MonoBehaviour` assets. Pocket Mortys is an
IL2CPP build, so Unity stripped the information that says what fields those
have, and every other asset viewer shows them as an unreadable blob.

This one ships that information, recovered once from the game's own metadata
and committed as `typetrees/pocketmortys.json`. So a spawn point reads as:

```json
{
  "m_Name": "Dimension6SpawnPoints",
  "PortalSpawn": { "x": 49, "y": 13 },
  "ArenaSpawnPoint": { "x": 36, "y": 20 },
  "RevivePoints": [ { "x": 89, "y": 49 }, { "x": 91, "y": 49 } ]
}
```

Export that JSON, change it, import it back, and build the bundle.

Rebuilding the dump for another game version needs Il2CppDumper output and a
.NET runtime:

```bash
python tools/make_typetrees.py path/to/DummyDll --unity 2022.3.62f1 --game-version 2.40.2
```

---

## Replacing an asset, start to finish

1. Start the server and open the address it prints.
2. Find the bundle, click it, click the asset.
3. **Export** it and edit that file. Keep the same size — a different size is
   scaled to fit, which rarely looks right.
4. **Replace this asset…** and pick your edited file.
5. The preview updates to what the rebuilt bundle will contain, and the
   pending panel appears at the bottom right.
6. Choose a compression mode and press **Build**. `LZ4` is what the game uses.
7. Take the file from `exports/` and put it where the original came from.

### Sprites live in a shared atlas

Most sprites are a rectangle inside an atlas texture shared with dozens of
others, and that atlas is often **in a different bundle**. Replacing a sprite
paints your image into the atlas, so the file you get back is the atlas
bundle, not the one you opened. The app tells you which file it wrote; that is
the one to replace, and the other sprites in it are untouched.

### What you lose

Mobile textures are stored compressed (ASTC, ETC2). Writing one back
re-encodes it, which is lossy, so colours shift very slightly even if you
import exactly what you exported. The preview shows you the re-encoded
result, so what you see is what you get.

---

## What is here

| | |
|---|---|
| `bundle_io.py` | All the bundle handling: loading, dependency resolution, export, import, repacking. |
| `catalog_builder.py` | Scans the bundles and writes `catalog.json`. |
| `catalog_server.py` | Serves the viewer and does the reading, exporting and rebuilding. |
| `catalog_viewer.html` | The interface, online and off. One file, no build step. |
| `catalog.json` | What is in all 4,024 bundles: 130,958 assets. |
| `typetrees/` | The MonoBehaviour field definitions described above. |
| `tools/prerender.py` | Renders every asset for the online version. |
| `tools/make_typetrees.py` | Rebuilds the type definitions from a DummyDll. |
| `tests/` | `python -m pytest -q` |

### catalog_builder.py

```bash
python catalog_builder.py AssetBundles -o catalog.json -j 8
```

`-j` workers (default: one per core, max 8) · `--fresh` rescan everything ·
`--pretty` readable JSON. About 15 seconds for 4,000 bundles on 8 cores, and
it resumes if interrupted.

### catalog_server.py

```bash
python catalog_server.py AssetBundles --port 7777
```

`-c` catalog path · `-o` where built bundles go (default `exports/`) ·
`--no-browser` · `--cors` to reach it from a viewer opened off disk ·
`--no-token` to drop the access token.

### tools/prerender.py

```bash
python tools/prerender.py AssetBundles -o site -j 8
```

`--only Texture2D,Sprite` to publish fewer types · `--resume` to keep what is
already rendered · `--limit N` for a trial run. The full run takes a few
minutes and produces about 75,000 files.

---

## HTTP API

Everything binds to `127.0.0.1`. Apart from `/` and `/api/status`, requests
carry an access token as `X-PM-Token` or `?token=`; the viewer the server
hands you already has it.

| | |
|---|---|
| `GET /api/status` | Directory, counts, pending edits, what can be imported. |
| `GET /api/catalog` | The catalog the server loaded. |
| `POST /api/set-dir` | `{"path": "..."}` — point it somewhere else. |
| `GET /api/assets?bundle=` | What is in that bundle right now. |
| `GET /api/preview?bundle=&path_id=` | One asset, inline. `raw=1` for undecoded bytes. |
| `GET /api/export?bundle=&path_id=` | The same, as a download. |
| `GET /api/export-bundle?bundle=&types=` | A zip of everything in it. |
| `POST /api/import` | Multipart `bundle`, `path_id`, `file`. Stages a replacement. |
| `GET /api/edits` · `DELETE /api/edits` | List or discard staged changes. |
| `POST /api/build` | `{"bundle": "...", "pack": "lz4"}`. Writes the bundle. |
| `GET /api/download?job=&name=` | Fetch a built file. |

Note that `path_id` is a **string** everywhere, not a number. They are signed
64-bit integers, and one asset in five has a value JavaScript cannot hold
exactly; left as numbers they quietly round and stop identifying an asset.

---

## Known limits

- **Audio cannot be replaced.** Clips are FMOD sample banks; they decode out
  but cannot be built back. Exporting works.
- **Meshes and animations** export but cannot be imported.
- **Textures must keep their size**, because the sprite rectangles and UVs
  around them assume the original.
- **Tightly packed sprites** export masked to their outline; importing writes
  the whole rectangle, which can bleed into a neighbour in the atlas.
- **Two audio clips** rely on a `.resource` file; both are included here, but
  a collection missing one will say so rather than failing silently.
- Bundles must be readable by UnityPy. Anything encrypted or unusual is
  recorded as invalid with the reason.

## Related tools

- [UABEA](https://github.com/nesrak1/UABEA) — the reference desktop tool for
  Unity bundle modding. Broader than this, and not built around one game.
- [AssetStudio](https://github.com/Perfare/AssetStudio) /
  [AssetRipper](https://github.com/AssetRipper/AssetRipper) — excellent for
  extraction and for recovering a whole project. Read-only.
- [UnityPy](https://github.com/K0lb3/UnityPy) — the library this is built on.
- [unity-bundle-tool](https://pypi.org/project/unity-bundle-tool/) — a UnityPy
  extract/repack command line tool, same idea without the UI or the catalog.

This one is for finding something in *Pocket Mortys* specifically, looking at
it, and changing it, without installing a toolchain.

## Safety

- The server binds to `127.0.0.1` and never reaches the network.
- Bundle names in requests are resolved against an index of the bundle
  folder, so a request cannot read a file outside it.
- Original bundles are never written to. Rebuilt files go to `exports/`.

## Disclaimer

An unofficial, non-commercial fan project, not affiliated with or endorsed by
the rights holders of *Pocket Mortys* or *Rick and Morty*. All game assets,
names and characters belong to their owners. The tools contain no game
assets; you supply your own copy of the data.

## License

[GNU General Public License v3.0](LICENSE), covering the tools only, not any
game content.
