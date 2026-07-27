#!/usr/bin/env python3
"""Convert the HavanaRP CDN skin bundle to launcher-native files."""

from __future__ import annotations

import hashlib
import re
import struct
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from skin_linkage import (
    CDN_SKIN_IDS,
    SkinLinkageError,
    parse_img_index,
    parse_ped_definitions,
    validate_skin_links,
)


CDN_VERSION = "763"
CDN_BASE_URL = (
    "https://raw.githubusercontent.com/manustest534h-dev/"
    f"havanarp-cdn/main/files/{CDN_VERSION}/multipart/{CDN_VERSION}"
)
CDN_PACKAGES = {
    "custom3": ".custom3",
    "custom3_dxt": ".custom3_dxt",
    "custom3_etc": ".custom3_etc",
    "custom3_pvr": ".custom3_pvr",
    "data": ".data",
}
CONTAINER_MAGIC = bytes.fromhex("b8d45c1f")
RW_TEXTURE_CHUNK = b"\x06\x00\x00\x00"
# These references are already unresolved in the signed v763 CDN bundle. One
# comes from the base player database; the other three are unused material slots.
KNOWN_LEGACY_TEXTURE_REFERENCES = frozenset(
    {
        "air95_lp",
        "grinch_telo",
        "material__640_2d_view",
        "sneakerbincblk",
    }
)


@dataclass(frozen=True)
class CdnSkinAssets:
    model_archive: bytes
    pedestrian_rows: bytes
    textures: dict[str, bytes]


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def parse_properties(payload: bytes) -> dict[str, str]:
    properties: dict[str, str] = {}
    for raw_line in payload.decode("ascii").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            raise RuntimeError(f"invalid CDN manifest row: {line}")
        properties[key] = value
    return properties


