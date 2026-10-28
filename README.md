# OSINT Tanker Tracker

[![CI](https://github.com/thumplik/OSINT-tanker-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/thumplik/OSINT-tanker-tracker/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)

**Tanker Watch** tracks US military aerial-refueling tankers and airlifters using public ADS-B data.
It learns what normal activity looks like for each region, flags activity that is statistically
unusual, and drafts short posts for a human to review before anything is published.

It reports **what** is unusual, never **why**.

> 🚧 **Early development.** M0–M3 are done: a year of history, sorties with airfields, baselines, the
> D1 and D2 detectors and a backtest report. The M4 live worker is built and on its 72-hour trial.
> The M5 dashboard (map, alerts, regions, sorties, any past day) is built; deployment is next.
> No posting yet. See the [roadmap](#roadmap).

---

## Why

Tanker and airlift traffic often changes before major military activity becomes public news.
That traffic is already broadcast openly over ADS-B and collected by community receiver networks,
but raw flight maps are noisy: receivers go offline, exercises happen every week, and one extra
C-17 means nothing. This project tries to separate real changes from noise using plain statistics.
The goal is that every alert can be explained in one sentence:

> 14 tanker sorties over the Eastern Med in 24h vs a typical 5. Coverage normal.

## How it works

```
providers ──▶ ingest ──▶ normalize + filter ──▶ points (Parquet, partitioned by UTC date)
                                   │
                                   ▼
                          sorties (origin, destination, path, H3 cells)
                                   │
                                   ▼
               baselines (median/MAD, nightly, stored as dated snapshots)
                                   │
                                   ▼
             detectors ──▶ alerts ──▶ human approval ──▶ X post with chart
                                   │
                                   ▼
                           API ──▶ dashboard (live + any past day)
```

- **Scope.** Aircraft with an ICAO address in the US military block `AE0000–AFFFFF` and a type in a
  tracked family: tankers (KC-135, KC-46) and strategic or tactical airlift (C-17, C-5M,
  C-130). The fleet is defined in [`config/fleet.yaml`](config/fleet.yaml).
- **Baselines.** A median and MAD per region × family × weekday over a trailing 56-day window,
  rebuilt nightly and saved as dated snapshots.
- **Detectors** (D1–D5): volume surge, novel destination, unusual orbit, airlift convergence and
  route shift. Every detector is a pure, deterministic function. No ML, no LLM, no network.
- **Guards.** A coverage guard suppresses alerts when receiver coverage in the area changed, using
  all aircraft (civil included) as the baseline. There is also an exercise calendar and a cooldown.
- **Human in the loop.** Nothing is posted unless an alert has been explicitly approved.
  Auto-posting is off by default.
- **Time travel.** You can view any past day exactly as the system saw it then, using that day's
  baseline snapshot, so there is no hindsight bias.

The full design is in [`SPEC.md`](SPEC.md).

## Principles

1. **Describe, don't speculate.** Posts say "unusual" or "above normal", never "strike" or
   "deployment". The approved vocabulary is in [`config/post_language.yaml`](config/post_language.yaml).
2. **No LLM produces a number.** Drafts come from templates. A validator checks that every number,
   place and callsign in a draft exists in the alert record.
3. **Explainable over clever.** Statistical baselines come first. Any later ML stage has to beat
   them on the backtest and still explain itself as "expected X, observed Y".
4. **Aggregates, not surveillance.** No tracking of crews or individuals, and no raw feeds are
   republished.
5. **Every threshold lives in config.** See [`config/detectors.yaml`](config/detectors.yaml).

## Quick start

Requires [uv](https://docs.astral.sh/uv/). uv installs Python 3.12 if needed.

```bash
git clone https://github.com/thumplik/OSINT-tanker-tracker.git
cd OSINT-tanker-tracker
uv sync
uv run pytest
```

Load history from [adsb.lol](https://github.com/adsblol) and check the counts. Each day is a
~3.5 GB release that is streamed and filtered on the fly; only the tracked fleet and coverage
counts are kept, about 6 MB per day (~2 GB per year). The ~800 MB of heatmap files at the
start of each release is skipped with range requests.

```bash
uv run tw load-history --days 7 --workers 4 --skip-existing   # last 7 complete UTC days
uv run tw load-history --from 2025-10-01 --to 2026-09-30 --workers 4 --skip-existing
uv run tw stats --date 2026-10-25
```

`--workers N` loads N days in parallel and splits every rate limit N ways, so the total request
rate never exceeds `config/providers.yaml`. A stopped run resumes with `--skip-existing`.

Split the loaded points into sorties (takeoff to landing, with origin and destination airfields)
and check how many ends resolved:

```bash
uv run tw build-sorties --from 2025-10-27 --to 2026-10-26 --workers 6
uv run tw sortie-stats --from 2026-09-01 --to 2026-09-28
uv run tw airfields update        # refresh config/airfields.csv from OurAirports
```

Or load a local dump (a folder of `trace_full_*.json` files or a `.tar`):

```bash
uv run tw load-history --path tests/fixtures/real_2026-10-25
```

Data goes to `./data` by default. Set `TW_DATA_DIR` to change it (see [`.env.example`](.env.example)).

### Commands

| Command | Status | What it does |
|---|---|---|
| `tw load-history --days N` / `--from D --to D` `[--workers N]` | ✅ | Stream adsb.lol daily releases (rate-limited, in parallel), filter to the fleet, write date-partitioned points and coverage |
| `tw load-history --path <dir\|tar>` | ✅ | Same, from a local dump |
| `tw stats --date YYYY-MM-DD` | ✅ | Count aircraft per family for one UTC day |
| `tw build-sorties --date D` / `--from D --to D` | ✅ | Build `sorties/date=D/` (from D and the day before only) |
| `tw sortie-stats --from D --to D` | ✅ | Airfield resolution report, with unresolved hot spots |
| `tw airfields update` | ✅ | Rebuild `config/airfields.csv` from OurAirports |
| `tw replay --from D --to D` | ✅ | Aggregates → baselines → D1, D2 → guards over stored days; prints alerts, stores none |
| `tw backtest --months 6` | ✅ | Writes `reports/backtest_<date>.md`: alert rate, top alerts, guard suppressions, z sweep |
| `tw worker [--once]` | M4 | Poll `/v2/mil` every 15 s (adsb.lol, adsb.fi fallback), hourly provisional rolling windows, nightly official alerts from the release |
| `tw worker-report --from D --to D` | M4 | Writes `reports/worker_<date>.md`: uptime, poll gaps, requests per provider vs rate limits, failures, provisional vs official |
| `tw build-theater --from D --to D` | M5c | CCMD inventory, moves, transits and snapshots for stored days (the official run builds them from then on) |
| `tw export [--from D --to D] [--dest local:<dir>\|blob]` | M5 | Dashboard day files (JSON) to `web/public/data` or Vercel Blob (`BLOB_READ_WRITE_TOKEN` in `.env`); no dates exports every day with data |

## Project layout

```
src/tw/
  ingest/        provider parsers (ADSBx v2, globe_history traces), rate limiter,
                 history downloader, loader
  normalize/     hex block + type family filter, registry resolution, H3 cells, coverage
  store/         date-partitioned Parquet (DuckDB reads one partition per day)
  tracks/        sortie segmentation, airfield resolution; orbits in M6
  baselines/     daily aggregates and median/MAD snapshots as of each day
  detectors/     D1, D2 and guards (D3–D5 and composite scoring in M6). Pure functions
  publisher/     templates, validator, charts, approval-gated X client (M7)
  views/         point-in-time day views for the dashboard         (M5)
  export/        day views to local files or Vercel Blob           (M5)
  theater/       CCMD mapping, fixes, inventory, moves, transits   (M5c)
config/          fleet, regions, detectors, providers, events, language, publisher
tests/           unit tests and synthetic fixtures (tests never call live APIs)
eval/            hand-labeled truth set for choosing thresholds
web/             dashboard (React + MapLibre + deck.gl, on Vercel)
```

## Roadmap

| | Milestone | Done when |
|---|---|---|
| ✅ | **M0** Scaffold, config, CI | Tests and lint pass |
| ✅ | **M1** Historical loader, normalize, partitioned storage | 12 months loaded, counts look sane |
| ✅ | **M2** Sortie segmenter and airfield resolution | ≥ 90% of ends seen low resolve to an airfield |
| ✅ | **M3** Baselines, D1, D2 and the coverage guard | Backtest report generated |
| 🟡 | **M4** Live ingest worker | Runs 72h unattended within rate limits (trial running) |
| 🟡 | **M5** Dashboard with heat map, alert feed and date picker | Any day loads in < 1 s (built; deploy pending) |
| ⬜ | **M5b** Playback, compare mode, retrospective re-runs | Point-in-time test passes |
| 🟡 | **M5c** Families kept apart; CCMD flows, inventory, D6/D7, Theaters tab | Built; PR open |
| 🟡 | **M5d** Overview front page: what stands out, latest positions, 180-day trends | Built; on the M5c PR |
| ⬜ | **M6** D3–D5 and composite scoring | Backtest precision reviewed |
| ⬜ | **M7** Publisher with approval queue | First approved post |
| ⬜ | **M8** Truth set and threshold sweep | 20+ labeled periods |

## Development

```bash
uv run pytest                                      # tests
uv run ruff check . && uv run ruff format .        # lint and format
uv run mypy src/                                   # types (strict on detectors/ and publisher/)
```

Contributions are welcome. Please read [`SPEC.md`](SPEC.md) first. Detector changes need a test on
synthetic data before the code, and any change to golden outputs must be explained in the PR.

## Data sources and attribution

This project uses community ADS-B data. Please respect each provider's terms.

- [adsb.lol](https://adsb.lol): history dumps and live API, under the
  [ODbL](https://opendatacommons.org/licenses/odbl/)
- [airplanes.live](https://airplanes.live): live API
- [adsb.fi](https://adsb.fi): open data for personal, non-commercial use, attribution required
- [OurAirports](https://ourairports.com/data/): airfield reference data (public domain)

Tanker Watch is a personal, non-commercial project. Its posts publish derived aggregates only.

## Disclaimer

Everything here comes from publicly broadcast data, which is incomplete. Many military flights do
not broadcast at all, and receiver coverage is patchy over oceans and parts of the Middle East and
Africa. An alert means activity is statistically unusual in the data. It says nothing about intent.
Don't use it for anything safety-critical.

## License

Code: [MIT](LICENSE). Data you load stays under its provider's license (for example, ODbL for
adsb.lol), and that applies to anything you redistribute from it.
