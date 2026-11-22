// Map labels (app/design/SPEC.md 5.2, layer 12): which names to show at which zoom, and a
// greedy declutter in screen space. Runs on view change (debounced by the host), never per frame.
//
// Priority, most important first: the searched place, prio 1 (the origin's district), states,
// prio 2 (districts and cities), neighbour countries, prio 3 (post office towns near the origin),
// then India-wide towns. A label whose box overlaps a kept box by more than 2px is dropped.
// Pins and the origin dot reserve their boxes first, so names never cover them.
//
// States and neighbour countries are also placed inside the visible part of their area when the
// map is zoomed in past state level and a border is in view: that is how Jokihat's view names
// both Bihar and West Bengal, and Nepal across the border.

import type { WebMercatorViewport } from '@deck.gl/core';
import { inRegion, type GeoAssets, type Region } from './map_assets';
import type { MapSimLabel } from './map_types';

export type LabelStyle = 'search' | 'state' | 'district' | 'city' | 'town' | 'country' | 'sea' | 'note' | 'ring' | 'claimnote' | 'whatif';

export interface LabelOut {
  text: string;
  lon: number;
  lat: number;
  style: LabelStyle;
  anchor: 'start' | 'middle' | 'end';
  offset: [number, number];
  /** Draw a small place dot at the point (towns and cities). */
  dot: boolean;
  /** Always shown, even over other labels. */
  force?: boolean;
  /** Other places to put it (anchor and offset) when the first overlaps a kept label or a pin. */
  alts?: Pick<LabelOut, 'anchor' | 'offset'>[];
  /** Placement priority for non-fixed labels passed in by the host (lower first). */
  prio?: number;
}

/** Right, left, above, below a point of radius r px. */
export function aroundPoint(r: number): Pick<LabelOut, 'anchor' | 'offset'>[] {
  return [
    { anchor: 'start', offset: [r, 0] }, { anchor: 'end', offset: [-r, 0] },
    { anchor: 'middle', offset: [0, -r - 4] }, { anchor: 'middle', offset: [0, r + 4] },
    { anchor: 'start', offset: [r - 2, -r] }, { anchor: 'start', offset: [r - 2, r] },
    { anchor: 'end', offset: [-r + 2, -r] }, { anchor: 'end', offset: [-r + 2, r] },
  ];
}

export interface LabelContext {
  geo: GeoAssets;
  vp: WebMercatorViewport;
  zoom: number;
  width: number;
  height: number;
  search: { text: string; lon: number; lat: number } | null;
  /** Fixed labels that must show (window ring, leader line, collapse marker), placed after the search label. */
  fixed: LabelOut[];
  originDistrict: string | null;
  simLabels: MapSimLabel[];
  /** Screen boxes reserved by pins and markers: [x0, y0, x1, y1]. */
  reserved: [number, number, number, number][];
  /** Labels the host adds below the fixed ones (pin names, the simulator's counts); each has a prio. */
  extra?: LabelOut[];
}

export const LABEL_SIZE: Record<LabelStyle, number> = {
  search: 15, state: 12, district: 11, city: 12, town: 11, country: 12, sea: 11, note: 11.5, ring: 11.5, claimnote: 11.5, whatif: 12,
};

const UPPER: Set<LabelStyle> = new Set(['state', 'country']);

interface Cand { out: LabelOut; prio: number; force: boolean }

function boxOf(x: number, y: number, o: LabelOut): [number, number, number, number] {
  const size = LABEL_SIZE[o.style];
  const per = UPPER.has(o.style) ? 0.7 : o.style === 'search' ? 0.6 : 0.56;
  const w = o.text.length * size * per + 4;
  const h = size * 1.25;
  const cx = x + o.offset[0], cy = y + o.offset[1];
  const x0 = o.anchor === 'start' ? cx : o.anchor === 'end' ? cx - w : cx - w / 2;
  return [x0, cy - h / 2, x0 + w, cy + h / 2];
}

function overlapArea(a: number[], b: number[]): number {
  const w = Math.min(a[2], b[2]) - Math.max(a[0], b[0]), h = Math.min(a[3], b[3]) - Math.max(a[1], b[1]);
  return w > 0 && h > 0 ? w * h : 0;
}

function overlaps(a: number[], b: number[], tol = 2): boolean {
  return a[0] < b[2] - tol && a[2] > b[0] + tol && a[1] < b[3] - tol && a[3] > b[1] + tol;
}

