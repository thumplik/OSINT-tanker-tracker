# Tanker Watch — Project Spec (v0.4)

Working name; rename freely.

## 1. Purpose

A public-data OSINT system that tracks US military tanker and airlift aircraft, learns their normal patterns, flags statistically unusual activity, and drafts posts for X highlighting that something may be happening.

Three surfaces:

1. **Dashboard**: H3 hex heat maps, timelines, airfield activity, live alert feed, and a historical view for any past day.
2. **Detection engine**: deterministic baselines and anomaly detectors.
3. **Publisher**: turns approved alerts into posts with a chart image.

### Non-goals (v1)

- No claims about intent or purpose. The system reports *what is unusual*, never *why*.
- No tracking of individual crews or people.
- No non-US fleets (keep the provider and type config generic so this can change later).

## 2. Data sources

All providers below speak the ADS-B Exchange v2 JSON schema, so one adapter interface covers them.

| Role | Provider | Notes |
|---|---|---|
| Baseline history | adsb.lol `globe_history` dumps | Free, ODbL. Use for the 90-day baseline and backtests. |
| Live primary | adsb.lol `/v2/mil` | Free, ~1 req/s. Polled every 15 s by the worker. |
| Live fallback | adsb.fi open data `/v2/mil` | Personal, non-commercial; attribution and link required. airplanes.live answers 403 until a project registers with them (checked Oct 2026), so it is configured but not in the failover order. |
| Upgrade path | ADS-B Exchange (RapidAPI or commercial) | Paid; no bulk redistribution. |
| Reference | OurAirports CSV | Airfield lat/lon, ICAO codes, type (military vs civil). |

Rules:

- Respect each provider's rate limit with a token bucket per provider.
- Store raw responses (gzip JSONL, `raw/date=D/<provider>-HH.jsonl.gz`) for 14 days so any alert can be re-derived. Every outbound live request is also logged (`worker/requests/`), which is how rate limits are shown to be respected.
- Keep normalized points, sorties, baselines and alerts **forever** (see §11). Filtered to the tracked fleet, this is small: expect well under 1 GB per year in Parquet.
- Backfill: load as far back as the history dumps allow (target 12+ months) so the historical view and backtests have depth from day one.
- History dumps come from the `prod` pod releases only (`v<date>-planes-readsb-prod-0`). Pods are not mixed, because that could double-count aircraft and shift coverage. Releases are streamed, not stored, since any day can be re-downloaded. A day with no release is reported as missing and never treated as a day with zero activity.
- A release tagged day D loads only trace rows inside day D (UTC). Releases also carry rows from before midnight and leftover traces from earlier days, almost all duplicates of points in those days' own releases; they are counted and dropped so every day comes from exactly one release.
- A trace file that does not decode (bad gzip, empty JSON) is skipped and listed in the load summary. More than `history.max_corrupt_traces` (providers.yaml) in one release fails the day without writing anything.
- **Decided: personal, non-commercial project.** This keeps the free tiers usable. Re-check licensing before anything that earns money (including ads, subscriptions or X creator revenue). Alerts publish derived aggregates only, never raw feeds.
- Home feeder: deferred (see §15). Not required for v1; the provider adapter must work without one.

## 3. Aircraft scope

Config-driven (`config/fleet.yaml`), not hard-coded.

Filter:

- ICAO hex in the US military block `AE0000–AFFFFF`
- **and** ICAO type in the tracked families

| Family | Type codes (initial) |
|---|---|
| Tanker | K35R, KC46 and B762 (KC-46A; adsb.lol uses B762). Not C30J: the code also covers the C-130J-30 airlifter, so USMC KC-130Js are counted as tactical airlift (decided Oct 2026) |
| Strategic airlift | C17, C5M |
| Tactical airlift | C130, C30J |

Notes:

- KC-10 retired in 2024; keep it out of the default list.
- Some aircraft broadcast without position (Mode S only) or via MLAT. Flag `position_source` on every point.
- A hex can appear without a type. Resolve it against a local registry table built from history before discarding it.

## 4. Architecture

