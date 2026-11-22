// Patient swarm (docs/superpowers/specs/2026-11-21-patient-swarm-design.md,
// approach A). Each dot is one post office area, not a person. Target: the
// nearest facility at the active grade for the active line. Every dot moves
// at the same speed in km; a dot whose target is beyond R km travels R km and
// stops (stranded). A stranded dot in a district the outcome model calls a
// data desert stays home and pulses: care likely exists, not in this data
// (est.). Deterministic: start offsets come from a hash of the dot index.

import type { AppData } from './data';
import { hash32, interpolateGreatCircle } from './geo';
import { SWARM } from './colors';
import type { Simulator } from './sim';

export const STATE_REACHED = 0;
export const STATE_STRANDED = 1;
export const STATE_LIKELY = 2;

export interface SwarmCounts {
  reached: number;
  stranded: number;
  likely: number;
}

interface BinaryData {
  length: number;
  attributes: Record<string, { value: ArrayBufferView; size: number; normalized?: boolean }>;
}

export class Swarm {
  readonly data: AppData;
  readonly n: number;
  /** animate 1 in `ratio` dots at national zoom, chosen by hash of the index */
  readonly ratio: number;
  /** km per animation second, the same for every dot */
  speedKmS = 15;
  readonly origin: Float32Array;
  readonly target: Float32Array;
  readonly timing: Float32Array;
  readonly color: Uint8Array;
  readonly state: Int8Array;
  readonly subset: Uint32Array;
  counts: SwarmCounts = { reached: 0, stranded: 0, likely: 0 };
  baseline: SwarmCounts | null = null;
  rescued = { stranded: 0, likely: 0 };
  version = 0;
  private originDescFull: { value: Float32Array; size: number };
  private originDescSub: { value: Float32Array; size: number };
  private cacheFull: { v: number; d: BinaryData } | null = null;
  private cacheSub: { v: number; d: BinaryData } | null = null;

