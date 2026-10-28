"""`tw` command-line entrypoint."""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tw.config import load_fleet
from tw.store.parquet import partition_dir

if TYPE_CHECKING:
    from tw.ingest.load_history import LoadSummary


def _data_dir() -> Path:
    return Path(os.environ.get("TW_DATA_DIR", "./data"))


def _print_summary(label: str, summary: LoadSummary) -> None:
    print(
        f"{label}: traces={summary.traces} military_points={summary.military_points} "
        f"kept={summary.kept_points} stray_rows={summary.stray_rows}"
    )
    for day, n in summary.points_per_day.items():
        print(f"  {day.isoformat()}  {n} points")
    for name in summary.corrupt_traces:
        print(f"  skipped corrupt {name}")


def _history_days(args: argparse.Namespace) -> list[date]:
    if args.days is not None:
        # Most recent complete UTC day is yesterday.
        end = datetime.now(UTC).date() - timedelta(days=1)
        start = end - timedelta(days=args.days - 1)
    else:
        start, end = args.from_, args.to or args.from_
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def cmd_load_history(args: argparse.Namespace) -> int:
    from concurrent.futures import ProcessPoolExecutor, as_completed

    from tw.ingest.load_history import init_worker, load_day_in_worker, load_dump

    if args.path is not None:
        fleet = load_fleet()
        sources = sorted(args.path.glob("*.tar*")) if args.tars else [args.path]
        for source in sources:
            _print_summary(source.name, load_dump(source, _data_dir(), fleet))
        return 0

    days = _history_days(args)
    if args.skip_existing:
        done = [d for d in days if partition_dir(_data_dir(), "points", d).exists()]
        if done:
            print(f"skipping {len(done)} day(s) already loaded")
        days = [d for d in days if d not in done]
    workers = max(1, min(args.workers, len(days) or 1))
    started = time.monotonic()
    missing: list[date] = []
    failed: list[date] = []
    skipped_bytes = 0
    with ProcessPoolExecutor(workers, initializer=init_worker, initargs=(workers,)) as pool:
        futures = [pool.submit(load_day_in_worker, d, _data_dir()) for d in days]
        for future in as_completed(futures):
            day, result, error = future.result()
            if error is not None:
                failed.append(day)
                print(f"{day.isoformat()}: FAILED {error}")
            elif result is None:
                missing.append(day)
                print(f"{day.isoformat()}: no release found")
            else:
                skipped_bytes += result.skipped_bytes
                _print_summary(
                    f"{result.tag} (skipped {result.skipped_bytes / 1e6:.0f} MB)", result.summary
                )
    loaded = len(days) - len(missing) - len(failed)
    print(
        f"loaded {loaded}/{len(days)} day(s) with {workers} worker(s) in "
        f"{time.monotonic() - started:.0f}s; skipped {skipped_bytes / 1e9:.1f} GB of heatmaps"
    )
    for label, group in (("no release", missing), ("failed", failed)):
        if group:
            print(f"{label}: {', '.join(d.isoformat() for d in sorted(group))}")
    return 1 if failed else 0


def cmd_stats(args: argparse.Namespace) -> int:
    from tw.store.parquet import read_day

    fleet = load_fleet()
    table = read_day(_data_dir(), "points", args.date)
    hexes_by_type: dict[str, set[str]] = {}
    for hex_code, type_code in zip(
        table["hex"].to_pylist(), table["type"].to_pylist(), strict=True
    ):
        hexes_by_type.setdefault(type_code, set()).add(hex_code)
    print(f"{args.date.isoformat()}: {table.num_rows} points")
    for family, types in sorted(fleet.families.items()):
        n = len(set().union(*(hexes_by_type.get(t, set()) for t in types)))
        print(f"  {family:<18} {n} aircraft")
    return 0


