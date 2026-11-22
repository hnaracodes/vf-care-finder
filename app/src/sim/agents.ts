// Event-driven patient agents (SPEC 7.4). Nothing is precomputed: each dot
// reads the current world when it departs, when it arrives, when a hold ends
// and whenever the world changes, and picks its next target by the stated
// rule for the mode. Between events a dot moves linearly in time along a
// straight line, at the speed the drive-time model gives for that leg
// (minutes = factor x a x km^b). No Math.random: departures come from an
// FNV-1a hash of (seed, village, index), events are ordered by
// (time, kind, insertion), so the same seed and the same edits at the same
// sim-minutes give the same state at every minute.

import {
  chooseClaims, chooseEvidence, chooseNearestEvidence, pointKm, pointMinutes, villageKm, villageMinutes,
} from './rules';
import { haversineKm, type Edit, type World } from './world';

export type Mode = 'evidence' | 'claims';

export const PH_WAIT = 0;
export const PH_MOVE = 1;
export const PH_HOLD = 2;
export const PH_ARRIVED = 3;
export const PH_STUCK = 4;
export const PH_QUEUE = 5;

/** how a leg is drawn */
export const LEG_EVIDENCE = 0; // to a place whose record shows it
export const LEG_CLAIM = 1; // first trip to a place that lists it, no evidence in its record
export const LEG_REFERRED = 2; // the extra trip, if that place cannot help

export const HOLD_MIN = 5;
export const DEPART_SPREAD_MIN = 24;
export const MAX_AGENTS = 6000;
export const CAPACITY_WINDOW_MIN = 60;

const EV_DEPART = 0, EV_ARRIVE = 1, EV_HOLD_END = 2, EV_ADMIT = 3, EV_WORLD = 9;

/** FNV-1a of `${seed}|${v}|${idx}`, divided by 2^32. */
export function hash01(seed: number, v: number, idx: number): number {
  const s = `${seed}|${v}|${idx}`;
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h / 4294967296;
}

interface Ev { t: number; kind: number; seq: number; i: number; leg: number }

class Heap {
  private a: Ev[] = [];
  get size(): number { return this.a.length; }
  peek(): Ev | undefined { return this.a[0]; }
  clear(): void { this.a = []; }
  private static lt(x: Ev, y: Ev): boolean {
    return x.t < y.t || (x.t === y.t && (x.kind < y.kind || (x.kind === y.kind && x.seq < y.seq)));
  }
  push(e: Ev): void {
    const a = this.a;
    a.push(e);
    let k = a.length - 1;
    while (k > 0) {
      const p = (k - 1) >> 1;
      if (!Heap.lt(a[k], a[p])) break;
      [a[k], a[p]] = [a[p], a[k]];
      k = p;
    }
  }
  pop(): Ev | undefined {
    const a = this.a;
    if (!a.length) return undefined;
    const top = a[0];
    const last = a.pop()!;
    if (a.length) {
      a[0] = last;
      let k = 0;
      for (;;) {
        const l = 2 * k + 1, r = l + 1;
        let m = k;
        if (l < a.length && Heap.lt(a[l], a[m])) m = l;
        if (r < a.length && Heap.lt(a[r], a[m])) m = r;
        if (m === k) break;
        [a[k], a[m]] = [a[m], a[k]];
        k = m;
      }
    }
    return top;
  }
}

export interface EngineOptions {
  seed?: number;
  /** dots per village */
  k?: number;
  mode?: Mode;
  /** P2: at most this many arrivals per facility per 60 sim-minutes (an assumption, not from data); null is off */
  capacity?: number | null;
}

export interface Snapshot { at: number; edits: Edit[] }

export interface Tally {
  arrived: number;
  moving: number;
  holding: number;
  waiting: number;
  stuck: number;
  queued: number;
  stoppedNoEvidence: number;
  turned: number;
  total: number;
}

export class Engine {
  readonly w: World;
  readonly seed: number;
  readonly k: number;
  readonly N: number;
  mode: Mode;
  capacity: number | null;