def fetch(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "HavanaRP-CDN-skin-migrator/1"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.read()


def download_package(name: str, cache_dir: Path) -> Path:
    """Download and verify one legacy multipart ZIP, reusing a valid cache."""

    manifest = parse_properties(fetch(f"{CDN_BASE_URL}/{name}/manifest.properties"))
    expected_digest = manifest["joined_sha256"]
    expected_size = int(manifest["joined_size"])
    output = cache_dir / f"{name}.zip"
    cache_dir.mkdir(parents=True, exist_ok=True)
    if (
        output.is_file()
        and output.stat().st_size == expected_size
        and hashlib.sha256(output.read_bytes()).hexdigest() == expected_digest
    ):
        return output

    digest = hashlib.sha256()
    temporary = output.with_suffix(".zip.part")
    with temporary.open("wb") as stream:
        for index in range(int(manifest["parts"])):
            key = f"part_{index:03d}"
            part = fetch(f"{CDN_BASE_URL}/{name}/part-{index:03d}")
            if len(part) != int(manifest[f"{key}_size"]):
                raise RuntimeError(f"CDN size mismatch: {name}/{key}")
            if sha256_bytes(part) != manifest[f"{key}_sha256"]:
                raise RuntimeError(f"CDN SHA-256 mismatch: {name}/{key}")
            stream.write(part)
            digest.update(part)
    if temporary.stat().st_size != expected_size or digest.hexdigest() != expected_digest:
        raise RuntimeError(f"joined CDN package mismatch: {name}")
    temporary.replace(output)
    return output


def read_inner_package(path: Path, expected_name: str) -> bytes:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if names != [expected_name]:
            raise RuntimeError(f"unexpected CDN package contents: {path.name}")
        return archive.read(expected_name)


def container_payloads(container: bytes) -> list[bytes]:
    """Return non-empty files from the legacy container format."""

    payloads: list[bytes] = []
    offset = 0
    while True:
        offset = container.find(CONTAINER_MAGIC, offset)
        if offset < 0:
            break
        if offset + 30 <= len(container):
            size = struct.unpack_from("<I", container, offset + 18)[0]
            repeated_size = struct.unpack_from("<I", container, offset + 22)[0]
            name_size = struct.unpack_from("<I", container, offset + 26)[0]
            start = offset + 30 + name_size
            end = start + size
            if (
                size == repeated_size
                and name_size < 512
                and end <= len(container)
                and size > 0
            ):
                payloads.append(container[start:end])
        offset += 1
    return payloads


def img_payloads(container: bytes) -> list[bytes]:
    images = []
    for payload in container_payloads(container):
        if payload.startswith(b"VER2"):
            required = 8 + (struct.unpack_from("<I", payload, 4)[0] * 32)
            parse_img_index(payload[:required])
            images.append(payload)
    return images


def select_pedestrian_rows(data_container: bytes, img_names: set[str]) -> bytes:
    """Select the CDN peds section whose models belong to the skin IMG."""

    candidates: list[tuple[dict[int, str], bytes]] = []
    for payload in container_payloads(data_container):
        if not payload.startswith((b"peds\n", b"peds\r\n")):
            continue
        try:
            pedestrians = parse_ped_definitions(payload)
        except SkinLinkageError:
            continue
        linked = {
            model_id: model
            for model_id, model in pedestrians.items()
            if f"{model}.dff".lower() in img_names
        }
        if linked:
            candidates.append((linked, payload))
    if not candidates:
        raise RuntimeError("CDN data does not contain linked skin definitions")

    linked, payload = max(candidates, key=lambda candidate: len(candidate[0]))
    validate_skin_links(linked, img_names, CDN_SKIN_IDS)
    rows = []
    for line in payload.splitlines():
        first = line.split(b",", 1)[0].strip()
        if first.isdigit() and int(first) in CDN_SKIN_IDS:
            rows.append(line)
    output = b"\n".join(rows) + b"\n"
    if set(parse_ped_definitions(output)) != set(CDN_SKIN_IDS):
        raise RuntimeError("unexpected CDN skin definition set")
    return output


def texture_groups(container: bytes, expected_count: int) -> list[dict[str, bytes]]:
    payloads = container_payloads(container)
    if len(payloads) != expected_count * 4:
        raise RuntimeError(
            f"unexpected CDN texture payload count: {len(payloads)}"
        )
    groups = []
    for index in range(expected_count):
        dat, tmb, toc, text = payloads[index * 4 : index * 4 + 4]
        if not text.startswith(b"cat="):
            raise RuntimeError(f"invalid CDN texture group: {index}")
        groups.append({"dat": dat, "tmb": tmb, "toc": toc, "txt": text})
    return groups


def parse_texture_catalog(payload: bytes) -> set[str]:
    """Return texture names declared by a mobile texdb metadata file."""

    names: set[str] = set()
    for line in payload.splitlines():
        match = re.match(br'"([^"\r\n]+)"', line.strip())
        if not match:
            continue
        try:
            names.add(match.group(1).decode("ascii").lower())
        except UnicodeDecodeError as error:
            raise RuntimeError("non-ASCII texture name in CDN metadata") from error
    if not names:
        raise RuntimeError("CDN texture metadata does not declare any textures")
    return names


def dff_texture_names(payload: bytes) -> set[str]:
    """Read texture-name string chunks from a RenderWare DFF model."""

    names: set[str] = set()
    offset = -1
    while True:
        offset = payload.find(RW_TEXTURE_CHUNK, offset + 1)
        if offset < 0:
            break
        if offset + 36 > len(payload):
            continue
        _, texture_size, texture_version = struct.unpack_from("<III", payload, offset)
        texture_end = offset + 12 + texture_size
        if not texture_version or texture_end > len(payload):
            continue

        struct_offset = offset + 12
        struct_type, struct_size, _ = struct.unpack_from("<III", payload, struct_offset)
        if struct_type != 1 or struct_size < 4:
            continue
        string_offset = struct_offset + 12 + struct_size
        if string_offset + 12 > texture_end:
            continue
        string_type, string_size, _ = struct.unpack_from("<III", payload, string_offset)
        if string_type != 2 or not 0 < string_size <= 128:
            continue
        string_end = string_offset + 12 + string_size
        if string_end > texture_end:
            continue
        raw_name = payload[string_offset + 12 : string_end].split(b"\0", 1)[0]
        try:
            name = raw_name.decode("ascii").lower()
        except UnicodeDecodeError:
            continue
        if name:
            names.add(name)
    return names


def img_model_payloads(payload: bytes) -> dict[str, bytes]:
    """Return DFF payloads from a complete GTA IMG v2 archive."""

    index_size = 8 + (struct.unpack_from("<I", payload, 4)[0] * 32)
    names = parse_img_index(payload[:index_size])
    models: dict[str, bytes] = {}
    for index in range(struct.unpack_from("<I", payload, 4)[0]):
        offset = 8 + (index * 32)
        sector, stream_sectors, archive_sectors = struct.unpack_from(
            "<IHH", payload, offset
        )
        raw_name = payload[offset + 8 : offset + 32].split(b"\0", 1)[0]
        name = raw_name.decode("ascii").lower()
        if not name.endswith(".dff"):
            continue
        sectors = archive_sectors or stream_sectors
        start = sector * 2048
        end = start + (sectors * 2048)
        if name not in names or not sectors or end > len(payload):
            raise RuntimeError(f"invalid CDN model payload: {name}")
        models[name] = payload[start:end]
    return models


def validate_texture_links(model_archive: bytes, metadata: bytes) -> int:
    """Ensure the selected texdb matches texture references in its DFF files."""

    catalog = parse_texture_catalog(metadata)
    referenced: set[str] = set()
    for payload in img_model_payloads(model_archive).values():
        referenced.update(dff_texture_names(payload))
    if not referenced:
        raise RuntimeError("CDN skin models do not contain texture references")
    unresolved = referenced.difference(catalog)
    if unresolved != KNOWN_LEGACY_TEXTURE_REFERENCES:
        names = ", ".join(sorted(unresolved)) or "none"
        raise RuntimeError(f"unexpected CDN texture references: {names}")
    return len(referenced)


def load_cdn_skin_assets(source_dir: Path | None, cache_dir: Path) -> CdnSkinAssets:
    packages: dict[str, bytes] = {}
    for name, inner_name in CDN_PACKAGES.items():
        path = (
            source_dir / f"{name}.zip"
            if source_dir is not None
            else download_package(name, cache_dir)
        )
        packages[name] = read_inner_package(path, inner_name)

    images = img_payloads(packages["custom3"])
    skin_index = None
    for index, image in enumerate(images):
        index_size = 8 + (struct.unpack_from("<I", image, 4)[0] * 32)
        try:
            rows = select_pedestrian_rows(
                packages["data"], parse_img_index(image[:index_size])
            )
        except (RuntimeError, SkinLinkageError):
            continue
        if set(parse_ped_definitions(rows)) == set(CDN_SKIN_IDS):
            skin_index = index
            break
    if skin_index is None:
        raise RuntimeError("could not locate the CDN skin model archive")

    model_archive = images[skin_index]
    index_size = 8 + (struct.unpack_from("<I", model_archive, 4)[0] * 32)
    img_names = parse_img_index(model_archive[:index_size])
    pedestrian_rows = select_pedestrian_rows(packages["data"], img_names)
    textures: dict[str, bytes] = {}
    common_text: bytes | None = None
    for format_name in ("dxt", "etc", "pvr"):
        groups = texture_groups(packages[f"custom3_{format_name}"], len(images))
        group = groups[skin_index]
        for extension in ("dat", "tmb", "toc"):
            textures[f"custom3.{format_name}.{extension}"] = group[extension]
        if common_text is None:
            common_text = group["txt"]
        elif common_text != group["txt"]:
            raise RuntimeError("CDN texture metadata differs by GPU format")
    if common_text is None:
        raise RuntimeError("CDN texture metadata is missing")
    textures["custom3.txt"] = common_text
    validate_texture_links(model_archive, common_text)
    return CdnSkinAssets(model_archive, pedestrian_rows, textures)
