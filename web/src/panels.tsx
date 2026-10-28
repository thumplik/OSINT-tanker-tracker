import { useMemo, useState } from "react";
import type { AlertRow, DayView, SiteMeta, SortieRow } from "./types";
import {
  alertNumbers,
  alertTitle,
  familyLabel,
  hhmm,
  regionName,
  SEVERITY_LABEL,
  utc,
} from "./wording";

// ---- Alerts -------------------------------------------------------------------------------

function AlertCard({
  a,
  meta,
  sorties,
}: {
  a: AlertRow;
  meta: SiteMeta | null;
  sorties: Map<string, SortieRow>;
}) {
  const [open, setOpen] = useState(false);
  const suppressed = a.status === "suppressed";
  const by = (a.evidence.suppressed_by as string[] | undefined) ?? [];
  return (
    <article className={`card ${suppressed ? "suppressed" : ""}`}>
      <header>
        <span className={`sev sev-${a.severity}`}>
          <i aria-hidden="true" />
          {SEVERITY_LABEL[a.severity] ?? a.severity}
        </span>
        <span className="det">{a.detector}</span>
        {a.tier === "provisional" && <span className="badge provisional small">provisional</span>}
        {suppressed && <span className="muted small">suppressed: {by.join(", ") || "guard"}</span>}
      </header>
      <h3>{alertTitle(a, meta)}</h3>
      <p className="nums">{alertNumbers(a)}</p>
      <p className="muted small">
        {utc(a.window_start)} – {utc(a.window_end)}
        {typeof a.evidence.coverage_ratio === "number" &&
          ` · receiver coverage ${Math.round(a.evidence.coverage_ratio * 100)}% of usual`}
        {a.evidence.coverage === "unchecked" && " · coverage not checked yet"}
      </p>
      {a.sortie_ids.length > 0 && (
        <button className="link" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? "Hide" : "Show"} {a.sortie_ids.length} sorties
        </button>
      )}
      {open && (
        <ul className="ids">
          {a.sortie_ids.map((id) => {
            const s = sorties.get(id);
            return (
              <li key={id}>
                {s ? (
                  <>
                    <b>{s.callsign ?? s.hex}</b> {s.type} {s.origin ?? "?"} → {s.dest ?? "?"}{" "}
                    <span className="muted">
                      {hhmm(s.start)}–{hhmm(s.end)}
                    </span>
                  </>
                ) : (
                  <span className="muted">{id} (earlier day)</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </article>
  );
}

const RANK: Record<string, number> = { high: 0, medium: 1, low: 2 };

export function bySeverity(rows: AlertRow[]): AlertRow[] {
  return [...rows].sort(
    (a, b) =>
      (RANK[a.severity] ?? 3) - (RANK[b.severity] ?? 3) ||
      a.created_at.localeCompare(b.created_at) ||
      a.alert_id.localeCompare(b.alert_id),
  );
}

export function AlertsPanel({
  view,
  meta,
}: {
  view: DayView;
  meta: SiteMeta | null;
}) {
  const [showSuppressed, setShowSuppressed] = useState(false);
  const sorties = useMemo(() => new Map(view.sorties.map((s) => [s.sortie_id, s])), [view]);
  const official = bySeverity(view.alerts.filter((a) => a.tier === "official"));
  const provisional = bySeverity(view.alerts.filter((a) => a.tier === "provisional"));
  const visible = (rows: AlertRow[]) =>
    showSuppressed ? rows : rows.filter((a) => a.status !== "suppressed");
  const hidden = view.alerts.filter((a) => a.status === "suppressed").length;
  const from = meta?.official_alerts_from;

  return (
    <div className="alerts">
      <p className="explain">
        Alerts compare a day with the same weekday over the previous{" "}
        {view.meta.baseline_window_days} days. Unusual counts are worth watching; they say nothing
        about purpose.
      </p>
      <h2>Official</h2>
      {visible(official).map((a) => (
        <AlertCard key={a.alert_id} a={a} meta={meta} sorties={sorties} />
      ))}
      {official.length === 0 && (
        <p className="muted">
          {view.meta.tier === "provisional"
            ? "The official run happens after the day's history release (around 03:40Z the next day)."
            : from && view.meta.date < from
              ? `No stored alerts: official alerts start on ${from}. Earlier days feed the backtest only.`
              : "Nothing above normal on this day."}
        </p>
      )}
      {provisional.length > 0 && (
        <>
          <h2>
            Provisional <span className="muted small">live data, coverage not checked</span>
          </h2>
          {visible(provisional).map((a) => (
            <AlertCard key={a.alert_id} a={a} meta={meta} sorties={sorties} />
          ))}
        </>
      )}
      {hidden > 0 && (
        <button className="link" onClick={() => setShowSuppressed(!showSuppressed)}>
          {showSuppressed ? "Hide" : "Show"} {hidden} suppressed (coverage or repeat)
        </button>
      )}
    </div>
  );
}

// ---- Regions ------------------------------------------------------------------------------

export function RegionsPanel({
  view,
  meta,
  family,
  selected,
  onRegion,
}: {
  view: DayView;
  meta: SiteMeta | null;
  family: string;
  selected: string | null;
  onRegion: (id: string | null) => void;
}) {
  const cov = new Map(view.coverage.map((c) => [c.region, c]));
  const fams = family === "all" ? [...new Set(view.regions.map((r) => r.family))] : [family];
  const { coverage_min_ratio: lo, coverage_max_ratio: hi } = view.meta;
  return (
    <div className="regions">
      <p className="explain">
        Sorties of {view.meta.min_sortie_minutes}+ minutes touching each region, against the median for this weekday
        {view.meta.baseline_as_of ? ` (baseline as of ${view.meta.baseline_as_of})` : ""}. Receiver
        coverage is all aircraft seen, vs usual; far off usual makes counts unreliable.
      </p>
      {fams.map((f) => (
        <table key={f} className="tbl">
          <caption>{familyLabel(f)}</caption>
          <thead>
            <tr>
              <th>Region</th>
              <th className="num">Sorties</th>
              <th className="num">Typical</th>
              <th className="num" title="Robust z-score: distance from typical in MAD units">
                z
              </th>
              <th className="num">Coverage</th>
            </tr>
          </thead>
          <tbody>
            {view.regions
              .filter((r) => r.family === f)
              .map((r) => {
                const c = cov.get(r.region);
                const off = c?.ratio != null && (c.ratio < lo || c.ratio > hi);
                return (
                  <tr
                    key={r.region}
                    className={r.region === selected ? "sel" : ""}
                    onClick={() => onRegion(r.region === selected ? null : r.region)}
                  >
                    <td>
                      {regionName(meta, r.region)}
                      {r.status === "alerting" && <span className="tag">alerting</span>}
                    </td>
                    <td className="num">{r.observed}</td>
                    <td className="num muted">{r.median ?? "–"}</td>
                    <td className={`num ${r.z !== null && r.z >= view.meta.d1_min_z ? "hi" : ""}`}>
                      {r.z === null ? "–" : (r.z > 0 ? "+" : "") + r.z.toFixed(1)}
                    </td>
                    <td className={`num ${off ? "warn" : "muted"}`}>
                      {c?.ratio == null ? "–" : `${Math.round(c.ratio * 100)}%`}
                      {off && <span className="sr"> (outside usual range)</span>}
                    </td>
                  </tr>
                );
              })}
          </tbody>
        </table>
      ))}
      {view.meta.tier === "provisional" && (
        <p className="muted small">Live day: coverage is known only after the official run.</p>
      )}
    </div>
  );
}

// ---- Sorties ------------------------------------------------------------------------------

const MAX_ROWS = 400;

export function SortiesPanel({
  view,
  meta,
  family,
  region,
  onRegion,
}: {
  view: DayView;
  meta: SiteMeta | null;
  family: string;
  region: string | null;
  onRegion: (id: string | null) => void;
}) {
  const [q, setQ] = useState("");
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return view.sorties.filter(
      (s) =>
        (family === "all" || s.families.includes(family)) &&
        (!region || s.regions.includes(region)) &&
        (!needle ||
          [s.callsign, s.hex, s.type, s.origin, s.dest].some((v) =>
            v?.toLowerCase().includes(needle),
          )),
    );
  }, [view, family, region, q]);
  return (
    <div className="sorties">
      <div className="filters">
        <input
          type="search"
          placeholder="Callsign, hex, type, airfield"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Filter sorties"
        />
        {region && (
          <button className="chip" onClick={() => onRegion(null)}>
            {regionName(meta, region)} ✕
          </button>
        )}
      </div>
      <p className="muted small">
        {rows.length} sorties{rows.length > MAX_ROWS && `, first ${MAX_ROWS} shown`}. Sorties under{" "}
        {view.meta.min_sortie_minutes} min are listed but not counted.
      </p>
      <table className="tbl">
        <thead>
          <tr>
            <th>Start</th>
            <th>Aircraft</th>
            <th>Route</th>
            <th className="num">Min</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, MAX_ROWS).map((s) => (
            <tr key={s.sortie_id} className={s.counted ? "" : "faint"}>
              <td className="mono">{hhmm(s.start)}</td>
              <td>
                <b>{s.callsign ?? s.hex}</b> <span className="muted">{s.type}</span>
              </td>
              <td>
                {s.origin ?? "?"} → {s.is_open ? "airborne" : (s.dest ?? "?")}
              </td>
              <td className="num">{s.minutes}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
