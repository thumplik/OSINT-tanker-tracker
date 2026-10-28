import { H3HexagonLayer } from "@deck.gl/geo-layers";
import { ArcLayer, PolygonLayer, TextLayer } from "@deck.gl/layers";
import { MapboxOverlay } from "@deck.gl/mapbox";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { useEffect, useMemo, useRef } from "react";
import { bucket, type Cell, RAMP } from "./heat";
import type { Arc } from "./theater";
import type { Ccmd, Region } from "./types";

const STYLE = "https://tiles.openfreemap.org/styles/positron";
// MapLibre 6 ships its worker as a self-contained module. Serve it as a plain asset (`?url`):
// Vite dev injects its client into `?worker` modules, which breaks the worker.
maplibregl.setWorkerUrl(workerUrl);

interface Props {
  cells: Cell[];
  unit: string;
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

export function MapView({
  cells,
  unit,
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
        ccmd.cells.map((hex) => ({ ccmd, hex, color: CCMD_COLORS[i % CCMD_COLORS.length] })),
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
          : new PolygonLayer<Box>({
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
        new H3HexagonLayer<Cell>({
          id: "heat",
          data: cells,
          getHexagon: (d) => d.hex,
          getFillColor: (d) => [...RAMP[bucket(d.n, max)], 210] as [number, number, number, number],
          extruded: false,
          stroked: true, // an outline in the fill color keeps small cells visible at world zoom
          getLineColor: (d) => [...RAMP[bucket(d.n, max)], 230] as [number, number, number, number],
          lineWidthUnits: "pixels",
          getLineWidth: 1,
          lineWidthMinPixels: 1,
          pickable: true,
          updateTriggers: { getFillColor: max, getLineColor: max },
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
        if (object && layer?.id === "transits") {
          const a = object as Arc;
          return { text: `${a.count} transits from the US resolved in ${a.dest.toUpperCase()}` };
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
  }, [cells, max, unit, regionBoxes, selectedRegion, onRegion, theater, shapes, selectedCcmd, onCcmd, arcs, maxArc, ccmds]);

  return (
    <div className="map-wrap">
      <div ref={container} className="map" />
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
    </div>
  );
}
