// Distance helpers. haversineKm must stay identical (formula and operation
// order) to haversine_km in scripts/wp8_mock_app_data.py and to WP7's
// simulator, so in-browser coverage matches scenarios.json.

export const DEFAULT_EARTH_R_KM = 6371.0088;
const DEG = Math.PI / 180;

export function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number, R = DEFAULT_EARTH_R_KM): number {
  const p1 = lat1 * DEG;
  const p2 = lat2 * DEG;
  const dlat = p2 - p1;
  const dlon = (lon2 - lon1) * DEG;
  const s1 = Math.sin(dlat / 2);
  const s2 = Math.sin(dlon / 2);
  const a = s1 * s1 + Math.cos(p1) * Math.cos(p2) * s2 * s2;
  return 2 * R * Math.asin(Math.sqrt(Math.min(1, a)));
}

/** Point a fraction f of the way along the great circle from A to B. */
export function interpolateGreatCircle(
  lat1: number, lon1: number, lat2: number, lon2: number, f: number,
): [number, number] {
  const p1 = lat1 * DEG, l1 = lon1 * DEG, p2 = lat2 * DEG, l2 = lon2 * DEG;
  const ax = Math.cos(p1) * Math.cos(l1), ay = Math.cos(p1) * Math.sin(l1), az = Math.sin(p1);
  const bx = Math.cos(p2) * Math.cos(l2), by = Math.cos(p2) * Math.sin(l2), bz = Math.sin(p2);
  const dot = Math.max(-1, Math.min(1, ax * bx + ay * by + az * bz));
  const th = Math.acos(dot);
  if (th < 1e-12) return [lon1, lat1];
  const s = Math.sin(th);
  const wa = Math.sin((1 - f) * th) / s, wb = Math.sin(f * th) / s;
  const x = wa * ax + wb * bx, y = wa * ay + wb * by, z = wa * az + wb * bz;
  return [Math.atan2(y, x) / DEG, Math.atan2(z, Math.sqrt(x * x + y * y)) / DEG];
}

/** Deterministic 32-bit integer hash (murmur3 finalizer). */
export function hash32(i: number): number {
  let h = (i ^ 0x9e3779b9) >>> 0;
  h ^= h >>> 16;
  h = Math.imul(h, 0x85ebca6b) >>> 0;
  h ^= h >>> 13;
  h = Math.imul(h, 0xc2b2ae35) >>> 0;
  h ^= h >>> 16;
  return h >>> 0;
}

export interface Pt {
  lon: number;
  lat: number;
}

/**
 * Nearest point of `pts` for every post office, with a uniform grid.
 * Used for the "any facility" slider stop, which is not in the contract's
 * per-grade files.
 */
export function nearestAll(
  poLat: Float32Array, poLon: Float32Array, pts: (Pt | null)[], noIndex: number, R = DEFAULT_EARTH_R_KM,
): { dist: Float32Array; near: Uint16Array } {
  const n = poLat.length;
  const dist = new Float32Array(n).fill(Infinity);
  const near = new Uint16Array(n).fill(noIndex);
  const cell = 0.5;
  const grid = new Map<number, number[]>();
  const key = (cx: number, cy: number) => cx * 4096 + cy;
  let minCx = Infinity, maxCx = -Infinity, minCy = Infinity, maxCy = -Infinity;
  pts.forEach((p, i) => {
    if (!p || !Number.isFinite(p.lat) || !Number.isFinite(p.lon)) return;
    const cx = Math.floor((p.lon + 180) / cell), cy = Math.floor((p.lat + 90) / cell);
    minCx = Math.min(minCx, cx); maxCx = Math.max(maxCx, cx);
    minCy = Math.min(minCy, cy); maxCy = Math.max(maxCy, cy);
    const k = key(cx, cy);
    let arr = grid.get(k);
    if (!arr) grid.set(k, (arr = []));
    arr.push(i);
  });
  if (grid.size === 0) return { dist, near };
  const kmPerCellLB = cell * 111.19 * Math.cos(40 * DEG); // lower bound per ring, valid below 40 deg latitude
  const maxRing = Math.max(maxCx - minCx, maxCy - minCy) + 2;
  for (let i = 0; i < n; i++) {
    const la = poLat[i], lo = poLon[i];
    const cx = Math.floor((lo + 180) / cell), cy = Math.floor((la + 90) / cell);
    let best = Infinity, bestJ = noIndex;
    for (let r = 0; r <= maxRing; r++) {
      for (let dx = -r; dx <= r; dx++) {
        for (let dy = -r; dy <= r; dy++) {
          if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
          const arr = grid.get(key(cx + dx, cy + dy));
          if (!arr) continue;
          for (const j of arr) {
            const p = pts[j]!;
            const d = haversineKm(la, lo, p.lat, p.lon, R);
            if (d < best) { best = d; bestJ = j; }
          }
        }
      }
      if (best <= r * kmPerCellLB) break;
    }
    dist[i] = best;
    near[i] = bestJ;
  }
  return { dist, near };
}
