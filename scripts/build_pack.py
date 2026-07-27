#!/usr/bin/env python3
"""Build a verified HavanaRP data release from immutable CDN parts."""

from __future__ import annotations

import argparse
import binascii
import concurrent.futures
import hashlib
import json
import shutil
import struct
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path


OWNER = "manustest534h-dev"
SOURCE_REPO = "havanarp-cdn"
TARGET_REPO = "havanarp-data"
RELEASE_TAG = "v1"
RAW_BASE = f"https://raw.githubusercontent.com/{OWNER}/{SOURCE_REPO}"


@dataclass(frozen=True)
class MultipartSource:
    target: str
    commit: str
    path: str


MULTIPART_SOURCES = (
    MultipartSource(".custom3", "e3b3d97ed329", "files/707/multipart/757/custom3"),
    MultipartSource(".custom3_dxt", "f5e476e6b4fd", "files/707/multipart/752/custom3_dxt"),
    MultipartSource(".custom3_etc", "f5e476e6b4fd", "files/707/multipart/752/custom3_etc"),
    MultipartSource(".custom3_pvr", "f5e476e6b4fd", "files/707/multipart/752/custom3_pvr"),
    MultipartSource(".data", "7722fbdff2fd", "files/707/multipart/756/data"),
)

SAMP_COMMIT = "da82921948fce2b1623f6e2e2a4bacb4e61c0d21"
SAMP_URL = f"{RAW_BASE}/{SAMP_COMMIT}/files/.samp"
SAMP_SIZE = 34_152_892


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def crc32(path: Path) -> int:
    value = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value = binascii.crc32(chunk, value)
    return value & 0xFFFFFFFF


def fetch_bytes(url: str, attempts: int = 5) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "HavanaRP-data-builder/1"})
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except (OSError, urllib.error.URLError) as error:
            last_error = error
            if attempt < attempts:
                time.sleep(attempt * 2)
    raise RuntimeError(f"failed to download {url}: {last_error}")


def download(url: str, destination: Path, expected_size: int, expected_sha256: str | None) -> None:
    if destination.exists() and destination.stat().st_size == expected_size:
        if expected_sha256 is None or sha256(destination) == expected_sha256:
            return

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "HavanaRP-data-builder/1"})
            with urllib.request.urlopen(request, timeout=180) as response, partial.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            if partial.stat().st_size != expected_size:
                raise RuntimeError(
                    f"size mismatch for {destination.name}: "
                    f"{partial.stat().st_size} != {expected_size}"
                )
            if expected_sha256 is not None:
                actual = sha256(partial)
                if actual != expected_sha256:
                    raise RuntimeError(f"SHA-256 mismatch for {destination.name}: {actual}")
            partial.replace(destination)
            return
        except (OSError, urllib.error.URLError, RuntimeError) as error:
            last_error = error
            partial.unlink(missing_ok=True)
            if attempt < 5:
                time.sleep(attempt * 2)
    raise RuntimeError(f"failed to download {url}: {last_error}")