/** Place a region's name inside its visible part: grid samples, keep the one deepest inside. */
function localRegionLabels(ctx: LabelContext): { reg: Region; kind: 'state' | 'country'; lon: number; lat: number }[] {
  const { vp, width, height, geo } = ctx;
  const cols = 14, rows = 10, m = 22;
  const regions: { reg: Region; kind: 'state' | 'country' }[] = [
    ...geo.states.map((reg) => ({ reg, kind: 'state' as const })),
    ...geo.neighbours.map((reg) => ({ reg, kind: 'country' as const })),
  ];
  const [w, s, e, n] = vp.getBounds();
  const live = regions.map((r, i) => ({ ...r, i })).filter(({ reg }) => !(reg.bbox[2] < w || reg.bbox[0] > e || reg.bbox[3] < s || reg.bbox[1] > n));
  if (!live.length) return [];
  const samples: { x: number; y: number; lon: number; lat: number; c: number }[] = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const x = m + ((width - 2 * m) * (c + 0.5)) / cols;
      const y = m + ((height - 2 * m) * (r + 0.5)) / rows;
      const [lon, lat] = vp.unproject([x, y]) as [number, number];
      let cls = -1;
      for (const L of live) if (inRegion(lon, lat, L.reg)) { cls = L.i; break; }
      samples.push({ x, y, lon, lat, c: cls });
    }
  }
  const present = new Set(samples.map((p) => p.c).filter((c) => c >= 0));
  const distinct = new Set(samples.map((p) => p.c));
  if (present.size === 0 || (distinct.size < 2 && ctx.zoom >= 10)) return [];
  const out: { reg: Region; kind: 'state' | 'country'; lon: number; lat: number }[] = [];
  for (const c of present) {
    let best = -1, bestScore = -Infinity;
    samples.forEach((p, k) => {
      if (p.c !== c) return;
      let d = Math.min(p.x, width - p.x, p.y, height - p.y) * 0.8;
      for (const q of samples) if (q.c !== c) d = Math.min(d, Math.hypot(q.x - p.x, q.y - p.y));
      if (d > bestScore) { bestScore = d; best = k; }
    });
    if (best < 0) continue;
    const p = samples[best];
    out.push({ reg: regions[c].reg, kind: regions[c].kind, lon: p.lon, lat: p.lat });
  }
  return out;
}

function lab(text: string, lon: number, lat: number, style: LabelStyle, dot = false): LabelOut {
  return { text, lon, lat, style, anchor: dot ? 'start' : 'middle', offset: dot ? [6, 0] : [0, 0], dot };
}