def _range_days(args: argparse.Namespace) -> list[date]:
    start = args.date or args.from_
    end = args.date or args.to or start
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def cmd_build_sorties(args: argparse.Namespace) -> int:
    from concurrent.futures import ProcessPoolExecutor

    from tw.tracks.sorties import build_day_in_worker, init_sortie_worker

    days = _range_days(args)
    started = time.monotonic()
    totals = [0, 0, 0]
    with ProcessPoolExecutor(max(1, args.workers), initializer=init_sortie_worker) as pool:
        for result in pool.map(build_day_in_worker, days, [_data_dir()] * len(days)):
            closed = result.sorties - result.open
            print(
                f"{result.day.isoformat()}: {result.sorties} sorties ({result.open} open), "
                f"both ends {result.both_ends}/{closed}"
            )
            totals = [totals[0] + result.sorties, totals[1] + closed, totals[2] + result.both_ends]
    print(
        f"built {len(days)} day(s) in {time.monotonic() - started:.0f}s: {totals[0]} sorties, "
        f"both ends on {totals[2]}/{totals[1]} closed"
    )
    return 0


def cmd_sortie_stats(args: argparse.Namespace) -> int:
    from tw.tracks.airfields import AirfieldIndex, load_airfields
    from tw.tracks.sorties import latest_sorties, resolution_report

    days = _range_days(args)
    rows = latest_sorties(_data_dir(), days)
    print(f"{days[0].isoformat()}..{days[-1].isoformat()}: {len(rows)} sorties")
    for line in resolution_report(rows, AirfieldIndex(load_airfields())):
        print(line)
    return 0


def cmd_airfields_update(_args: argparse.Namespace) -> int:
    import urllib.request

    from tw.config import load_yaml
    from tw.ingest.ratelimit import RateLimiter
    from tw.tracks.airfields import AIRFIELDS_PATH, PROVIDER, update_airfields

    providers = load_yaml("providers.yaml")
    limiter = RateLimiter.from_config()
    user_agent = providers["history"]["user_agent"]

    def fetch(url: str) -> bytes:
        limiter.acquire(PROVIDER)
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return bytes(resp.read())

    n = update_airfields(providers["airfields"], fetch)
    print(f"wrote {n} airfields to {AIRFIELDS_PATH}")
    return 0


def _latest_points_day() -> date:
    days = sorted(p.name[5:] for p in (_data_dir() / "points").glob("date=*"))
    if not days:
        raise SystemExit("no points loaded; run `tw load-history` first")
    return date.fromisoformat(days[-1])


def cmd_build_theater(args: argparse.Namespace) -> int:
    """Theater aggregates and write-once snapshots for stored days, in date order (M5c). Writes
    no alerts; the official run builds these itself from then on."""
    from tw.pipeline import Context, prepare_theater
    from tw.theater.aggregates import AGG_AIRFIELDS, AGG_INVENTORY

    ctx = Context(_data_dir(), rebuild_baselines=args.rebuild_baselines)
    built = missing = 0
    for day in _range_days(args):
        older = partition_dir(ctx.data_dir, AGG_INVENTORY, day).exists() and not (
            partition_dir(ctx.data_dir, AGG_AIRFIELDS, day).exists()
        )
        if older:  # built before airfield presence existed (M5d): rebuild; deterministic
            ctx.build_theater(day)
        if prepare_theater(day, ctx) is None:
            missing += 1
        else:
            built += 1
    print(f"theater: {built} days built, {missing} without data")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    """Retrospective: runs aggregates, baselines and detectors over stored days and prints the
    alerts. Nothing is stored as an alert and nothing is posted (SPEC §11)."""
    from tw.pipeline import Context, run_days

    days = _range_days(args)
    ctx = Context(_data_dir(), rebuild_baselines=args.rebuild_baselines)
    alerts, missing, _ = run_days(days, ctx)
    for a in alerts:
        day = (a.window_end - timedelta(days=1)).date()
        what = a.evidence.get("airfield", "") + " " + str(a.evidence.get("family", ""))
        print(
            f"{day} {a.detector} {a.severity:<6} {a.status:<10} {a.region:<26} {what.strip():<28} "
            f"observed {a.observed:g} vs expected {a.expected:g} (score {a.score:.1f})"
        )
    live = sum(a.status != "suppressed" for a in alerts)
    print(
        f"{len(days)} day(s), {len(missing)} without data: {live} alert(s), "
        f"{len(alerts) - live} suppressed (retrospective, not stored)"
    )
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    from tw.backtest import REPO, run_backtest

    if args.from_:
        days = _range_days(args)
    else:
        end = _latest_points_day()
        start = end - timedelta(days=int(args.months * 365.25 / 12) - 1)
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    path, alerts = run_backtest(days, _data_dir(), REPO / "reports", args.rebuild_baselines)
    live = sum(a.status != "suppressed" for a in alerts)
    print(f"wrote {path} ({live} alerts, {len(alerts) - live} suppressed over {len(days)} days)")
    return 0


