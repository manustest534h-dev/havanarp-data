#!/usr/bin/env python3
"""Verify HavanaRP v1 manifest and launcher skin linkage over HTTP ranges."""

from __future__ import annotations

import binascii
import hashlib
import json
import urllib.request
import zlib

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
MANIFEST_SHA256 = "032d46df82134b1511ca9e99f2ac5aa2f618119cc30173a7b1699084545824a2"
ARCHIVE_SHA256 = "108fb6947a6b220e419fbe6c6c956f0eaa0393c676b1e70babfc57fac5c5628f"
ARCHIVE_SIZE = 1_387_227_370
FILE_COUNT = 416
CRITICAL_FILES = (
    "LuxuryMobile/data/clothes.dat",
    "LuxuryMobile/data/peds.ide",
    "LuxuryMobile/SAMP/peds.ide",
    "LuxuryMobile/texdb/player.img",
    "LuxuryMobile/texdb/player/player.pvr.dat",
    "LuxuryMobile/texdb/playerhi/playerhi.pvr.dat",
    "LuxuryMobile/texdb/samp/samp.txt",
)
SAMP_IMG = "LuxuryMobile/texdb/samp.img"
SAMP_TEXTURES = (
    "LuxuryMobile/texdb/samp/samp.dxt.dat",
    "LuxuryMobile/texdb/samp/samp.etc.dat",
    "LuxuryMobile/texdb/samp/samp.pvr.dat",
    "LuxuryMobile/texdb/samp/samp.unc.dat",
)


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
    pedestrians = parse_ped_definitions(extracted["LuxuryMobile/SAMP/peds.ide"])
    samp_item = by_name.get(SAMP_IMG)
    if samp_item is None:
        raise RuntimeError(f"missing critical file: {SAMP_IMG}")
    img_names = parse_img_index(fetch_img_index(samp_item))
    linked_skins = validate_skin_links(pedestrians, img_names)
    if set(linked_skins) != set(CUSTOM_SKIN_IDS):
        raise RuntimeError("unexpected custom skin IDs")
    for name in SAMP_TEXTURES:
        item = by_name.get(name)
        if item is None or int(item["s"]) <= 0:
            raise RuntimeError(f"missing SAMP texture database: {name}")
    print(f"PASS custom skin linkage ({len(linked_skins)} models)")

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
    print(f"PASS manifest ({FILE_COUNT} files)")
    print(f"PASS archive metadata ({ARCHIVE_SIZE} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
