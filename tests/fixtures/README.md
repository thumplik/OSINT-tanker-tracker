# Fixtures

No live API is ever called from tests.

- `globe_history_2026-07-01/` (synthetic): readsb-style `trace_full_*.json` files.
  `ae1234` K35R is gzip-compressed and has a geometric-altitude row (`flags & 8`) and a null
  altitude; `ae9999` has no type; `ae7777` is military but untracked; `a12345` is civil and only
  counts toward coverage.
- `real_2026-10-25/` (real): six trace files copied unmodified from the adsb.lol release
  `v2026.10.25-planes-readsb-prod-0`: C-17 `ae07f0`, MLAT-only C-130J `ae29da`, C-130J `ae5df0`,
  UH-60 `ae4ff0` (untracked), civil `a99d05` and non-ICAO `~2b7bf0`. Data © adsb.lol
  contributors, licensed under the [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
- `v2_mil_2026-07-01T0100Z.json`: ADSBx v2 response with an MLAT point, a Mode-S-only point
  (no position), a `~` non-ICAO track and an on-ground point.