def _build_worker() -> Any:
    from tw.ingest.history_download import HistoryConfig, http_opener
    from tw.ingest.live import LiveClient, LiveConfig, http_fetch
    from tw.ingest.ratelimit import RateLimiter
    from tw.worker import Worker

    live = LiveConfig.load()
    limiter = RateLimiter.from_config()
    history = HistoryConfig.load()
    client = LiveClient(live, http_fetch(live), limiter)
    worker = Worker(_data_dir(), client, limiter, history, http_opener(history))
    worker.sink = _blob_sink(limiter, required=False)
    return worker


def _blob_sink(limiter: Any, required: bool) -> Any:
    """Vercel Blob sink from BLOB_READ_WRITE_TOKEN, or None when unset (and not required)."""
    from tw.export.sinks import BlobConfig, BlobSink, blob_token, http_send

    token = blob_token()
    if token is None:
        if required:
            raise SystemExit("BLOB_READ_WRITE_TOKEN is not set (.env)")
        return None
    cfg = BlobConfig.load()
    return BlobSink(token, cfg, limiter, http_send(cfg.timeout_s))


def cmd_export(args: argparse.Namespace) -> int:
    """Dashboard day files (SPEC §4, M5) to a local directory or Vercel Blob."""
    from tw.export.run import export
    from tw.export.sinks import LocalSink
    from tw.ingest.ratelimit import RateLimiter
    from tw.views.day import ViewContext, available_days

    ctx = ViewContext(_data_dir())
    if args.from_ is None:
        days = list(available_days(ctx))
    else:
        last = args.to or args.from_
        days = [args.from_ + timedelta(days=i) for i in range((last - args.from_).days + 1)]
    if args.dest == "blob":
        sink = _blob_sink(RateLimiter.from_config(), required=True)
    elif args.dest.startswith("local:"):
        sink = LocalSink(Path(args.dest[6:]))
    else:
        raise SystemExit("--dest must be blob or local:<dir>")
    started = time.monotonic()
    result = export(ctx, days, sink, datetime.now(UTC))
    print(
        f"exported {len(result.days)} day(s), {result.bytes / 1e6:.1f} MB "
        f"in {time.monotonic() - started:.0f}s; no data for {len(result.missing)}"
    )
    if args.dest == "blob":
        print(f"site data URL: {sink.base_url}")
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    """Live polling, hourly provisional windows and the nightly official run (SPEC §4). With
    --once: one poll, flush and provisional run, for a smoke test."""
    import logging

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)sZ %(levelname)s %(name)s %(message)s"
    )
    logging.Formatter.converter = time.gmtime  # UTC everywhere, logs included
    logging.getLogger("apscheduler").setLevel(logging.WARNING)  # not one line per poll
    worker = _build_worker()
    if not args.once:
        worker.run()
        return 0
    now = datetime.now(UTC)
    worker.poll(now)
    worker.flush(now)
    written = worker.provisional(now)
    worker.heartbeat(now)
    print(json.dumps(worker.status, indent=1, default=str))
    print(f"provisional alerts written: {written}")
    return 0 if worker.status.get("polls_ok") else 1


def cmd_worker_report(args: argparse.Namespace) -> int:
    from tw.backtest import REPO
    from tw.worker_report import write_report

    path, respected = write_report(_data_dir(), args.from_, args.to, REPO / "reports")
    print(f"wrote {path} (rate limits respected: {'yes' if respected else 'NO'})")
    return 0 if respected else 1