```
providers ──▶ ingest worker ──▶ raw store (JSONL.gz)
                    │
                    ▼
            normalize + filter ──▶ points (Parquet / DuckDB)
                    │
                    ▼
            track segmenter ──▶ sorties (origin, dest, path, H3 cells)
                    │
                    ▼
            baseline builder ──▶ baselines (nightly)
                    │
                    ▼
            detectors ──▶ alerts ──▶ approval queue ──▶ X publisher
                    │
                    ▼
               API (FastAPI) ──▶ dashboard
```

### Stack (proposed)

- Python 3.12, `uv`, FastAPI, DuckDB over Parquet (single node; migrate to Postgres/Timescale only if needed)
- `h3` v4 for spatial indexing
- Dashboard: reuse Analysis Workbench widgets if the stack fits; otherwise React + MapLibre + deck.gl `H3HexagonLayer`
- Chart images for posts: Plotly + Kaleido (static PNG)
- Scheduling: one long-running worker with APScheduler

### Hosting (decided)

- **Frontend + read API: Vercel** (Tom's account).
- **Ingest/detection worker: not on Vercel.** It polls every few seconds around the clock and needs a disk, which serverless functions don't provide. Run it on a small always-on box: a cheap VPS, or a home Raspberry Pi (which can double as a feeder later).
- **Hand-off between them (M5, decided Oct 2026)**: the worker exports each day as static JSON (`tw export`, `src/tw/views/day.py`) to a public Vercel Blob store, and the site is a static React app that reads those files. There is no server-side API or database until approvals need one. Files: `days/<D>.json` (heat r4 x hour x family, region counts vs the baseline snapshot, coverage, official + provisional alerts, sortie summaries), `days/<D>.r6.json` (whole-day r6 heat, loaded on demand), `index.json` (days, tier, alert counts), `meta.json` (regions, families, attribution). The worker re-exports today and yesterday hourly, and each day the official run loads. Raw points stay on the worker. The dashboard is public (owner decision, Oct 2026): provisional alerts are shown labeled "coverage not checked".
- From M7: approve/reject writes go to Postgres; the worker picks up approved alerts and posts them. The X credentials live only on the worker.

### Worker (M4)

`tw worker` runs these jobs (times UTC, all in `config/worker.yaml` / `providers.yaml`):

- **poll** every 15 s: one rate-limited `/v2/mil` request; after 3 consecutive failures it fails over to the next provider and tries the primary again after 15 min; failures back off exponentially (15 s → 5 min).
- **flush** every 5 min: live points for the current and previous hour are re-derived from raw (`live/points/`), so a crash loses nothing that was fetched.
- **provisional** hourly at :05: live sorties (`live/sorties/`, same segmenter), then rolling-window D1/D2 (§8) → `provisional_alerts/`.
- **official** every 30 min from 03:40 on D+1 (the release lands ~03:25) until 12:00, then once a night: load day D's release, build its sorties, run the day pipeline, write `alerts/date=D/` once, then build today's baseline snapshot. Days before the worker's first start are history (backtest), not official alerts.
- **retention** daily: delete raw older than 14 days. **heartbeat**: `worker/status.json`.

Live data never enters the official `points/`/`sorties/` datasets: each official day comes from exactly one release (§2).

## 5. Core data model

**Point**: `ts, hex, callsign, type, lat, lon, alt_baro, gs, track, on_ground, position_source, provider, h3_r4, h3_r6`

**Sortie**:
- Starts at first airborne point; ends at landing (a ground run of ≥ 60 s; shorter is a touch-and-go) or a gap > 30 min. A missing ground flag is never a landing.
- A longer gap is a coverage hole, not a landing, and does not end the sortie when the aircraft is ≥ 10,000 ft on both sides, the gap is ≤ 6 h, and the implied speed is ≤ 600 kt (decided Oct 2026: real data has hundreds of such gaps per week over oceans and receiver holes).
- `origin_icao` / `dest_icao`: nearest airfield within 15 km of the first (last) point below 4,000 ft AGL (chosen from a sweep, Oct 2026; was 10 km / 3,000 ft), including the ground point next to takeoff (landing); `null` if unresolved or first (last) seen above that height. ICAO code where one exists, else the OurAirports ident. Airfields: OurAirports, runway ≥ 3,000 ft (`config/airfields.csv`, `tw airfields update`).
- Fields: `sortie_id, hex, type, families[], callsign, start_ts, end_ts, origin_icao, dest_icao, start/end lat-lon, h3_cells[], max_alt, n_points, low_quality_share, start_seen_low, end_seen_low, end_reason, is_open, start_truncated, is_orbit, orbit_hours`. `families[]` replaces a single family because a type may be in more than one. Orbit fields are filled by D3 (M6).
- `sorties/date=D/` holds every sortie with a point in D, built from points of D and the day before only (never later days). A sortie still airborne at the end of D is stored open, with no destination, and the next day's build closes it under the same `sortie_id`. Multi-day counts dedupe by `sortie_id`, latest partition ≤ as_of.
- All thresholds are in `config/detectors.yaml` → `sortie:`.

**Coverage**: per H3 r4 cell per hour, count of *all* aircraft seen (civil included). This is the denominator that separates real surges from receiver coverage changes.

**Fix, presence, move, transit** (theater layer, M5c; `src/tw/theater/`, thresholds in `detectors.yaml → theater:`):
- A **fix** is where a counted sortie (≥ `counting.min_sortie_minutes`) starts or ends, mapped to a combatant command (§6). Its quality is `ground` (start/end seen low), `aloft` (lost from view in the air: about 80% of arrivals into CENTCOM end this way) or `last_seen` (still airborne). The track in between is never used, so overflying a CCMD is not entering it.
- **Inventory** ("seen in"): aircraft per (CCMD, family, type) whose latest fix in the last `max_age_days` (14) is in that CCMD at the end of the day. It is visible inventory, a floor: aircraft that don't broadcast are not counted.
- A **move** is a change of CCMD between consecutive fixes of one aircraft: an exit from one and an entry to the other, on the day of the arriving fix. An aircraft's first fix in the lookback is not a move.
- A **transit from home** starts at the last fix in `ccmds.yaml → home` (NORTHCOM). Its destination is the CCMD of the fix farthest from that departure; earlier stops are `via`. It resolves when the aircraft is back home, or after it has stayed `dwell_hours` (48) in one CCMD, and counts on the day it resolves. A tanker staging in Europe for days before going on to CENTCOM is therefore a transit to EUCOM, then a EUCOM → CENTCOM move.
- Day D uses sorties dated D - `lookback_days` .. D only, with no carried state (point-in-time by construction). Stored as `aggregates/ccmd_inventory|ccmd_moves|ccmd_transits|ccmd_coverage/date=D`; `tw build-theater` backfills them.

**Alert**: `alert_id, detector, region, window, observed, expected, score, severity, evidence{}, sortie_ids[], created_at, status (new|approved|rejected|posted|suppressed), post_draft, chart_path`

Stored in two places: `alerts/date=D/` (official, write-once; the only source for the post queue) and `provisional_alerts/date=D/part-HH` (live rolling windows, `evidence.tier = provisional`; never queued or posted).

## 6. Regions

**Decided: global.** Ingest is global by default; one military-endpoint poll returns tracked aircraft worldwide, so there is no extra cost.

Defined in `config/regions.yaml` as named polygons converted to H3 r4 cell sets that tile the globe: CONUS East, CONUS West, Alaska, Caribbean/Central America, North Atlantic, Europe, Eastern Med, Middle East/Gulf, Africa, Indian Ocean, Western Pacific, Pacific/Hawaii, plus "Other".

Because ADS-B coverage varies a lot (good over the US and Europe, sparse over oceans and parts of the Middle East and Africa), each region carries a status:

- `alerting`: 8+ weeks of baseline, and coverage stable enough that the coverage guard passes most days. Alerts go to the post queue.
- `watch`: data shown on the dashboard and detectors run, but alerts stay in-app only.

Every region starts as `watch`. Promotion to `alerting` is a manual config change, made after reviewing that region's backtest.

Heat maps use r4 (regional view) and r6 (local view).

### Combatant commands (M5c)

`config/ccmds.yaml` maps positions to the six geographic CCMDs (NORTHCOM, EUCOM, CENTCOM, AFRICOM, SOUTHCOM, INDOPACOM) with ordered polygons, first match wins, following the Unified Command Plan as publicly described (Greenland in NORTHCOM since 2025-06-17; Israel and Egypt in CENTCOM; Hawaii in INDOPACOM). No official boundary file exists, so the polygons are approximate; a list of pinned airfields (Ramstein, Al Udeid, Djibouti, Diego Garcia, Pituffik, Guantanamo, ...) is asserted by a test. CCMDs carry the same `watch | alerting` status as regions, all `watch`; promotion is a manual edit. Coverage per CCMD is the region coverage summed by the CCMD of each r4 cell centre.

## 7. Baselines

### How "abnormal" is defined

v1 uses **forecast + surprise**, not a trained classifier. There are no labeled examples of "abnormal" to train on, and every alert must be explainable in one sentence ("14 tanker sorties in 24h vs a typical 5").

For each key, an alert requires all four of:

1. **Expected**: the baseline median for that key.
2. **Surprise**: robust z = (observed − median) / (1.4826 × MAD) above threshold. If MAD is 0, use a configured floor so sparse keys don't produce infinite scores.
3. **Real**: the coverage guard passes (§8). A rise in total traffic in the same area suggests receivers, not aircraft.
4. **Material**: the observed count clears an absolute minimum, so 1 → 3 sorties never fires on its own.

### Mechanics

- Rebuilt nightly from a trailing 56-day window (configurable)
- Keyed by `(region, family, day_of_week)` and, where needed, `(airfield, family)` and `(route, family)`
- Theater flows (M5c) are too sparse for weekday keys: `baselines/ccmd/as_of=D` holds, per (CCMD, family), the median/MAD of up to `baseline_blocks` (8) earlier non-overlapping `flow_window_days` (7) sums of entries, exits and transits from home (only complete windows; at least `min_blocks` (4) to judge), and of daily inventory over the baseline window leaving out the current flow window, so a build-up is not absorbed into its own baseline. CCMD coverage is per weekday, as for regions.
- Inputs are per-day aggregates (`aggregates/*/date=D/`). A sortie counts once in **every region it flies in** (any of its H3 r4 cells), and sorties shorter than `counting.min_sortie_minutes` (30) don't count (both decided Oct 2026). Days without data are skipped, never zero.
- Official runs evaluate whole UTC days (window end = midnight). The M4 worker's provisional tier evaluates rolling windows of the same length (`window_hours`) ending on each hour, with the same counting and detectors, against the latest official snapshot (as_of ≤ the window's end date), compared on the weekday of the window's midpoint. It never builds a snapshot: one built before the previous day's release is loaded would miss that day for good.
- Robust statistics: median and MAD. No means/standard deviations on sparse counts.
- Minimum history: a key with fewer than 4 weeks of data cannot alert. It is shown as "insufficient baseline".

