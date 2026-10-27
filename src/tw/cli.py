"""`tw` command-line entrypoint."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from tw.config import load_fleet


def _data_dir() -> Path:
    return Path(os.environ.get("TW_DATA_DIR", "./data"))


def cmd_load_history(args: argparse.Namespace) -> int:
    from tw.ingest.load_history import load_dump

    fleet = load_fleet()
    sources = sorted(args.path.glob("*.tar*")) if args.path.is_dir() and args.tars else [args.path]
    for source in sources:
        summary = load_dump(source, _data_dir(), fleet)
        print(
            f"{source.name}: raw={summary.raw_points} military={summary.military_points} "
            f"kept={summary.kept_points}"
        )
        for day, n in summary.points_per_day.items():
            print(f"  {day.isoformat()}  {n} points")
    return 0


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

    p = sub.add_parser("load-history", help="load local globe_history dumps")
    p.add_argument("--path", type=Path, required=True, help="dump directory or tar file")
    p.add_argument(
        "--tars", action="store_true", help="treat --path as a folder of tar files, one per day"
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
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
