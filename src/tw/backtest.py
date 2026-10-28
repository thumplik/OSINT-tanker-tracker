"""`tw backtest`: run the detectors over history and write `reports/backtest_<date>.md` (SPEC §12).

Retrospective only: nothing is written to the alert store and nothing is posted (CLAUDE.md
rule 8). The report and its `_retrospective.parquet` table of would-have-fired alerts are review
files in `reports/`, labeled retrospective. Every day is judged with the baseline snapshot as of
that day, so there is no hindsight.
"""

from __future__ import annotations

import copy
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from tw.config import DEFAULT_CONFIG_DIR, config_hash, load_regions
from tw.detectors.types import Alert
from tw.pipeline import Context, DayInputs, detect_day, run_days
from tw.provenance import code_version, effective_config_hash

REPO = DEFAULT_CONFIG_DIR.parent
SWEEP_Z = (2.5, 3.0, 3.5, 4.0)


def _family_or_field(a: Alert) -> str:
    if a.detector == "D2":
        return f"{a.evidence['airfield']} {a.evidence['family']}"
    return str(a.evidence["family"])


def _sweep(prepared: Sequence[DayInputs], ctx: Context, z: float) -> list[Alert]:
    cfg = copy.deepcopy(ctx.cfg)
    cfg["d1_volume_surge"]["min_z"] = z
    provenance = (effective_config_hash(cfg), code_version())
    alerts: list[Alert] = []
    for inputs in prepared:
        alerts += detect_day(inputs, cfg, ctx.events, alerts[-200:], provenance)
    return alerts


def load_truth_set(path: Path = REPO / "eval" / "truth_set.yaml") -> list[dict[str, Any]]:
    raw = yaml.safe_load(path.read_text()) or {}
    return list(raw.get("periods") or [])


def _matches(a: Alert, p: Mapping[str, Any], slack: timedelta = timedelta(days=1)) -> bool:
    first, last = a.window_start.date(), a.window_end.date() - timedelta(days=1)
    start, end = p["from"] - slack, p["to"] + slack
    family_ok = p.get("family") in (None, a.evidence.get("family"))
    return p["region"] == a.region and family_ok and first <= end and start <= last


def truth_metrics(alerts: Sequence[Alert], periods: Sequence[Mapping[str, Any]]) -> str:
    surges = [p for p in periods if p["label"] == "surge"]
    if not surges:
        return "n/a"
    live = [a for a in alerts if a.status != "suppressed"]
    recall = sum(any(_matches(a, p) for a in live) for p in surges) / len(surges)
    precision = sum(any(_matches(a, p) for p in surges) for a in live) / max(1, len(live))
    return f"precision {precision:.0%}, recall {recall:.0%}"


def write_retrospective(path: Path, alerts: Sequence[Alert]) -> None:
    """Would-have-fired alerts for review. Never read back as alerts and never posted."""
    rows = [
        {
            "retrospective": True,
            "config_hash": a.config_hash,
            "code_version": a.code_version,
            **{k: getattr(a, k) for k in ("alert_id", "detector", "region", "severity", "status")},
            "window_start": a.window_start,
            "window_end": a.window_end,
            "observed": a.observed,
            "expected": a.expected,
            "score": a.score,
            "key": _family_or_field(a),
            "evidence": json.dumps(a.evidence, sort_keys=True, default=str),
            "sortie_ids": list(a.sortie_ids),
        }
        for a in alerts
    ]
    pq.write_table(pa.Table.from_pylist(rows), path)


