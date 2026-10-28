import { describe, expect, it } from "vitest";
import { bucket, cellsFor, RAMP } from "./heat";

const r4 = {
  families: ["tanker", "all"],
  cells: ["a", "b"],
  rows: [
    [0, 9, 0, 2],
    [0, 10, 0, 3],
    [1, 9, 0, 1],
    [0, 9, 1, 4],
  ],
};

describe("heat cells", () => {
  it("shows one hour as aircraft", () => {
    expect(cellsFor(r4, "tanker", 9)).toEqual([
      { hex: "a", n: 2 },
      { hex: "b", n: 1 },
    ]);
  });

  it("sums the day into aircraft-hours", () => {
    expect(cellsFor(r4, "tanker", null)).toEqual([
      { hex: "a", n: 5 },
      { hex: "b", n: 1 },
    ]);
  });

  it("reads r6 day rows and ignores unknown families", () => {
    const r6 = { families: ["tanker"], cells: ["x"], rows: [[0, 0, 7]] };
    expect(cellsFor(r6, "tanker", 5)).toEqual([{ hex: "x", n: 7 }]);
    expect(cellsFor(r6, "bomber", null)).toEqual([]);
  });

  it("buckets on a log scale within the ramp", () => {
    expect(bucket(1, 100)).toBe(0);
    expect(bucket(100, 100)).toBe(RAMP.length - 1);
    expect(bucket(10, 100)).toBe(3);
    expect(bucket(1, 1)).toBe(RAMP.length - 1);
  });
});
