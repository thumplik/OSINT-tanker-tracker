"""`tw worker-report`: was the worker up, did it respect rate limits, and what did it find?

Built from the worker's own logs (request log, events) and stored alerts; the M4 "done when"
(72 h unattended, rate limits respected) is read off this report.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from itertools import pairwise
from pathlib import Path

from tw.config import load_yaml
from tw.ingest.live import read_request_log
from tw.store.alerts import OFFICIAL, PROVISIONAL, read_alerts
from tw.worker import read_events


def max_in_window(times: Sequence[float], seconds: float) -> int:
    """Most requests in any half-open window [t, t + seconds)."""
    best, lo = 0, 0
    ordered = sorted(times)
    for hi, t in enumerate(ordered):
        while ordered[lo] <= t - seconds:
            lo += 1
        best = max(best, hi - lo + 1)
    return best


def rate_check(
    times: Sequence[float], rate: float, burst: float, seconds: float
) -> tuple[int, int]:
    """(observed max in any `seconds` window, what a token bucket allows: burst + rate x s)."""
    return max_in_window(times, seconds), int(burst + rate * seconds)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def build_report(data_dir: Path, first: date, last: date) -> tuple[str, bool]:
    """(markdown, rate limits respected)."""
    limits = load_yaml("providers.yaml")["rate_limits"]
    poll_s = float(load_yaml("providers.yaml")["live"]["poll_seconds"])
    requests = [
        r for r in read_request_log(data_dir, first, last) if first <= _ts(r["ts"]).date() <= last
    ]
    events = [e for e in read_events(data_dir) if first <= _ts(e["ts"]).date() <= last]
    lines = [f"# Worker report {first} .. {last}", ""]

    # Uptime and gaps
    ok_times = sorted(_ts(r["ts"]) for r in requests if r["ok"])
    starts = [e for e in events if e["kind"] == "start"]
    stops = [e for e in events if e["kind"] == "stop"]
    lines += ["## Uptime", ""]
    if ok_times:
        span = ok_times[-1] - ok_times[0]
        gaps = sorted(((b - a, a) for a, b in pairwise(ok_times)), reverse=True)
        expected = span.total_seconds() / poll_s + 1
        lines += [
            f"- First / last good poll: {ok_times[0]:%Y-%m-%d %H:%M:%S}Z / "
            f"{ok_times[-1]:%Y-%m-%d %H:%M:%S}Z ({span.total_seconds() / 3600:.1f} h)",
            f"- Good polls: {len(ok_times)} of ~{expected:.0f} scheduled "
            f"({100 * len(ok_times) / expected:.1f}%)",
            f"- Starts: {len(starts)}, clean stops: {len(stops)}",
            "- Longest gaps between good polls: "
            + ", ".join(
                f"{g.total_seconds() / 60:.1f} min at {a:%m-%d %H:%M}Z" for g, a in gaps[:5]
            ),
        ]
    else:
        lines.append("- No good polls in this range.")
    lines.append("")

    # Rate limits
    by_provider: dict[str, list[float]] = defaultdict(list)
    for r in requests:
        by_provider[r["provider"]].append(_ts(r["ts"]).timestamp())
    respected = True
    lines += [
        "## Rate limits (from the request log)",
        "",
        "| Provider | Requests | OK | Max in 1 s (allowed) | Max in 60 s (allowed) |",
        "|---|---|---|---|---|",
    ]
    ok_by = Counter(r["provider"] for r in requests if r["ok"])
    for provider, times in sorted(by_provider.items()):
        rate, burst = (float(v) for v in limits[provider])
        one, one_ok = rate_check(times, rate, burst, 1)
        sixty, sixty_ok = rate_check(times, rate, burst, 60)
        respected &= one <= one_ok and sixty <= sixty_ok
        lines.append(
            f"| {provider} | {len(times)} | {ok_by[provider]} | {one} ({one_ok}) | "
            f"{sixty} ({sixty_ok}) |"
        )
    lines += ["", f"Rate limits respected: **{'yes' if respected else 'NO'}**", ""]

    # Failures
    errors = Counter(r["error"] for r in requests if not r["ok"])
    switches = sum(1 for a, b in pairwise(requests) if a["provider"] != b["provider"])
    job_errors = Counter(e["job"] for e in events if e["kind"] == "job_error")
    lines += ["## Failures", "", f"- Provider switches: {switches}"]
    lines += [f"- {n} x {err}" for err, n in errors.most_common(8)]
    lines += [f"- Job errors: {job}: {n}" for job, n in sorted(job_errors.items())]
    if not errors and not job_errors:
        lines.append("- No failed requests or job errors.")
    lines.append("")

    # Official tier
    lines += ["## Official days", ""]
    for e in events:
        if e["kind"].startswith("official_"):
            detail = {k: v for k, v in e.items() if k not in ("ts", "kind")}
            lines.append(f"- {e['ts'][:16]}Z {e['kind']}: {detail}")
    lines.append("")

    # Provisional vs official
    days = [first + timedelta(days=n) for n in range((last - first).days + 1)]
    provisional = [a for a in read_alerts(data_dir, PROVISIONAL, days) if a.status != "suppressed"]
    official = read_alerts(data_dir, OFFICIAL, days)
    official_keys = {
        (a.detector, a.region, a.window_start.date()) for a in official if a.status != "suppressed"
    }
    lines += [
        "## Provisional vs official",
        "",
        "| Day | Provisional (unsuppressed, distinct detector x region) | Also official that day |",
        "|---|---|---|",
    ]
    per_day: dict[date, set[tuple[str, str]]] = defaultdict(set)
    for a in provisional:
        per_day[(a.window_end - timedelta(microseconds=1)).date()].add((a.detector, a.region))
    for day in days:
        keys = per_day.get(day, set())
        matched = sum(1 for d, r in keys if (d, r, day) in official_keys)
        lines.append(f"| {day} | {len(keys)} | {matched} |")
    lines.append("")
    return "\n".join(lines), respected


def write_report(data_dir: Path, first: date, last: date, out_dir: Path) -> tuple[Path, bool]:
    text, ok = build_report(data_dir, first, last)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"worker_{last.isoformat()}.md"
    path.write_text(text)
    return path, ok
