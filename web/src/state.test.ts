import { describe, expect, it } from "vitest";
import { DEFAULT_STATE, parseState, serializeState, stepDate } from "./state";

describe("URL state", () => {
  it("round-trips a full view", () => {
    const s = parseState(
      "?date=2026-10-27&family=strategic_airlift&region=europe&ccmd=centcom&hour=14&tab=sorties",
    );
    expect(s).toEqual({
      date: "2026-10-27",
      family: "strategic_airlift",
      region: "europe",
      ccmd: "centcom",
      res: 4,
      hour: 14,
      tab: "sorties",
    });
    expect(parseState(serializeState(s))).toEqual(s);
  });

  it("falls back to defaults on junk", () => {
    expect(parseState("?date=yesterday&hour=25&res=9&tab=x")).toEqual(DEFAULT_STATE);
    expect(serializeState(DEFAULT_STATE)).toBe("");
  });

  it("opens on tankers, never on mixed families", () => {
    expect(DEFAULT_STATE.family).toBe("tanker");
    expect(parseState("?tab=theaters").tab).toBe("theaters");
  });

  it("drops the hour for the whole-day r6 view", () => {
    expect(serializeState({ ...DEFAULT_STATE, res: 6, hour: 3 })).toBe("?res=6");
  });

  it("steps between available days only", () => {
    const days = ["2026-10-25", "2026-10-26", "2026-10-28"];
    expect(stepDate(days, "2026-10-26", 1)).toBe("2026-10-28");
    expect(stepDate(days, "2026-10-25", -1)).toBeNull();
    expect(stepDate(days, "2026-10-27", 1)).toBeNull();
  });
});
