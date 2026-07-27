# HavanaRP Data

This repository publishes the complete launcher-native HavanaRP data pack.
Release `v1` mirrors the verified base assets from
`manustest534h-dev/havana-launcher` and is intentionally different from the
legacy `havanarp-cdn` multipart format.

The release installs the full `LuxuryMobile/` tree, including:

- `data/clothes.dat` and `data/peds.ide`.
- `texdb/player` and `texdb/playerhi` texture databases.
- GTA, SAMP, menu, mobile, and TXD texture databases.
- 427 repairable files listed with ZIP byte ranges and CRC values.

Custom server skins are defined identically in both `data/peds.ide` and
`SAMP/peds.ide`. The 22 launcher-native models remain in `texdb/samp.img`; the
62 HavanaRP CDN models (IDs `16852`–`16914`, excluding `16899`) are migrated to
`texdb/custom3.img` with their matching PVR, DXT, and ETC texture databases.
Both `gta.dat` loader paths explicitly load the new archive. The builder checks
all 84 model links and the texture references embedded in the migrated DFFs.

Release assets:

- `luxury.zip` — 1,435,793,981 bytes.
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
repairs the malformed pedestrian row, mirrors the corrected IDE to both loader
paths, validates all 84 custom skin models, and regenerates the range manifest
and release metadata. Pass `--cdn-source-dir` to reuse locally downloaded
`custom3.zip`, `custom3_dxt.zip`, `custom3_etc.zip`, `custom3_pvr.zip`, and
`data.zip` files.