def parse_properties(payload: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in payload.decode("utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def reconstruct(source: MultipartSource, staging: Path, work: Path, workers: int) -> dict[str, object]:
    base_url = f"{RAW_BASE}/{source.commit}/{source.path}"
    properties = parse_properties(fetch_bytes(f"{base_url}/manifest.properties"))
    if properties.get("target") != source.target:
        raise RuntimeError(f"unexpected target for {source.path}: {properties.get('target')}")

    part_count = int(properties["parts"])
    part_dir = work / source.target.removeprefix(".")
    jobs: list[tuple[str, Path, int, str]] = []
    for index in range(part_count):
        name = f"part-{index:03d}"
        jobs.append((
            f"{base_url}/{name}",
            part_dir / name,
            int(properties[f"part_{index:03d}_size"]),
            properties[f"part_{index:03d}_sha256"],
        ))

    print(f"Downloading {source.target} ({part_count} parts)", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(download, *job) for job in jobs]
        for completed, future in enumerate(concurrent.futures.as_completed(futures), 1):
            future.result()
            print(f"  {completed}/{part_count}", end="\r", flush=True)
    print()

    joined = work / f"{source.target.removeprefix('.')}.zip"
    with joined.open("wb") as output:
        for _, part, _, _ in jobs:
            with part.open("rb") as stream:
                shutil.copyfileobj(stream, output, length=1024 * 1024)
    if joined.stat().st_size != int(properties["joined_size"]):
        raise RuntimeError(f"joined size mismatch for {source.target}")
    if sha256(joined) != properties["joined_sha256"]:
        raise RuntimeError(f"joined SHA-256 mismatch for {source.target}")

    destination = staging / source.target
    with zipfile.ZipFile(joined) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if names != [source.target]:
            raise RuntimeError(f"unexpected archive members for {source.target}: {names}")
        with archive.open(source.target) as input_stream, destination.open("wb") as output:
            shutil.copyfileobj(input_stream, output, length=1024 * 1024)

    expected_crc = int(properties["output_crc32"], 16)
    if destination.stat().st_size != int(properties["output_size"]):
        raise RuntimeError(f"output size mismatch for {source.target}")
    if crc32(destination) != expected_crc:
        raise RuntimeError(f"output CRC mismatch for {source.target}")

    shutil.rmtree(part_dir)
    joined.unlink()
    return {
        "name": source.target,
        "size": destination.stat().st_size,
        "sha256": sha256(destination),
        "crc32": f"{expected_crc:08X}",
        "source": f"{source.commit}/{source.path}",
    }


def zip_data_offset(stream, info: zipfile.ZipInfo) -> int:
    stream.seek(info.header_offset)
    header = stream.read(30)
    if len(header) != 30:
        raise RuntimeError(f"short local header for {info.filename}")
    fields = struct.unpack("<IHHHHHIIIHH", header)
    if fields[0] != 0x04034B50:
        raise RuntimeError(f"invalid local header for {info.filename}")
    return info.header_offset + 30 + fields[-2] + fields[-1]


def build_release(staging: Path, release_dir: Path, records: list[dict[str, object]]) -> None:
    release_dir.mkdir(parents=True, exist_ok=True)
    archive_path = release_dir / "luxury.zip"
    print(f"Building {archive_path}", flush=True)
    with zipfile.ZipFile(
        archive_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        for record in records:
            archive.write(staging / str(record["name"]), arcname=str(record["name"]))

    manifest_files = []
    with archive_path.open("rb") as raw_archive, zipfile.ZipFile(archive_path) as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise RuntimeError(f"release CRC check failed for {bad_member}")
        for info in archive.infolist():
            manifest_files.append({
                "n": info.filename,
                "o": zip_data_offset(raw_archive, info),
                "c": info.compress_size,
                "s": info.file_size,
                "crc": info.CRC,
                "m": info.compress_type,
            })

    archive_sha = sha256(archive_path)
    repair_manifest = {
        "version": "live-russia-757.756.752",
        "archive": "luxury.zip",
        "archive_size": archive_path.stat().st_size,
        "archive_sha256": archive_sha,
        "files": manifest_files,
    }
    (release_dir / "luxury_manifest.json").write_text(
        json.dumps(repair_manifest, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    release = {
        "version": 1,
        "tag": RELEASE_TAG,
        "archive": {
            "url": f"https://github.com/{OWNER}/{TARGET_REPO}/releases/download/{RELEASE_TAG}/luxury.zip",
            "size": archive_path.stat().st_size,
            "sha256": archive_sha,
        },
        "repair_manifest_url": (
            f"https://github.com/{OWNER}/{TARGET_REPO}/releases/download/"
            f"{RELEASE_TAG}/luxury_manifest.json"
        ),
        "files": records,
    }
    (release_dir / "release.json").write_text(
        json.dumps(release, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--build-dir", type=Path)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    build = (args.build_dir or root / "build").resolve()
    staging = build / "staging"
    work = build / "work"
    release_dir = build / RELEASE_TAG
    staging.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    records = [reconstruct(source, staging, work, args.workers) for source in MULTIPART_SOURCES]
    samp_path = staging / ".samp"
    print("Downloading .samp", flush=True)
    download(SAMP_URL, samp_path, SAMP_SIZE, None)
    records.append({
        "name": ".samp",
        "size": samp_path.stat().st_size,
        "sha256": sha256(samp_path),
        "crc32": f"{crc32(samp_path):08X}",
        "source": f"{SAMP_COMMIT}/files/.samp",
    })
    build_release(staging, release_dir, records)
    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(staging, ignore_errors=True)
    print(f"Release ready: {release_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