## 8. Detectors

Every detector is a pure function: `(window_data, baseline, config) -> list[Alert]`. No randomness, no LLM, no network access. Every threshold lives in `config/detectors.yaml`.

| ID | Name | Fires when |
|---|---|---|
| D1 | Volume surge | Region × family sortie count over 24h has robust z ≥ 3.0 **and** count ≥ `min_count` **and** coverage-normalized ratio also exceeds threshold |
| D2 | Novel destination | ≥ 2 distinct aircraft of a family arriving at an airfield in 72h, where the baseline window (before those 72h) saw ≤ 1 distinct aircraft of that family there. Counts aircraft, not sorties (decided Oct 2026). Evaluated daily, so one event can alert on each day of its window |
| D3 | Unusual orbit | Tanker orbit-hours (cumulative heading change ≥ 720° within 40 km radius) in an r4 cell above that cell's baseline p95 |
| D4 | Airlift convergence | ≥ N distinct strategic-airlift hexes arriving at one airfield or region within 48h, robust z ≥ 3.0 |
| D5 | Route shift | Share of a family's sorties on a route pair departs from baseline share by ≥ X points over 72h |
| D6 | Flow change (M5c) | 7-day entries into, exits from, or transits from home to a CCMD × family: robust z ≥ 3.0 **and** count ≥ `min_count` **and** the coverage-normalized ratio also exceeds threshold (as D1) |
| D7 | Inventory shift (M5c) | Aircraft of a family seen in a CCMD vs its baseline, either direction: \|z\| ≥ 3.0 **and** change ≥ `min_change` aircraft **and** ≥ `min_rel_change` of normal. Coverage-checked on every day of the flow window |

