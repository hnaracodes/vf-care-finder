// Geography assets for the map (app/design/SPEC.md 5.1), built by app/public/geo/build_geo_assets.py.
// vite.config.ts has publicDir: false, so the files are imported with ?url and Vite emits
// exactly these into the build (assetsInlineLimit: 0 keeps them as files). Loaded once.

import statesUrl from '../public/geo/india_states.json?url';
import outlineUrl from '../public/geo/india_outline.json?url';
import neighboursUrl from '../public/geo/neighbours.json?url';
import poUrl from '../public/geo/po_points.bin?url';
import labelsUrl from '../public/geo/labels_india.json?url';

/** Quantisation box of po_points.bin (same constants as the build script). */
const LON0 = 68.0, LON1 = 97.5, LAT0 = 6.5, LAT1 = 37.2;

export type Ring = [number, number][];

export interface Region {
  name: string;
  /** Polygons, each a list of rings (outer first). */
  polygons: Ring[][];
  bbox: [number, number, number, number];
}

export interface StateLabel { name: string; lon: number; lat: number; minz: number }
export interface PointLabel { name: string; lon: number; lat: number; state?: string }

export interface GeoAssets {
  states: Region[];
  neighbours: Region[];
  /** Every ring of every state, for the border PathLayer. */
  stateRings: Ring[];
  outline: Ring[];
  neighbourRings: Ring[];
  /** Post office points, interleaved lon, lat. */
  po: Float32Array;
  nPo: number;
  labels: {
    states: StateLabel[];
    districts: PointLabel[];
    countries: PointLabel[];
    seas: PointLabel[];
    /** [name, lon, lat, rank]; rank 1000 is a city from the gazetteer. */
    towns: [string, number, number, number][];
  };
}

function bboxOf(polys: Ring[][]): [number, number, number, number] {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const p of polys) for (const r of p) for (const [x, y] of r) {
    if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
  }
  return [x0, y0, x1, y1];
}

async function json<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`map asset ${url}: HTTP ${r.status}`);
  return (await r.json()) as T;
}

interface StatesFile { features: { properties: { name: string }; geometry: { coordinates: Ring[][] } }[] }
interface NeighboursFile { countries: { name: string; rings: Ring[] }[] }

let cache: Promise<GeoAssets> | null = null;

export function loadGeo(): Promise<GeoAssets> {
  if (!cache) cache = load();
  return cache;
}

async function load(): Promise<GeoAssets> {
  const [sf, of, nf, labels, poBuf] = await Promise.all([
    json<StatesFile>(statesUrl),
    json<{ rings: Ring[] }>(outlineUrl),
    json<NeighboursFile>(neighboursUrl).catch(() => ({ countries: [] }) as NeighboursFile),
    json<GeoAssets['labels']>(labelsUrl),
    fetch(poUrl).then((r) => (r.ok ? r.arrayBuffer() : new ArrayBuffer(0))),
  ]);
  const states: Region[] = sf.features.map((f) => ({
    name: f.properties.name, polygons: f.geometry.coordinates, bbox: bboxOf(f.geometry.coordinates),
  }));
  const neighbours: Region[] = nf.countries.map((c) => {
    const polys = c.rings.map((r) => [r]);
    return { name: c.name, polygons: polys, bbox: bboxOf(polys) };
  });
  const q = new Uint16Array(poBuf);
  const nPo = q.length >> 1;
  const po = new Float32Array(nPo * 2);
  const kx = (LON1 - LON0) / 65535, ky = (LAT1 - LAT0) / 65535;
  for (let i = 0; i < nPo; i++) {
    po[2 * i] = LON0 + q[2 * i] * kx;
    po[2 * i + 1] = LAT0 + q[2 * i + 1] * ky;
  }
  return {
    states,
    neighbours,
    stateRings: states.flatMap((s) => s.polygons.flat()),
    outline: of.rings,
    neighbourRings: neighbours.flatMap((n) => n.polygons.flat()).flatMap(dropClipEdges),
    po,
    nPo,
    labels,
  };
}

/** The build clips neighbours to this box; its edges are not borders, so they are not drawn. */
const CLIP = [58, -2, 104, 44];
function onClip(p: [number, number]): boolean {
  return Math.abs(p[0] - CLIP[0]) < 1e-6 || Math.abs(p[0] - CLIP[2]) < 1e-6 || Math.abs(p[1] - CLIP[1]) < 1e-6 || Math.abs(p[1] - CLIP[3]) < 1e-6;
}
function dropClipEdges(r: Ring): Ring[] {
  const out: Ring[] = [];
  let cur: Ring = [];
  for (let i = 0; i < r.length; i++) {
    if (i > 0 && onClip(r[i]) && onClip(r[i - 1])) {
      if (cur.length > 1) out.push(cur);
      cur = [r[i]];
    } else cur.push(r[i]);
  }
  if (cur.length > 1) out.push(cur);
  return out;
}

/** Even-odd point in polygon (outer ring and holes). */
export function inPolygon(lon: number, lat: number, rings: Ring[]): boolean {
  let inside = false;
  for (const r of rings) {
    for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
      const [xi, yi] = r[i], [xj, yj] = r[j];
      if ((yi > lat) !== (yj > lat) && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
    }
  }
  return inside;
}

export function inRegion(lon: number, lat: number, reg: Region): boolean {
  const [x0, y0, x1, y1] = reg.bbox;
  if (lon < x0 || lon > x1 || lat < y0 || lat > y1) return false;
  for (const p of reg.polygons) if (inPolygon(lon, lat, p)) return true;
  return false;
}