def report(
    days: Sequence[date],
    alerts: Sequence[Alert],
    missing: Sequence[date],
    sweep: Mapping[float, Sequence[Alert]],
    periods: Sequence[Mapping[str, Any]],
    base_z: float,
    prepared: Sequence[DayInputs] = (),
    config: str = "",
) -> str:
    weeks = max(1.0, len(days) / 7)
    live = [a for a in alerts if a.status != "suppressed"]
    suppressed = [a for a in alerts if a.status == "suppressed"]
    statuses = {r.id: r.status for r in load_regions().regions}
    out = [
        f"# Backtest {days[0]} to {days[-1]}",
        "",
        f"- Days: {len(days)} ({len(missing)} without data: "
        f"{', '.join(map(str, missing)) or 'none'})",
        f"- Config hash: `{config_hash()}`; D1 min_z {base_z}",
        "- Retrospective: nothing written to the alert store, nothing posted. Each day uses the "
        "baseline snapshot as of that day (snapshots are write-once).",
        f"- Snapshots built with a different config than this run: "
        f"{sum(p.baseline_config_hash != config for p in prepared)} of {len(prepared)} days "
        "(rebuild with `--rebuild-baselines` after a config change)",
        f"- Region status: {', '.join(sorted(set(statuses.values())))} "
        "(only `alerting` regions would feed the post queue)",
        "",
        "## Alert rate",
        "",
        "| | alerts | per week |",
        "|---|---:|---:|",
    ]
    by_sev = Counter((a.detector, a.severity) for a in live)
    for (det, sev), n in sorted(by_sev.items()):
        out.append(f"| {det} {sev} | {n} | {n / weeks:.1f} |")
    out.append(f"| suppressed by guards | {len(suppressed)} | {len(suppressed) / weeks:.1f} |")
    reasons = Counter(r for a in suppressed for r in a.evidence.get("suppressed_by", []))
    out += [
        "",
        "Suppressed by: " + (", ".join(f"{k} {v}" for k, v in sorted(reasons.items())) or "none"),
    ]

    out += [
        "",
        "## By region",
        "",
        "| region | status | D1 | D2 | suppressed |",
        "|---|---|---:|---:|---:|",
    ]
    per_region: dict[str, Counter[str]] = defaultdict(Counter)
    for a in alerts:
        per_region[a.region]["suppressed" if a.status == "suppressed" else a.detector] += 1
    for region in sorted(per_region):
        c, status = per_region[region], statuses.get(region, "?")
        out.append(f"| {region} | {status} | {c['D1']} | {c['D2']} | {c['suppressed']} |")

    out += [
        "",
        "## Weekly",
        "",
        "| week of | D1 low | D1 medium | D2 | suppressed |",
        "|---|---:|---:|---:|---:|",
    ]
    weekly: dict[date, Counter[str]] = defaultdict(Counter)
    for a in alerts:
        d = a.window_end.date() - timedelta(days=1)
        key = "suppressed" if a.status == "suppressed" else f"{a.detector} {a.severity}"
        weekly[d - timedelta(days=d.weekday())][key] += 1
    for week in sorted(weekly):
        c = weekly[week]
        out.append(
            f"| {week} | {c['D1 low']} | {c['D1 medium']} | {c['D2 low']} | {c['suppressed']} |"
        )

    out += [
        "",
        "## Top alerts",
        "",
        "| day | detector | region | what | observed vs expected | score | coverage | status |",
        "|---|---|---|---|---|---:|---:|---|",
    ]
    for a in sorted(alerts, key=lambda a: (-a.score, a.alert_id))[:25]:
        day = a.window_end.date() - timedelta(days=1)
        cov = a.evidence.get("coverage_ratio")
        out.append(
            f"| {day} | {a.detector} | {a.region} | {_family_or_field(a)} | "
            f"{a.observed:g} vs {a.expected:g} | {a.score:.1f} | "
            f"{'' if cov is None else f'{cov:.2f}'} | {a.status} |"
        )

    out += [
        "",
        "## D1 threshold sweep",
        "",
        "Chosen deliberately from this table, never tuned automatically (SPEC §12).",
        "",
        "| min_z | D1 alerts | per week | medium | suppressed | truth set |",
        "|---:|---:|---:|---:|---:|---|",
    ]
    for z, z_alerts in sweep.items():
        d1 = [a for a in z_alerts if a.detector == "D1"]
        d1_live = [a for a in d1 if a.status != "suppressed"]
        out.append(
            f"| {z:g} | {len(d1_live)} | {len(d1_live) / weeks:.1f} | "
            f"{sum(a.severity == 'medium' for a in d1_live)} | {len(d1) - len(d1_live)} | "
            f"{truth_metrics(z_alerts, periods)} |"
        )
    out += [
        "",
        "## Truth set",
        "",
        f"{len(periods)} labeled periods in `eval/truth_set.yaml`: "
        + (
            truth_metrics(alerts, periods)
            if periods
            else "none yet, so precision and recall can't be "
            "measured. Add 20-40 periods (SPEC §12)."
        ),
        "",
    ]
    return "\n".join(out)


def run_backtest(
    days: Sequence[date], data_dir: Path, out_dir: Path, rebuild_baselines: bool = False
) -> tuple[Path, list[Alert]]:
    ctx = Context(data_dir, rebuild_baselines=rebuild_baselines)
    alerts, missing, prepared = run_days(days, ctx)
    sweep = {z: _sweep(prepared, ctx, z) for z in SWEEP_Z}
    periods = load_truth_set()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"backtest_{days[-1].isoformat()}.md"
    path.write_text(
        report(
            days,
            alerts,
            missing,
            sweep,
            periods,
            float(ctx.cfg["d1_volume_surge"]["min_z"]),
            prepared,
            ctx.provenance[0],
        )
    )
    write_retrospective(out_dir / f"backtest_{days[-1].isoformat()}_retrospective.parquet", alerts)
    return path, alerts