### Guards (always on)

- **Coverage guard**: suppress if regional total traffic is below 60% or above 167% of its own weekday baseline (the upper bound added Oct 2026, after Gulf days at 5-7x). Fewer receivers means fake drops; recovered receivers mean fake surges. A region with no coverage baseline cannot be checked and is suppressed. Suppressed alerts keep their record and reason.
- **Exercise calendar**: optional `config/known_events.yaml` (announced exercises, air shows). Matching alerts are downgraded and labeled, not hidden.
- **Cooldown**: same detector + region + family (+ flow series for D6) cannot alert again within 12h (72h for D6/D7, `cooldown_hours_by_detector`) unless severity rises. A tanker alert never silences an airlift one (decided Oct 2026: keyed by region only, 46% of cooldown suppressions in the backtest were one family silencing another). This includes alerts from the same run: the strongest (severity, then score) is kept and the rest are suppressed on the record.

### Two tiers (decided Oct 2026)

- **Official**: the nightly run on the release, with every guard. These are the alerts of record.
- **Provisional**: hourly rolling windows on live `/v2/mil` data. The live feed has no civil traffic, so the coverage guard cannot run; these alerts skip only that guard, carry `coverage: unchecked`, and never go to the post queue. The worker report compares them with the official run.

### Composite scoring

