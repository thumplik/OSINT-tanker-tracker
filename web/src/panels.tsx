import { useMemo, useState } from "react";
import { byLabel, route, TYPE_LABEL } from "./theater";
import type { AlertRow, DayView, Judged, SiteMeta, SortieRow } from "./types";
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
        {typeof a.evidence.family === "string" && (
          <span className="badge small">{familyLabel(a.evidence.family)}</span>
        )}
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

/** Alerts of one family ("all": every family). Families are never mixed in one count. */
export function forFamily(rows: AlertRow[], family: string): AlertRow[] {
  return family === "all" ? rows : rows.filter((a) => a.evidence.family === family);
}

export function AlertsPanel({
  view,
  meta,
  family,
}: {
  view: DayView;
  meta: SiteMeta | null;
  family: string;
}) {
  const [showSuppressed, setShowSuppressed] = useState(false);
  const sorties = useMemo(() => new Map(view.sorties.map((s) => [s.sortie_id, s])), [view]);
  const mine = forFamily(view.alerts, family);
  const official = bySeverity(mine.filter((a) => a.tier === "official"));
  const provisional = bySeverity(mine.filter((a) => a.tier === "provisional"));
  const visible = (rows: AlertRow[]) =>
    showSuppressed ? rows : rows.filter((a) => a.status !== "suppressed");
  const hidden = mine.filter((a) => a.status === "suppressed").length;
  const others = view.alerts.length - mine.length;
  const from = meta?.official_alerts_from;

  return (
    <div className="alerts">
      <details className="about">
        <summary>How to read this</summary>
        <p>
          Alerts compare a day with the same weekday over the previous{" "}
          {view.meta.baseline_window_days} days. Unusual counts are worth watching; they say nothing
          about purpose.
        </p>
      </details>
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
              : `Nothing above normal for ${familyLabel(family).toLowerCase()} on this day.`}
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
      {family !== "all" && others > 0 && (
        <p className="muted small">
          {others} more for other families: pick them in the family menu.
        </p>
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
      <details className="about">
        <summary>How to read this</summary>
        <p>
          Sorties of {view.meta.min_sortie_minutes}+ minutes touching each region, against the
          median for this weekday
          {view.meta.baseline_as_of ? ` (baseline as of ${view.meta.baseline_as_of})` : ""}.
          Receiver coverage is all aircraft seen, vs usual; far off usual makes counts unreliable.
        </p>
      </details>
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
        {rows.length} sorties
        {rows.length > MAX_ROWS && `, first ${MAX_ROWS} shown`}. Sorties under{" "}
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

// ---- Theaters (CCMDs) ---------------------------------------------------------------------

function Spark({ values, median }: { values: (number | null)[]; median: number | null }) {
  const w = 120;
  const h = 28;
  const nums = values.filter((v): v is number => v !== null);
  const max = Math.max(1, ...nums, median ?? 0);
  const x = (i: number) => (i / Math.max(1, values.length - 1)) * w;
  const y = (v: number) => h - 2 - (v / max) * (h - 4);
  let d = "";
  values.forEach((v, i) => {
    if (v === null) return;
    d += `${d && values[i - 1] !== null ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
  });
  return (
    <svg className="spark" width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      {median !== null && (
        <line x1={0} x2={w} y1={y(median)} y2={y(median)} className="spark-typ" />
      )}
      <path d={d} className="spark-line" />
    </svg>
  );
}

function zText(j: { z: number | null }): string {
  return j.z === null ? "" : ` (z ${j.z > 0 ? "+" : ""}${j.z.toFixed(1)})`;
}

function Flow({ label, j, minZ }: { label: string; j: Judged | null; minZ: number }) {
  if (!j) return <span className="muted">{label} –</span>;
  const hi = j.z !== null && j.z >= minZ;
  return (
    <span className={hi ? "flow hi" : "flow"} title={`typical ${j.median ?? "n/a"}${zText(j)}`}>
      {label} <b>{j.observed}</b>
      <span className="muted"> /{j.median ?? "–"}</span>
    </span>
  );
}

export function TheatersPanel({
  view,
  meta,
  family,
  selected,
  onCcmd,
}: {
  view: DayView;
  meta: SiteMeta | null;
  family: string;
  selected: string | null;
  onCcmd: (id: string | null) => void;
}) {
  const th = view.theaters;
  const [showMoves, setShowMoves] = useState(false);
  if (!th) return <p className="muted">This day was exported before theater data existed.</p>;
  const name = (id: string) => regionName(meta, id);
  const fams = family === "all" ? [...new Set(th.rows.map((r) => r.family))] : [family];
  const cov = new Map(th.coverage.map((c) => [c.ccmd, c]));
  const { coverage_min_ratio: lo, coverage_max_ratio: hi, d1_min_z: minZ } = view.meta;
  const ccmds = meta?.ccmds?.map((c) => c.id) ?? [...new Set(th.rows.map((r) => r.ccmd))];
  const mine = <T extends { family: string }>(rows: T[]) =>
    rows.filter((r) => family === "all" || r.family === family);
  const transits = mine(th.transits).filter((t) => t.dest !== th.home);
  const enRoute = mine(th.en_route);
  const moves = mine(th.moves);
  return (
    <div className="theaters">
      <details className="about">
        <summary>How to read this</summary>
        <p>
          Aircraft by combatant command, from where flights start and end (boundaries approximate).
          "Seen in" counts aircraft whose latest position in the last {th.max_age_days} days was
          there; aircraft not broadcasting aren't counted, so these are floors. Flows are the last{" "}
          {th.window_days} days, shown as observed / typical.
        </p>
      </details>
      {!th.window_complete && (
        <p className="muted small">
          A day of the {th.window_days}-day window has no data: flows not shown.
        </p>
      )}
      {ccmds.map((c) => {
        const cv = cov.get(c);
        const off = cv?.ratio != null && (cv.ratio < lo || cv.ratio > hi);
        return (
          <article
            key={c}
            className={`card ccmd ${c === selected ? "sel" : ""}`}
            onClick={() => onCcmd(c === selected ? null : c)}
          >
            <header>
              <h3>{name(c)}</h3>
              {c === th.home && <span className="muted small">home</span>}
              {cv?.ratio != null && (
                <span className={`small ${off ? "warn-text" : "muted"}`}>
                  coverage {Math.round(cv.ratio * 100)}% of usual
                  {off ? ": counts unreliable" : ""}
                </span>
              )}
            </header>
            {fams.map((f) => {
              const r = th.rows.find((x) => x.ccmd === c && x.family === f);
              if (!r) return null;
              const inv = r.inventory;
              const big = inv.z !== null && Math.abs(inv.z) >= minZ;
              return (
                <div key={f} className="trow">
                  <div className="tfam">{familyLabel(f)}</div>
                  <div className="tinv">
                    <span className={big ? "tnum hi" : "tnum"}>{inv.observed}</span>
                    <span className="muted small">
                      {" "}
                      seen · typical {inv.median ?? "–"}
                      {zText(inv)}
                    </span>
                    <div className="muted small">
                      {byLabel(r.by_type)
                        .map(([t, n]) => `${n} ${t}`)
                        .join(" · ") || "none"}
                    </div>
                  </div>
                  <Spark values={th.history[`${c}|${f}`] ?? []} median={inv.median} />
                  <div className="tflows small">
                    <Flow label="in" j={r.entries} minZ={minZ} />
                    <Flow label="out" j={r.exits} minZ={minZ} />
                    {c !== th.home && <Flow label="from US" j={r.transits} minZ={minZ} />}
                  </div>
                </div>
              );
            })}
          </article>
        );
      })}

      <h2>Transits from the US, last {th.window_days} days</h2>
      {transits.length === 0 && <p className="muted">None resolved.</p>}
      <ul className="ids">
        {transits.map((t) => (
          <li key={`${t.hex}-${t.depart_ts}`}>
            <b>{t.hex}</b> {TYPE_LABEL[t.type] ?? t.type} → {route(t, name)}
            {t.dest_airfield && ` (${t.dest_airfield})`}{" "}
            <span className="muted">
              left {utc(t.depart_ts).slice(5)}, {t.resolution === "returned" ? "back" : "stayed"}
            </span>
          </li>
        ))}
      </ul>
      {enRoute.length > 0 && (
        <>
          <h2>Away from the US, not settled yet</h2>
          <ul className="ids">
            {enRoute.map((t) => (
              <li key={`${t.hex}-${t.depart_ts}`}>
                <b>{t.hex}</b> {TYPE_LABEL[t.type] ?? t.type} farthest so far {route(t, name)}{" "}
                <span className="muted">left {utc(t.depart_ts).slice(5)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      <button className="link" onClick={() => setShowMoves(!showMoves)}>
        {showMoves ? "Hide" : "Show"} {moves.length} CCMD changes on this day
      </button>
      {showMoves && (
        <ul className="ids">
          {moves.map((m) => (
            <li key={`${m.hex}-${m.ts}`}>
              {hhmm(m.ts)} <b>{m.hex}</b> {TYPE_LABEL[m.type] ?? m.type} {name(m.src)} →{" "}
              {name(m.dst)}
              {m.dst_airfield && ` (${m.dst_airfield})`}{" "}
              <span className="muted">
                {m.quality === "ground"
                  ? "landed"
                  : m.quality === "aloft"
                    ? "lost from view"
                    : "airborne"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
