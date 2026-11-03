"""
Round-trip tests for bundle_io.

They run against a handful of real bundles from AssetBundles/. If that folder
is not checked out the tests skip rather than fail, so a clone without the game
data still passes.

    python -m pytest -q
"""

from __future__ import annotations

import io
import shutil
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bundle_io  # noqa: E402

BUNDLES = Path(__file__).resolve().parents[1] / "AssetBundles"

TEXTURE = "support_icon-7bb3a2229d74e4049a87a7d52f859adb.assetbundle"
TEXT = "AdsConfigInfo-f7b81504443f44f5aaf4cecb2277b36d.assetbundle"
PACKED_SPRITE = "ShadowCharacter-07e11ae5121e3438692f32845f15bb50.assetbundle"
SPRITE_ATLAS = ("sactx-0-256x512-ETC2-WorldDefault-b4aaf91f-"
                "a56446fe408b44ef3a6a93f8b8e062a3.assetbundle")
UNITYFS = "v2.36.0.assetbundle"

ALL_FIXTURES = [TEXTURE, TEXT, PACKED_SPRITE, SPRITE_ATLAS, UNITYFS]


def need(*names: str) -> None:
    missing = [n for n in names if not (BUNDLES / n).is_file()]
    if missing:
        pytest.skip(f"fixture bundles not checked out: {missing[0]}")


@pytest.fixture(scope="module")
def index() -> bundle_io.FileIndex:
    need(*ALL_FIXTURES)
    return bundle_io.FileIndex(str(BUNDLES))


def png(width: int, height: int, fill=(0, 255, 0, 255), marker=(255, 0, 0, 255)) -> bytes:
    image = Image.new("RGBA", (width, height), fill)
    for x in range(min(8, width)):
        for y in range(min(8, height)):
            image.putpixel((x, y), marker)
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def only(env, type_name: str):
    for obj in env.objects:
        if bundle_io.type_name(obj) == type_name:
            return obj
    raise AssertionError(f"no {type_name} in this bundle")


# -- Index -------------------------------------------------------------------

def test_index_finds_a_dependency_by_its_hash(index):
    """The atlas is on disk under a readable name; Unity asks for the hash."""
    hit = index.lookup("a56446fe408b44ef3a6a93f8b8e062a3")
    assert hit is not None and Path(hit).name == SPRITE_ATLAS


@pytest.mark.parametrize("bad", ["../catalog_server.py", "C:/Windows/win.ini",
                                 "..\\..\\secrets", "", "nope.assetbundle"])
def test_index_refuses_paths_outside_the_directory(index, bad):
    assert index.resolve_in_dir(bad) is None


# -- Describe ----------------------------------------------------------------

def test_describe_names_and_categorises(index):
    entry = bundle_io.describe(index.resolve_in_dir(TEXTURE))
    assert entry["valid"] is True
    assert entry["derived_name"] == "support_icon"
    # Filed by what it is for, not by what type is inside it: this one holds
    # a Texture2D, but an icon is part of the interface.
    assert entry["category"] == "UI"
    assert entry["dominant_type"] == "Texture2D"
    assert entry["asset_count"] == 1
    # The viewer reads these two; a catalog without them shows nothing.
    assert entry["derived_name"] and entry["all_asset_names"]


# -- Categories --------------------------------------------------------------

def test_categorise_reads_the_name():
    cases = [
        ("MortyBigBrain", "Characters"),
        ("BethTheDestroyer_Down_4", "Characters"),
        ("2_1+Sprite+SiameseHost+Shadow", "Characters"),
        ("MPArenaModeInfo", "Multiplayer"),
        ("GachaPurchaseCell", "Shop & crafting"),
        ("AttackButtonPopGolden", "Battle & attacks"),
        ("TreeLargeTex", "World & buildings"),
        ("PauseBtnItem", "UI"),
        ("Sprite_ZH_TW", "Localisation"),
        ("sactx-0-256x512-ETC2-WorldDefault-b4aaf91f", "Sprite atlas"),
    ]
    for name, expected in cases:
        assert bundle_io.categorise(name, [], "Texture2D") == expected, name


