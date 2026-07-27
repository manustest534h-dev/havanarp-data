#!/usr/bin/env python3
"""Validate the launcher-native pedestrian model linkage."""

from __future__ import annotations

import struct
from collections.abc import Iterable


NATIVE_SKIN_IDS = frozenset(
    {
        793,
        794,
        795,
        796,
        797,
        798,
        799,
        907,
        908,
        909,
        965,
        999,
        1194,
        1195,
        1196,
        1197,
        1198,
        1199,
        1200,
        1201,
        1202,
        1203,
    }
)

CDN_SKIN_IDS = frozenset(
    {
        *range(16852, 16899),
        *range(16900, 16915),
    }
)

CUSTOM_SKIN_IDS = NATIVE_SKIN_IDS | CDN_SKIN_IDS

BROKEN_PED_ROW = b"man,2,0 PED_TYPE_GEN"
FIXED_PED_ROW = b"man,2,0,PED_TYPE_GEN"


class SkinLinkageError(RuntimeError):
    """Raised when skin definitions do not match the model archive."""


def repair_ped_definitions(payload: bytes) -> bytes:
    """Repair the known malformed ID 51 row without changing other bytes."""

    broken_count = payload.count(BROKEN_PED_ROW)
    fixed_count = payload.count(FIXED_PED_ROW)
    if broken_count == 1 and fixed_count == 0:
        return payload.replace(BROKEN_PED_ROW, FIXED_PED_ROW, 1)
    if broken_count == 0 and fixed_count == 1:
        return payload
    raise SkinLinkageError(
        "expected exactly one broken or repaired ID 51 pedestrian row"
    )


def parse_ped_definitions(payload: bytes) -> dict[int, str]:
    """Return model names by ID and reject malformed or duplicate rows."""

    records: dict[int, str] = {}
    text = payload.decode("latin-1")
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.split("#", 1)[0].strip()
        if not line or "," not in line:
            continue
        fields = [field.strip() for field in line.split(",")]
        if not fields[0].isdigit():
            continue
        if len(fields) != 14:
            raise SkinLinkageError(
                f"malformed pedestrian row at line {line_number}: "
                f"expected 14 fields, got {len(fields)}"
            )
        model_id = int(fields[0])
        model_name = fields[1]
        if not model_name:
            raise SkinLinkageError(f"empty model name for pedestrian {model_id}")
        if model_id in records:
            raise SkinLinkageError(f"duplicate pedestrian ID: {model_id}")
        records[model_id] = model_name
    return records


def img_index_size(prefix: bytes) -> int:
    """Return the byte count needed for a complete GTA IMG v2 index."""

    if len(prefix) < 8 or prefix[:4] != b"VER2":
        raise SkinLinkageError("invalid GTA IMG v2 header")
    entry_count = struct.unpack_from("<I", prefix, 4)[0]
    if entry_count <= 0 or entry_count > 1_000_000:
        raise SkinLinkageError(f"invalid GTA IMG entry count: {entry_count}")
    return 8 + (entry_count * 32)


def parse_img_index(prefix: bytes) -> set[str]:
    """Parse lower-case entry names from a complete GTA IMG v2 index."""

    required = img_index_size(prefix)
    if len(prefix) < required:
        raise SkinLinkageError(
            f"short GTA IMG index: expected {required}, got {len(prefix)}"
        )
    names: set[str] = set()
    entry_count = struct.unpack_from("<I", prefix, 4)[0]
    for index in range(entry_count):
        offset = 8 + (index * 32)
        sector, stream_size, archive_size = struct.unpack_from("<IHH", prefix, offset)
        raw_name = prefix[offset + 8 : offset + 32].split(b"\0", 1)[0]
        try:
            name = raw_name.decode("ascii").lower()
        except UnicodeDecodeError as error:
            raise SkinLinkageError(
                f"non-ASCII GTA IMG name at index {index}"
            ) from error
        if not name or sector == 0 or (stream_size == 0 and archive_size == 0):
            raise SkinLinkageError(f"invalid GTA IMG entry at index {index}")
        names.add(name)
    return names


def merge_img_archives(base: bytes, addon: bytes) -> bytes:
    """Merge two GTA IMG v2 archives into one self-contained archive."""

    def entries(payload: bytes) -> list[tuple[int, int, bytes, bytes]]:
        required = img_index_size(payload)
        if len(payload) % 2048:
            raise SkinLinkageError("GTA IMG size is not sector aligned")
        records: list[tuple[int, int, bytes, bytes]] = []
        entry_count = struct.unpack_from("<I", payload, 4)[0]
        for index in range(entry_count):
            offset = 8 + (index * 32)
            sector, stream_size, archive_size = struct.unpack_from(
                "<IHH", payload, offset
            )
            raw_name = payload[offset + 8 : offset + 32]
            sectors = archive_size or stream_size
            start = sector * 2048
            end = start + (sectors * 2048)
            if not sectors or start < required or end > len(payload):
                raise SkinLinkageError(f"invalid GTA IMG payload at index {index}")
            records.append(
                (stream_size, archive_size, raw_name, payload[start:end])
            )
        return records

    base_names = parse_img_index(base)
    addon_names = parse_img_index(addon)
    overlap = base_names.intersection(addon_names)
    if overlap == addon_names:
        return base
    if overlap:
        raise SkinLinkageError(
            "GTA IMG archives contain a partial duplicate model set: "
            + ", ".join(sorted(overlap))
        )

    records = entries(base) + entries(addon)
    index_size = 8 + (len(records) * 32)
    first_sector = (index_size + 2047) // 2048
    output = bytearray(first_sector * 2048)
    output[:8] = b"VER2" + struct.pack("<I", len(records))
    sector = first_sector
    for index, (stream_size, archive_size, raw_name, payload) in enumerate(records):
        offset = 8 + (index * 32)
        output[offset : offset + 32] = struct.pack(
            "<IHH24s", sector, stream_size, archive_size, raw_name
        )
        output.extend(payload)
        sector += len(payload) // 2048

    merged = bytes(output)
    if parse_img_index(merged) != base_names | addon_names:
        raise SkinLinkageError("merged GTA IMG index mismatch")
    return merged


def validate_skin_links(
    pedestrian_models: dict[int, str],
    img_names: set[str],
    expected_ids: Iterable[int] = CUSTOM_SKIN_IDS,
) -> dict[int, str]:
    """Ensure every expected skin ID resolves to a DFF in samp.img."""

    linked: dict[int, str] = {}
    for model_id in sorted(expected_ids):
        model_name = pedestrian_models.get(model_id)
        if model_name is None:
            raise SkinLinkageError(f"missing custom skin definition: {model_id}")
        dff_name = f"{model_name}.dff".lower()
        if dff_name not in img_names:
            raise SkinLinkageError(
                f"custom skin {model_id} points to missing model: {dff_name}"
            )
        linked[model_id] = model_name
    return linked
