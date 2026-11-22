// The stated rules (SPEC 7.3), pure functions over a World. The server's
// sim_payload.py computes `summary` with the same rules, so the browser's
// real-data counters must equal it (the parity check in selftest.ts).

import { haversineKm, round1, ST_CLAIM_ONLY, ST_HELD_BACK, type World } from './world';

export interface Counters {
  n: number;
  within: number;
  avg_minutes: number | null;
  n_no_evidence_in_reach: number;
  first_no_evidence: number;
  first_claim_only: number;
  first_held_back: number;
  extra_minutes_mean: number | null;
}

export interface Base {
  /** minutes to the nearest place with evidence, Infinity when none */
  near: Float64Array;
  /** that place, -1 when none */
  nearJ: Int32Array;
  /** the nearest open place by km, any status (ties to the lower index), -1 when none */
  first: Int32Array;
}

export function base(w: World): Base {
  const V = w.V, F = w.F;
  const near = new Float64Array(V).fill(Infinity);
  const nearJ = new Int32Array(V).fill(-1);
  const first = new Int32Array(V).fill(-1);
  const bestKm = new Float64Array(V).fill(Infinity);
  for (let j = 0; j < F; j++) {
    if (!w.open[j]) continue;
    const o = j * V;
    const isEv = w.ev(j);
    for (let v = 0; v < V; v++) {
      const km = w.KM[o + v];
      if (km < bestKm[v]) { bestKm[v] = km; first[v] = j; }
      if (isEv) {
        const m = w.MIN[o + v];
        if (m < near[v]) { near[v] = m; nearJ[v] = j; }
      }
    }
  }
  return { near, nearJ, first };
}

/** The evidence place nearest (km) to facility j's position; ties to the lower index. */
export function nearestEvToFacility(w: World, j: number): { j: number; km: number } {
  let bj = -1, bk = Infinity;
  for (let k = 0; k < w.F; k++) {
    if (!w.ev(k)) continue;
    const km = haversineKm(w.flat[j], w.flon[j], w.flat[k], w.flon[k]);
    if (km < bk) { bk = km; bj = k; }
  }
  return { j: bj, km: bk };
}

export function counters(w: World, b: Base = base(w)): Counters {
  const V = w.V;
  let within = 0, sum = 0, nFin = 0, noReach = 0, fNo = 0, fClaim = 0, fHeld = 0, xSum = 0, xN = 0;
  const jStar = new Map<number, { j: number; km: number }>();
  for (let v = 0; v < V; v++) {
    const m = b.near[v];
    if (isFinite(m)) { nFin++; sum += m; if (m <= w.W) within++; } else noReach++;
    const j = b.first[v];
    if (j < 0 || w.ev(j)) continue;
    fNo++;
    if (w.status[j] === ST_CLAIM_ONLY) fClaim++;
    else if (w.status[j] === ST_HELD_BACK) fHeld++;
    if (!isFinite(m)) continue;
    let s = jStar.get(j);
    if (!s) { s = nearestEvToFacility(w, j); jStar.set(j, s); }
    if (s.j < 0) continue;
    xSum += w.MIN[j * V + v] + w.vfac[v] * w.a * Math.pow(s.km, w.b) - m;
    xN++;
  }
  return {
    n: V, within, avg_minutes: nFin ? round1(sum / nFin) : null, n_no_evidence_in_reach: noReach,
    first_no_evidence: fNo, first_claim_only: fClaim, first_held_back: fHeld,
    extra_minutes_mean: xN ? round1(xSum / xN) : null,
  };
}

// ------------------------------------------------------------------ choices

export interface Choice { j: number; m: number }

/**
 * Evidence rule for a dot: among places with evidence within the window, the
 * lowest (minutes - allowance if strong), ties to the lower minutes then the
 * lower index; with none inside the window, the nearest by minutes. null when
 * there is no place with evidence at all (the dot is stuck).
 */
export function chooseEvidence(w: World, minutesTo: (j: number) => number): Choice | null {
  let bj = -1, bKey = Infinity, bM = Infinity; // inside the window
  let oj = -1, oM = Infinity; // nearest overall
  for (let j = 0; j < w.F; j++) {
    if (!w.ev(j)) continue;
    const m = minutesTo(j);
    if (m < oM) { oM = m; oj = j; }
    if (m <= w.W) {
      const key = m - (w.isStrong(j) ? w.A : 0);
      if (key < bKey || (key === bKey && m < bM)) { bKey = key; bM = m; bj = j; }
    }
  }
  if (bj >= 0) return { j: bj, m: bM };
  if (oj >= 0) return { j: oj, m: oM };
  return null;
}

