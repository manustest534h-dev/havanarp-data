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

from cdn_skin_migration import (
    CdnSkinAssets,
    load_cdn_skin_assets,
    parse_texture_catalog,
    validate_texture_links,
)
from skin_linkage import (
    CDN_SKIN_IDS,
    CUSTOM_SKIN_IDS,
    NATIVE_SKIN_IDS,
    img_index_size,
    merge_img_archives,
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
CUSTOM3_IMG = "LuxuryMobile/texdb/custom3.img"
DATA_GTA = "LuxuryMobile/data/gta.dat"
SAMP_GTA = "LuxuryMobile/SAMP/gta.dat"
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
CUSTOM3_LOAD_LINE = b"IMG TEXDB\\CUSTOM3.IMG"
EXPECTED_FILE_COUNT = 416
EXPECTED_SAMP_TEXTURE_NAMES = 2386


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


def read_img_index(archive: zipfile.ZipFile, name: str) -> set[str]:
    with archive.open(name) as stream:
        prefix = stream.read(8)
        required = img_index_size(prefix)
        prefix += stream.read(required - len(prefix))
    return parse_img_index(prefix)


def texture_row_count(payload: bytes) -> int:
    return sum(line.lstrip().startswith(b'"') for line in payload.splitlines())


def merge_texture_metadata(base: bytes, addon: bytes) -> bytes:
    """Append a disjoint mobile texdb catalog to an existing catalog."""

    base_names = parse_texture_catalog(base)
    addon_names = parse_texture_catalog(addon)
    overlap = base_names.intersection(addon_names)
    if overlap == addon_names:
        return base
    if overlap:
        raise RuntimeError(
            "texture catalogs contain a partial duplicate set: "
            + ", ".join(sorted(overlap))
        )
    newline = b"\r\n" if base.count(b"\r\n") > base.count(b"\n") // 2 else b"\n"
    rows = [line for line in addon.splitlines() if line.lstrip().startswith(b'"')]
    if len(rows) != len(addon_names):
        raise RuntimeError("CDN texture catalog contains duplicate names")
    return base.rstrip(b"\r\n") + newline + newline.join(rows) + newline


def merge_texture_toc(base: bytes, addon: bytes, base_size: int, addon_size: int) -> bytes:
    """Merge mobile texdb offset tables while preserving missing-entry markers."""

    if len(base) % 4 or len(addon) % 4:
        raise RuntimeError("invalid mobile texture offset table")
    base_values = list(struct.unpack(f"<{len(base) // 4}I", base))
    addon_values = list(struct.unpack(f"<{len(addon) // 4}I", addon))
    if not base_values or base_values[0] != base_size:
        raise RuntimeError("base texture data size does not match its offset table")
    if not addon_values or addon_values[0] != addon_size:
        raise RuntimeError("CDN texture data size does not match its offset table")
    shifted = [
        value if value == 0xFFFFFFFF else value + base_size
        for value in addon_values[1:]
    ]
    values = [base_size + addon_size, *base_values[1:], *shifted]
    return struct.pack(f"<{len(values)}I", *values)


def merge_texture_database(
    archive: zipfile.ZipFile, cdn_assets: CdnSkinAssets
) -> dict[str, bytes]:
    """Merge the CDN textures into the launcher-supported samp texdb."""

    base_metadata = archive.read("LuxuryMobile/texdb/samp/samp.txt")
    addon_metadata = cdn_assets.textures["custom3.txt"]
    merged_metadata = merge_texture_metadata(base_metadata, addon_metadata)
    if merged_metadata == base_metadata:
        return {}

    replacements = {"LuxuryMobile/texdb/samp/samp.txt": merged_metadata}
    base_rows = texture_row_count(base_metadata)
    addon_rows = texture_row_count(addon_metadata)
    for format_name in ("dxt", "etc", "pvr"):
        prefix = f"LuxuryMobile/texdb/samp/samp.{format_name}"
        base_dat = archive.read(f"{prefix}.dat")
        base_tmb = archive.read(f"{prefix}.tmb")
        base_toc = archive.read(f"{prefix}.toc")
        addon_dat = cdn_assets.textures[f"custom3.{format_name}.dat"]
        addon_tmb = cdn_assets.textures[f"custom3.{format_name}.tmb"]
        addon_toc = cdn_assets.textures[f"custom3.{format_name}.toc"]
        if len(base_toc) // 4 != base_rows + 1:
            raise RuntimeError(f"base {format_name} texture catalog mismatch")
        if len(addon_toc) // 4 != addon_rows + 1:
            raise RuntimeError(f"CDN {format_name} texture catalog mismatch")
        replacements[f"{prefix}.dat"] = base_dat + addon_dat
        replacements[f"{prefix}.tmb"] = base_tmb + addon_tmb
        replacements[f"{prefix}.toc"] = merge_texture_toc(
            base_toc, addon_toc, len(base_dat), len(addon_dat)
        )
    return replacements


def validate_archive(archive_path: Path) -> dict[int, str]:
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("release archive contains duplicate ZIP entries")
        required = {
            DATA_PEDS,
            SAMP_PEDS,
            DATA_GTA,
            SAMP_GTA,
            SAMP_IMG,
            *SAMP_TEXTURES,
        }
        missing = sorted(required.difference(names))
        if missing:
            raise RuntimeError(f"release archive is missing: {', '.join(missing)}")
        data_peds = archive.read(DATA_PEDS)
        samp_peds = archive.read(SAMP_PEDS)
        if data_peds != samp_peds:
            raise RuntimeError("data/peds.ide and SAMP/peds.ide are not identical")
        for name in (DATA_GTA, SAMP_GTA):
            if CUSTOM3_LOAD_LINE in archive.read(name).upper():
                raise RuntimeError(f"unsafe custom3 model loader remains in {name}")
        forbidden = {CUSTOM3_IMG, *CUSTOM3_TEXTURES}.intersection(names)
        if forbidden:
            raise RuntimeError("release archive still contains standalone custom3 assets")
        pedestrians = parse_ped_definitions(samp_peds)
        linked = validate_skin_links(
            pedestrians, read_img_index(archive, SAMP_IMG), CUSTOM_SKIN_IDS
        )
        metadata = archive.read("LuxuryMobile/texdb/samp/samp.txt")
        if len(parse_texture_catalog(metadata)) != EXPECTED_SAMP_TEXTURE_NAMES:
            raise RuntimeError("merged samp texture catalog is incomplete")
        return linked


def add_cdn_pedestrians(payload: bytes, rows: bytes) -> bytes:
    repaired = repair_ped_definitions(payload)
    current = parse_ped_definitions(repaired)
    present = set(current).intersection(CDN_SKIN_IDS)
    if present:
        if present == set(CDN_SKIN_IDS):
            return repaired
        raise RuntimeError("pedestrian definitions contain a partial CDN skin set")

    incoming = parse_ped_definitions(rows)
    if set(incoming) != set(CDN_SKIN_IDS):
        raise RuntimeError("unexpected CDN skin rows")
    duplicates = set(current).intersection(incoming)
    if duplicates:
        raise RuntimeError(f"duplicate CDN pedestrian IDs: {sorted(duplicates)}")

    newline = b"\r\n" if repaired.count(b"\r\n") > repaired.count(b"\n") // 2 else b"\n"
    marker = newline + b"end"
    insertion = repaired.rfind(marker)
    if insertion < 0:
        raise RuntimeError("pedestrian definitions are missing the final end marker")
    normalized_rows = newline.join(rows.splitlines()) + newline
    section = newline + b"# HavanaRP CDN v763 skins" + newline + normalized_rows
    return repaired[:insertion] + section + repaired[insertion:]


def remove_custom3_loader(payload: bytes) -> bytes:
    return b"".join(
        line
        for line in payload.splitlines(keepends=True)
        if line.strip().upper() != CUSTOM3_LOAD_LINE
    )


def patch_archive(
    source: Path, destination: Path, cdn_assets: CdnSkinAssets
) -> dict[int, str]:
    if source.resolve() == destination.resolve():
        raise RuntimeError("source and destination archives must differ")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)

    with zipfile.ZipFile(source) as archive:
        repaired_peds = add_cdn_pedestrians(
            archive.read(SAMP_PEDS), cdn_assets.pedestrian_rows
        )
        pedestrians = parse_ped_definitions(repaired_peds)
        validate_skin_links(
            pedestrians, read_img_index(archive, SAMP_IMG), NATIVE_SKIN_IDS
        )
        custom_index_size = img_index_size(cdn_assets.model_archive[:8])
        validate_skin_links(
            pedestrians,
            parse_img_index(cdn_assets.model_archive[:custom_index_size]),
            CDN_SKIN_IDS,
        )
        merged_samp_img = merge_img_archives(
            archive.read(SAMP_IMG), cdn_assets.model_archive
        )
        merged_texture_files = merge_texture_database(archive, cdn_assets)
        validate_texture_links(
            cdn_assets.model_archive,
            merged_texture_files.get(
                "LuxuryMobile/texdb/samp/samp.txt",
                archive.read("LuxuryMobile/texdb/samp/samp.txt"),
            ),
        )
        gta_files = {
            DATA_GTA: remove_custom3_loader(archive.read(DATA_GTA)),
            SAMP_GTA: remove_custom3_loader(archive.read(SAMP_GTA)),
        }
        source_timestamp = datetime.datetime(
            *archive.getinfo(SAMP_PEDS).date_time,
            tzinfo=datetime.timezone.utc,
        ).timestamp()

    with tempfile.TemporaryDirectory(prefix="havanarp-skins-") as temporary:
        staging = Path(temporary)
        replacements: dict[str, bytes] = {
            DATA_PEDS: repaired_peds,
            SAMP_PEDS: repaired_peds,
            SAMP_IMG: merged_samp_img,
            **gta_files,
            **merged_texture_files,
        }
        for name, payload in replacements.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            target.chmod(0o660)
            os.utime(target, (source_timestamp, source_timestamp))
        subprocess.run(
            ["zip", "-q", "-X", str(destination), *replacements],
            cwd=staging,
            check=True,
        )
        with zipfile.ZipFile(source) as archive:
            removable = [
                name
                for name in (CUSTOM3_IMG, *CUSTOM3_TEXTURES)
                if name in archive.namelist()
            ]
        if removable:
            subprocess.run(
                ["zip", "-q", "-d", str(destination), *removable],
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
    if len(manifest_files) != EXPECTED_FILE_COUNT:
        raise RuntimeError(
            f"unexpected release file count: {len(manifest_files)} "
            f"(expected {EXPECTED_FILE_COUNT})"
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
            "model_archives": [SAMP_IMG],
            "custom_skin_count": len(linked_skins),
            "custom_skin_ids": sorted(linked_skins),
            "texture_databases": [*SAMP_TEXTURES],
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
    parser.add_argument(
        "--cdn-source-dir",
        type=Path,
        help="directory containing verified v763 multipart ZIPs",
    )
    parser.add_argument(
        "--cdn-cache-dir",
        type=Path,
        default=Path("build/cdn-v763"),
        help="download cache used when --cdn-source-dir is omitted",
    )
    args = parser.parse_args()

    source = args.archive.resolve()
    if not source.is_file():
        raise RuntimeError(f"source archive does not exist: {source}")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "luxury.zip"

    cdn_assets = load_cdn_skin_assets(args.cdn_source_dir, args.cdn_cache_dir)
    linked_skins = patch_archive(source, destination, cdn_assets)
    if set(linked_skins) != set(CUSTOM_SKIN_IDS):
        raise RuntimeError("unexpected custom skin linkage result")
    write_metadata(destination, output_dir, linked_skins)
    print(f"PASS mirrored pedestrian definitions ({len(linked_skins)} custom skins)")
    print(f"Release ready: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
