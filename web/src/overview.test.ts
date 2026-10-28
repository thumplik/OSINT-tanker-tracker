import { describe, expect, it } from "vitest";
import { airfieldCards, extreme } from "./overview";
import { dayBefore, groupPositions, standout, totalSeries, typicalOf, whereNow } from "./overview";
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

describe("aircraft grouped by airfield", () => {
  const pos = (hex: string, quality: string, airfield: string | null, from: string | null) =>
    ({
      hex,
      quality,
      airfield,
      from_airfield: from,
      ts: `2026-06-29T1${hex.length}:00:00Z`,
    }) as never;
  const fields = {
    KCHS: { name: "Charleston", lat: 32.9, lon: -80.0, ccmd: "northcom" },
    OTBH: { name: "Al Udeid", lat: 25.1, lon: 51.3, ccmd: "centcom" },
  };

  it("puts ground aircraft at their airfield and lost ones at their departure", () => {
    const g = groupPositions(
      [
        pos("a", "ground", "KCHS", "OTBH"),
        pos("b", "ground", "KCHS", null),
        pos("c", "aloft", null, "KCHS"),
        pos("d", "last_seen", null, "OTBH"),
        pos("e", "aloft", null, null),
        pos("f", "ground", "ZZZZ", null), // not in the lookup
      ],
      fields,
    );
    expect(g.fields.map((f) => [f.code, f.ground.length, f.lost.length])).toEqual([["KCHS", 2, 1]]);
    expect(g.airborne.map((p) => p.hex)).toEqual(["d"]);
    expect(g.lostUnknown.map((p) => p.hex)).toEqual(["e"]);
    expect(g.groundUnknown.map((p) => p.hex)).toEqual(["f"]);
  });
});

describe("airfield cards", () => {
  const stat = (airfield: string, ground: number, mean: number, extra = {}) =>
    ({
      airfield,
      family: "tanker",
      ground,
      airborne: 0,
      lost: 0,
      min: 1,
      max: 9,
      mean,
      days: 50,
      series: [],
      ...extra,
    }) as never;
  const fields = {
    EGUN: { name: "RAF Mildenhall", lat: 52, lon: 0.5, ccmd: "eucom" },
    KCHS: { name: "Charleston", lat: 32.9, lon: -80, ccmd: "northcom" },
    OTBH: { name: "Al Udeid", lat: 25, lon: 51, ccmd: "centcom" },
  };
  const stats = [stat("EGUN", 8, 8), stat("KCHS", 5, 1), stat("OTBH", 0, 0, { lost: 4 })];

  it("sorts by aircraft on the ground, or by how far above average", () => {
    const ids = (sort: never) =>
      airfieldCards(stats, fields, "tanker", null, "", sort).map((s) => s.airfield);
    expect(ids("ground" as never)).toEqual(["EGUN", "KCHS", "OTBH"]);
    expect(ids("above" as never)).toEqual(["KCHS", "EGUN", "OTBH"]);
    expect(ids("lost" as never)[0]).toBe("OTBH");
  });

  it("filters by CCMD, name and family", () => {
    expect(airfieldCards(stats, fields, "tanker", "eucom", "", "ground").length).toBe(1);
    expect(airfieldCards(stats, fields, "tanker", null, "udeid", "ground")[0].airfield).toBe(
      "OTBH",
    );
    expect(airfieldCards(stats, fields, "strategic_airlift", null, "", "ground")).toEqual([]);
  });

  it("marks days outside the window's range", () => {
    expect(extreme(stat("X", 10, 5))).toBe("high");
    expect(extreme(stat("X", 0, 5))).toBe("low");
    expect(extreme(stat("X", 5, 5))).toBeNull();
  });
});
