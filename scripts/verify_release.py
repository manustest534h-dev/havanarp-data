#!/usr/bin/env python3
"""Verify HavanaRP v1 manifest and launcher clothing files over HTTP ranges."""

from __future__ import annotations

import binascii
import hashlib
import json
import urllib.request
import zlib


BASE_URL = (
    "https://github.com/manustest534h-dev/"
    "havanarp-data/releases/download/v1"
)
MANIFEST_SHA256 = "78eab1dd43741459b907dfa8c6f48c61298d30d6ea9db204dc76878ed09d3be1"
ARCHIVE_SIZE = 1_387_227_329
FILE_COUNT = 416
CRITICAL_FILES = (
    "LuxuryMobile/data/clothes.dat",
    "LuxuryMobile/data/peds.ide",
    "LuxuryMobile/texdb/player.img",
    "LuxuryMobile/texdb/player/player.pvr.dat",
    "LuxuryMobile/texdb/playerhi/playerhi.pvr.dat",
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


def main() -> int:
    manifest_bytes = fetch(f"{BASE_URL}/luxury_manifest.json")
    actual_manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    if actual_manifest_sha != MANIFEST_SHA256:
        raise RuntimeError(f"manifest SHA-256 mismatch: {actual_manifest_sha}")

    manifest = json.loads(manifest_bytes)
    files = manifest.get("files", [])
    if len(files) != FILE_COUNT:
        raise RuntimeError(f"manifest file count mismatch: {len(files)}")
    by_name = {item["n"]: item for item in files}

    for name in CRITICAL_FILES:
        item = by_name.get(name)
        if item is None:
            raise RuntimeError(f"missing critical file: {name}")
        first = int(item["o"])
        last = first + int(item["c"]) - 1
        packed = fetch(
            f"{BASE_URL}/luxury.zip",
            {"Range": f"bytes={first}-{last}"},
        )
        if len(packed) != int(item["c"]):
            raise RuntimeError(f"range size mismatch for {name}")
        data = zlib.decompress(packed, -15) if int(item["m"]) == 8 else packed
        if len(data) != int(item["s"]):
            raise RuntimeError(f"file size mismatch for {name}")
        if binascii.crc32(data) & 0xFFFFFFFF != int(item["crc"]):
            raise RuntimeError(f"CRC mismatch for {name}")
        print(f"PASS {name} ({len(data)} bytes)")

    release = json.loads(fetch(f"{BASE_URL}/release.json"))
    if int(release["archive"]["size"]) != ARCHIVE_SIZE:
        raise RuntimeError("release archive size mismatch")
    print(f"PASS manifest ({FILE_COUNT} files)")
    print(f"PASS archive metadata ({ARCHIVE_SIZE} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

