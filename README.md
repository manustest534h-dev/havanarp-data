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
`SAMP/peds.ide`. The 62 HavanaRP CDN models (IDs `16852`–`16914`, excluding
`16899`) and their PVR, DXT, and ETC textures are merged into the launcher's
existing `texdb/samp` model and texture databases alongside the 22 native
models. This keeps the base `gta3.img` untouched and avoids loading a second
IMG from `gta.dat`, which crashes the mobile game during startup. The builder
checks all 84 model links and the texture references embedded in the migrated
DFFs.

Release assets:

- `luxury.zip` — 1,430,065,220 bytes.
- `luxury_manifest.json` — per-file range repair manifest.
- `release.json` — source and SHA-256 metadata.

The Android launcher downloads these files from release tag `v1`. The legacy
`.custom3*` and `.data` packages are conversion inputs only: the builder
extracts their skin-specific IMG, IDE, and texture slices into the expanded
`LuxuryMobile/` layout expected by the current launcher.

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

The patcher downloads and verifies the HavanaRP CDN v763 multipart inputs,
repairs the malformed pedestrian row, merges the models and texture catalogs
into the existing launcher-supported `samp` databases, removes the unsafe
standalone `custom3` loader, mirrors the corrected IDE to both paths, validates
all 84 custom skin models, and regenerates the range manifest and release
metadata. Pass `--cdn-source-dir` to reuse locally downloaded `custom3.zip`,
`custom3_dxt.zip`, `custom3_etc.zip`, `custom3_pvr.zip`, and `data.zip` files.
