#!/usr/bin/env python3
"""Verify HavanaRP v1 manifest and launcher skin linkage over HTTP ranges."""

from __future__ import annotations

import binascii
import hashlib
import json
import urllib.request
import zlib

from cdn_skin_migration import parse_texture_catalog
from skin_linkage import (
    CUSTOM_SKIN_IDS,
    img_index_size,
    parse_img_index,
    parse_ped_definitions,
    validate_skin_links,
)


BASE_URL = (
    "https://github.com/manustest534h-dev/"
    "havanarp-data/releases/download/v1"
)
MANIFEST_SHA256 = "96933453432c8cdd31e1f658c239f56feac19ed043682807662cfa11ac859dd6"
ARCHIVE_SHA256 = "36da14afcf44df4b6ef74a78b6bf70c6124fe5a352c0adb49d67b87586be6eec"
ARCHIVE_SIZE = 1_430_065_220
FILE_COUNT = 416
SAMP_IMG_ENTRY_COUNT = 1623
CRITICAL_FILES = (
    "LuxuryMobile/data/clothes.dat",
    "LuxuryMobile/data/gta.dat",
    "LuxuryMobile/data/peds.ide",
    "LuxuryMobile/SAMP/gta.dat",
    "LuxuryMobile/SAMP/peds.ide",
    "LuxuryMobile/texdb/player.img",
    "LuxuryMobile/texdb/player/player.pvr.dat",
    "LuxuryMobile/texdb/playerhi/playerhi.pvr.dat",
    "LuxuryMobile/texdb/samp/samp.txt",
)
SAMP_IMG = "LuxuryMobile/texdb/samp.img"
CUSTOM3_IMG = "LuxuryMobile/texdb/custom3.img"
CUSTOM3_LOAD_LINE = b"IMG TEXDB\\CUSTOM3.IMG"
SAMP_TEXTURES = (
    "LuxuryMobile/texdb/samp/samp.dxt.dat",
    "LuxuryMobile/texdb/samp/samp.etc.dat",
    "LuxuryMobile/texdb/samp/samp.pvr.dat",
    "LuxuryMobile/texdb/samp/samp.txt",
    "LuxuryMobile/texdb/samp/samp.unc.dat",
)
CUSTOM3_TEXTURES = tuple(
    f"LuxuryMobile/texdb/custom3/custom3.{format_name}.{extension}"
    for format_name in ("dxt", "etc", "pvr")
    for extension in ("dat", "tmb", "toc")
) + ("LuxuryMobile/texdb/custom3/custom3.txt",)