  // per agent
  readonly vOf: Int32Array;
  readonly depart: Float64Array;
  readonly x0: Float64Array;
  readonly y0: Float64Array;
  readonly tx: Float64Array;
  readonly ty: Float64Array;
  readonly t0: Float64Array;
  readonly dur: Float64Array;
  readonly target: Int32Array;
  readonly phase: Uint8Array;
  readonly referred: Uint8Array;
  readonly turned: Uint8Array;
  readonly stopped: Uint8Array;
  readonly legKind: Uint8Array;
  readonly legId: Int32Array;
  readonly visited: Int32Array;
  readonly nVis: Uint8Array;

  time = 0;
  speed = 5;
  /** set when any agent's leg changes; the renderer clears it */
  dirty = true;
  /** world snapshots this run replays: [0] is the world at t = 0 */
  timeline: Snapshot[] = [];
  /** per facility: dots that arrived there, and dots that stopped there first and found no evidence */
  arrivedAt = new Map<number, number>();
  stoppedAt = new Map<number, number>();
  /** road so far: x0, y0, x1, y1 per completed or cut leg; kind per leg */
  road = new Float32Array(4096);
  roadKind = new Uint8Array(1024);
  roadTurned = new Uint8Array(1024);
  nRoad = 0;
  /** the targets of the first decision pass (camera fit) */
  firstTargets = new Set<number>();

  private fixedSpeed: number | null = null;
  private heap = new Heap();
  private seq = 0;
  private admitted = new Map<number, number[]>();
  private pendingWorld = 0;

  constructor(w: World, opts: EngineOptions = {}) {
    this.w = w;
    this.seed = opts.seed ?? 7;
    this.mode = opts.mode ?? 'evidence';
    this.capacity = opts.capacity ?? null;
    const V = w.V;
    this.k = Math.max(1, Math.min(opts.k ?? 3, Math.floor(MAX_AGENTS / Math.max(1, V))));
    const N = (this.N = V * this.k);
    this.vOf = new Int32Array(N);
    this.depart = new Float64Array(N);
    for (let v = 0, i = 0; v < V; v++) {
      for (let q = 0; q < this.k; q++, i++) {
        this.vOf[i] = v;
        this.depart[i] = hash01(this.seed, v, q) * DEPART_SPREAD_MIN;
      }
    }
    this.x0 = new Float64Array(N); this.y0 = new Float64Array(N);
    this.tx = new Float64Array(N); this.ty = new Float64Array(N);
    this.t0 = new Float64Array(N); this.dur = new Float64Array(N);
    this.target = new Int32Array(N);
    this.phase = new Uint8Array(N);
    this.referred = new Uint8Array(N);
    this.turned = new Uint8Array(N);
    this.stopped = new Uint8Array(N);
    this.legKind = new Uint8Array(N);
    this.legId = new Int32Array(N);
    this.visited = new Int32Array(N * 4);
    this.nVis = new Uint8Array(N);
    this.timeline = [{ at: 0, edits: w.edits.slice() }];
    this.restart();
  }

  // ------------------------------------------------------------------ run control

  /** Back to t = 0 with the world as it was at the start of this run (timeline[0]); timed edits replay. */
  restart(): void {
    const w = this.w;
    w.setEdits(this.timeline[0].edits);
    this.heap.clear();
    this.seq = 0;
    this.time = 0;
    this.nRoad = 0;
    this.arrivedAt.clear();
    this.stoppedAt.clear();
    this.admitted.clear();
    this.firstTargets.clear();
    this.pendingWorld = 0;
    for (let i = 0; i < this.N; i++) {
      const v = this.vOf[i];
      this.x0[i] = this.tx[i] = w.vlon[v];
      this.y0[i] = this.ty[i] = w.vlat[v];
      this.t0[i] = this.depart[i];
      this.dur[i] = 0;
      this.target[i] = -1;
      this.phase[i] = PH_WAIT;
      this.referred[i] = this.turned[i] = this.stopped[i] = this.nVis[i] = 0;
      this.legKind[i] = LEG_EVIDENCE;
      this.legId[i] = 0;
      this.push(this.depart[i], EV_DEPART, i, 0);
    }
    for (let s = 1; s < this.timeline.length; s++) {
      this.push(this.timeline[s].at, EV_WORLD, s, 0);
      this.pendingWorld++;
    }
    // One time scale for both rules and for every replay, so the two runs can be compared second by second.
    if (this.fixedSpeed === null) this.fixedSpeed = Math.max(this.estimateSpeed('evidence'), this.estimateSpeed('claims'));
    this.speed = this.fixedSpeed;
    this.syncSeen();
    this.dirty = true;
  }

