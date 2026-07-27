# HavanaRP Data

This package definition builds the complete HavanaRP mobile data pack from
immutable assets already published in `manustest534h-dev/havanarp-cdn`.

The selected pack combines:

- Live Russia texture variants from data version 752.
- The mobile draw-distance fix from data version 756.
- The corrected Live Russia collision archive from data version 757.
- The `.samp` archive pinned to CDN commit `da82921948fce2b1623f6e2e2a4bacb4e61c0d21`.

Run:

```bash
python3 scripts/build_pack.py
```

The command verifies every source part, reconstructs the six runtime archives,
and writes these release assets to `build/v1/`:

- `luxury.zip` — full data archive extracted by the Android launcher.
- `luxury_manifest.json` — per-entry range manifest used by HavanaRepair.
- `release.json` — version, size, and SHA-256 metadata.

Publish all three files in a GitHub release tagged `v1`. The launcher expects
the release at `manustest534h-dev/havanarp-data`.