REBUILD_HELP = (
    "rebuild existing baseline snapshots (write-once otherwise; use after config changes)"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tw", description="Tanker Watch")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("load-history", help="load adsb.lol globe_history dumps")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--path", type=Path, help="local dump directory or tar file")
    src.add_argument("--days", type=int, help="download the last N complete UTC days")
    src.add_argument("--from", dest="from_", type=date.fromisoformat, help="first UTC day")
    p.add_argument("--to", type=date.fromisoformat, help="last UTC day (with --from)")
    p.add_argument(
        "--tars", action="store_true", help="treat --path as a folder of tar files, one per day"
    )
    p.add_argument(
        "--skip-existing", action="store_true", help="skip days that already have points"
    )
    p.add_argument(
        "--workers", type=int, default=1, help="days to load in parallel (rate limits are shared)"
    )
    p.set_defaults(func=cmd_load_history)

    p = sub.add_parser("stats", help="per-family aircraft counts for one UTC day")
    p.add_argument("--date", type=date.fromisoformat, required=True)
    p.set_defaults(func=cmd_stats)

    for name, func, help_ in (
        ("build-sorties", cmd_build_sorties, "build sorties/date=D/ from stored points"),
        ("sortie-stats", cmd_sortie_stats, "airfield resolution report for a date range"),
    ):
        p = sub.add_parser(name, help=help_)
        when = p.add_mutually_exclusive_group(required=True)
        when.add_argument("--date", type=date.fromisoformat, help="one UTC day")
        when.add_argument("--from", dest="from_", type=date.fromisoformat, help="first UTC day")
        p.add_argument("--to", type=date.fromisoformat, help="last UTC day (with --from)")
        if func is cmd_build_sorties:
            p.add_argument("--workers", type=int, default=1, help="days to build in parallel")
        p.set_defaults(func=func)

    p = sub.add_parser("airfields", help="airfield reference data")
    airfields = p.add_subparsers(dest="action", required=True)
    p = airfields.add_parser("update", help="download OurAirports into config/airfields.csv")
    p.set_defaults(func=cmd_airfields_update)

    p = sub.add_parser("build-theater", help="CCMD flows and inventory for stored days (M5c)")
    when = p.add_mutually_exclusive_group(required=True)
    when.add_argument("--date", type=date.fromisoformat, help="one UTC day")
    when.add_argument("--from", dest="from_", type=date.fromisoformat, help="first UTC day")
    p.add_argument("--to", type=date.fromisoformat, help="last UTC day (with --from)")
    p.add_argument("--rebuild-baselines", action="store_true", help=REBUILD_HELP)
    p.set_defaults(func=cmd_build_theater)

    p = sub.add_parser("replay", help="run the pipeline over stored days (retrospective)")
    when = p.add_mutually_exclusive_group(required=True)
    when.add_argument("--date", type=date.fromisoformat, help="one UTC day")
    when.add_argument("--from", dest="from_", type=date.fromisoformat, help="first UTC day")
    p.add_argument("--to", type=date.fromisoformat, help="last UTC day (with --from)")
    p.add_argument("--speed", type=float, default=0, help="0 = as fast as possible (offline)")
    p.add_argument("--rebuild-baselines", action="store_true", help=REBUILD_HELP)
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("backtest", help="write reports/backtest_<date>.md")
    when = p.add_mutually_exclusive_group()
    when.add_argument("--months", type=float, default=6, help="months ending at the latest day")
    when.add_argument("--from", dest="from_", type=date.fromisoformat, help="first UTC day")
    p.add_argument("--to", type=date.fromisoformat, help="last UTC day (with --from)")
    p.add_argument("--rebuild-baselines", action="store_true", help=REBUILD_HELP)
    p.set_defaults(func=cmd_backtest, date=None)

    p = sub.add_parser("worker", help="live ingest + detection (runs until stopped)")
    p.add_argument(
        "--once", action="store_true", help="one poll/flush/provisional cycle, then exit"
    )
    p.set_defaults(func=cmd_worker)

    p = sub.add_parser("worker-report", help="write reports/worker_<date>.md from worker logs")
    p.add_argument("--from", dest="from_", type=date.fromisoformat, required=True)
    p.add_argument("--to", type=date.fromisoformat, required=True)
    p.set_defaults(func=cmd_worker_report)

    p = sub.add_parser("export", help="dashboard day files to local:<dir> or Vercel Blob")
    p.add_argument("--from", dest="from_", type=date.fromisoformat, help="first UTC day")
    p.add_argument("--to", type=date.fromisoformat, help="last UTC day (default: --from)")
    p.add_argument("--dest", default="local:web/public/data", help="blob | local:<dir>")
    p.set_defaults(func=cmd_export)
    return parser


def load_dotenv(path: Path = Path(".env")) -> None:
    """KEY=VALUE lines from .env into the environment; variables already set win. Values are
    never printed."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv()
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)  # progress shows up in redirected logs
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
