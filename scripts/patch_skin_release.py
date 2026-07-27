#!/usr/bin/env python3
"""Patch the v1 data archive so custom skins load from both IDE paths."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shutil
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path

from skin_linkage import (
    CUSTOM_SKIN_IDS,
    img_index_size,
    parse_img_index,
    parse_ped_definitions,
    repair_ped_definitions,
    validate_skin_links,
)


OWNER = "manustest534h-dev"
REPOSITORY = "havanarp-data"
RELEASE_TAG = "v1"
BASE_URL = f"https://github.com/{OWNER}/{REPOSITORY}/releases/download/{RELEASE_TAG}"
DATA_PEDS = "LuxuryMobile/data/peds.ide"
SAMP_PEDS = "LuxuryMobile/SAMP/peds.ide"
SAMP_IMG = "LuxuryMobile/texdb/samp.img"
SAMP_TEXTURES = (
    "LuxuryMobile/texdb/samp/samp.dxt.dat",
    "LuxuryMobile/texdb/samp/samp.etc.dat",
    "LuxuryMobile/texdb/samp/samp.pvr.dat",
    "LuxuryMobile/texdb/samp/samp.txt",
    "LuxuryMobile/texdb/samp/samp.unc.dat",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def zip_data_offset(stream, info: zipfile.ZipInfo) -> int:
    stream.seek(info.header_offset)
    header = stream.read(30)
    if len(header) != 30:
        raise RuntimeError(f"short local ZIP header for {info.filename}")
    fields = struct.unpack("<IHHHHHIIIHH", header)
    if fields[0] != 0x04034B50:
        raise RuntimeError(f"invalid local ZIP header for {info.filename}")
    return info.header_offset + 30 + fields[-2] + fields[-1]


def read_img_index(archive: zipfile.ZipFile) -> set[str]:
    with archive.open(SAMP_IMG) as stream:
        prefix = stream.read(8)
        required = img_index_size(prefix)
        prefix += stream.read(required - len(prefix))
    return parse_img_index(prefix)


def validate_archive(archive_path: Path) -> dict[int, str]:
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("release archive contains duplicate ZIP entries")
        required = {DATA_PEDS, SAMP_PEDS, SAMP_IMG, *SAMP_TEXTURES}
        missing = sorted(required.difference(names))
        if missing:
            raise RuntimeError(f"release archive is missing: {', '.join(missing)}")
        data_peds = archive.read(DATA_PEDS)
        samp_peds = archive.read(SAMP_PEDS)
        if data_peds != samp_peds:
            raise RuntimeError("data/peds.ide and SAMP/peds.ide are not identical")
        pedestrians = parse_ped_definitions(samp_peds)
        return validate_skin_links(pedestrians, read_img_index(archive))


def patch_archive(source: Path, destination: Path) -> dict[int, str]:
    if source.resolve() == destination.resolve():
        raise RuntimeError("source and destination archives must differ")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)

    with zipfile.ZipFile(source) as archive:
        repaired_peds = repair_ped_definitions(archive.read(SAMP_PEDS))
        pedestrians = parse_ped_definitions(repaired_peds)
        validate_skin_links(pedestrians, read_img_index(archive))
        source_timestamp = datetime.datetime(
            *archive.getinfo(SAMP_PEDS).date_time,
            tzinfo=datetime.timezone.utc,
        ).timestamp()

    with tempfile.TemporaryDirectory(prefix="havanarp-skins-") as temporary:
        staging = Path(temporary)
        for name in (DATA_PEDS, SAMP_PEDS):
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(repaired_peds)
            target.chmod(0o660)
            os.utime(target, (source_timestamp, source_timestamp))
        subprocess.run(
            ["zip", "-q", "-X", str(destination), DATA_PEDS, SAMP_PEDS],
            cwd=staging,
            check=True,
        )
    return validate_archive(destination)


def write_metadata(
    archive_path: Path, output_dir: Path, linked_skins: dict[int, str]
) -> None:
    archive_digest = sha256(archive_path)
    manifest_files = []
    with archive_path.open("rb") as raw_archive, zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            if info.is_dir() or info.filename.lower().endswith(".ini"):
                continue
            manifest_files.append(
                {
                    "n": info.filename,
                    "o": zip_data_offset(raw_archive, info),
                    "c": info.compress_size,
                    "s": info.file_size,
                    "crc": info.CRC,
                    "m": info.compress_type,
                }
            )
    if len(manifest_files) != 416:
        raise RuntimeError(
            f"unexpected release file count: {len(manifest_files)} (expected 416)"
        )

    manifest = {
        "version": 3,
        "archive": "luxury.zip",
        "archive_size": archive_path.stat().st_size,
        "archive_sha256": archive_digest,
        "files": manifest_files,
    }
    (output_dir / "luxury_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    release = {
        "version": 3,
        "tag": RELEASE_TAG,
        "archive": {
            "url": f"{BASE_URL}/luxury.zip",
            "size": archive_path.stat().st_size,
            "sha256": archive_digest,
        },
        "repair_manifest_url": f"{BASE_URL}/luxury_manifest.json",
        "source": {
            "repository": f"{OWNER}/havana-launcher",
            "release": "v1",
        },
        "skin_linkage": {
            "definitions": [DATA_PEDS, SAMP_PEDS],
            "model_archive": SAMP_IMG,
            "custom_skin_count": len(linked_skins),
            "custom_skin_ids": sorted(linked_skins),
            "texture_databases": list(SAMP_TEXTURES),
        },
    }
    (output_dir / "release.json").write_text(
        json.dumps(release, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("build/v1"))
    args = parser.parse_args()

    source = args.archive.resolve()
    if not source.is_file():
        raise RuntimeError(f"source archive does not exist: {source}")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "luxury.zip"

    linked_skins = patch_archive(source, destination)
    if set(linked_skins) != set(CUSTOM_SKIN_IDS):
        raise RuntimeError("unexpected custom skin linkage result")
    write_metadata(destination, output_dir, linked_skins)
    print(f"PASS mirrored pedestrian definitions ({len(linked_skins)} custom skins)")
    print(f"Release ready: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
