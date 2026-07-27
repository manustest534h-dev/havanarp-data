from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from skin_linkage import (  # noqa: E402
    SkinLinkageError,
    parse_img_index,
    parse_ped_definitions,
    repair_ped_definitions,
    validate_skin_links,
)


def ped_row(model_id: int, model: str, *, broken: bool = False) -> bytes:
    middle = "man,2,0 PED_TYPE_GEN" if broken else "man,2,0,PED_TYPE_GEN"
    return (
        f"{model_id},{model},{model},CIVMALE,STAT,man,0,0,{middle},VOICE,VOICE\n"
    ).encode()


def img_index(*names: str) -> bytes:
    payload = bytearray(b"VER2" + struct.pack("<I", len(names)))
    for index, name in enumerate(names, 1):
        encoded = name.encode().ljust(24, b"\0")
        payload.extend(struct.pack("<IHH24s", index, 1, 0, encoded))
    return bytes(payload)


class SkinLinkageTests(unittest.TestCase):
    def test_repairs_known_pedestrian_row(self) -> None:
        repaired = repair_ped_definitions(ped_row(51, "BMYMOUN", broken=True))
        self.assertEqual(parse_ped_definitions(repaired), {51: "BMYMOUN"})

    def test_rejects_other_malformed_pedestrian_rows(self) -> None:
        with self.assertRaises(SkinLinkageError):
            parse_ped_definitions(b"793,araby,araby\n")

    def test_links_custom_skin_to_img_entry(self) -> None:
        pedestrians = parse_ped_definitions(ped_row(793, "araby"))
        names = parse_img_index(img_index("araby.dff"))
        self.assertEqual(
            validate_skin_links(pedestrians, names, expected_ids={793}),
            {793: "araby"},
        )

    def test_rejects_missing_img_entry(self) -> None:
        pedestrians = parse_ped_definitions(ped_row(793, "araby"))
        with self.assertRaises(SkinLinkageError):
            validate_skin_links(pedestrians, set(), expected_ids={793})


if __name__ == "__main__":
    unittest.main()
