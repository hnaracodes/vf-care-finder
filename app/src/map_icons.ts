// A small icon atlas drawn once in a canvas (no image files, no web fonts). Exported so the
// simulator can use the same symbols: IconLayer({ iconAtlas: iconAtlas(), iconMapping: ICON_MAPPING,
// getIcon: () => 'diamond-claim', sizeUnits: 'pixels', getSize: 18 }).

import { CLAIM, NEUTRAL, WHATIF } from './colors';

const S = 64;
const NAMES = ['diamond-neutral', 'diamond-claim', 'diamond-whatif', 'plus-whatif', 'diamond-mask'] as const;
export type IconName = (typeof NAMES)[number];

export const ICON_MAPPING: Record<IconName, { x: number; y: number; width: number; height: number; mask: boolean }> =
  Object.fromEntries(NAMES.map((n, i) => [n, { x: i * S, y: 0, width: S, height: S, mask: n === 'diamond-mask' }])) as never;

let url: string | null = null;

function diamond(g: CanvasRenderingContext2D, ox: number, fill: string, stroke: string | null): void {
  const c = S / 2, r = S / 2 - 5;
  g.beginPath();
  g.moveTo(ox + c, c - r); g.lineTo(ox + c + r, c); g.lineTo(ox + c, c + r); g.lineTo(ox + c - r, c); g.closePath();
  g.fillStyle = fill; g.fill();
  if (stroke) { g.lineWidth = 5; g.strokeStyle = stroke; g.stroke(); }
}

/** Data URL of the atlas (5 icons of 64 px in a row). */
export function iconAtlas(): string {
  if (url) return url;
  const cv = document.createElement('canvas');
  cv.width = S * NAMES.length; cv.height = S;
  const g = cv.getContext('2d')!;
  diamond(g, 0, NEUTRAL, '#ffffff');
  diamond(g, S, CLAIM, '#ffffff');
  diamond(g, 2 * S, WHATIF, '#ffffff');
  // plus
  const ox = 3 * S, c = S / 2;
  g.fillStyle = '#ffffff';
  g.beginPath(); g.arc(ox + c, c, S / 2 - 3, 0, Math.PI * 2); g.fill();
  g.fillStyle = WHATIF;
  g.beginPath(); g.arc(ox + c, c, S / 2 - 7, 0, Math.PI * 2); g.fill();
  g.fillStyle = '#ffffff';
  g.fillRect(ox + c - 4, c - 16, 8, 32); g.fillRect(ox + c - 16, c - 4, 32, 8);
  diamond(g, 4 * S, '#ffffff', null);
  url = cv.toDataURL('image/png');
  return url;
}
