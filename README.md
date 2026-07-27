# HavanaRP Data

This repository publishes the complete launcher-native HavanaRP data pack.
Release `v1` mirrors the verified base assets from
`manustest534h-dev/havana-launcher` and is intentionally different from the
legacy `havanarp-cdn` multipart format.

The release installs the full `LuxuryMobile/` tree, including:

- `data/clothes.dat` and `data/peds.ide`.
- `texdb/player` and `texdb/playerhi` texture databases.
- GTA, SAMP, menu, mobile, and TXD texture databases.
- 416 repairable files listed with ZIP byte ranges and CRC values.

Custom server skins are defined identically in both `data/peds.ide` and
`SAMP/peds.ide`. Every custom skin ID is validated against its `.dff` model in
`texdb/samp.img`, and the SAMP texture databases for PVR, DXT, ETC, and
uncompressed devices are required. Keeping both IDE paths identical prevents
the launcher hook and the base game loader from selecting different skin maps.

Release assets:

- `luxury.zip` — 1,387,227,370 bytes.
- `luxury_manifest.json` — per-file range repair manifest.
- `release.json` — source and SHA-256 metadata.

The Android launcher downloads these files from release tag `v1`. Do not
replace them with `.custom3*`, `.data`, or `.samp` files from `havanarp-cdn`;
those belong to a different launcher data layout and do not contain the full
clothing/player databases expected by this build.

Verify the public release and custom skin linkage without downloading the whole
archive:

```bash
python3 scripts/verify_release.py
```

To rebuild the repaired release from a downloaded `v1` archive:

```bash
python3 scripts/patch_skin_release.py \
  --archive /path/to/luxury.zip \
  --output-dir build/v1
```

The patcher repairs the malformed pedestrian row, mirrors the corrected IDE to
both loader paths, validates all 22 custom skin models, and regenerates the
range manifest and release metadata.