  /** Replay: t = 0 with the current world (every edit applies from the start). */
  replay(): void {
    this.timeline = [{ at: 0, edits: this.w.edits.slice() }];
    this.restart();
  }

  setMode(m: Mode): void {
    this.mode = m;
    this.replay();
  }

  setCapacity(n: number | null): void {
    this.capacity = n;
    this.replay();
  }

  /** Re-run from t = 0 to t with no rendering (frame-exact screenshots). */
  seek(t: number): void {
    this.restart();
    this.advanceTo(t);
  }

  /** The caller changed the world (apply, undo, reset) at the current time. */
  worldChanged(): void {
    const edits = this.w.edits.slice();
    if (this.time === 0) {
      this.timeline = [{ at: 0, edits }];
      this.restart();
      return;
    }
    // a change after a backward seek drops the snapshots that were later than now
    this.timeline = this.timeline.filter((s, k) => k === 0 || s.at <= this.time);
    this.timeline.push({ at: this.time, edits });
    this.redecideAll();
  }

  get done(): boolean {
    if (this.heap.size > 0) return false;
    for (let i = 0; i < this.N; i++) {
      const p = this.phase[i];
      if (p === PH_MOVE || p === PH_WAIT || p === PH_HOLD || p === PH_QUEUE) return false;
    }
    return true;
  }

  /** Process every event up to sim-minute t, in order. */
  advanceTo(t: number): void {
    for (;;) {
      const e = this.heap.peek();
      if (!e || e.t > t) break;
      this.heap.pop();
      this.time = e.t;
      this.handle(e);
    }
    this.time = Math.max(this.time, t);
  }

  // ------------------------------------------------------------------ events

  private push(t: number, kind: number, i: number, leg: number): void {
    this.heap.push({ t, kind, seq: this.seq++, i, leg });
  }

  private handle(e: Ev): void {
    if (e.kind === EV_WORLD) {
      this.pendingWorld--;
      this.w.setEdits(this.timeline[e.i].edits);
      this.redecideAll();
      return;
    }
    const i = e.i;
    if (e.leg !== this.legId[i]) return; // stale: the dot was re-routed
    if (e.kind === EV_DEPART) this.onDepart(i);
    else if (e.kind === EV_ARRIVE) this.onArrive(i);
    else if (e.kind === EV_HOLD_END) this.onHoldEnd(i);
    else if (e.kind === EV_ADMIT) this.onArrive(i);
  }

  private onDepart(i: number): void {
    const w = this.w, v = this.vOf[i];
    if (this.mode === 'evidence') {
      const c = chooseEvidence(w, villageMinutes(w, v));
      if (!c) return this.setStuck(i, w.vlon[v], w.vlat[v]);
      this.startLeg(i, w.vlon[v], w.vlat[v], c.j, c.m);
    } else {
      const c = chooseClaims(w, villageKm(w, v));
      if (!c) return this.setStuck(i, w.vlon[v], w.vlat[v]);
      this.startLeg(i, w.vlon[v], w.vlat[v], c.j, w.MIN[c.j * w.V + v]);
    }
    if (this.timeline.length === 1 || this.pendingWorld === this.timeline.length - 1) this.firstTargets.add(this.target[i]);
  }

  private onArrive(i: number): void {
    const w = this.w, j = this.target[i];
    if (w.ev(j)) {
      if (this.capacity != null && this.capacity > 0 && !this.admit(i, j)) return;
      this.cutRoad(i, this.tx[i], this.ty[i]);
      this.phase[i] = PH_ARRIVED;
      this.arrivedAt.set(j, (this.arrivedAt.get(j) ?? 0) + 1);
      this.dirty = true;
      return;
    }
    if (this.mode === 'claims' && !this.referred[i] && !this.stopped[i]) {
      // the record shows no evidence: wait here, then go on "if that place cannot help"
      this.cutRoad(i, this.tx[i], this.ty[i]);
      this.stopped[i] = 1;
      this.stoppedAt.set(j, (this.stoppedAt.get(j) ?? 0) + 1);
      if (this.nVis[i] < 4) this.visited[i * 4 + this.nVis[i]++] = j;
      this.phase[i] = PH_HOLD;
      this.x0[i] = this.tx[i]; this.y0[i] = this.ty[i];
      this.t0[i] = this.time; this.dur[i] = 0;
      this.legId[i]++;
      this.push(this.time + HOLD_MIN, EV_HOLD_END, i, this.legId[i]);
      this.dirty = true;
      return;
    }
    // reached a place that no longer shows evidence (defensive: a change re-routes first)
    this.cutRoad(i, this.tx[i], this.ty[i]);
    this.routeToNearestEvidence(i, this.tx[i], this.ty[i]);
  }

