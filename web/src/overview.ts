// Overview (front page): what stands out, where the aircraft are, how that compares over time.
// Pure helpers over one day file; one family at a time (families are never summed together).
import type { AlertRow, DayView, PositionRow, Theaters } from "./types";

export interface Standout {
  /** "alerts": the day's stored official alerts. "indicators": what the day's numbers flag
   * (the same detectors, run for this page; never stored or posted). */
  source: "alerts" | "indicators";
  shown: AlertRow[];
  held: AlertRow[]; // suppressed by the coverage guard: maybe receivers, not aircraft
}

const ofFamily = (rows: AlertRow[], family: string) =>
  rows.filter((a) => a.evidence.family === family);

const strongest = (rows: AlertRow[]) =>
  [...rows].sort(
    (a, b) => Math.abs(b.score) - Math.abs(a.score) || a.alert_id.localeCompare(b.alert_id),
  );

/** Stored official alerts once the official run exists for the day; otherwise indicators. */
export function standout(view: DayView, family: string, officialFrom: string | null): Standout {
  const ran = view.meta.tier === "official" && !!officialFrom && view.meta.date >= officialFrom;
  const rows = ran
    ? ofFamily(view.alerts, family).filter((a) => a.tier === "official")
    : ofFamily(view.indicators ?? [], family);
  return {
    source: ran ? "alerts" : "indicators",
    shown: strongest(rows.filter((a) => a.status !== "suppressed")),
    held: strongest(rows.filter((a) => a.status === "suppressed")),
  };
}

export function median(values: number[]): number | null {
  if (!values.length) return null;
  const s = [...values].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/** Daily totals of one family over CCMDs (each aircraft sits in exactly one); null when a day
 * is missing. */
export function totalSeries(th: Theaters, family: string, ccmds: string[]): (number | null)[] {
  const series = ccmds.map((c) => th.history[`${c}|${family}`] ?? []);
  const n = Math.max(0, ...series.map((s) => s.length));
  return Array.from({ length: n }, (_, i) => {
    let sum = 0;
    for (const s of series) {
      const v = s[i];
      if (v === null || v === undefined) return null;
      sum += v;
    }
    return sum;
  });
}

/** Typical value of a daily series as the baseline sees it: the median of the `span` days before
 * the latest `skip` days (D7 leaves out the current flow window the same way). */
export function typicalOf(series: (number | null)[], span: number, skip: number): number | null {
  const end = series.length - skip;
  const vals = series.slice(Math.max(0, end - span), end).filter((v): v is number => v !== null);
  return median(vals);
}

export interface WhereRow {
  ccmd: string;
  seen: number;
  typical: number | null;
  z: number | null;
  byType: Record<string, number>;
}

/** Aircraft seen per CCMD, most first. */
export function whereNow(th: Theaters, family: string, ccmds: string[]): WhereRow[] {
  return th.rows
    .filter((r) => r.family === family && ccmds.includes(r.ccmd))
    .map((r) => ({
      ccmd: r.ccmd,
      seen: r.inventory.observed,
      typical: r.inventory.median,
      z: r.inventory.z,
      byType: r.by_type,
    }))
    .sort((a, b) => b.seen - a.seen || ccmds.indexOf(a.ccmd) - ccmds.indexOf(b.ccmd));
}

/** Hours between a position's fix and the end of the day (or `now` on a live day). */
export function ageHours(p: PositionRow, at: string): number {
  return (Date.parse(at) - Date.parse(p.ts)) / 3_600_000;
}

/** The date `i` days before `last` (ISO), for chart axes. */
export function dayBefore(last: string, back: number): string {
  const d = new Date(`${last}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - back);
  return d.toISOString().slice(0, 10);
}
