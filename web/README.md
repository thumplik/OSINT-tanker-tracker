# web/

Dashboard (milestone M5). Planned stack: React + MapLibre + deck.gl `H3HexagonLayer`, deployed on
Vercel, reading the read API over Postgres + object storage. The ingest worker never runs here.

Every view encodes its state in the URL (`?date=2026-06-14&t=1430Z&family=tanker&region=europe`)
and shows UTC; local time conversion happens only in the UI.