Unlike the cooldown, composite scoring merges across families on purpose: a tanker surge and an airlift convergence in one region is the co-occurrence it looks for.

Alerts in the same region within 24h merge into one event. Severity: `low` (one detector), `medium` (two detectors or z ≥ 4), `high` (three or more detectors). Only medium and high go to the post queue by default.

## 9. Publisher

### Workflow

1. Alert reaches `new`.
2. Draft is generated: template fills in the numbers; an LLM may optionally smooth the wording but must not add or change any number, place or claim. A validator re-checks every number in the draft against the alert record.
3. Chart rendered: last 30 days vs baseline band, plus a hex map snapshot.
4. Owner approves or rejects in the dashboard (or through a push/Telegram message).
5. Only `approved` alerts are posted. **Auto-post mode is a config flag that defaults to off.** No target is set yet: thresholds will be chosen from the backtest sweep and the approval log once real data exists. Auto-post stays off until a target is written into this spec.

### Post rules

- Hedged language: "unusual", "above normal", "worth watching". Never "strike", "deployment" or any claim of intent.
- Every post states observed vs normal (e.g. "14 tanker sorties in 24h vs a typical 5").
- Attribute the data source.
- Keep links out of the main post (X charges far more for posts with links); put the dashboard link in a self-reply.
- Daily post cap (default 6).

## 10. Dashboard views