  private onHoldEnd(i: number): void {
    const w = this.w, j = this.target[i];
    if (w.ev(j)) {
      // verified while the dot waited
      this.phase[i] = PH_ARRIVED;
      this.arrivedAt.set(j, (this.arrivedAt.get(j) ?? 0) + 1);
      this.dirty = true;
      return;
    }
    this.referred[i] = 1;
    this.routeToNearestEvidence(i, this.tx[i], this.ty[i]);
  }

  private routeToNearestEvidence(i: number, lon: number, lat: number): void {
    const w = this.w;
    const c = chooseNearestEvidence(w, pointMinutes(w, lon, lat, w.vfac[this.vOf[i]]));
    if (!c) return this.setStuck(i, lon, lat);
    this.startLeg(i, lon, lat, c.j, c.m);
  }

  /** P2 capacity: true when the place has room in the last 60 sim-minutes; otherwise queue the dot. */
  private admit(i: number, j: number): boolean {
    const n = this.capacity!;
    let list = this.admitted.get(j);
    if (!list) { list = []; this.admitted.set(j, list); }
    let k = 0;
    while (k < list.length && list[k] <= this.time - CAPACITY_WINDOW_MIN) k++;
    if (k) list.splice(0, k);
    if (list.length < n) { list.push(this.time); return true; }
    const free = list[list.length - n] + CAPACITY_WINDOW_MIN;
    if (this.phase[i] !== PH_QUEUE) {
      this.cutRoad(i, this.tx[i], this.ty[i]);
      this.x0[i] = this.tx[i]; this.y0[i] = this.ty[i]; this.t0[i] = this.time; this.dur[i] = 0;
      this.phase[i] = PH_QUEUE;
      this.dirty = true;
    }
    this.legId[i]++;
    this.push(Math.max(free, this.time + 1e-6), EV_ADMIT, i, this.legId[i]);
    return false;
  }

  // ------------------------------------------------------------------ legs

  private startLeg(i: number, lon: number, lat: number, j: number, minutes: number): void {
    const w = this.w;
    this.x0[i] = lon; this.y0[i] = lat;
    this.tx[i] = w.flon[j]; this.ty[i] = w.flat[j];
    this.t0[i] = this.time;
    this.dur[i] = minutes;
    this.target[i] = j;
    this.phase[i] = PH_MOVE;
    this.legKind[i] = this.referred[i] ? LEG_REFERRED : w.ev(j) ? LEG_EVIDENCE : LEG_CLAIM;
    this.legId[i]++;
    this.push(this.time + minutes, EV_ARRIVE, i, this.legId[i]);
    this.dirty = true;
  }

  private setStuck(i: number, lon: number, lat: number): void {
    this.x0[i] = this.tx[i] = lon;
    this.y0[i] = this.ty[i] = lat;
    this.t0[i] = this.time; this.dur[i] = 0;
    this.phase[i] = PH_STUCK;
    this.legId[i]++;
    this.dirty = true;
  }

  /** Current position of dot i (linear in time along its leg). */
  pos(i: number, t = this.time): [number, number] {
    const d = this.dur[i];
    const p = d > 0 ? Math.min(1, Math.max(0, (t - this.t0[i]) / d)) : 1;
    return [this.x0[i] + (this.tx[i] - this.x0[i]) * p, this.y0[i] + (this.ty[i] - this.y0[i]) * p];
  }

