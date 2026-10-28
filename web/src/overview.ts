// Overview (front page): what stands out, where the aircraft are, how that compares over time.
// Pure helpers over one day file; one family at a time (families are never summed together).
import type { AirfieldInfo, AirfieldStat, AlertRow, DayView, PositionRow, Theaters } from "./types";

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

export interface FieldGroup extends AirfieldInfo {
  code: string;
  ground: PositionRow[]; // last seen on the ground here
  lost: PositionRow[]; // last flight began here, then lost from view in the air
}

export interface Grouped {
  fields: FieldGroup[]; // most aircraft on the ground first
  airborne: PositionRow[]; // still being tracked in the air at the end of the data
  lostUnknown: PositionRow[]; // lost from view in the air, departure not known
  groundUnknown: PositionRow[]; // on the ground, airfield not identified
}

const latestFirst = (a: PositionRow, b: PositionRow) =>
  b.ts.localeCompare(a.ts) || a.hex.localeCompare(b.hex);

/** Aircraft by where they were last seen: on the ground at an airfield, still airborne, or lost
 * from view in the air (credited to the airfield their last flight left from). */
export function groupPositions(
  positions: PositionRow[],
  airfields: Record<string, AirfieldInfo>,
): Grouped {
  const fields = new Map<string, FieldGroup>();
  const field = (code: string | null): FieldGroup | null => {
    const info = code ? airfields[code] : undefined;
    if (!code || !info) return null;
    if (!fields.has(code)) fields.set(code, { code, ...info, ground: [], lost: [] });
    return fields.get(code)!;
  };
  const out: Grouped = { fields: [], airborne: [], lostUnknown: [], groundUnknown: [] };
  for (const p of positions) {
    if (p.quality === "last_seen") out.airborne.push(p);
    else if (p.quality === "ground") {
      const f = field(p.airfield);
      if (f) f.ground.push(p);
      else out.groundUnknown.push(p);
    } else {
      const f = field(p.from_airfield);
      if (f) f.lost.push(p);
      else out.lostUnknown.push(p);
    }
  }
  for (const f of fields.values()) {
    f.ground.sort(latestFirst);
    f.lost.sort(latestFirst);
  }
  out.fields = [...fields.values()].sort(
    (a, b) =>
      b.ground.length - a.ground.length ||
      b.lost.length - a.lost.length ||
      a.code.localeCompare(b.code),
  );
  out.airborne.sort(latestFirst);
  out.lostUnknown.sort(latestFirst);
  out.groundUnknown.sort(latestFirst);
  return out;
}

export type CardSort = "ground" | "above" | "lost" | "name";

/** Airfield cards of one family, filtered by CCMD and a search string, then sorted. */
export function airfieldCards(
  stats: AirfieldStat[],
  airfields: Record<string, AirfieldInfo>,
  family: string,
  ccmd: string | null,
  query: string,
  sort: CardSort,
): AirfieldStat[] {
  const q = query.trim().toLowerCase();
  const above = (s: AirfieldStat) => s.ground - (s.mean ?? 0);
  const key: Record<CardSort, (a: AirfieldStat, b: AirfieldStat) => number> = {
    ground: (a, b) => b.ground - a.ground || b.lost + b.airborne - (a.lost + a.airborne),
    above: (a, b) => above(b) - above(a),
    lost: (a, b) => b.lost + b.airborne - (a.lost + a.airborne),
    name: () => 0,
  };
  return stats
    .filter((s) => s.family === family && airfields[s.airfield])
    .filter((s) => !ccmd || airfields[s.airfield].ccmd === ccmd)
    .filter(
      (s) =>
        !q ||
        s.airfield.toLowerCase().includes(q) ||
        airfields[s.airfield].name.toLowerCase().includes(q),
    )
    .sort((a, b) => key[sort](a, b) || a.airfield.localeCompare(b.airfield));
}

/** "record high" / "record low" when today is outside the window's range. */
export function extreme(s: AirfieldStat): "high" | "low" | null {
  if (s.max === null || s.min === null || s.days === 0) return null;
  if (s.ground > s.max) return "high";
  if (s.ground < s.min) return "low";
  return null;
}
