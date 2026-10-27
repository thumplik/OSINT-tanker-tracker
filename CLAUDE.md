# CLAUDE.md — Tanker Watch

OSINT system that tracks US military tanker and airlift aircraft from public ADS-B data, detects unusual activity against learned baselines, and drafts X posts for human approval.

**Read `SPEC.md` before any non-trivial change. It is the source of truth.** If code and spec disagree, stop and ask; don't silently pick one.

## Hard rules (never break these)

1. **Nothing posts to X unless `alert.status == "approved"`.** Auto-post is a config flag that defaults to `false`. Do not change that default, and do not add any code path that bypasses the approval check.
2. **Detectors are pure and deterministic.** No network calls, no randomness, no LLM calls inside `detectors/`. Same input yields same output.
3. **No LLM ever produces a number.** LLMs may only reword a template-filled draft. `publisher/validate.py` must confirm every number, place name and callsign in the draft exists in the alert record. If validation fails, the draft is rejected.
4. **All thresholds live in `config/*.yaml`.** No magic numbers in detector code.
5. **Tests never hit live APIs.** Use fixtures in `tests/fixtures/` or replay mode.
6. **Respect provider rate limits** via `ingest/ratelimit.py`. Never loop requests without it.
7. **Posts never claim intent** ("strike", "attack", "deployment for X"). Approved vocabulary is in `config/post_language.yaml`.
8. **Point-in-time correctness.** Any view or computation for a past date uses only data and the baseline snapshot available on that date. Retrospective detector re-runs never overwrite stored alerts and never post. See SPEC §11.
9. **One code path for live and history.** Every query takes a date/range; "live" is `date=today`. Don't build a separate live-only path.
10. **No ML in `detectors/`.** Statistical baselines first. ML stages (SPEC §13) live in `models/`, must beat the current approach on the backtest, and must still give an "expected X, observed Y" explanation. Never tune thresholds against the truth set without showing the sweep.
11. Secrets live in `.env` only (see `.env.example`). Never commit keys, never print them in logs.

## Stack

Python 3.12 · uv · FastAPI · DuckDB over Parquet · h3 v4 · APScheduler · Plotly + Kaleido · pytest · ruff · mypy (strict on `detectors/` and `publisher/`)

Frontend: see `web/README.md` (Workbench widgets or React + MapLibre + deck.gl). Deployed on Vercel.

Hosting split (SPEC §4): the worker runs on an always-on box, never on Vercel. Vercel serves the dashboard and a read API over Postgres + object storage. X credentials exist only on the worker.

## Commands

```bash
uv sync                          # install
uv run pytest                    # all tests
uv run pytest tests/detectors    # detector tests only
uv run ruff check . && uv run ruff format .
uv run mypy src/
uv run tw load-history --days 30 # load historical dumps
uv run tw replay --from 2026-07-01 --to 2026-07-31 --speed 0   # full pipeline, offline
uv run tw backtest --months 6    # writes reports/backtest_<date>.md
uv run tw worker                 # live ingest + detection
uv run tw api                    # FastAPI dev server
```

## Layout

```
src/tw/
  ingest/        provider adapters (ADSBx v2 schema), rate limiter, raw store
  normalize/     filtering by hex block + type family, registry resolution
  tracks/        sortie segmentation, airfield resolution, orbit detection
  baselines/     nightly baseline builder (median/MAD)
  detectors/     D1–D5, guards, composite scoring — PURE
  publisher/     templates, LLM rewording (optional), validator, chart render, X client
  api/           FastAPI routes for the dashboard
  cli.py         `tw` entrypoint
config/          fleet.yaml, regions.yaml, detectors.yaml, known_events.yaml, post_language.yaml
tests/           unit, golden, fixtures
eval/            truth_set.yaml, approvals.parquet
models/          later ML stages (SPEC §13); empty in v1
reports/         backtest output (gitignored except README)
web/             dashboard
```

## How to work in this repo

- **One milestone at a time** (SPEC §14). Start each milestone in plan mode; write the plan into `handoff.md` before coding.
- **Update `handoff.md` at the end of every session**: what's done, what's next, open questions. Keep it under 60 lines and replace stale content instead of appending.
- **Detector changes**: add or adjust a synthetic-data unit test first, then the code. If golden outputs change, explain why in the commit message.
- **Before merging anything in `detectors/` or `publisher/`**: run the adversarial Codex review command and address its findings or note why they don't apply.
- Prefer small, reviewable diffs. Don't refactor unrelated code in the same change.
- When unsure about data semantics (a type code, an airfield, a schema field), check the fixture data or ask. Don't guess.

## Domain notes

- US military ICAO hex block: `AE0000–AFFFFF`. Type families are defined in `config/fleet.yaml`.
- `position_source` matters: MLAT and Mode-S-only points are lower quality. Never treat a missing position as "on the ground".
- Count changes are often caused by **receiver coverage** changes, not aircraft. The coverage guard (SPEC §8) exists for this; never disable it to make a test pass.
- Storage is partitioned by UTC date. Never scan all partitions to answer a single-day question.
- Regions have a status (`watch` | `alerting`) in `config/regions.yaml`. Only `alerting` regions feed the post queue. Never promote a region in code.
- Times are UTC everywhere, including storage, API and charts. Convert only in the UI.

## Definition of done (any task)

Tests pass · ruff and mypy clean · SPEC updated if behavior changed · `handoff.md` updated.
