import { useEffect, useMemo, useRef, useState } from "react";
import {
  dayBefore,
  type FieldGroup,
  type Grouped,
  standout,
  totalSeries,
  typicalOf,
  whereNow,
} from "./overview";
import { byLabel, TYPE_LABEL } from "./theater";
import type { AlertRow, DayView, PositionRow, SiteMeta } from "./types";
import { alertNumbers, alertTitle, familyLabel, hhmm, regionName } from "./wording";

// ---- Trend chart ---------------------------------------------------------------------------

const W = 320;

export function TrendChart({
  values,
  last,
  typical,
  height = 120,
  label,
}: {
  values: (number | null)[];
  last: string;
  typical: number | null;
  height?: number;
  label: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const box = useRef<SVGSVGElement>(null);
  const pad = { l: 4, r: 30, t: 8, b: 16 };
  const nums = values.filter((v): v is number => v !== null);
  const max = Math.max(1, ...nums, typical ?? 0);
  const n = values.length;
  const x = (i: number) => pad.l + (i / Math.max(1, n - 1)) * (W - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - v / max) * (height - pad.t - pad.b);
  let d = "";
  values.forEach((v, i) => {
    if (v === null) return;
    d += `${d && values[i - 1] != null ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
  });
  const lastVal = values[n - 1];
  const onMove = (e: React.PointerEvent) => {
    const r = box.current?.getBoundingClientRect();
    if (!r || n < 2) return;
    const px = ((e.clientX - r.left) / r.width) * W;
    const i = Math.round(((px - pad.l) / (W - pad.l - pad.r)) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, i)));
  };
  const months = [];
  for (let i = 0; i < n; i++) {
    const date = dayBefore(last, n - 1 - i);
    if (date.endsWith("-01"))
      months.push({
        i,
        text: date.slice(5, 7) === "01" ? date.slice(0, 4) : MONTH[Number(date.slice(5, 7)) - 1],
      });
  }
  const hv = hover !== null ? values[hover] : null;
  return (
    <div className="trend">
      <svg
        ref={box}
        viewBox={`0 0 ${W} ${height}`}
        role="img"
        aria-label={`${label}: ${n} days to ${last}; latest ${lastVal ?? "missing"}, typical ${typical ?? "unknown"}`}
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
      >
        <line className="axis" x1={pad.l} x2={W - pad.r} y1={y(0)} y2={y(0)} />
        <text className="tick" x={W - pad.r + 4} y={y(max) + 4}>
          {max}
        </text>
        <text className="tick" x={W - pad.r + 4} y={y(0) + 3}>
          0
        </text>
        {months.map((m) => (
          <text key={m.i} className="tick" x={x(m.i)} y={height - 3} textAnchor="middle">
            {m.text}
          </text>
        ))}
        {typical !== null && (
          <>
            <line className="typ" x1={pad.l} x2={W - pad.r} y1={y(typical)} y2={y(typical)} />
            <text className="tick" x={W - pad.r + 4} y={y(typical) + 3}>
              typ
            </text>
          </>
        )}
        <path className="series" d={d} />
        {lastVal != null && <circle className="dot" cx={x(n - 1)} cy={y(lastVal)} r={3.5} />}
        {hover !== null && (
          <>
            <line className="cross" x1={x(hover)} x2={x(hover)} y1={pad.t} y2={y(0)} />
            {hv != null && <circle className="dot" cx={x(hover)} cy={y(hv)} r={3.5} />}
          </>
        )}
      </svg>
      {hover !== null && (
        <div className="tip" style={{ left: `${(x(hover) / W) * 100}%` }}>
          {dayBefore(last, n - 1 - hover)}: <b>{hv ?? "no data"}</b>
        </div>
      )}
    </div>
  );
}

const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// ---- Aircraft by airfield ------------------------------------------------------------------

/** "06-29 14:22Z (3 h ago)": a time and its age at `at` (the end of the day, or now). */
function when(ts: string, at: string): string {
  const h = (Date.parse(at) - Date.parse(ts)) / 3_600_000;
  const ago = h < 1 ? "under 1 h" : h < 48 ? `${Math.round(h)} h` : `${Math.round(h / 24)} d`;
  return `${ts.slice(5, 10)} ${hhmm(ts)} (${ago} ago)`;
}

function Aircraft({ p, detail }: { p: PositionRow; detail: string }) {
  return (
    <li>
      {p.callsign && <b>{p.callsign} </b>}
      {TYPE_LABEL[p.type] ?? p.type}{" "}
      <a
        className="hexlink"
        href={`https://globe.adsb.lol/?icao=${p.hex}`}
        target="_blank"
        rel="noreferrer"
        title="Open this aircraft on globe.adsb.lol (registration, history)"
      >
        {p.hex}
      </a>
      <span className="muted small"> · {detail}</span>
    </li>
  );
}

export function AirfieldCard({
  f,
  at,
  name,
  onClose,
}: {
  f: FieldGroup;
  at: string;
  name: (id: string) => string;
  onClose: () => void;
}) {
  const card = useRef<HTMLElement>(null);
  useEffect(() => {
    card.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [f.code]);
  return (
    <article className="card field" ref={card}>
      <header>
        <h3>
          {f.code} <span className="muted">{f.name}</span>
        </h3>
        <button className="link close" onClick={onClose} aria-label="Close airfield">
          ✕
        </button>
      </header>
      <p className="muted small">{name(f.ccmd)}</p>
      <h4>On the ground ({f.ground.length})</h4>
      {f.ground.length === 0 && <p className="muted small">None last seen here.</p>}
      <ul className="ids">
        {f.ground.map((p) => (
          <Aircraft
            key={p.hex}
            p={p}
            detail={`${p.from_airfield ? `from ${p.from_airfield}, ` : ""}landed ${when(p.ts, at)}`}
          />
        ))}
      </ul>
      {f.lost.length > 0 && (
        <>
          <h4>Left here, then lost from view in the air ({f.lost.length})</h4>
          <p className="muted small">
            Possibly still flying, or landed where no receiver hears it.
          </p>
          <ul className="ids">
            {f.lost.map((p) => (
              <Aircraft
                key={p.hex}
                p={p}
                detail={`left ${p.departed ? when(p.departed, at) : "?"}; last seen ${when(p.ts, at)} over ${name(p.ccmd)}`}
              />
            ))}
          </ul>
        </>
      )}
    </article>
  );
}

const TOP_FIELDS = 8;

// ---- Panel ---------------------------------------------------------------------------------

function signed(n: number): string {
  return n > 0 ? `+${n}` : String(n);
}

function Standout({
  a,
  meta,
  onPick,
}: {
  a: AlertRow;
  meta: SiteMeta | null;
  onPick: (a: AlertRow) => void;
}) {
  const cov = a.evidence.coverage_ratio;
  return (
    <li>
      <button className="so" onClick={() => onPick(a)}>
        <span className="so-title">{alertTitle(a, meta)}</span>
        <span className="so-nums">{alertNumbers(a)}</span>
        <span className="so-meta">
          {a.detector}
          {typeof cov === "number" && ` · receiver coverage ${Math.round(cov * 100)}% of usual`}
          {a.evidence.coverage === "unchecked" && " · live data, coverage not checked yet"}
        </span>
      </button>
    </li>
  );
}

export function OverviewPanel({
  view,
  meta,
  family,
  ccmd,
  onCcmd,
  onRegion,
  grouped,
  field,
  onField,
  at,
}: {
  view: DayView;
  meta: SiteMeta | null;
  family: string;
  ccmd: string | null;
  onCcmd: (id: string | null) => void;
  onRegion: (id: string | null) => void;
  grouped: Grouped;
  field: string | null;
  onField: (code: string | null) => void;
  at: string;
}) {
  const th = view.theaters;
  const ccmds = useMemo(() => meta?.ccmds?.map((c) => c.id) ?? [], [meta]);
  const so = standout(view, family, meta?.official_alerts_from ?? null);
  const fam = familyLabel(family).toLowerCase();
  const name = (id: string) => regionName(meta, id);
  const minZ = view.meta.d1_min_z;
  const pick = (a: AlertRow) => (ccmds.includes(a.region) ? onCcmd(a.region) : onRegion(a.region));

  const where = th ? whereNow(th, family, ccmds) : [];
  const total = where.reduce((s, r) => s + r.seen, 0);
  const span = view.meta.baseline_window_days - (th?.window_days ?? 7) + 1;
  const skip = th?.window_days ?? 7;
  const series = th
    ? ccmd
      ? (th.history[`${ccmd}|${family}`] ?? [])
      : totalSeries(th, family, ccmds)
    : [];
  const typical = typicalOf(series, span, skip);
  const totalTypical = th ? typicalOf(totalSeries(th, family, ccmds), span, skip) : null;
  const [showHeld, setShowHeld] = useState(true);
  const [allFields, setAllFields] = useState(false);
  const selected = grouped.fields.find((f) => f.code === field);
  const onGround = grouped.fields.reduce((n, f) => n + f.ground.length, 0);
  const lost = grouped.fields.reduce((n, f) => n + f.lost.length, 0) + grouped.lostUnknown.length;
  const fieldsWithGround = grouped.fields.filter((f) => f.ground.length > 0).length;
  const shownFields = (ccmd ? grouped.fields.filter((f) => f.ccmd === ccmd) : grouped.fields).slice(
    0,
    allFields ? undefined : TOP_FIELDS,
  );
  const latestLost = [...grouped.fields.flatMap((f) => f.lost), ...grouped.lostUnknown]
    .map((p) => p.ts)
    .sort()
    .pop();

  return (
    <div className="overview">
      {selected && <AirfieldCard f={selected} at={at} name={name} onClose={() => onField(null)} />}
      <section>
        <h2>What stands out</h2>
        <p className="muted small">
          {so.source === "alerts"
            ? `Official alerts for ${view.meta.date}.`
            : view.meta.tier === "provisional"
              ? "From today's live data so far; the official run follows after the day's history release."
              : "What this day's numbers flag, by the same rules as alerts (worked out for this page; not stored or posted)."}
        </p>
        {so.shown.length > 0 ? (
          <ul className="standout">
            {so.shown.map((a) => (
              <Standout key={a.alert_id} a={a} meta={meta} onPick={pick} />
            ))}
          </ul>
        ) : (
          <p className="calm">Nothing unusual for {fam} on this day.</p>
        )}
        {so.held.length > 0 && (
          <>
            <button
              className="link"
              onClick={() => setShowHeld(!showHeld)}
              aria-expanded={showHeld}
            >
              {so.held.length} more held back: receiver coverage was far from usual, so these may
              reflect receivers rather than aircraft
            </button>
            {showHeld && (
              <ul className="standout held">
                {so.held.map((a) => (
                  <Standout key={a.alert_id} a={a} meta={meta} onPick={pick} />
                ))}
              </ul>
            )}
          </>
        )}
      </section>

      {th && (
        <section>
          <h2>Where they are</h2>
          <p className="hero">
            <b>{total}</b> {fam} seen in the last {th.max_age_days} days
            {totalTypical !== null && (
              <span className="muted"> · typical {Math.round(totalTypical)}</span>
            )}
          </p>
          <ul className="status">
            <li>
              <i className="pt ground" /> <b>{onGround}</b> on the ground at {fieldsWithGround}{" "}
              airfields
            </li>
            <li>
              <i className="pt air" /> <b>{grouped.airborne.length}</b> airborne
              {view.meta.tier === "official" ? " at the end of the day" : " now"}
            </li>
            <li>
              <i className="pt ring" /> <b>{lost}</b> lost from view in the air
              {latestLost && <span className="muted"> · latest {when(latestLost, at)}</span>}
            </li>
          </ul>
          <table className="tbl where">
            <thead>
              <tr>
                <th>Airfield{ccmd ? ` in ${name(ccmd)}` : ""}</th>
                <th className="num" title="Last seen on the ground here">
                  Ground
                </th>
                <th className="num" title="Last flight left here, then lost from view in the air">
                  Lost
                </th>
              </tr>
            </thead>
            <tbody>
              {shownFields.map((f) => (
                <tr
                  key={f.code}
                  className={f.code === field ? "sel" : ""}
                  onClick={() => onField(f.code === field ? null : f.code)}
                >
                  <td>
                    <b>{f.code}</b> <span className="muted small">{f.name}</span>
                  </td>
                  <td className="num">{f.ground.length || "–"}</td>
                  <td className="num muted">{f.lost.length || "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {grouped.fields.length > TOP_FIELDS && !ccmd && (
            <button className="link" onClick={() => setAllFields(!allFields)}>
              {allFields
                ? "Show the top " + TOP_FIELDS
                : `Show all ${grouped.fields.length} airfields`}
            </button>
          )}
          {grouped.airborne.length > 0 && (
            <details className="about">
              <summary>Airborne ({grouped.airborne.length})</summary>
              <ul className="ids">
                {grouped.airborne.map((p) => (
                  <Aircraft
                    key={p.hex}
                    p={p}
                    detail={`${p.from_airfield ? `from ${p.from_airfield}, ` : ""}last seen ${when(p.ts, at)} over ${name(p.ccmd)}`}
                  />
                ))}
              </ul>
            </details>
          )}
          {grouped.lostUnknown.length > 0 && (
            <details className="about">
              <summary>Lost from view, departure not known ({grouped.lostUnknown.length})</summary>
              <ul className="ids">
                {grouped.lostUnknown.map((p) => (
                  <Aircraft
                    key={p.hex}
                    p={p}
                    detail={`last seen ${when(p.ts, at)} over ${name(p.ccmd)}`}
                  />
                ))}
              </ul>
            </details>
          )}
          {grouped.groundUnknown.length > 0 && (
            <details className="about">
              <summary>
                On the ground, airfield not identified ({grouped.groundUnknown.length})
              </summary>
              <ul className="ids">
                {grouped.groundUnknown.map((p) => (
                  <Aircraft
                    key={p.hex}
                    p={p}
                    detail={`${p.lat.toFixed(2)}, ${p.lon.toFixed(2)} · ${when(p.ts, at)}`}
                  />
                ))}
              </ul>
            </details>
          )}
          <h3 className="sub">By combatant command</h3>
          <table className="tbl where">
            <thead>
              <tr>
                <th>Last seen in</th>
                <th className="num">Aircraft</th>
                <th className="num">Typical</th>
                <th className="num">Change</th>
              </tr>
            </thead>
            <tbody>
              {where.map((r) => {
                const big = r.z !== null && Math.abs(r.z) >= minZ;
                const types = byLabel(r.byType)
                  .map(([t, k]) => `${k} ${t}`)
                  .join(", ");
                return (
                  <tr
                    key={r.ccmd}
                    className={r.ccmd === ccmd ? "sel" : ""}
                    onClick={() => onCcmd(r.ccmd === ccmd ? null : r.ccmd)}
                    title={types || "none"}
                  >
                    <td>{name(r.ccmd)}</td>
                    <td className="num">
                      <b>{r.seen}</b>
                    </td>
                    <td className="num muted">{r.typical ?? "–"}</td>
                    <td className={`num ${big ? "hi" : "muted"}`}>
                      {r.typical === null ? "–" : signed(Math.round(r.seen - r.typical))}
                      {big && <span className="flag" aria-label="far from typical" />}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="muted small">
            Each aircraft counts where its latest flight ended (or, if it is still flying, where it
            is). Aircraft not broadcasting aren't counted, so these are floors.
          </p>
        </section>
      )}

      {th && (
        <section>
          <h2>
            Over time{" "}
            <span className="muted small">
              {ccmd ? `${fam} last seen in ${name(ccmd)}` : `all ${fam} seen`}, daily
            </span>
          </h2>
          <TrendChart
            values={series}
            last={view.meta.date}
            typical={typical}
            label={`${familyLabel(family)} ${ccmd ? `in ${name(ccmd)}` : "seen"}`}
          />
          <div className="minis">
            {ccmds.map((c) => {
              const s = th.history[`${c}|${family}`] ?? [];
              return (
                <button
                  key={c}
                  className={`mini ${c === ccmd ? "sel" : ""}`}
                  onClick={() => onCcmd(c === ccmd ? null : c)}
                  aria-pressed={c === ccmd}
                >
                  <span className="mini-head">
                    {name(c)} <b>{s[s.length - 1] ?? "–"}</b>
                  </span>
                  <TrendChart
                    values={s}
                    last={view.meta.date}
                    typical={typicalOf(s, span, skip)}
                    height={56}
                    label={`${familyLabel(family)} in ${name(c)}`}
                  />
                </button>
              );
            })}
          </div>
          {ccmd && (
            <button className="link" onClick={() => onCcmd(null)}>
              Show all CCMDs
            </button>
          )}
        </section>
      )}

      <details className="about">
        <summary>About these numbers</summary>
        <p>
          Public ADS-B only: aircraft that don't broadcast, or fly where no receiver hears them,
          aren't seen. Counts go up and down with receiver coverage as well as aircraft, so each
          number is compared with its own recent normal and checked against coverage. CCMD
          boundaries are approximate. Unusual counts say nothing about purpose. Times are UTC.
        </p>
      </details>
    </div>
  );
}
