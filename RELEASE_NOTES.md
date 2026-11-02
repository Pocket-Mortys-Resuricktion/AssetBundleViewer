# v1.0 — Browse it online, mod it offline

Look inside every asset bundle in *Pocket Mortys*, export anything you find,
and put your own version back in.

**Browse all 4,024 bundles and 130,958 assets in your browser, nothing to
install:** https://pocket-mortys-resuricktion.github.io/AssetBundleViewer/

That page shows every sprite, texture, sound and data file, and lets you
download any of them. It cannot change anything — that is what the downloads
below are for.

---

## Which download do I want?

**`PocketMortysBundleCatalog-v1.0.0-complete.zip`** — everything, bundles
included. Unzip it and double-click `run.bat` (Windows) or run `./run.sh`
(macOS, Linux). Nothing else to find or set up.

**`PocketMortysBundleCatalog-v1.0.0-tools.zip`** — the programs only, for
anyone who already has their own bundles or who cloned the repository.
Point it at your folder:

```
python catalog_server.py "C:/path/to/your/bundles"
```

Both need **Python 3.9 or newer**. The launcher installs the rest.

---

## What the desktop version does

- Preview anything in the bundle, read straight from the file
- **Export** an asset as PNG, WAV, TXT, OBJ or JSON — or a whole bundle as a zip
- **Replace** a `Texture2D`, `Sprite`, `TextAsset` or `MonoBehaviour` with a
  file of your own, then build a bundle you can put back on your device
- See the asset as it will actually look once rebuilt, re-encoded into the
  game's own texture format
- Drop your own bundles in beside the stock ones; they are marked **yours**

## Reading the game's data

Most of what a modder wants — stats, spawn points, shop contents, tuning
values — lives in `MonoBehaviour` assets. Pocket Mortys is an IL2CPP build,
so Unity stripped the information that says what fields those have, and every
other asset viewer shows them as an unreadable blob.

This one ships that information for 2,791 classes, so a spawn point reads as:

```json
{
  "m_Name": "Dimension6SpawnPoints",
  "PortalSpawn": { "x": 49, "y": 13 },
  "ArenaSpawnPoint": { "x": 36, "y": 20 },
  "RevivePoints": [ { "x": 89, "y": 49 }, { "x": 91, "y": 49 } ]
}
```

Export that JSON, change it, import it back, build the bundle.

## Where a modified bundle goes

The tool renames bundles so you can find things; the game does not know those
names, so anything written out is given its original name back. It also tells
you which file it wrote and where that file belongs.

| | what it is | where it goes |
|---|---|---|
| **in the APK** | 3,867 bundles | `assets/bin/Data/` |
| **downloaded** | 157 bundles | `Android/data/com.turner.pocketmorties/files/UnityCache/Shared/<name>/<version>/__data` |

Replacing a sprite usually rewrites the **atlas** it is packed into, which is
often a different bundle. The app names the file it actually wrote — that is
the one to replace.

## Known limits

- Audio cannot be replaced (FMOD sample banks). Exporting works.
- Meshes and animations export but cannot be imported.
- Textures must keep their original size.
- Compressed textures (ASTC, ETC2) are re-encoded on import, which is lossy.
- 11 of 130,958 assets cannot be decoded at all; they are listed when it happens.

---

*Unofficial, non-commercial fan project. Not affiliated with or endorsed by
the rights holders of Pocket Mortys or Rick and Morty. GPL-3.0; the licence
covers the tools, not any game content.*
