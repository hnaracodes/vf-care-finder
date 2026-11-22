// Visual language (docs/09_presentation.md): dark background, one accent,
// one fixed sequential km scale for distance in every scene, four
// colour-blind-safe quadrant colours that never rely on red versus green.
// Quadrant palette validated with the dataviz validator (dark surface,
// all pairs): #c98500, #3987e5, #d55181 pass; "served" is a neutral gray.

export type RGBA = [number, number, number, number];

export const BG = '#0b0e14';
export const ACCENT: RGBA = [76, 201, 240, 255];

function hex(h: string, a = 255): RGBA {
  const v = parseInt(h.slice(1), 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255, a];
}

/** Distance classes, km. Fixed across scenes so the viewer can compare. */
export const DIST_EDGES_KM = [10, 30, 60, 100, 200];
export const DIST_COLORS: RGBA[] = [
  hex('#4a4338'), // <= 10 km: recedes toward the background
  hex('#6e5631'),
  hex('#9a6a1f'),
  hex('#c98500'),
  hex('#f0a830'),
  hex('#ffdb8f'), // > 200 km or none at this level
];
export const DIST_LABELS = ['10 km or less', '10 to 30 km', '30 to 60 km', '60 to 100 km', '100 to 200 km', 'over 200 km'];

export function distClass(d: number): number {
  if (!(d <= DIST_EDGES_KM[DIST_EDGES_KM.length - 1])) return DIST_EDGES_KM.length; // also NaN, Infinity
  for (let i = 0; i < DIST_EDGES_KM.length; i++) if (d <= DIST_EDGES_KM[i]) return i;
  return DIST_EDGES_KM.length;
}

export const QUADRANT_ORDER = ['care_desert', 'data_desert', 'cost_barrier', 'served'] as const;
export const QUADRANT_COLORS: Record<string, RGBA> = {
  care_desert: hex('#c98500'),
  data_desert: hex('#3987e5'),
  cost_barrier: hex('#d55181'),
  served: hex('#5d6472'),
  unscored: hex('#2b303a'),
};
export const QUADRANT_LABELS: Record<string, string> = {
  care_desert: 'Care desert',
  data_desert: 'Data desert',
  cost_barrier: 'Cost or quality barrier',
  served: 'Served',
  unscored: 'Not scored',
};
export const QUADRANT_ACTIONS: Record<string, string> = {
  care_desert: 'Poor access, poor outcome: send care',
  data_desert: 'Poor access in the data, good outcome: send data collectors',
  cost_barrier: 'Good access, poor outcome: look at cost and quality',
  served: 'Good access, good outcome',
  unscored: 'District not matched or no outcome for this line',
};

/** Tolerant mapping of WP6 quadrant strings to our keys. */
export function normQuadrant(q: string | null | undefined): string {
  if (!q) return 'unscored';
  const s = q.toLowerCase().replace(/[\s-]+/g, '_');
  if (s.includes('care')) return 'care_desert';
  if (s.includes('data')) return 'data_desert';
  if (s.includes('cost') || s.includes('quality') || s.includes('barrier')) return 'cost_barrier';
  if (s.includes('served')) return 'served';
  return 'unscored';
}

/** Facility markers: neutral ramp by grade so they never clash with the amber distance scale. */
export const GRADE_COLORS: RGBA[] = [hex('#6b7280', 200), hex('#9aa5b1'), hex('#d7dde4'), hex('#ffffff')];
export const GRADE_RADIUS_PX = [1.6, 2.4, 3.4, 4.4];

/** Swarm states. */
export const SWARM = {
  reached: hex('#f2f5f8', 235),
  stranded: hex('#c98500', 255),
  likely: hex('#3987e5', 255),
  travel: hex('#c9d1d9', 215),
};
export const DIM_PO: RGBA = [42, 47, 56, 255];

export function rgbaCss(c: RGBA, alpha?: number): string {
  return `rgba(${c[0]},${c[1]},${c[2]},${alpha ?? c[3] / 255})`;
}