  constructor(data: AppData, ratio = 5) {
    this.data = data;
    this.n = data.n;
    this.ratio = ratio;
    const n = this.n;
    this.origin = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      this.origin[i * 3] = data.lon[i];
      this.origin[i * 3 + 1] = data.lat[i];
    }
    this.target = this.origin.slice();
    this.timing = new Float32Array(n * 4);
    this.color = new Uint8Array(n * 4);
    this.state = new Int8Array(n).fill(-1);
    const sub: number[] = [];
    for (let i = 0; i < n; i++) if (hash32(i) % ratio === 0) sub.push(i);
    this.subset = Uint32Array.from(sub);
    const subOrigin = new Float32Array(sub.length * 3);
    sub.forEach((i, j) => {
      subOrigin[j * 3] = this.origin[i * 3];
      subOrigin[j * 3 + 1] = this.origin[i * 3 + 1];
    });
    this.originDescFull = { value: this.origin, size: 3 };
    this.originDescSub = { value: subOrigin, size: 3 };
  }

  private setDot(i: number, sim: Simulator, k: number, R: number, likelyByDistrict: Uint8Array, t0: number): number {
    const d = sim.dist[k][i];
    const f = sim.near[k][i];
    const di = this.data.district[i];
    const likely = di !== this.data.noIndex && likelyByDistrict[di] === 1;
    const lon = this.data.lon[i], lat = this.data.lat[i];
    let st: number, tlon = lon, tlat = lat, dur = 0;
    const fac = f !== this.data.noIndex ? sim.facility(f) : null;
    if (!fac || fac.lat == null || fac.lon == null || !Number.isFinite(d)) {
      st = likely ? STATE_LIKELY : STATE_STRANDED;
    } else if (d <= Math.fround(R)) {
      st = STATE_REACHED;
      tlon = fac.lon; tlat = fac.lat;
      dur = d / this.speedKmS;
    } else if (likely) {
      st = STATE_LIKELY;
    } else {
      st = STATE_STRANDED;
      [tlon, tlat] = interpolateGreatCircle(lat, lon, fac.lat, fac.lon, R / d);
      dur = R / this.speedKmS;
    }
    const changed = st !== this.state[i] || this.target[i * 3] !== Math.fround(tlon) || this.target[i * 3 + 1] !== Math.fround(tlat);
    this.state[i] = st;
    this.target[i * 3] = tlon;
    this.target[i * 3 + 1] = tlat;
    if (changed) {
      this.timing[i * 4] = t0 + (hash32(i) / 4294967296) * 1.5;
      this.timing[i * 4 + 1] = dur;
    }
    this.timing[i * 4 + 2] = st;
    this.timing[i * 4 + 3] = (hash32(i ^ 0x5bd1e995) / 4294967296) * Math.PI * 2;
    const c = st === STATE_REACHED ? SWARM.reached : st === STATE_STRANDED ? SWARM.stranded : SWARM.likely;
    this.color.set(c, i * 4);
    return st;
  }

  private recount(): void {
    const c: SwarmCounts = { reached: 0, stranded: 0, likely: 0 };
    for (let i = 0; i < this.n; i++) {
      const s = this.state[i];
      if (s === STATE_REACHED) c.reached++;
      else if (s === STATE_STRANDED) c.stranded++;
      else if (s === STATE_LIKELY) c.likely++;
    }
    this.counts = c;
  }

  /** Release every dot from its post office at time t0. */
  run(sim: Simulator, k: number, R: number, likelyByDistrict: Uint8Array, t0: number, keepBaseline = false, settled = false): void {
    this.state.fill(-1);
    // settled: every dot already sits at its end point (used to rewind before a plan replays)
    const start = settled ? t0 - 1000 : t0;
    for (let i = 0; i < this.n; i++) this.setDot(i, sim, k, R, likelyByDistrict, start);
    this.recount();
    if (!keepBaseline || !this.baseline) this.baseline = { ...this.counts };
    this.rescued = { stranded: 0, likely: 0 };
    this.version++;
  }

  /** Recompute only these post offices (those within R km of a new site) and replay the ones that changed. */
  replay(sim: Simulator, k: number, R: number, likelyByDistrict: Uint8Array, idx: Int32Array, now: number): number {
    let replayed = 0;
    for (let j = 0; j < idx.length; j++) {
      const i = idx[j];
      const before = this.state[i];
      const bx = this.target[i * 3], by = this.target[i * 3 + 1];
      const after = this.setDot(i, sim, k, R, likelyByDistrict, now);
      if (after !== before || bx !== this.target[i * 3] || by !== this.target[i * 3 + 1]) {
        replayed++;
        // Replays start from home with a short stagger, so the rescue reads clearly.
        this.timing[i * 4] = now + (hash32(i) / 4294967296) * 0.6;
        if (before === STATE_STRANDED && after === STATE_REACHED) this.rescued.stranded++;
        if (before === STATE_LIKELY && after === STATE_REACHED) this.rescued.likely++;
      }
    }
    this.recount();
    this.version++;
    return replayed;
  }

  layerData(all: boolean): BinaryData {
    if (all) {
      if (this.cacheFull?.v !== this.version) {
        this.cacheFull = {
          v: this.version,
          d: {
            length: this.n,
            attributes: {
              getPosition: this.originDescFull,
              getTarget: { value: this.target, size: 3 },
              getTiming: { value: this.timing, size: 4 },
              getFillColor: { value: this.color, size: 4, normalized: true },
            },
          },
        };
      }
      return this.cacheFull.d;
    }
    if (this.cacheSub?.v !== this.version) {
      const s = this.subset, m = s.length;
      const tg = new Float32Array(m * 3), tm = new Float32Array(m * 4), co = new Uint8Array(m * 4);
      for (let j = 0; j < m; j++) {
        const i = s[j];
        tg[j * 3] = this.target[i * 3]; tg[j * 3 + 1] = this.target[i * 3 + 1];
        tm.set(this.timing.subarray(i * 4, i * 4 + 4), j * 4);
        co.set(this.color.subarray(i * 4, i * 4 + 4), j * 4);
      }
      this.cacheSub = {
        v: this.version,
        d: {
          length: m,
          attributes: {
            getPosition: this.originDescSub,
            getTarget: { value: tg, size: 3 },
            getTiming: { value: tm, size: 4 },
            getFillColor: { value: co, size: 4, normalized: true },
          },
        },
      };
    }
    return this.cacheSub.d;
  }

  /** Last time any dot is still moving, to know when to stop redrawing. */
  endTime(): number {
    let t = 0;
    for (let i = 0; i < this.n; i++) {
      const e = this.timing[i * 4] + this.timing[i * 4 + 1];
      if (e > t) t = e;
    }
    return t;
  }
}
