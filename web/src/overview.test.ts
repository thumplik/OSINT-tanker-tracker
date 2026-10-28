import { describe, expect, it } from "vitest";
import { dayBefore, standout, totalSeries, typicalOf, whereNow } from "./overview";
import type { AlertRow, DayView, Theaters } from "./types";

const alert = (id: string, family: string, extra: Partial<AlertRow> = {}): AlertRow => ({
  alert_id: id,
  tier: "official",
  detector: "D7",
  region: "centcom",
  window_start: "2026-06-23T00:00:00Z",
  window_end: "2026-06-30T00:00:00Z",
  observed: 84,
  expected: 11,
  score: 7,
  severity: "low",
  status: "new",
  evidence: { family },
  sortie_ids: [],
  created_at: "2026-06-30T03:40:00Z",
  ...extra,
});

const day = (date: string, alerts: AlertRow[], indicators: AlertRow[]): DayView =>
  ({
    meta: { date, tier: "official" },
    alerts,
    indicators,
  }) as unknown as DayView;

describe("what stands out", () => {
  it("shows indicators before the official run existed, one family only", () => {
    const ind = [alert("a", "tanker"), alert("b", "strategic_airlift", { tier: "indicator" })];
    const s = standout(day("2026-06-29", [], ind), "tanker", "2026-10-27");
    expect(s.source).toBe("indicators");
    expect(s.shown.map((a) => a.alert_id)).toEqual(["a"]);
  });

  it("shows stored alerts once the official run exists, holding back suppressed ones", () => {
    const stored = [
      alert("weak", "tanker", { score: 3.1 }),
      alert("held", "tanker", { status: "suppressed" }),
      alert("strong", "tanker", { score: -6 }),
    ];
    const s = standout(day("2026-10-28", stored, [alert("x", "tanker")]), "tanker", "2026-10-27");
    expect(s.source).toBe("alerts");
    expect(s.shown.map((a) => a.alert_id)).toEqual(["strong", "weak"]);
    expect(s.held.map((a) => a.alert_id)).toEqual(["held"]);
  });
});

const th = {
  rows: [
    {
      ccmd: "eucom",
      family: "tanker",
      by_type: { K35R: 3 },
      inventory: { observed: 3, median: 5, n: 50, z: -1 },
    },
    {
      ccmd: "centcom",
      family: "tanker",
      by_type: { K35R: 80 },
      inventory: { observed: 84, median: 11, n: 50, z: 7 },
    },
    {
      ccmd: "centcom",
      family: "strategic_airlift",
      by_type: {},
      inventory: { observed: 9, median: 9, n: 50, z: 0 },
    },
  ],
  history: { "eucom|tanker": [1, null, 3], "centcom|tanker": [2, 2, 84] },
} as unknown as Theaters;

describe("where they are and over time", () => {
  it("lists one family by aircraft seen, most first", () => {
    expect(whereNow(th, "tanker", ["eucom", "centcom"]).map((r) => [r.ccmd, r.seen])).toEqual([
      ["centcom", 84],
      ["eucom", 3],
    ]);
  });

  it("totals over CCMDs and keeps missing days missing", () => {
    expect(totalSeries(th, "tanker", ["eucom", "centcom"])).toEqual([3, null, 87]);
  });

  it("takes the typical value from before the latest days", () => {
    expect(typicalOf([1, 2, 3, null, 100], 4, 1)).toBe(2);
    expect(typicalOf([null], 4, 0)).toBeNull();
  });

  it("counts days back in UTC", () => {
    expect(dayBefore("2026-03-01", 1)).toBe("2026-02-28");
  });
});
