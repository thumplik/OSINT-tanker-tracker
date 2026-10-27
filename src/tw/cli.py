"""`tw` command-line entrypoint."""

from __future__ import annotations

import argparse
import io
import os
import sys
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

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


def _not_yet(milestone: str) -> int:
    print(f"not implemented yet (milestone {milestone}, see SPEC §14)", file=sys.stderr)
    return 2


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

    for name, milestone in [
        ("replay", "M3"),
        ("backtest", "M3"),
        ("worker", "M4"),
        ("api", "M5"),
    ]:
        p = sub.add_parser(name, help=f"(milestone {milestone})")
        p.set_defaults(func=lambda _a, m=milestone: _not_yet(m))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)  # progress shows up in redirected logs
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
