# v1.2 — Works on a phone, notes per asset, and a real zip online

Look inside every asset bundle in *Pocket Mortys*, export anything you find,
and put your own version back in.

**Browse all 4,024 bundles and 130,958 assets in your browser, nothing to
install:** https://pocket-mortys-resuricktion.github.io/AssetBundleViewer/

That page shows every sprite, texture, sound and data file, and lets you
download any of them. It cannot change anything — that is what the downloads
below are for.

---

## What's new since v1.1

All of it applies to **both** versions unless it says otherwise.

### It fits on the screen now

The page had no mobile viewport tag and not one media query, so a phone laid
it out at 980px and scaled the result down: the edges were cut off and the
pager sat below the bottom of a page that could not scroll. On a narrow
window the desktop did a smaller version of the same thing.

- The layout is **responsive**. Below 900px the filter sidebar becomes a
  drawer behind a **Filters** button and the preview opens as a full panel,
  so the list always gets the whole width.
- The toolbar, the top bar and the pager **wrap** instead of clipping
  whatever did not fit.
- Page height uses the part of the screen you can actually see, so the pager
  is always reachable.
- On phones the table keeps the two columns that identify a row and drops the
  rest, rather than squeezing four into 360px.

### Buttons look like buttons

The two tabs were borderless text, which is why people could not find **All
Assets**. They are proper buttons now, and the one you are on is filled in.
Every other flat outline — the toolbar, the pager, the asset actions, the
sidebar — is filled and shifts when pressed.

### A real zip online

Exporting a selection online used to save files one at a time, which browsers
throttle and warn about, and it refused outright above 300 of them. The page
now **writes a proper zip itself** — no library, nothing to install:

- **No file limit.** The ceiling is memory, and you get a warning before a
  very large selection rather than a refusal.
- Picking a **whole bundle** online now exports every published asset in it,
  instead of telling you it cannot.
- Progress is shown while it fetches, and anything that fails is reported
  rather than silently missing.

### Notes on assets, not just bundles

A note used to belong to a whole bundle, which is the wrong size when the
thing worth remembering is one sprite out of two hundred.

- **Every asset can be bookmarked and noted on its own**, alongside the
  bundle's own bookmark and note. Both are in the preview, clearly separated.
- **The two filters follow the tab.** On **Bundles** they narrow to bundles
  you marked; on **All Assets** to assets you marked; inside a bundle, to
  marked assets in that bundle. The sidebar says which it is counting.
- A starred row shows ★ and a noted row shows 📝, in both views.
- **★ Bundle notes** in the breadcrumb opens a bundle's own note without
  having to pick an asset first.
- Anything you saved before still loads, and Save/Restore carries both kinds.

### The preview scrolls properly on a phone

The picture was pinned while only the details beneath it scrolled, so on a
phone — where the preview fills the screen — the notes and dependencies were
left moving in a sliver. The panel is now one scroller on narrow screens,
with only the asset's name staying put. On a desktop the picture still stays
in view beside the details, which is the useful behaviour there.

### Fixes

- The page **opened in grid while the List button was highlighted**, because
  the saved view was read without telling the toolbar. Both come from one
  place now.
- Online now **starts on All Assets in grid**, which is what the published
  copy is for.
- Online no longer says **"Server offline"**. It checked for a server before
  working out that it was the online copy, then kept asking every eight
  seconds for one that was never coming.
- **All Assets in list mode** opened on a few hundred GameObjects called "1"
  to "6" and three called "?", because only the grid put assets with a
  picture first. Both views use the same order now.
- **Deselecting the last item leaves select mode**, and so does **Clear**.

---

---

## Which download do I want?

**`PocketMortysBundleCatalog-v1.2-complete.zip`** — everything, bundles
included. Unzip it and double-click `run.bat` (Windows) or run `./run.sh`
(macOS, Linux). Nothing else to find or set up.

**`PocketMortysBundleCatalog-v1.2-tools.zip`** — the programs only, for
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
