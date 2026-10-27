# Fixtures

Synthetic data only; no live API is ever called from tests.

- `globe_history_2026-07-01/`: readsb-style `trace_full_*.json` files (one gzip-compressed).
  `ae1234` K35R crosses midnight UTC; `ae9999` has no type; `ae7777` is military but untracked;
  `a12345` is civil and only counts toward coverage.
- `v2_mil_2026-07-01T0100Z.json`: ADSBx v2 response with an MLAT point, a Mode-S-only point
  (no position), a `~` non-ICAO track and an on-ground point.
