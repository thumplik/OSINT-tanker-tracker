import { describe, expect, it } from "vitest";
import { forFamily } from "./panels";
import { byLabel, transitArcs } from "./theater";
import type { AlertRow, Ccmd, TransitRow } from "./types";

const ccmd = (id: string, anchor: [number, number]): Ccmd => ({
  id,
  name: id.toUpperCase(),
  status: "watch",
  anchor,
  cells: [],
});
const trip = (dest: string, family: string): TransitRow => ({
  hex: "ae0001",
  type: "C17",
  family,
  depart_ts: "2026-06-01T00:00:00Z",
  arrive_ts: "2026-06-01T12:00:00Z",
  resolved_ts: "2026-06-03T12:00:00Z",
  dest,
  via: [],
  resolution: "stayed",
  dest_airfield: null,
});

describe("theater helpers", () => {
  it("merges type codes that are the same aircraft", () => {
    expect(byLabel({ K35R: 66, B762: 10, KC46: 8 })).toEqual([
      ["KC-135R", 66],
      ["KC-46A", 18],
    ]);
  });

  it("draws one arc per destination for one family only", () => {
    const ccmds = [ccmd("northcom", [-98, 39]), ccmd("centcom", [47, 28]), ccmd("eucom", [10, 50])];
    const trips = [trip("centcom", "tanker"), trip("centcom", "tanker"), trip("eucom", "strategic_airlift")];
    expect(transitArcs(trips, ccmds, "northcom", "tanker")).toEqual([
      { dest: "centcom", count: 2, from: [-98, 39], to: [47, 28] },
    ]);
    expect(transitArcs(trips, ccmds, "northcom", "all").map((a) => a.count)).toEqual([2, 1]);
  });
});

describe("alerts by family", () => {
  const a = (family: string) => ({ evidence: { family } }) as unknown as AlertRow;
  it("never mixes families unless asked", () => {
    const rows = [a("tanker"), a("strategic_airlift")];
    expect(forFamily(rows, "tanker")).toEqual([rows[0]]);
    expect(forFamily(rows, "all")).toEqual(rows);
  });
});