export function computeLabels(ctx: LabelContext): LabelOut[] {
  const { geo, zoom: z, vp, width, height } = ctx;
  const [w, s, e, n] = vp.getBounds();
  const padLon = (e - w) * 0.05, padLat = (n - s) * 0.05;
  const inView = (lon: number, lat: number) => lon >= w - padLon && lon <= e + padLon && lat >= s - padLat && lat <= n + padLat;
  const cands: Cand[] = [];
  const searchKey = ctx.search?.text.toLowerCase() ?? '';

  if (ctx.search) {
    // Try above, below, right, left of the origin dot; take the first spot clear of pins.
    const base = lab(ctx.search.text, ctx.search.lon, ctx.search.lat, 'search');
    const tries: Pick<LabelOut, 'anchor' | 'offset'>[] = [
      { anchor: 'middle', offset: [0, -19] }, { anchor: 'middle', offset: [0, 19] },
      { anchor: 'start', offset: [12, 0] }, { anchor: 'end', offset: [-12, 0] },
      { anchor: 'start', offset: [10, -16] }, { anchor: 'end', offset: [-10, 16] },
    ];
    // farther spots too, so the place name never sits on the option markers around it
    tries.push({ anchor: 'middle', offset: [0, -34] }, { anchor: 'middle', offset: [0, 34] },
      { anchor: 'start', offset: [28, 0] }, { anchor: 'end', offset: [-28, 0] });
    const [sx, sy] = vp.project([base.lon, base.lat]);
    // the spot clear of pins, else the one overlapping them least
    let pick = tries[0], least = Infinity;
    for (const t of tries) {
      const b = boxOf(sx, sy, { ...base, ...t });
      const hit = ctx.reserved.reduce((s2, k) => s2 + overlapArea(b, k), 0);
      if (hit < least) { least = hit; pick = t; }
      if (hit === 0) break;
    }
    cands.push({ out: { ...base, ...pick }, prio: 0, force: true });
  }
  for (const f of ctx.fixed) cands.push({ out: f, prio: 0.5, force: !!f.force });
  for (const f of ctx.extra ?? []) cands.push({ out: f, prio: f.prio ?? 0.8, force: !!f.force });

  // The origin's district first.
  const od = ctx.originDistrict?.toLowerCase() ?? null;
  const simDistrict = ctx.simLabels.filter((l) => l.kind === 'district' || l.kind === 'city');
  if (z >= 6.5 && z < 11.5) {
    for (const l of simDistrict) {
      if (!inView(l.lon, l.lat)) continue;
      const isOrigin = l.prio === 1 || l.name.toLowerCase() === od;
      cands.push({ out: lab(l.name, l.lon, l.lat, l.kind === 'city' ? 'city' : 'district', l.kind === 'city'), prio: isOrigin ? 1 : 3, force: false });
    }
    for (const d of geo.labels.districts) {
      if (!inView(d.lon, d.lat)) continue;
      cands.push({ out: lab(d.name, d.lon, d.lat, 'district'), prio: d.name.toLowerCase() === od ? 1 : 3.1, force: false });
    }
  }

  // States and neighbours: the national label point when it is well inside the view (state zoom),
  // otherwise a point inside the visible part of the area (so a zoomed-in view still names them).
  const wellIn = (lon: number, lat: number) => {
    const [x, y] = vp.project([lon, lat]);
    return x > 40 && y > 20 && x < width - 40 && y < height - 20;
  };
  const placed = new Set<string>();
  if (z < 7) {
    for (const st of geo.labels.states) {
      if (z < st.minz || !wellIn(st.lon, st.lat)) continue;
      placed.add(st.name);
      cands.push({ out: lab(st.name.toUpperCase(), st.lon, st.lat, 'state'), prio: 2, force: false });
    }
    for (const c of geo.labels.countries) {
      if (!wellIn(c.lon, c.lat)) continue;
      placed.add(c.name);
      cands.push({ out: lab(c.name.toUpperCase(), c.lon, c.lat, 'country'), prio: 3.5, force: false });
    }
    if (z < 6) for (const c of geo.labels.seas) if (inView(c.lon, c.lat)) cands.push({ out: lab(c.name, c.lon, c.lat, 'sea'), prio: 3.8, force: false });
  }
  if (z >= 5) {
    for (const r of localRegionLabels(ctx)) {
      if (placed.has(r.reg.name)) continue;
      const style = r.kind === 'state' ? 'state' : 'country';
      cands.push({ out: lab(r.reg.name.toUpperCase(), r.lon, r.lat, style), prio: r.kind === 'state' ? 2 : 3.5, force: false });
    }
  }

  // Cities from the gazetteer at state zoom; towns once zoomed in.
  for (const [name, lon, lat, rank] of geo.labels.towns) {
    if (!inView(lon, lat)) continue;
    if (rank >= 1000 && z >= 6) cands.push({ out: lab(name, lon, lat, 'city', true), prio: 3.2, force: false });
    else if (rank < 1000 && z >= 8) cands.push({ out: lab(name, lon, lat, 'town', true), prio: 5 - Math.min(rank, 50) / 100, force: false });
  }
  if (z >= 9) {
    for (const l of ctx.simLabels) {
      if (l.kind !== 'post_office' || !inView(l.lon, l.lat)) continue;
      cands.push({ out: lab(l.name, l.lon, l.lat, 'town', true), prio: 4, force: false });
    }
  }

  cands.sort((a, b) => a.prio - b.prio);
  const kept: LabelOut[] = [];
  const boxes: number[][] = [...ctx.reserved];
  const seen: { t: string; x: number; y: number }[] = [];
  for (const c of cands) {
    const [x, y] = vp.project([c.out.lon, c.out.lat]);
    if (!c.force && (x < -20 || y < -20 || x > width + 20 || y > height + 20)) continue;
    const key = c.out.text.toLowerCase();
    if (c.prio > 0 && key === searchKey) continue;
    if (seen.some((q) => q.t === key && Math.hypot(q.x - x, q.y - y) < 140)) continue;
    let b = boxOf(x, y, c.out);
    // Labels with other spots (pin names, forced notes) take the first spot clear of kept labels and pins.
    if (c.out.alts?.length) {
      const fits = (bb: number[]) => !boxes.some((k) => overlaps(bb, k)) && bb[0] >= 2 && bb[1] >= 2 && bb[2] <= width - 2 && bb[3] <= height - 2;
      if (!fits(b)) {
        for (const alt of c.out.alts) {
          const o2 = { ...c.out, ...alt };
          const b2 = boxOf(x, y, o2);
          if (fits(b2)) { c.out = o2; b = b2; break; }
        }
      }
    }
    if (!c.force && c.out.anchor === 'start' && b[2] > width - 2) {
      // Runs off the right edge: put it on the left of its point instead.
      c.out = { ...c.out, anchor: 'end', offset: [-c.out.offset[0], c.out.offset[1]] };
      b = boxOf(x, y, c.out);
    }
    if (!c.force) {
      if (b[0] < 2 || b[1] < 2 || b[2] > width - 2 || b[3] > height - 2) continue;
      if (boxes.some((k) => overlaps(b, k))) continue;
    }
    boxes.push(b);
    kept.push(c.out);
    seen.push({ t: key, x, y });
  }
  return kept;
}
