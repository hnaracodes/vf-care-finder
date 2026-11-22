// Route swarm for the small map's trip view (D032) and simulation (D038).
// Reuses the India-wide patient swarm's approach (the old class is kept in
// legacy/swarm.ts): each dot is one post office area, not a person; every dot
// moves at the same speed in km; start offsets come from a hash of the dot
// index, so every playback is identical. A route is a list of stops. Each leg
// (stop k to stop k+1) is one SwarmLayer instance; legs after the first stay
// hidden until their dot arrives at the leg's start, so a re-routed dot reads
// as one dot that stops, then moves on.

import { hash32, haversineKm } from './geo';
import { SWARM, type RGBA } from './colors';

export interface Route {
  /** [lon, lat] stops; the first is the post office */
  path: [number, number][];
  /** colour at each intermediate stop (no service found) and at the end */
  end: 'reached' | 'stranded';
}

export interface LegData {
  length: number;
  hideBefore: boolean;
  attributes: Record<string, { value: Float32Array | Uint8Array; size: number; normalized?: boolean }>;
}

export interface Segment {
  from: [number, number];
  to: [number, number];
  /** true when this leg leads to a stop where the service was not found */
  toDeadEnd: boolean;
}

export interface SwarmOptions {
  /** seconds for a typical (85th percentile) leg */
  typicalLegS?: number;
  /** seconds for the longest single leg, at most */
  longestLegS?: number;
  /** spread of start times, seconds */
  stagger?: number;
  /** pause at a stop before moving on, seconds */
  pause?: number;
  /** animation time the playback starts at */
  t0?: number;
}

const END_COLOR: Record<Route['end'], RGBA> = { reached: SWARM.reached, stranded: SWARM.stranded };

export class RouteSwarm {
  readonly legs: LegData[] = [];
  /** every leg as a straight line, for the faint trip lines under the dots */
  readonly segments: Segment[] = [];
  readonly endTime: number;
  readonly speedKmS: number;

  constructor(routes: Route[], opts: SwarmOptions = {}) {
    const stagger = opts.stagger ?? 1.2;
    const pause = opts.pause ?? 0.5;
    const t0 = opts.t0 ?? 0;
    let maxLegs = 0;
    const lens: number[] = [];
    for (const r of routes) {
      maxLegs = Math.max(maxLegs, r.path.length - 1);
      for (let k = 0; k + 1 < r.path.length; k++) {
        const [a, b] = [r.path[k], r.path[k + 1]];
        lens.push(haversineKm(a[1], a[0], b[1], b[0]));
        const last = k + 2 === r.path.length;
        this.segments.push({ from: a, to: b, toDeadEnd: !last || r.end === 'stranded' });
      }
    }
    lens.sort((x, y) => x - y);
    const p85 = lens.length ? lens[Math.floor(0.85 * (lens.length - 1))] : 1;
    const longest = lens.length ? lens[lens.length - 1] : 1;
    // Typical legs take a readable time; the longest never takes more than longestLegS.
    this.speedKmS = Math.max(0.5, p85 / (opts.typicalLegS ?? 2.2), longest / (opts.longestLegS ?? 6));
    const start = routes.map((_, i) => t0 + (hash32(i) / 4294967296) * stagger);
    const clock = start.slice();
    let end = t0;
    for (let k = 0; k < maxLegs; k++) {
      const idx: number[] = [];
      routes.forEach((r, i) => { if (r.path.length > k + 1) idx.push(i); });
      const m = idx.length;
      const pos = new Float32Array(m * 3), tgt = new Float32Array(m * 3), tim = new Float32Array(m * 4), col = new Uint8Array(m * 4);
      idx.forEach((i, j) => {
        const r = routes[i];
        const a = r.path[k], b = r.path[k + 1];
        const d = haversineKm(a[1], a[0], b[1], b[0]);
        const dur = d / this.speedKmS;
        pos[j * 3] = a[0]; pos[j * 3 + 1] = a[1];
        tgt[j * 3] = b[0]; tgt[j * 3 + 1] = b[1];
        tim[j * 4] = clock[i];
        tim[j * 4 + 1] = dur;
        tim[j * 4 + 2] = 0;
        tim[j * 4 + 3] = (hash32(i ^ 0x5bd1e995) / 4294967296) * Math.PI * 2;
        const last = k + 2 === r.path.length;
        col.set(last ? END_COLOR[r.end] : SWARM.stranded, j * 4);
        clock[i] += dur + pause;
        end = Math.max(end, clock[i]);
      });
      this.legs.push({
        length: m,
        hideBefore: k > 0,
        attributes: {
          getPosition: { value: pos, size: 3 },
          getTarget: { value: tgt, size: 3 },
          getTiming: { value: tim, size: 4 },
          getFillColor: { value: col, size: 4, normalized: true },
        },
      });
    }
    this.endTime = end;
  }
}