  private cutRoad(i: number, lon: number, lat: number): void {
    if (this.x0[i] === lon && this.y0[i] === lat) return;
    if ((this.nRoad + 1) * 4 > this.road.length) {
      const r = new Float32Array(this.road.length * 2); r.set(this.road); this.road = r;
      const k = new Uint8Array(this.roadKind.length * 2); k.set(this.roadKind); this.roadKind = k;
      const t = new Uint8Array(this.roadTurned.length * 2); t.set(this.roadTurned); this.roadTurned = t;
    }
    const o = this.nRoad * 4;
    this.road[o] = this.x0[i]; this.road[o + 1] = this.y0[i]; this.road[o + 2] = lon; this.road[o + 3] = lat;
    this.roadKind[this.nRoad] = this.legKind[i];
    this.roadTurned[this.nRoad] = this.turned[i];
    this.nRoad++;
  }

  /** Flags of the world the dots last read: which places were open, and which showed evidence. */
  private seenOpen = new Uint8Array(0);
  private seenEv = new Uint8Array(0);

  private syncSeen(): void {
    const w = this.w;
    this.seenOpen = new Uint8Array(w.F);
    this.seenEv = new Uint8Array(w.F);
    for (let j = 0; j < w.F; j++) { this.seenOpen[j] = w.open[j]; this.seenEv[j] = w.ev(j) ? 1 : 0; }
  }

  /**
   * The world changed while dots were on the road. Each moving dot decides
   * again from where it stands, by its mode's rule, but only where the change
   * reaches it:
   * - its target closed, or stopped showing evidence on an evidence trip:
   *   it picks again among every place, by the rule;
   * - a place opened or now shows evidence (an add or a verify): it switches
   *   only if the rule, from where it stands, prefers that place to its
   *   current target;
   * - otherwise it keeps its leg (its colour follows its target's status).
   * A stuck dot picks again among every place. A new target marks it turned.
   */
  private redecideAll(): void {
    const w = this.w;
    const fresh: number[] = [];
    for (let j = 0; j < w.F; j++) {
      const wasOpen = j < this.seenOpen.length ? this.seenOpen[j] : 0;
      const wasEv = j < this.seenEv.length ? this.seenEv[j] : 0;
      if ((w.open[j] && !wasOpen) || (w.ev(j) && !wasEv)) fresh.push(j);
    }
    for (let i = 0; i < this.N; i++) {
      const ph = this.phase[i];
      if (ph !== PH_MOVE && ph !== PH_STUCK) continue;
      const [lon, lat] = this.pos(i);
      const f = w.vfac[this.vOf[i]];
      const cur = this.target[i];
      const evTrip = this.referred[i] || this.mode === 'evidence';
      const lost = ph === PH_STUCK || cur < 0 || !w.open[cur] || (evTrip && !w.ev(cur));
      let j = -1, m = 0;
      if (lost) {
        if (this.referred[i]) {
          const c = chooseNearestEvidence(w, pointMinutes(w, lon, lat, f));
          if (c) { j = c.j; m = c.m; }
        } else if (this.mode === 'evidence') {
          const c = chooseEvidence(w, pointMinutes(w, lon, lat, f));
          if (c) { j = c.j; m = c.m; }
        } else {
          const c = chooseClaims(w, pointKm(w, lon, lat), this.visited.subarray(i * 4, i * 4 + 4), this.nVis[i]);
          if (c) { j = c.j; m = w.minutesForKm(c.km, f); }
        }
      } else if (fresh.length) {
        // the rule over the current target and the places the change made available
        const only = new Set<number>([cur, ...fresh]);
        const mins = (k: number) => (only.has(k) ? w.minutesFrom(lon, lat, f, k) : Infinity);
        if (this.referred[i]) {
          const c = chooseNearestEvidence(w, mins);
          if (c) { j = c.j; m = c.m; }
        } else if (this.mode === 'evidence') {
          const c = chooseEvidence(w, mins);
          if (c) { j = c.j; m = c.m; }
        } else {
          const km = (k: number) => (only.has(k) ? haversineKm(lat, lon, w.flat[k], w.flon[k]) : Infinity);
          const c = chooseClaims(w, km, this.visited.subarray(i * 4, i * 4 + 4), this.nVis[i]);
          if (c) { j = c.j; m = w.minutesForKm(c.km, f); }
        }
        if (j < 0 || !isFinite(m)) j = cur;
      } else {
        j = cur;
      }
      if (ph === PH_MOVE && j === cur) {
        // same place: keep the leg, only its colour may change (a verified target)
        const kind = this.referred[i] ? LEG_REFERRED : w.ev(j) ? LEG_EVIDENCE : LEG_CLAIM;
        if (kind !== this.legKind[i]) { this.legKind[i] = kind; this.dirty = true; }
        continue;
      }
      if (j < 0) {
        if (ph === PH_MOVE) { this.cutRoad(i, lon, lat); this.turned[i] = 1; this.setStuck(i, lon, lat); }
        continue;
      }
      if (ph === PH_MOVE) this.cutRoad(i, lon, lat);
      this.turned[i] = 1;
      this.startLeg(i, lon, lat, j, m);
    }
    this.syncSeen();
  }

