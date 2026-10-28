# web/

Dashboard (M5): a static React site that reads per-day JSON files written by `tw export`
(SPEC §4, §10, §11). No server code; the worker never runs here.

- Stack: Vite + React + TypeScript, MapLibre GL (OpenFreeMap basemap) + deck.gl `H3HexagonLayer`.
- Data: `VITE_DATA_BASE_URL` (default `/data`) holds `index.json`, `meta.json`,
  `days/<D>.json` and `days/<D>.r6.json`.
- Every view keeps its state in the URL (`?date=2026-10-27&family=tanker&region=europe&res=4&hour=14`).
  All times are UTC.

```bash
# from the repo root: export some days locally, then run the site
uv run tw export --from 2026-10-20 --to 2026-10-28 --dest local:web/public/data
cd web && npm install && npm run dev
npm test && npm run build
```

Deployed on Vercel from this folder (root directory `web/`), with `VITE_DATA_BASE_URL` set to
the Blob store's public URL.