/** Nearest place with evidence by minutes, no window, no allowance (the referral after a hold). */
export function chooseNearestEvidence(w: World, minutesTo: (j: number) => number): Choice | null {
  let oj = -1, oM = Infinity;
  for (let j = 0; j < w.F; j++) {
    if (!w.ev(j)) continue;
    const m = minutesTo(j);
    if (m < oM) { oM = m; oj = j; }
  }
  return oj >= 0 ? { j: oj, m: oM } : null;
}

/** Claims rule: the nearest open place that lists the service, by km, skipping places already visited. */
export function chooseClaims(w: World, kmTo: (j: number) => number, visited: ArrayLike<number> = [], nVisited = 0): { j: number; km: number } | null {
  let bj = -1, bk = Infinity;
  outer: for (let j = 0; j < w.F; j++) {
    if (!w.open[j]) continue;
    for (let k = 0; k < nVisited; k++) if (visited[k] === j) continue outer;
    const km = kmTo(j);
    if (km < bk) { bk = km; bj = j; }
  }
  return bj >= 0 ? { j: bj, km: bk } : null;
}

export const villageMinutes = (w: World, v: number) => (j: number) => w.MIN[j * w.V + v];
export const villageKm = (w: World, v: number) => (j: number) => w.KM[j * w.V + v];
export const pointMinutes = (w: World, lon: number, lat: number, f: number) => (j: number) => w.minutesFrom(lon, lat, f, j);
export const pointKm = (w: World, lon: number, lat: number) => (j: number) => haversineKm(lat, lon, w.flat[j], w.flon[j]);

// ------------------------------------------------------------------ what-if helpers

/** Counters before and after a what-if facility at (lon, lat), without changing the world. */
export function previewAdd(w: World, lon: number, lat: number): { before: Counters; after: Counters } {
  const before = counters(w);
  const j = w.pushTemp(lon, lat);
  try {
    return { before, after: counters(w) };
  } finally {
    w.popTemp(j);
  }
}

export interface VerifyRow {
  j: number;
  facility_id: string;
  name: string;
  km_from_origin: number;
  approximate: boolean;
  held_back: boolean;
  before: { within: number; first_no_evidence: number; avg_minutes: number | null };
  after: { within: number; first_no_evidence: number; avg_minutes: number | null };
  gain_within: number;
  gain_first: number;
  saved: number;
}

export interface VerifyFirst {
  n_candidates: number;
  rows: VerifyRow[];
  ms: number;
}

/**
 * "Which record should be verified first?" (SPEC 7.7): for every open record
 * that lists the service without evidence, the counters if it alone were
 * verified. O(V) per candidate from the precomputed nearest and first.
 */
export function verifyFirst(w: World, top = 3): VerifyFirst {
  const t0 = performance.now();
  const b = base(w);
  const V = w.V;
  let within0 = 0, sum0 = 0, nFin0 = 0, first0 = 0;
  const firstCount = new Int32Array(w.F);
  for (let v = 0; v < V; v++) {
    const m = b.near[v];
    if (isFinite(m)) { nFin0++; sum0 += m; if (m <= w.W) within0++; }
    const j = b.first[v];
    if (j >= 0 && !w.ev(j)) { first0++; firstCount[j]++; }
  }
  const avg0 = nFin0 ? round1(sum0 / nFin0) : null;
  const rows: VerifyRow[] = [];
  for (let j = 0; j < w.F; j++) {
    if (!w.open[j] || w.ev(j) || w.isAdded(j)) continue;
    const o = j * V;
    let gain = 0, sum = 0;
    for (let v = 0; v < V; v++) {
      const m0 = b.near[v], mj = w.MIN[o + v];
      if (!(m0 <= w.W) && mj <= w.W) gain++;
      sum += mj < m0 ? mj : m0;
    }
    const avg1 = V ? round1(sum / V) : null;
    const meta = w.meta[j];
    rows.push({
      j, facility_id: meta.facility_id, name: meta.name, km_from_origin: meta.km_from_origin,
      approximate: meta.approximate, held_back: w.status[j] === ST_HELD_BACK,
      before: { within: within0, first_no_evidence: first0, avg_minutes: avg0 },
      after: { within: within0 + gain, first_no_evidence: first0 - firstCount[j], avg_minutes: avg1 },
      gain_within: gain, gain_first: firstCount[j],
      saved: avg0 === null ? (avg1 === null ? 0 : Infinity) : (avg1 === null ? 0 : (sum0 / nFin0) - sum / V),
    });
  }
  rows.sort((x, y) =>
    y.gain_within - x.gain_within || y.gain_first - x.gain_first || cmpNum(y.saved, x.saved)
    || x.km_from_origin - y.km_from_origin || (x.facility_id < y.facility_id ? -1 : x.facility_id > y.facility_id ? 1 : 0));
  return { n_candidates: rows.length, rows: rows.slice(0, top), ms: performance.now() - t0 };
}

function cmpNum(a: number, b: number): number {
  return a === b ? 0 : a > b ? 1 : -1;
}
