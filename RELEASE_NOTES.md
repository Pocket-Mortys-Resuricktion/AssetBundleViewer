# v1.1 — Dependencies, bulk export, and the small things that were in the way

Look inside every asset bundle in *Pocket Mortys*, export anything you find,
and put your own version back in.

**Browse all 4,024 bundles and 130,958 assets in your browser, nothing to
install:** https://pocket-mortys-resuricktion.github.io/AssetBundleViewer/

That page shows every sprite, texture, sound and data file, and lets you
download any of them. It cannot change anything — that is what the downloads
below are for.

---

## What's new since v1.0

Everything here landed in **both** versions unless it says otherwise.

### Finding things

- **Dependencies.** Every bundle now lists the bundles it pulls from and the
  bundles that pull from it. This is the fastest way to find where a sprite
  really lives: 646 bundles reach into `globalgamemanagers.assets` alone.
- **Keyboard navigation.** Arrows move, Enter opens, Escape backs out,
  Page Up/Down and Home/End jump, `/` goes to the search box, `g` switches
  grid and list, `s` turns on selecting and Space ticks what you are on.
- **Jump to a page** by typing its number, instead of clicking next 40 times.
- **Grid or list anywhere.** The toggle used to appear only inside a bundle;
  it now works on the bundle list too, with thumbnails.
- **Your search survives.** Opening a bundle no longer wipes what you typed.
- **Save and restore your bookmarks** as a file, so they outlive the tab.

### Exporting

- **Select many, export once.** Tick the box in the corner of anything —
  assets or whole bundles — and export the lot as a single zip, one folder per
  bundle. On a touch screen, press and hold to start selecting.
- **"Export all" now means what is on screen.** Search `morty` and it exports
  the Mortys, not all 130,958 assets. Previously the filter was ignored.

### Desktop only

- **Open ▾** replaces the lone "Load catalog.json" button. It can point the
  server at a different bundle folder, copy your own files in beside the stock
  ones — bundles, or the side-car files that belong with them, such as `.resS`
  and `.resource` — load a catalog built elsewhere, or restore your bookmarks.
- **JSON previews work again** for `Material`, `Mesh`, `GameObject`,
  `SpriteAtlas` and `AnimationClip`. The viewer was hiding previews the server
  was perfectly willing to produce. Long ones now show 60,000 characters
  instead of 12,000.

### Online only

- **The explainer banner can be dismissed**, and stays dismissed.
- **No "Load catalog.json" button**, which online was asking you to load
  something already loaded.

---

## Which download do I want?

**`PocketMortysBundleCatalog-v1.1-complete.zip`** — everything, bundles
included. Unzip it and double-click `run.bat` (Windows) or run `./run.sh`
(macOS, Linux). Nothing else to find or set up.

**`PocketMortysBundleCatalog-v1.1-tools.zip`** — the programs only, for
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