def fetch(url: str, headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept-Encoding": "identity",
            "User-Agent": "HavanaRP-release-verifier/1",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.read()


def unpack_entry(item: dict[str, object]) -> bytes:
    first = int(item["o"])
    last = first + int(item["c"]) - 1
    packed = fetch(
        f"{BASE_URL}/luxury.zip",
        {"Range": f"bytes={first}-{last}"},
    )
    if len(packed) != int(item["c"]):
        raise RuntimeError(f"range size mismatch for {item['n']}")
    data = zlib.decompress(packed, -15) if int(item["m"]) == 8 else packed
    if len(data) != int(item["s"]):
        raise RuntimeError(f"file size mismatch for {item['n']}")
    if binascii.crc32(data) & 0xFFFFFFFF != int(item["crc"]):
        raise RuntimeError(f"CRC mismatch for {item['n']}")
    return data


def fetch_img_index(item: dict[str, object]) -> bytes:
    first = int(item["o"])
    last = first + int(item["c"]) - 1
    request = urllib.request.Request(
        f"{BASE_URL}/luxury.zip",
        headers={
            "Accept-Encoding": "identity",
            "Range": f"bytes={first}-{last}",
            "User-Agent": "HavanaRP-release-verifier/1",
        },
    )
    output = bytearray()
    decoder = zlib.decompressobj(-15) if int(item["m"]) == 8 else None
    required = 8
    with urllib.request.urlopen(request, timeout=300) as response:
        while len(output) < required:
            chunk = response.read(65536)
            if not chunk:
                break
            output.extend(decoder.decompress(chunk) if decoder else chunk)
            if len(output) >= 8:
                required = img_index_size(output)
    if len(output) < required:
        raise RuntimeError(f"short GTA IMG index for {item['n']}")
    return bytes(output[:required])


def main() -> int:
    manifest_bytes = fetch(f"{BASE_URL}/luxury_manifest.json")
    actual_manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    if actual_manifest_sha != MANIFEST_SHA256:
        raise RuntimeError(f"manifest SHA-256 mismatch: {actual_manifest_sha}")

    manifest = json.loads(manifest_bytes)
    if int(manifest.get("version", 0)) != 3:
        raise RuntimeError("manifest version mismatch")
    if int(manifest.get("archive_size", 0)) != ARCHIVE_SIZE:
        raise RuntimeError("manifest archive size mismatch")
    if manifest.get("archive_sha256") != ARCHIVE_SHA256:
        raise RuntimeError("manifest archive SHA-256 mismatch")
    files = manifest.get("files", [])
    if len(files) != FILE_COUNT:
        raise RuntimeError(f"manifest file count mismatch: {len(files)}")
    by_name = {item["n"]: item for item in files}

    extracted: dict[str, bytes] = {}
    for name in CRITICAL_FILES:
        item = by_name.get(name)
        if item is None:
            raise RuntimeError(f"missing critical file: {name}")
        data = unpack_entry(item)
        extracted[name] = data
        print(f"PASS {name} ({len(data)} bytes)")

    if extracted["LuxuryMobile/data/peds.ide"] != extracted["LuxuryMobile/SAMP/peds.ide"]:
        raise RuntimeError("pedestrian definitions are not mirrored")
    for name in ("LuxuryMobile/data/gta.dat", "LuxuryMobile/SAMP/gta.dat"):
        if CUSTOM3_LOAD_LINE in extracted[name].upper():
            raise RuntimeError(f"unsafe custom3 model loader remains in {name}")
    pedestrians = parse_ped_definitions(extracted["LuxuryMobile/SAMP/peds.ide"])
    samp_item = by_name.get(SAMP_IMG)
    if samp_item is None:
        raise RuntimeError(f"missing critical file: {SAMP_IMG}")
    samp_index = fetch_img_index(samp_item)
    if (len(samp_index) - 8) // 32 != SAMP_IMG_ENTRY_COUNT:
        raise RuntimeError("merged samp IMG entry count mismatch")
    linked_skins = validate_skin_links(
        pedestrians,
        parse_img_index(samp_index),
        CUSTOM_SKIN_IDS,
    )
    if set(linked_skins) != set(CUSTOM_SKIN_IDS):
        raise RuntimeError("unexpected custom skin IDs")
    if int(samp_item["s"]) <= 0:
        raise RuntimeError("merged samp model archive is empty")
    catalog = parse_texture_catalog(
        extracted["LuxuryMobile/texdb/samp/samp.txt"]
    )
    if len(catalog) != 2386:
        raise RuntimeError(f"merged samp texture catalog mismatch: {len(catalog)}")
    forbidden = {CUSTOM3_IMG, *CUSTOM3_TEXTURES}.intersection(by_name)
    if forbidden:
        raise RuntimeError("standalone custom3 assets remain in the release")
    for name in SAMP_TEXTURES:
        item = by_name.get(name)
        if item is None or int(item["s"]) <= 0:
            raise RuntimeError(f"missing texture database: {name}")
    print(f"PASS custom skin linkage ({len(linked_skins)} models)")
    print(f"PASS merged samp texture catalog ({len(catalog)} textures)")

    release = json.loads(fetch(f"{BASE_URL}/release.json"))
    if int(release["version"]) != 3:
        raise RuntimeError("release version mismatch")
    if int(release["archive"]["size"]) != ARCHIVE_SIZE:
        raise RuntimeError("release archive size mismatch")
    if release["archive"]["sha256"] != ARCHIVE_SHA256:
        raise RuntimeError("release archive SHA-256 mismatch")
    linkage = release.get("skin_linkage", {})
    if int(linkage.get("custom_skin_count", 0)) != len(CUSTOM_SKIN_IDS):
        raise RuntimeError("release skin linkage metadata mismatch")
    if set(linkage.get("custom_skin_ids", [])) != set(CUSTOM_SKIN_IDS):
        raise RuntimeError("release custom skin IDs mismatch")
    if set(linkage.get("model_archives", [])) != {SAMP_IMG}:
        raise RuntimeError("release model archive metadata mismatch")
    if set(linkage.get("texture_databases", [])) != {
        *SAMP_TEXTURES,
    }:
        raise RuntimeError("release texture database metadata mismatch")
    print(f"PASS manifest ({FILE_COUNT} files)")
    print(f"PASS archive metadata ({ARCHIVE_SIZE} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
