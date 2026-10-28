// Shapes of the files written by `tw export` (src/tw/views/day.py). Schema version 3.

export type Tier = "official" | "provisional";

export interface Region {
  id: string;
  name: string;
  status: "watch" | "alerting";
  boxes: [number, number, number, number][]; // [lat_min, lat_max, lon_min, lon_max]
}

export interface Ccmd {
  id: string;
  name: string;
  status: "watch" | "alerting";
  anchor: [number, number]; // [lon, lat]
  cells: string[]; // H3 res-2 cells whose centre is in this CCMD (boundaries approximate)
}

export interface SiteMeta {
  schema: number;
  regions: Region[];
  ccmds?: Ccmd[]; // schema 2+
  families: Record<string, string[]>;
  attribution: string[];
  official_alerts_from: string | null;
}

export interface IndexDay {
  date: string;
  tier: Tier;
  alerts: number;
  provisional_alerts: number;
}

export interface SiteIndex {
  schema: number;
  days: IndexDay[];
  latest: string | null;
}

export interface Heat {
  families: string[];
  cells: string[];
  rows: number[][]; // r4: [cell, hour, family, n]; r6: [cell, family, n]
}

export interface RegionRow {
  region: string;
  family: string;
  status: Region["status"] | null;
  observed: number;
  aircraft: number;
  median: number | null;
  mad: number | null;
  n_days: number;
  z: number | null;
}

export interface CoverageRow {
  region: string;
  aircraft_hours: number;
  median: number | null;
  ratio: number | null;
}

export interface AlertRow {
  alert_id: string;
  tier: Tier | "indicator"; // indicator: what the day's numbers flag; computed, never stored
  detector: string;
  region: string;
  window_start: string;
  window_end: string;
  observed: number;
  expected: number;
  score: number;
  severity: string;
  status: string;
  evidence: Record<string, unknown>;
  sortie_ids: string[];
  created_at: string;
}

export interface SortieRow {
  sortie_id: string;
  hex: string;
  type: string | null;
  families: string[];
  callsign: string | null;
  start: string;
  end: string;
  minutes: number;
  origin: string | null;
  dest: string | null;
  regions: string[];
  max_alt: number | null;
  end_reason: string | null;
  is_open: boolean;
  counted: boolean;
}

export interface Judged {
  observed: number;
  median: number | null;
  n: number;
  z: number | null; // null: insufficient baseline
}

export interface TheaterRow {
  ccmd: string;
  family: string;
  by_type: Record<string, number>;
  inventory: Judged;
  entries: Judged | null; // null: a day of the window has no data
  exits: Judged | null;
  transits: Judged | null; // trips from home that ended in this CCMD
}

export interface MoveRow {
  hex: string;
  type: string;
  family: string;
  ts: string;
  src: string;
  dst: string;
  quality: "ground" | "aloft" | "last_seen";
  sortie_id: string;
  src_airfield: string | null;
  dst_airfield: string | null;
}

export interface TransitRow {
  hex: string;
  type: string;
  family: string;
  depart_ts: string;
  arrive_ts: string;
  resolved_ts: string | null;
  dest: string;
  via: string[];
  resolution: "returned" | "stayed" | "en_route";
  dest_airfield: string | null;
}

export interface PositionRow {
  hex: string;
  type: string;
  family: string;
  callsign: string | null;
  ts: string; // the aircraft's latest fix up to the end of the day
  lat: number;
  lon: number;
  ccmd: string;
  quality: "ground" | "aloft" | "last_seen";
  airfield: string | null;
}

export interface Theaters {
  home: string;
  baseline_as_of: string | null;
  window_days: number;
  window_complete: boolean;
  max_age_days: number;
  dwell_hours: number;
  min_blocks: number;
  rows: TheaterRow[];
  history: Record<string, (number | null)[]>; // "ccmd|family" -> daily inventory, oldest first
  coverage: {
    ccmd: string;
    aircraft_hours: number;
    median: number | null;
    ratio: number | null;
  }[];
  moves: MoveRow[];
  transits: TransitRow[]; // resolved within the window
  en_route: TransitRow[];
  positions?: PositionRow[]; // schema 3+: one per aircraft counted in `rows` inventory
}

export interface DayView {
  meta: {
    schema: number;
    date: string;
    tier: Tier;
    baseline_as_of: string | null;
    weekday: number;
    min_sortie_minutes: number;
    min_baseline_samples: number;
    baseline_window_days: number;
    d1_min_z: number;
    coverage_min_ratio: number;
    coverage_max_ratio: number;
    generated_at: string;
    config_hash: string;
    code_version: string;
  };
  heat: Heat;
  regions: RegionRow[];
  coverage: CoverageRow[];
  alerts: AlertRow[];
  sorties: SortieRow[];
  theaters?: Theaters; // schema 2+
  indicators?: AlertRow[]; // schema 3+
}

export interface DayR6 extends Heat {
  date: string;
  tier: Tier;
}