  // ------------------------------------------------------------------ summaries

  tally(): Tally {
    const t: Tally = { arrived: 0, moving: 0, holding: 0, waiting: 0, stuck: 0, queued: 0, stoppedNoEvidence: 0, turned: 0, total: this.N };
    for (let i = 0; i < this.N; i++) {
      const p = this.phase[i];
      if (p === PH_ARRIVED) t.arrived++;
      else if (p === PH_MOVE) t.moving++;
      else if (p === PH_HOLD) t.holding++;
      else if (p === PH_WAIT) t.waiting++;
      else if (p === PH_STUCK) t.stuck++;
      else if (p === PH_QUEUE) t.queued++;
      if (this.stopped[i]) t.stoppedNoEvidence++;
      if (this.turned[i]) t.turned++;
    }
    return t;
  }

  /** The run counted in villages (all dots of a village follow the same rule from the same start):
   *  arrived = every dot of the village has arrived, stuck = no place with evidence to go to, the rest are on
   *  the way; stoppedFirst = villages whose patients stopped first at a place with no evidence (a subset). */
  villageTally(): { n: number; arrived: number; onWay: number; notLeft: number; stuck: number; stoppedFirst: number; turned: number } {
    const V = this.w.V, k = this.k;
    const out = { n: V, arrived: 0, onWay: 0, notLeft: 0, stuck: 0, stoppedFirst: 0, turned: 0 };
    for (let v = 0; v < V; v++) {
      let arr = 0, wait = 0, stuck = 0, stop = 0, turned = 0;
      for (let q = 0; q < k; q++) {
        const i = v * k + q, p = this.phase[i];
        if (p === PH_ARRIVED) arr++;
        else if (p === PH_WAIT) wait++;
        else if (p === PH_STUCK) stuck++;
        if (this.stopped[i]) stop = 1;
        if (this.turned[i]) turned = 1;
      }
      if (stuck === k) out.stuck++;
      else if (arr + stuck === k) out.arrived++;
      else if (wait === k) out.notLeft++;
      else out.onWay++;
      out.stoppedFirst += stop;
      out.turned += turned;
    }
    return out;
  }

  /** Default playback speed: the 90th-percentile settle time plays in about 12 s (sim-minutes per second). */
  private estimateSpeed(mode: Mode): number {
    const w = this.w;
    const settle: number[] = [];
    for (let v = 0; v < w.V; v++) {
      const dep = this.depart[v * this.k];
      let m = 0;
      if (mode === 'evidence') {
        const c = chooseEvidence(w, villageMinutes(w, v));
        m = c ? c.m : 0;
      } else {
        const c = chooseClaims(w, villageKm(w, v));
        if (c) {
          m = w.MIN[c.j * w.V + v];
          if (!w.ev(c.j)) {
            const r = chooseNearestEvidence(w, pointMinutes(w, w.flon[c.j], w.flat[c.j], w.vfac[v]));
            m += HOLD_MIN + (r ? r.m : 0);
          }
        }
      }
      settle.push(dep + m);
    }
    settle.sort((a, b) => a - b);
    const p90 = settle.length ? settle[Math.floor(0.9 * (settle.length - 1))] : 60;
    return Math.max(5, Math.ceil(p90 / 12));
  }

  /** Straight-line km of the leg dot i is on (debug, tests). */
  legKm(i: number): number {
    return haversineKm(this.y0[i], this.x0[i], this.ty[i], this.tx[i]);
  }
}