def test_categorise_separates_scenery_from_the_characters_it_is_named_after():
    # Each of these matches both rules, and each went to the wrong pile
    # before the order and the word boundaries were sorted out.
    assert bundle_io.categorise("BuildingMortyLabSignTex", [], "Texture2D") == "World & buildings"
    assert bundle_io.categorise("AnimeCityTerrainMat", [], "Material") == "World & buildings"
    # "_Front" with no frame number is the end of a bridge, not a walk cycle.
    assert bundle_io.categorise("Bridge_End_Front", [], "Mesh") == "World & buildings"
    # ...but Snowball is a Morty, and must not be caught by "snow".
    assert bundle_io.categorise("4_1+Snowball+Sprite+Shadow", [], "GameObject") == "Characters"
    assert bundle_io.categorise("SnowTile", [], "Texture2D") == "World & buildings"


def test_categorise_prefers_the_games_own_paths():
    paths = ["assets/resourceassetbundles/anime101/characters/x/xback.png"]
    assert bundle_io.categorise("anime101", paths, "Texture2D") == "Characters"


def test_categorise_does_not_read_language_codes_out_of_paths():
    # " it " turns up inside .../audio_sfx/... and once filed a bundle of
    # sound effects under Localisation. Paths get their own, specific rules.
    paths = ["assets/resourceassetbundles/preload/audio_sfx/sfx/buffattack.wav"]
    assert bundle_io.categorise("preload", paths, "AudioClip") == "Audio"


def test_categorise_falls_back_to_the_type():
    assert bundle_io.categorise("", [], "Mesh") == "3D models"
    assert bundle_io.categorise("zzz", [], "AudioClip") == "Audio"
    assert bundle_io.categorise("zzz", [], "") == "Other"


def test_describe_records_dependencies(index):
    entry = bundle_io.describe(index.resolve_in_dir(PACKED_SPRITE))
    assert "a56446fe408b44ef3a6a93f8b8e062a3" in entry["dependencies"]


def test_describe_survives_a_file_that_is_not_a_bundle(tmp_path):
    junk = tmp_path / "notabundle.bin"
    junk.write_bytes(b"this is not a Unity bundle" * 10)
    entry = bundle_io.describe(str(junk))
    assert entry["valid"] is False and entry["error"]


# -- Export ------------------------------------------------------------------

def test_export_texture_as_png(index):
    env = bundle_io.open_bundle(index.resolve_in_dir(TEXTURE), index)
    item = bundle_io.export_asset(only(env, "Texture2D"))
    assert item.kind == "image" and item.filename.endswith(".png")
    assert Image.open(io.BytesIO(item.data)).size[0] > 0


def test_export_textasset(index):
    env = bundle_io.open_bundle(index.resolve_in_dir(TEXT), index)
    item = bundle_io.export_asset(only(env, "TextAsset"))
    assert item.kind == "text" and item.data


def test_export_raw_always_works(index):
    env = bundle_io.open_bundle(index.resolve_in_dir(TEXT), index)
    item = bundle_io.export_asset(only(env, "TextAsset"), prefer="raw")
    assert item.kind == "binary" and item.data


def test_sprite_in_another_bundle_decodes(index):
    """Without dependency resolution this one raises FileNotFoundError."""
    env = bundle_io.open_bundle(index.resolve_in_dir(PACKED_SPRITE), index)
    item = bundle_io.export_asset(only(env, "Sprite"))
    assert item.kind == "image"
    assert Image.open(io.BytesIO(item.data)).size[0] > 0


def test_export_zip_lists_every_asset(index):
    env = bundle_io.open_bundle(index.resolve_in_dir(UNITYFS), index)
    data, report = bundle_io.export_bundle_zip(env)
    assert report and all("type" in row for row in report)
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert "_export_report.json" in zf.namelist()
        assert len(zf.namelist()) > 1


# -- Import and repack -------------------------------------------------------

def test_replace_texture_and_rebuild(index, tmp_path):
    env = bundle_io.open_bundle(index.resolve_in_dir(TEXTURE), index)
    obj = only(env, "Texture2D")
    texture = obj.read()
    width, height = int(texture.m_Width), int(texture.m_Height)

    result = bundle_io.import_asset(obj, png(width, height), "test.png")
    assert result.type == "Texture2D" and result.preview

    written = bundle_io.repack(env, str(tmp_path), "lz4")
    assert len(written) == 1 and Path(written[0]).name == TEXTURE

    again = bundle_io.open_bundle(written[0], bundle_io.FileIndex(str(tmp_path)))
    image = only(again, "Texture2D").read().image.convert("RGBA")
    assert image.size == (width, height)
    red, green, _, _ = image.getpixel((2, 2))
    assert red > 200 and green < 80, "the marker should survive the round trip"


