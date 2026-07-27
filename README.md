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

Release assets:

- `luxury.zip` — 1,387,227,329 bytes.
- `luxury_manifest.json` — per-file range repair manifest.
- `release.json` — source and SHA-256 metadata.

The Android launcher downloads these files from release tag `v1`. Do not
replace them with `.custom3*`, `.data`, or `.samp` files from `havanarp-cdn`;
those belong to a different launcher data layout and do not contain the full
clothing/player databases expected by this build.

Verify the public release and critical clothing files without downloading the
whole archive:

```bash
python3 scripts/verify_release.py
```

