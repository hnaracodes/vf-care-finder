// Deck layers for the agent simulator (SPEC 7.5), handed to the map through
// map.setOverlayLayers. Heads move on the GPU (SwarmLayer); their arrays are
// rebuilt only when the engine marks a leg change. Trails are rebuilt at
// 10 Hz and never reach ahead of a dot. The road so far is append-only.

import type { Layer } from '@deck.gl/core';
import { LineLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers';
import { SwarmLayer } from '../swarm-layer';
import {
  LEG_CLAIM, LEG_EVIDENCE, LEG_REFERRED, PH_ARRIVED, PH_HOLD, PH_MOVE, PH_QUEUE, PH_STUCK, PH_WAIT, type Engine,
} from './agents';
import { C, type RGBA } from './tokens';
import { base } from './rules';
import { haversineKm, ST_EVIDENCE, ST_HELD_BACK, type World } from './world';

const TRAIL_MIN = 6;
const DASH_KM = 0.6;
const FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

export interface LayerOptions {
  phone?: boolean;
  /** facility index to emphasise (hover in the rail) */
  highlight?: number | null;
  /** the verify tool is active: claim-only diamonds grow */
  verifyTool?: boolean;
}

interface FacMark { j: number; pos: [number, number]; text: string; color: RGBA; size: number }
interface Label { pos: [number, number]; text: string; color: RGBA; size: number; dy: number }

export class SimLayers {
  private headData: { length: number; attributes: Record<string, unknown> } | null = null;
  private trailData: { length: number; attributes: Record<string, unknown> } | null = null;
  private roadData: { length: number; attributes: Record<string, unknown> } | null = null;
  private roadN = -1;
  private lastTrail = -1;
  private ringData: { pos: [number, number]; color: RGBA }[] = [];
  private facKey = '';
  private facMarks: FacMark[] = [];
  private facDots: { pos: [number, number]; j: number }[] = [];
  private whatifRings: { pos: [number, number] }[] = [];
  private whatifLabels: Label[] = [];
  private tallyKey = '';
  private holeData: { length: number; attributes: Record<string, unknown> } | null = null;
  private outKey = '';
  private outside: { pos: [number, number] }[] = [];
  /** The counts at each place, for the map's label declutter (MapHost.setOverlayLabels). */
  mapLabels: { text: string; lon: number; lat: number; style: 'ring' | 'claimnote' | 'note' | 'whatif' }[] = [];
  private countLabels: { text: string; lon: number; lat: number; style: 'ring' | 'claimnote' | 'note' | 'whatif' }[] = [];

  reset(): void {
    this.headData = this.trailData = this.roadData = null;
    this.roadN = -1;
    this.lastTrail = -1;
    this.facKey = '';
    this.tallyKey = '';
    this.outKey = '';
  }

  build(e: Engine, w: World, realSec: number, pulseSec: number, opts: LayerOptions = {}): Layer[] {
    const t = e.time;
    if (e.dirty || !this.headData) {
      this.buildHeads(e);
      this.buildRings(e);
      e.dirty = false;
      this.lastTrail = -1;
    }
    if (this.lastTrail < 0 || realSec - this.lastTrail >= 0.1) {
      this.buildTrails(e);
      this.lastTrail = realSec;
    }
    if (e.nRoad !== this.roadN) this.buildRoad(e);
    const fk = `${w.version}|${w.F}|${opts.highlight ?? ''}|${opts.verifyTool ? 1 : 0}`;
    if (fk !== this.facKey) { this.buildFacilities(w, opts); this.facKey = fk; }
    const tk = `${w.version}|${sumMap(e.arrivedAt)}|${sumMap(e.stoppedAt)}`;
    if (tk !== this.tallyKey) { this.buildTallies(e, w); this.tallyKey = tk; }
    this.mapLabels = [...this.whatifLabels.map((l) => ({ text: l.text, lon: l.pos[0], lat: l.pos[1], style: 'whatif' as const })), ...this.countLabels];
    const ok = `${w.version}`;
    if (ok !== this.outKey) { this.buildOutside(w); this.outKey = ok; }
    const hl = opts.highlight != null && opts.highlight >= 0 ? [{ pos: [w.flon[opts.highlight], w.flat[opts.highlight]] as [number, number] }] : [];

    const r = opts.phone ? 3 : 3.5;
    const pulse = 1 + 0.35 * (0.5 + 0.5 * Math.sin(pulseSec * 5));
    return [
      // villages with no place with evidence inside the window: grey rings (they stay put on the map)
      new ScatterplotLayer<{ pos: [number, number] }>({
        id: 'sim-outside', data: this.outside, getPosition: (d) => d.pos, radiusUnits: 'pixels', getRadius: 6,
        filled: false, stroked: true, getLineColor: [90, 84, 76, 220], lineWidthUnits: 'pixels', getLineWidth: 1.6, pickable: false,
      }),
      new LineLayer({
        id: 'sim-road', data: this.roadData as never, widthUnits: 'pixels', getWidth: 1, pickable: false,
      }),
      new ScatterplotLayer<{ pos: [number, number]; j: number }>({
        id: 'sim-fac-evidence', data: this.facDots, getPosition: (d) => d.pos, radiusUnits: 'pixels', getRadius: 3,
        getFillColor: C.evidence, stroked: true, getLineColor: [255, 255, 255, 220], lineWidthUnits: 'pixels',
        getLineWidth: 1, pickable: false, updateTriggers: { getFillColor: this.facKey },
      }),
      new ScatterplotLayer<{ pos: [number, number] }>({
        id: 'sim-fac-verified', data: this.whatifRings, getPosition: (d) => d.pos, radiusUnits: 'pixels', getRadius: 9,
        filled: false, stroked: true, getLineColor: C.whatif, lineWidthUnits: 'pixels', getLineWidth: 2, pickable: false,
      }),
      new TextLayer<FacMark>({
        id: 'sim-fac-marks', data: this.facMarks, getPosition: (d) => d.pos, getText: (d) => d.text,
        getColor: (d) => d.color, getSize: (d) => d.size, sizeUnits: 'pixels', characterSet: 'auto',
        fontFamily: FONT, fontWeight: 700, getTextAnchor: 'middle', getAlignmentBaseline: 'center', pickable: false,
        updateTriggers: { getColor: this.facKey, getSize: this.facKey, getText: this.facKey },
      }),
      new LineLayer({
        id: 'sim-trails', data: this.trailData as never, widthUnits: 'pixels', getWidth: 1.5, pickable: false,
      }),
      new ScatterplotLayer<{ pos: [number, number]; color: RGBA }>({
        id: 'sim-rings', data: this.ringData, getPosition: (d) => d.pos, radiusUnits: 'pixels',
        getRadius: 7 * pulse, filled: false, stroked: true, getLineColor: (d) => d.color, lineWidthUnits: 'pixels',
        getLineWidth: 1.5, pickable: false, updateTriggers: { getRadius: pulse },
      }),
      new SwarmLayer({
        id: 'sim-heads', data: this.headData as never, time: t, agentMode: true, pulse: pulseSec,
        radiusUnits: 'pixels', getRadius: r + 0.5, stroked: false, pickable: false,
      }),
      // a white centre on dots heading to a place with no evidence: they differ by shape, not only colour
      new SwarmLayer({
        id: 'sim-heads-hole', data: this.holeData as never, time: t, agentMode: true, pulse: pulseSec,
        radiusUnits: 'pixels', getRadius: (r + 0.5) * 0.5, stroked: false, pickable: false,
      }),
      new ScatterplotLayer<{ pos: [number, number] }>({
        id: 'sim-highlight', data: hl, getPosition: (d) => d.pos, radiusUnits: 'pixels', getRadius: 16,
        filled: false, stroked: true, getLineColor: C.whatif, lineWidthUnits: 'pixels', getLineWidth: 3, pickable: false,
        updateTriggers: { getPosition: opts.highlight ?? -1 },
      }),
    ];
  }

  // ------------------------------------------------------------------ heads

  private buildHeads(e: Engine): void {
    const N = e.N;
    const pos = new Float32Array(N * 3), tgt = new Float32Array(N * 3), tim = new Float32Array(N * 4);
    const col = new Uint8Array(N * 4);
    for (let i = 0; i < N; i++) {
      const ph = e.phase[i];
      pos[i * 3] = e.x0[i]; pos[i * 3 + 1] = e.y0[i];
      tgt[i * 3] = e.tx[i]; tgt[i * 3 + 1] = e.ty[i];
      let state = 0;
      if (ph === PH_HOLD || ph === PH_STUCK || ph === PH_QUEUE) state = 2;
      else if (ph === PH_ARRIVED) state = 3;
      tim[i * 4] = ph === PH_WAIT ? e.depart[i] : e.t0[i];
      tim[i * 4 + 1] = e.dur[i];
      tim[i * 4 + 2] = state;
      tim[i * 4 + 3] = (i * 2.399963) % (Math.PI * 2);
      col.set(headColor(e, i), i * 4);
    }
    this.headData = {
      length: N,
      attributes: {
        getPosition: { value: pos, size: 3 },
        getTarget: { value: tgt, size: 3 },
        getTiming: { value: tim, size: 4 },
        getFillColor: { value: col, size: 4, normalized: true },
      },
    };
    // the holes: the same dots, for those heading to (or waiting at) a place with no evidence
    const idx: number[] = [];
    for (let i = 0; i < N; i++) {
      if (e.turned[i] || e.phase[i] === PH_ARRIVED || e.phase[i] === PH_QUEUE) continue;
      const k = e.legKind[i];
      if (k === LEG_CLAIM || k === LEG_REFERRED || e.phase[i] === PH_HOLD || e.phase[i] === PH_STUCK) idx.push(i);
    }
    const n = idx.length;
    const hp = new Float32Array(n * 3), ht = new Float32Array(n * 3), hm = new Float32Array(n * 4), hc = new Uint8Array(n * 4);
    idx.forEach((i, q) => {
      hp.set(pos.subarray(i * 3, i * 3 + 3), q * 3);
      ht.set(tgt.subarray(i * 3, i * 3 + 3), q * 3);
      hm.set(tim.subarray(i * 4, i * 4 + 4), q * 4);
      hc.set([255, 253, 248, 255], q * 4);
    });
    this.holeData = {
      length: n,
      attributes: {
        getPosition: { value: hp, size: 3 },
        getTarget: { value: ht, size: 3 },
        getTiming: { value: hm, size: 4 },
        getFillColor: { value: hc, size: 4, normalized: true },
      },
    };
  }

  private buildOutside(w: World): void {
    const b = base(w);
    const W = w.p.window.minutes;
    const out: { pos: [number, number] }[] = [];
    for (let v = 0; v < w.V; v++) if (!(b.near[v] <= W)) out.push({ pos: [w.vlon[v], w.vlat[v]] });
    this.outside = out;
  }

  private buildRings(e: Engine): void {
    const seen = new Set<string>();
    const out: { pos: [number, number]; color: RGBA }[] = [];
    for (let i = 0; i < e.N; i++) {
      const ph = e.phase[i];
      if (ph !== PH_HOLD && ph !== PH_STUCK && ph !== PH_QUEUE) continue;
      const k = `${ph}|${e.x0[i].toFixed(5)}|${e.y0[i].toFixed(5)}`;
      if (seen.has(k)) continue;
      seen.add(k);
      out.push({ pos: [e.x0[i], e.y0[i]], color: ph === PH_QUEUE ? C.neutral : C.claim });
    }
    this.ringData = out;
  }

  // ------------------------------------------------------------------ trails and road

  private buildTrails(e: Engine): void {
    const t = e.time;
    const src: number[] = [], dst: number[] = [], col: number[] = [];
    for (let i = 0; i < e.N; i++) {
      if (e.phase[i] !== PH_MOVE || t < e.t0[i]) continue;
      const d = e.dur[i];
      if (!(d > 0)) continue;
      const pHead = Math.min(1, (t - e.t0[i]) / d);
      const pTail = Math.max(0, (t - TRAIL_MIN - e.t0[i]) / d);
      if (pHead <= pTail) continue;
      const c = headColor(e, i);
      const x0 = e.x0[i], y0 = e.y0[i], dx = e.tx[i] - x0, dy = e.ty[i] - y0;
      if (e.legKind[i] === LEG_REFERRED && !e.turned[i]) {
        // dashed in JS (no @deck.gl/extensions): 0.6 km on, 0.6 km off, anchored at the leg start
        const L = haversineKm(y0, x0, e.ty[i], e.tx[i]);
        if (L <= 0) continue;
        const step = DASH_KM / L;
        let k = Math.floor(pTail / (2 * step));
        for (; k * 2 * step < pHead; k++) {
          const a = Math.max(pTail, k * 2 * step), b = Math.min(pHead, k * 2 * step + step);
          if (b <= a) continue;
          src.push(x0 + dx * a, y0 + dy * a); dst.push(x0 + dx * b, y0 + dy * b); col.push(c[0], c[1], c[2], 150);
          if (src.length > 400000) break;
        }
      } else {
        src.push(x0 + dx * pTail, y0 + dy * pTail); dst.push(x0 + dx * pHead, y0 + dy * pHead);
        col.push(c[0], c[1], c[2], 150);
      }
    }
    const n = src.length / 2;
    this.trailData = {
      length: n,
      attributes: {
        getSourcePosition: { value: Float32Array.from(src), size: 2 },
        getTargetPosition: { value: Float32Array.from(dst), size: 2 },
        getColor: { value: Uint8Array.from(col), size: 4, normalized: true },
      },
    };
  }

  private buildRoad(e: Engine): void {
    const n = e.nRoad;
    const src = new Float32Array(n * 2), dst = new Float32Array(n * 2), col = new Uint8Array(n * 4);
    for (let s = 0; s < n; s++) {
      src[s * 2] = e.road[s * 4]; src[s * 2 + 1] = e.road[s * 4 + 1];
      dst[s * 2] = e.road[s * 4 + 2]; dst[s * 2 + 1] = e.road[s * 4 + 3];
      const c = e.roadTurned[s] ? C.whatif : e.roadKind[s] === LEG_EVIDENCE ? C.evidence : C.claim;
      col.set([c[0], c[1], c[2], 31], s * 4);
    }
    this.roadData = {
      length: n,
      attributes: {
        getSourcePosition: { value: src, size: 2 },
        getTargetPosition: { value: dst, size: 2 },
        getColor: { value: col, size: 4, normalized: true },
      },
    };
    this.roadN = n;
  }

  // ------------------------------------------------------------------ facilities

  private buildFacilities(w: World, opts: LayerOptions): void {
    const marks: FacMark[] = [], dots: { pos: [number, number]; j: number }[] = [];
    const rings: { pos: [number, number] }[] = [], labels: Label[] = [];
    const big = opts.verifyTool ? 4 : 0;
    for (let j = 0; j < w.F; j++) {
      const pos: [number, number] = [w.flon[j], w.flat[j]];
      const hl = opts.highlight === j ? 5 : 0;
      if (w.isAdded(j)) {
        if (!w.added[j] && !w.open[j]) continue; // an undone what-if unit
        if (!w.open[j]) { marks.push({ j, pos, text: '×', color: C.whatif, size: 18 + hl }); continue; }
        marks.push({ j, pos, text: '+', color: C.whatif, size: 24 + hl });
        labels.push({ pos, text: `What-if: new ${w.p.service_phrase} unit`, color: C.whatif, size: 12, dy: 0 });
        continue;
      }
      if (!w.open[j]) {
        marks.push({ j, pos, text: '◆', color: C.neutral, size: 13 + hl });
        marks.push({ j, pos, text: '×', color: C.whatif, size: 20 + hl });
        labels.push({ pos, text: 'What-if: closed', color: C.whatif, size: 11, dy: 0 });
        continue;
      }
      if (w.status[j] === ST_EVIDENCE) { dots.push({ pos, j }); continue; }
      const held = w.status[j] === ST_HELD_BACK;
      marks.push({ j, pos, text: held ? '◇' : '◆', color: C.claim, size: 12 + big + hl });
      if (w.verified[j]) {
        rings.push({ pos });
        labels.push({ pos, text: 'What-if: verified', color: C.whatif, size: 11, dy: 0 });
      }
    }
    this.facMarks = marks;
    this.facDots = dots;
    this.whatifRings = rings;
    this.whatifLabels = labels;
  }

  /** The busiest places only (3 arrivals, 3 first stops), counted in villages; the map declutters them. */
  private buildTallies(e: Engine, w: World): void {
    const top = (m: Map<number, number>, n: number) => [...m.entries()].filter(([, c]) => c > 0)
      .sort((a, b) => b[1] - a[1] || a[0] - b[0]).slice(0, n);
    const vill = (n: number) => Math.max(1, Math.round(n / Math.max(1, e.k)));
    const out: { text: string; lon: number; lat: number; style: 'ring' | 'claimnote' | 'note' | 'whatif' }[] = [];
    for (const [j, n] of top(e.stoppedAt, 3)) {
      const v = vill(n);
      out.push({ text: `${v} ${v === 1 ? 'village' : 'villages'} stopped here first`, lon: w.flon[j], lat: w.flat[j], style: 'claimnote' });
    }
    for (const [j, n] of top(e.arrivedAt, 3)) {
      const v = vill(n);
      out.push({ text: `${v} ${v === 1 ? 'village' : 'villages'} reached care here`, lon: w.flon[j], lat: w.flat[j], style: 'ring' });
    }
    this.countLabels = out;
  }
}

export function headColor(e: Engine, i: number): RGBA {
  if (e.turned[i]) return C.whatif;
  if (e.phase[i] === PH_QUEUE) return C.neutral;
  if (e.phase[i] === PH_STUCK || e.phase[i] === PH_HOLD) return C.claim;
  const k = e.legKind[i];
  if (k === LEG_CLAIM || k === LEG_REFERRED) return C.claim;
  return C.evidence;
}

function sumMap(m: Map<number, number>): number {
  let s = 0;
  for (const v of m.values()) s += v;
  return s;
}