def test_replace_textasset_and_rebuild(index, tmp_path):
    env = bundle_io.open_bundle(index.resolve_in_dir(TEXT), index)
    bundle_io.import_asset(only(env, "TextAsset"), b"replaced by the test suite", "x.txt")
    written = bundle_io.repack(env, str(tmp_path), "none")

    again = bundle_io.open_bundle(written[0], bundle_io.FileIndex(str(tmp_path)))
    assert bundle_io.export_asset(only(again, "TextAsset")).data == b"replaced by the test suite"


def test_replace_packed_sprite_writes_the_atlas(index, tmp_path):
    """
    A packed sprite lives inside a shared atlas, often in a different bundle.
    The rebuilt file has to be that atlas, under its own name.
    """
    env = bundle_io.open_bundle(index.resolve_in_dir(PACKED_SPRITE), index)
    obj = only(env, "Sprite")
    size = Image.open(io.BytesIO(bundle_io.export_asset(obj).data)).size

    bundle_io.import_asset(obj, png(*size), "sprite.png")
    written = bundle_io.repack(env, str(tmp_path), "lz4")
    assert [Path(p).name for p in written] == [SPRITE_ATLAS]

    # Rebuild the folder the way a modder would: the new atlas next to the
    # original sprite bundle.
    shutil.copy(index.resolve_in_dir(PACKED_SPRITE), tmp_path)
    again = bundle_io.open_bundle(str(tmp_path / PACKED_SPRITE),
                                  bundle_io.FileIndex(str(tmp_path)))
    image = Image.open(io.BytesIO(
        bundle_io.export_asset(only(again, "Sprite")).data)).convert("RGBA")
    assert image.size == size
    red, green, _, _ = image.getpixel((2, 2))
    assert red > 200 and green < 80, "top-left marker, so the orientation is right"


@pytest.mark.parametrize("pack", bundle_io.PACKERS)
def test_every_packer_produces_a_readable_bundle(index, tmp_path, pack):
    env = bundle_io.open_bundle(index.resolve_in_dir(UNITYFS), index)
    before = len(list(env.objects))
    obj = only(env, "Texture2D")
    texture = obj.read()
    bundle_io.import_asset(obj, png(int(texture.m_Width), int(texture.m_Height)), "x.png")

    out = tmp_path / pack
    written = bundle_io.repack(env, str(out), pack)
    again = bundle_io.open_bundle(written[0], None)
    assert len(list(again.objects)) == before


def test_repack_refuses_when_nothing_changed(index, tmp_path):
    env = bundle_io.open_bundle(index.resolve_in_dir(TEXTURE), index)
    with pytest.raises(ValueError, match="nothing has been replaced"):
        bundle_io.repack(env, str(tmp_path))


def test_import_rejects_an_unsupported_type(index):
    env = bundle_io.open_bundle(index.resolve_in_dir(UNITYFS), index)
    for obj in env.objects:
        if bundle_io.type_name(obj) not in bundle_io.IMPORTABLE | {"AssetBundle"}:
            with pytest.raises(ValueError, match="not supported"):
                bundle_io.import_asset(obj, b"\x00\x01", "x.bin")
            return
    pytest.skip("this bundle holds only importable types")


def test_import_rejects_an_empty_file(index):
    env = bundle_io.open_bundle(index.resolve_in_dir(TEXT), index)
    with pytest.raises(ValueError, match="empty"):
        bundle_io.import_asset(only(env, "TextAsset"), b"", "x.txt")


# -- Small helpers -----------------------------------------------------------

@pytest.mark.parametrize("size,expected", [
    (0, "0.0 B"), (1023, "1023.0 B"), (1024, "1.0 KB"),
    (1024 ** 2, "1.0 MB"), (1024 ** 3, "1.0 GB"), (1024 ** 4, "1.0 TB"),
])
def test_format_size(size, expected):
    assert bundle_io.format_size(size) == expected


@pytest.mark.parametrize("raw", ["a/b", "a\\b", "a:b", 'a"b', "a*b", "  ..  "])
def test_safe_filename_strips_path_characters(raw):
    cleaned = bundle_io.safe_filename(raw)
    assert not any(ch in cleaned for ch in '/\\:"*?<>|')
    assert cleaned
