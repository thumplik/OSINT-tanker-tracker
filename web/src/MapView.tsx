import { H3HexagonLayer } from "@deck.gl/geo-layers";
import { ArcLayer, PolygonLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import { MapboxOverlay } from "@deck.gl/mapbox";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { useEffect, useMemo, useRef } from "react";
import { bucket, type Cell, RAMP } from "./heat";
import type { Arc } from "./theater";
import type { FieldGroup, Grouped } from "./overview";
import type { Layer } from "./state";
import { TYPE_LABEL } from "./theater";
import type { Ccmd, PositionRow, Region } from "./types";
import { hhmm } from "./wording";

const STYLE = "https://tiles.openfreemap.org/styles/positron";
// MapLibre 6 ships its worker as a self-contained module. Serve it as a plain asset (`?url`):
// Vite dev injects its client into `?worker` modules, which breaks the worker.
maplibregl.setWorkerUrl(workerUrl);

interface Props {
  layer: Layer;
  cells: Cell[];
  unit: string;
  // Positions layer: aircraft grouped at airfields; dots only for those still airborne.
  grouped: Grouped;
  selectedField: string | null;
  onField: (code: string | null) => void;
  showRegions: boolean;
  regions: Region[];
  selectedRegion: string | null;
  onRegion: (id: string | null) => void;
  // Theaters tab: CCMD outlines and transit arcs replace the region boxes.
  ccmds?: Ccmd[];
  arcs?: Arc[];
  selectedCcmd?: string | null;
  onCcmd?: (id: string | null) => void;
}

interface CcmdShape {
  ccmd: Ccmd;
  hex: string;
  color: [number, number, number];
}

// Categorical slots 2-7 (fixed order by CCMD); slot 1 (blue) is the heat map's hue.
const CCMD_COLORS: [number, number, number][] = [
  [235, 104, 52],
  [27, 175, 122],
  [237, 161, 0],
  [232, 123, 164],
  [0, 131, 0],
  [98, 80, 214],
];

interface Box {
  region: Region;
  polygon: [number, number][];
}

function boxes(regions: Region[]): Box[] {
  return regions.flatMap((region) =>
    region.boxes.map(([la0, la1, lo0, lo1]) => ({
      region,
      polygon: [
        [lo0, la0],
        [lo1, la0],
        [lo1, la1],
        [lo0, la1],
        [lo0, la0],
      ] as [number, number][],
    })),
  );
}

export function regionBounds(region: Region): [[number, number], [number, number]] | null {
  if (!region.boxes.length) return null;
  const la0 = Math.min(...region.boxes.map((b) => b[0]));
  const la1 = Math.max(...region.boxes.map((b) => b[1]));
  const lo0 = Math.min(...region.boxes.map((b) => b[2]));
  const lo1 = Math.max(...region.boxes.map((b) => b[3]));
  return [
    [lo0, la0],
    [lo1, la1],
  ];
}

const POS: [number, number, number] = [42, 120, 214]; // on the ground (series slot 1)
const AIR: [number, number, number] = [235, 104, 52]; // airborne (slot 2)

/** Marker radius in pixels for `n` aircraft on the ground (area grows with the count). */
export function fieldRadius(n: number): number {
  return n > 0 ? 4 + 2.2 * Math.sqrt(n) : 0;
}

export function MapView({
  layer,
  cells,
  unit,
  grouped,
  selectedField,
  onField,
  showRegions,
  regions,
  selectedRegion,
  onRegion,
  ccmds,
  arcs,
  selectedCcmd = null,
  onCcmd,
}: Props) {
  const theater = ccmds !== undefined;
  const shapes = useMemo<CcmdShape[]>(
    () =>
      (ccmds ?? []).flatMap((ccmd, i) =>
        ccmd.cells.map((hex) => ({
          ccmd,
          hex,
          color: CCMD_COLORS[i % CCMD_COLORS.length],
        })),
      ),
    [ccmds],
  );
  const maxArc = useMemo(() => (arcs ?? []).reduce((m, a) => Math.max(m, a.count), 1), [arcs]);
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const overlay = useRef<MapboxOverlay | null>(null);
  const regionBoxes = useMemo(() => boxes(regions), [regions]);
  const max = useMemo(() => cells.reduce((m, c) => Math.max(m, c.n), 1), [cells]);

  useEffect(() => {
    if (!container.current) return;
    const m = new maplibregl.Map({
      container: container.current,
      style: STYLE,
      center: [10, 35],
      zoom: 1.3,
      attributionControl: { compact: true },
      dragRotate: false,
      pitchWithRotate: false,
    });
    m.touchZoomRotate.disableRotation();
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    const o = new MapboxOverlay({ interleaved: false, layers: [] });
    m.addControl(o);
    map.current = m;
    overlay.current = o;
    return () => {
      m.remove();
      map.current = null;
      overlay.current = null;
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    const region = regions.find((r) => r.id === selectedRegion);
    if (!m || !region) return;
    const b = regionBounds(region);
    if (b) m.fitBounds(b, { padding: 24, duration: 600 });
  }, [selectedRegion, regions]);

  useEffect(() => {
    const m = map.current;
    const f = grouped.fields.find((x) => x.code === selectedField);
    if (!m || !f) return;
    m.flyTo({ center: [f.lon, f.lat], zoom: Math.max(m.getZoom(), 6), duration: 600 });
  }, [selectedField, grouped]);

  useEffect(() => {
    const dim = (ccmd: string) => selectedCcmd !== null && ccmd !== selectedCcmd;
    const alpha = (ccmd: string, a: number) => (dim(ccmd) ? Math.round(a / 6) : a);
    const pickField = (info: { object?: unknown }) => {
      const code = (info.object as FieldGroup | undefined)?.code ?? null;
      onField(code === selectedField ? null : code);
    };
    overlay.current?.setProps({
      layers: [
        theater
          ? new H3HexagonLayer<CcmdShape>({
              id: "ccmds",
              data: shapes,
              getHexagon: (d) => d.hex,
              extruded: false,
              stroked: false,
              getFillColor: (d) =>
                [...d.color, d.ccmd.id === selectedCcmd ? 70 : selectedCcmd ? 14 : 34] as [
                  number,
                  number,
                  number,
                  number,
                ],
              pickable: true,
              onClick: (info) => {
                const id = (info.object as CcmdShape | undefined)?.ccmd.id ?? null;
                onCcmd?.(id === selectedCcmd ? null : id);
              },
              updateTriggers: { getFillColor: selectedCcmd },
            })
          : showRegions &&
            new PolygonLayer<Box>({
              id: "regions",
              data: regionBoxes,
              getPolygon: (d) => d.polygon,
              stroked: true,
              filled: true,
              getFillColor: (d) =>
                d.region.id === selectedRegion ? [42, 120, 214, 18] : [0, 0, 0, 0],
              getLineColor: (d) =>
                d.region.id === selectedRegion ? [42, 120, 214, 220] : [82, 81, 78, 90],
              getLineWidth: (d) => (d.region.id === selectedRegion ? 2 : 1),
              lineWidthUnits: "pixels",
              pickable: true,
              onClick: (info) => {
                const id = (info.object as Box | undefined)?.region.id ?? null;
                onRegion(id === selectedRegion ? null : id);
              },
              updateTriggers: {
                getFillColor: selectedRegion,
                getLineColor: selectedRegion,
                getLineWidth: selectedRegion,
              },
            }),
        layer === "activity" &&
          new H3HexagonLayer<Cell>({
            id: "heat",
            data: cells,
            getHexagon: (d) => d.hex,
            getFillColor: (d) =>
              [...RAMP[bucket(d.n, max)], 210] as [number, number, number, number],
            extruded: false,
            stroked: true, // an outline in the fill color keeps small cells visible at world zoom
            getLineColor: (d) =>
              [...RAMP[bucket(d.n, max)], 230] as [number, number, number, number],
            lineWidthUnits: "pixels",
            getLineWidth: 1,
            lineWidthMinPixels: 1,
            pickable: true,
            updateTriggers: { getFillColor: max, getLineColor: max },
          }),
        layer === "positions" &&
          new ScatterplotLayer<FieldGroup>({
            id: "lost-rings",
            data: grouped.fields.filter((f) => f.lost.length > 0),
            getPosition: (d) => [d.lon, d.lat],
            radiusUnits: "pixels",
            getRadius: (d) => fieldRadius(d.ground.length) + 4,
            filled: false,
            stroked: true,
            lineWidthUnits: "pixels",
            getLineWidth: 1.5,
            getLineColor: (d) => [...POS, alpha(d.ccmd, 170)] as [number, number, number, number],
            pickable: true,
            onClick: pickField,
            updateTriggers: { getLineColor: selectedCcmd },
          }),
        layer === "positions" &&
          new ScatterplotLayer<FieldGroup>({
            id: "fields",
            data: grouped.fields.filter((f) => f.ground.length > 0),
            getPosition: (d) => [d.lon, d.lat],
            radiusUnits: "pixels",
            getRadius: (d) => fieldRadius(d.ground.length),
            stroked: true,
            lineWidthUnits: "pixels",
            getLineWidth: (d) => (d.code === selectedField ? 3 : 1),
            getFillColor: (d) => [...POS, alpha(d.ccmd, 225)] as [number, number, number, number],
            getLineColor: (d) =>
              (d.code === selectedField
                ? [11, 11, 11, 255]
                : [255, 255, 255, alpha(d.ccmd, 230)]) as [number, number, number, number],
            pickable: true,
            onClick: pickField,
            updateTriggers: {
              getFillColor: selectedCcmd,
              getLineColor: [selectedCcmd, selectedField],
              getLineWidth: selectedField,
            },
          }),
        layer === "positions" &&
          new TextLayer<FieldGroup>({
            id: "field-counts",
            data: grouped.fields.filter((f) => f.ground.length > 1),
            getPosition: (d) => [d.lon, d.lat],
            getText: (d) => String(d.ground.length),
            getSize: 10,
            getColor: (d) => [255, 255, 255, alpha(d.ccmd, 255)],
            fontWeight: 700,
            getTextAnchor: "middle",
            getAlignmentBaseline: "center",
            updateTriggers: { getColor: selectedCcmd },
          }),
        layer === "positions" &&
          new ScatterplotLayer<PositionRow>({
            id: "airborne",
            data: grouped.airborne,
            getPosition: (d) => [d.lon, d.lat],
            radiusUnits: "pixels",
            getRadius: 4,
            stroked: true,
            lineWidthUnits: "pixels",
            getLineWidth: 1,
            getFillColor: (d) => [...AIR, alpha(d.ccmd, 235)] as [number, number, number, number],
            getLineColor: (d) => [255, 255, 255, alpha(d.ccmd, 230)],
            pickable: true,
            updateTriggers: { getFillColor: selectedCcmd, getLineColor: selectedCcmd },
          }),
        theater &&
          new ArcLayer<Arc>({
            id: "transits",
            data: arcs ?? [],
            getSourcePosition: (d) => d.from,
            getTargetPosition: (d) => d.to,
            getSourceColor: [42, 120, 214, 200],
            getTargetColor: [208, 59, 59, 220],
            getWidth: (d) => 2 + (8 * d.count) / maxArc,
            widthUnits: "pixels",
            greatCircle: true,
            pickable: true,
            updateTriggers: { getWidth: maxArc },
          }),
        theater &&
          new TextLayer<Ccmd>({
            id: "ccmd-names",
            data: ccmds ?? [],
            getPosition: (d) => d.anchor,
            getText: (d) => d.name,
            getSize: 11,
            getColor: [82, 81, 78, 230],
            fontWeight: 600,
            getTextAnchor: "middle",
            getAlignmentBaseline: "center",
          }),
      ],
      getTooltip: ({ object, layer }) => {
        if (object && (layer?.id === "fields" || layer?.id === "lost-rings")) {
          const f = object as FieldGroup;
          const lost = f.lost.length ? `\n${f.lost.length} left here, then lost from view` : "";
          return { text: `${f.code} ${f.name}\n${f.ground.length} on the ground${lost}` };
        }
        if (object && layer?.id === "airborne") {
          const p = object as PositionRow;
          const who = p.callsign ? `${p.callsign} (${p.hex})` : p.hex;
          const from = p.from_airfield ? ` from ${p.from_airfield}` : "";
          return {
            text: `${who} · ${TYPE_LABEL[p.type] ?? p.type}${from}\nairborne, last seen ${hhmm(p.ts)}`,
          };
        }
        if (object && layer?.id === "transits") {
          const a = object as Arc;
          return {
            text: `${a.count} transits from the US resolved in ${a.dest.toUpperCase()}`,
          };
        }
        if (object && layer?.id === "ccmds") return { text: (object as CcmdShape).ccmd.name };
        if (!object || layer?.id !== "heat") {
          const box = object as Box | undefined;
          return box?.region ? { text: box.region.name } : null;
        }
        const c = object as Cell;
        return { text: `${c.n} ${unit}` };
      },
    });
  }, [
    layer,
    cells,
    max,
    unit,
    grouped,
    selectedField,
    onField,
    showRegions,
    regionBoxes,
    selectedRegion,
    onRegion,
    theater,
    shapes,
    selectedCcmd,
    onCcmd,
    arcs,
    maxArc,
    ccmds,
  ]);

  return (
    <div className="map-wrap">
      <div ref={container} className="map" />
      {layer === "activity" ? (
        <div className="legend" aria-label={`Color scale: ${unit}`}>
          <span>{unit}</span>
          <div className="ramp">
            {RAMP.map((c, i) => (
              <i key={i} style={{ background: `rgb(${c.join(",")})` }} />
            ))}
          </div>
          <span className="legend-ends">
            <b>1</b>
            <b>{max}</b>
          </span>
        </div>
      ) : (
        <div className="legend pos-legend">
          <span>
            <i className="pt ground" /> on the ground (size: aircraft)
          </span>
          <span>
            <i className="pt ring" /> ring: left there, lost from view
          </span>
          <span>
            <i className="pt air" /> airborne
          </span>
        </div>
      )}
    </div>
  );
}