0. **Overview** (M5d, the front page): one family, one day, three questions in order.
   - *What stands out*: the day's stored official alerts once the official run exists for it; before that (history, or today's live data) the **indicators**: D1, D6 and D7 plus the coverage guard run on that day's inputs for the page (`views/indicators.py`), labeled as such, never stored or posted. Guard-suppressed items are listed separately as "held back", with the coverage ratio. D2 isn't re-run.
   - *Where they are*: aircraft (`theaters.positions`, the fixes the inventory counts) are grouped by where they were last seen. On the ground at an airfield: one marker per airfield sized by aircraft. Lost from view in the air: credited to the airfield their last flight left (`from_airfield`: the sortie's origin, else the last airfield the aircraft was seen on the ground at in the lookback), drawn as a ring there. Only aircraft still airborne are individual dots. Clicking an airfield lists its aircraft (callsign, type, hex linked to a public tracker, landed / left / last seen times). Aircraft with no known departure, or on the ground away from any airfield, are listed, not drawn. Below: aircraft last seen per CCMD vs typical.
   - *Over time*: the family's total seen and each CCMD's, daily over `dashboard.history_days` (180), with the typical line; clicking a CCMD selects it everywhere.
   - *Airfields* tab: a card per airfield with aircraft of the family (`theaters.airfield_stats`): on the ground today, with the previous `baseline.window_days` of the daily on-ground count (avg, min, max, chart; flagged when today is outside that range), and aircraft whose last flight left there, airborne or not seen since. From `aggregates/airfield_presence` (per day, built with the theater aggregates; `tw build-theater` backfills days built before it). Sort, search, filter by CCMD; a card opens the airfield's aircraft list.
   The heat map is the map's "Activity" mode; region boxes show only on the Regions and Flights tabs.
1. **Heat map**: H3 hexes by family and time range, with a baseline-difference toggle (observed minus expected).
2. **Alert feed**: cards with evidence, sortie list and approve/reject.
3. **Airfield board**: arrivals and departures by family, sparklines vs baseline.
4. **Sortie inspector**: track replay for any sortie.
5. **Coverage panel**: receiver coverage health by region.
6. **Date picker + time scrubber**: present on every view; switches the whole dashboard to a past day (see §11).
7. **Theaters** (M5c): per CCMD and family, aircraft seen (by type) vs typical with a 56-day sparkline, 7-day entries/exits/transits from the US vs typical, CCMD coverage; transits from the US (destination, via), aircraft away and not settled, the day's CCMD changes. The map shows the CCMDs (H3 res-2 cells by centre) and arcs from home to each destination.

Every view shows one family at a time by default (tankers); "all tracked (mixed)" is an explicit choice.

## 11. Historical view ("time travel")

Pick any past date (or date range) and see the system exactly as it would have looked then.

### What the user can do

- **Day view**: choose a date; the heat map, airfield board, sortie list and alert feed all show that day.
- **Playback**: a time scrubber replays that day's tracks (1×, 10×, 60×, 600×), with sorties appearing and disappearing on the map.
- **Then vs. normal**: every count shows the baseline *as it stood on that date*, so "unusual for then" is judged honestly.
- **Compare**: pick two dates (or a date vs. its baseline) for side-by-side or difference heat maps.
- **Alert re-run**: "What would have fired?" runs the current detectors over the past day without saving or posting anything. Results are labeled as retrospective. (M5d: the Overview's indicators do this for D1, D6 and D7 at export time.)
- **Shareable links**: every view encodes its state in the URL (`?date=2026-06-14&t=1430Z&family=tanker&region=med`).

### Point-in-time correctness (non-negotiable)

- Baselines are stored as dated snapshots (`baselines/<table>/as_of=YYYY-MM-DD/`), built from days before the snapshot date only. Viewing a past day always uses the snapshot from that day, never today's baseline. This prevents future data leaking into the past.
- The hex → type registry (§3) is stored per day (`registry/date=YYYY-MM-DD/`). A day's untyped hexes are resolved only from registry entries dated that day or earlier.
- Historical alerts are shown exactly as they were created (status, wording, draft). Retrospective re-runs are a separate, clearly labeled layer and never overwrite them.
- Every detector and baseline run records its config hash and code version, so a past alert can be reproduced. The hash covers the effective settings of the run, not just the files on disk.
- Baseline snapshots are write-once: replaying a past day reuses its snapshot even if older data was backfilled since. Rebuilding is an explicit choice (`--rebuild-baselines`), e.g. after a config change; the backtest report counts snapshots built under a different config.
- The coverage guard checks every day an alert rests on (for D2, each day an arrival in its window landed), not only the last day.

### Storage and API

- Points and sorties are partitioned by UTC date (`points/date=YYYY-MM-DD/*.parquet`) so loading a day reads one partition.
- Pre-compute per-day H3 aggregates (r4 and r6, hourly) when a day closes, so day views render in under a second.
- Every view is a function of a date (`day_view(day)`), exported as one file per day. "Live" is just `date=today` (tier `provisional`, from live data); there is one code path, not two. A day's view reads only that day's and earlier partitions, and the newest baseline snapshot dated on or before it.
- Playback endpoint returns a compact per-minute track stream for the selected day and filters.

## 12. Testing and evaluation

### Truth set (for tuning, not training)

- `eval/truth_set.yaml`: hand-labeled date ranges, each with region, family, label (`surge` | `quiet` | `exercise` | `coverage_artifact`), and a source note (public reporting, NOTAM, announced exercise).
- Aim for 20–40 labeled periods, including quiet ones, so false positives are measured too.
- The truth set is used to **choose thresholds** and to **compare detector versions**. Nothing is fit to it automatically.

### Metrics (`tw backtest` report)

- **Recall on labeled surges**: did an alert fire within ±1 day?
- **Precision**: share of alerts that land on a labeled surge or that the owner would have posted
- **Alert rate**: alerts per week by severity. Target: a few medium/high per week.
- **Lead time**: hours from first alert to first public reporting, where known
- Threshold sweep: precision/recall/alert-rate table across z thresholds, so tuning is a deliberate choice

### Approval-log labels

Every approve/reject in the publisher is stored with the alert's full feature snapshot (`eval/approvals.parquet`). Note: these labels mean "worth posting", which is not the same as "truly anomalous".

### Tests

- **Unit**: each detector tested on synthetic data (inject a surge; confirm it fires. Inject a coverage drop; confirm it is suppressed).
- **Replay**: `replay` mode runs the full pipeline over historical dumps at accelerated speed, with no live calls.
- **Backtest**: run detectors over 6+ months of history. Hand-label notable periods as a small truth set. Report precision and alerts per week.
- **Point-in-time test**: compute a past day's view, then add later data; the past view must not change.
- **Golden files**: fixed input day → fixed alert output. Any detector change that alters golden output must be explained in the PR.

## 13. Detection roadmap (ML, later)

Ordered by value. Each stage must beat the previous one on the backtest before it ships, and every stage still has to produce an "expected X, observed Y" explanation.

| Stage | Method | Prerequisite | Role |
|---|---|---|---|
| S0 (v1) | Day-of-week median/MAD baselines | 8 weeks of history | Primary detector |
| S1 | Seasonal forecast (STL or Prophet-style), with holiday and exercise-cycle effects | 12+ months of history | Replaces the median as "expected"; detectors unchanged |
| S2 | Isolation forest over daily per-region feature vectors (counts, new destinations, orbit hours, route mix) | S1 shipped; 12+ months | Catches unusual *combinations*; feeds composite scoring, never alerts alone |
| S3 | Supervised ranker trained on approval-log labels | ~200+ approve/reject decisions | Ranks the post queue by "worth posting". Does **not** decide what is anomalous. |

Rules for every ML stage:

- Lives in `models/`, never in `detectors/`. Detectors consume model outputs as inputs, so they stay pure.
- Trained only on data before the evaluation period (no leakage), and versioned with the training window recorded.
- Never posts directly and never produces a number that appears in a post.

## 14. Milestones

| M | Deliverable | Done when |
|---|---|---|
| M0 | Repo scaffold, config files, CI, CLAUDE.md | `uv run pytest` passes; lint clean |
| M1 | Historical loader + normalize/filter, date-partitioned storage | 12 months of history loaded; per-family counts look sane |
| M2 | Track segmenter + airfield resolution | ≥ 90% of origins and destinations seen low (on the ground or < 5,000 ft AGL) resolve to an airfield. Ends first/last seen at altitude are coverage edges and are reported, not counted (decided Oct 2026) |
| M3 | Baselines + D1, D2 + coverage guard | Backtest report generated |
| M4 | Live ingest worker | Runs 72h unattended; rate limits respected (both read off `tw worker-report`, from the worker's request log) |
| M5 | Dashboard (heat map, alert feed) with date picker | Usable on desktop and phone; any backfilled day loads in < 1 s. Heat-map baseline difference, airfield board and coverage map move to M5b (they need a per-hex baseline) |
| M5b | Playback, compare mode, retrospective alert re-run | Scrub a full day smoothly; point-in-time test passes |
| M5c | Families kept apart; theater layer (CCMD inventory, entries/exits, transits from home), D6/D7, Theaters tab | Backtest reviewed; point-in-time tests pass |
| M5d | Overview front page: what stands out (alerts or indicators), aircraft by airfield, 180-day trends; airfield cards | An analyst can answer "anything unusual, where are they, vs normal" without leaving the first screen, on desktop and phone |
| M6 | D3–D5, composite scoring | Backtest precision reviewed |
| M7 | Publisher with approval queue | First approved post goes out |
| M8 | Truth set + threshold sweep report | 20+ labeled periods; thresholds chosen from the sweep |

## 15. Open decisions

Decided (Oct 2026): personal project · Vercel frontend + separate always-on worker · global coverage with per-region promotion · targets set later from data.

Still open:

- X account name and posting voice
- Worker host: VPS or home Raspberry Pi
- Home feeder: optional; revisit after M4
- Precision and alert-rate target for auto-post (set after M8)
