from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from cdn_skin_migration import (  # noqa: E402
    CONTAINER_MAGIC,
    container_payloads,
    dff_texture_names,
    parse_texture_catalog,
    select_pedestrian_rows,
    texture_groups,
)
from patch_skin_release import add_cdn_pedestrians  # noqa: E402
from skin_linkage import CDN_SKIN_IDS, parse_ped_definitions  # noqa: E402


def container_record(payload: bytes, name_size: int = 4) -> bytes:
    header = bytearray(30)
    header[:4] = CONTAINER_MAGIC
    struct.pack_into("<I", header, 18, len(payload))
    struct.pack_into("<I", header, 22, len(payload))
    struct.pack_into("<I", header, 26, name_size)
    return bytes(header) + (b"x" * name_size) + payload


def img_index(*names: str) -> bytes:
    payload = bytearray(b"VER2" + struct.pack("<I", len(names)))
    for index, name in enumerate(names, 1):
        payload.extend(
            struct.pack("<IHH24s", index, 1, 0, name.encode().ljust(24, b"\0"))
        )
    return bytes(payload)


def ped_row(model_id: int, model: str, broken: bool = False) -> bytes:
    middle = "man,2,0 PED_TYPE_GEN" if broken else "man,2,0,PED_TYPE_GEN"
    return (
        f"{model_id},{model},{model},CIVMALE,STAT,man,0,0,{middle},VOICE,VOICE\n"
    ).encode()


def rw_chunk(kind: int, payload: bytes) -> bytes:
    return struct.pack("<III", kind, len(payload), 0x1803FFFF) + payload


def rw_texture(name: str) -> bytes:
    encoded = name.encode() + b"\0"
    encoded += b"\0" * (-len(encoded) % 4)
    return rw_chunk(
        6,
        rw_chunk(1, b"\x06\x01\x01\0")
        + rw_chunk(2, encoded)
        + rw_chunk(2, b"\0\0\0\0"),
    )


class CdnSkinMigrationTests(unittest.TestCase):
    def test_extracts_nonempty_container_records(self) -> None:
        payload = container_record(b"") + container_record(b"one") + container_record(b"two")
        self.assertEqual(container_payloads(payload), [b"one", b"two"])

    def test_selects_only_the_linked_cdn_pedestrian_section(self) -> None:
        models = {model_id: f"skin{model_id}" for model_id in CDN_SKIN_IDS}
        rows = b"peds\n" + b"".join(
            ped_row(model_id, model) for model_id, model in sorted(models.items())
        ) + b"end\n"
        unrelated = b"peds\n" + ped_row(9999, "missing") + b"end\n"
        data = container_record(unrelated) + container_record(rows)
        names = {f"{model}.dff" for model in models.values()}

        selected = select_pedestrian_rows(data, names)

        self.assertEqual(set(parse_ped_definitions(selected)), set(CDN_SKIN_IDS))

    def test_groups_each_texture_database_quartet(self) -> None:
        payload = b"".join(
            container_record(item)
            for item in (
                b"dat0",
                b"tmb0",
                b"toc0",
                b"cat=0\n\"texture0\"\n",
                b"dat1",
                b"tmb1",
                b"toc1",
                b"cat=0\n\"texture1\"\n",
            )
        )

        groups = texture_groups(payload, 2)

        self.assertEqual(groups[1]["dat"], b"dat1")
        self.assertEqual(groups[1]["txt"], b"cat=0\n\"texture1\"\n")

    def test_reads_renderware_texture_references(self) -> None:
        payload = b"prefix" + rw_texture("Skin_Face") + rw_texture("Skin_Body")

        self.assertEqual(dff_texture_names(payload), {"skin_face", "skin_body"})

    def test_reads_mobile_texture_catalog(self) -> None:
        payload = (
            b"cat=0 name=Default\n"
            b'"Skin_Face" width=256 height=256 img=1234\n'
            b'"Skin_Body" "affiliate=shared_body"\n'
        )

        self.assertEqual(parse_texture_catalog(payload), {"skin_face", "skin_body"})

    def test_adds_cdn_rows_before_end_and_repairs_base_row(self) -> None:
        base = b"peds\n" + ped_row(51, "BMYMOUN", broken=True) + b"end\n"
        incoming = b"".join(
            ped_row(model_id, f"skin{model_id}") for model_id in sorted(CDN_SKIN_IDS)
        )

        patched = add_cdn_pedestrians(base, incoming)
        parsed = parse_ped_definitions(patched)

        self.assertEqual(set(parsed), {51, *CDN_SKIN_IDS})
        self.assertLess(patched.index(b"16852,"), patched.rindex(b"end"))


if __name__ == "__main__":
    unittest.main()
