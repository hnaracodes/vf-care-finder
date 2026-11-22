// Small text helpers for the map. The formatters mirror vf.travel (fmt_minutes, fmt_hours)
// so a label built here from an API number reads the same as the server's text. They only
// format numbers the API sent; they never invent one.

import type { MapAnswer, MapCard } from './map_types';

export function fmtMinutes(m: number): string {
  let r = m < 10 ? Math.round(m) : 5 * Math.round(m / 5);
  r = Math.max(r, 1);
  if (r < 60) return `${r} min`;
  const h = Math.floor(r / 60), mm = r % 60;
  return mm === 0 ? `${h} h` : `${h} h ${mm} min`;
}

export function fmtHours(windowMin: number): string {
  const h = windowMin / 60;
  if (Math.abs(h - 1) < 1e-9) return '1 hour';
  if (Math.abs(h - Math.round(h)) < 1e-9) return `${Math.round(h)} hours`;
  return `${h.toFixed(1)} hours`;
}

export function titleCase(s: string): string {
  return s.split(/\s+/).filter(Boolean).map((w, i) => {
    if (w.length <= 3 && w === w.toUpperCase() && /[A-Z]/.test(w) && !/[AEIOU]/.test(w)) return w; // abbreviations
    const lw = w.toLowerCase();
    if (i > 0 && (lw === 'and' || lw === 'of' || lw === 'the')) return lw;
    return lw.charAt(0).toUpperCase() + lw.slice(1);
  }).join(' ');
}

/** The searched place's short name: origin.short_name, else a short form of the resolver label. */
export function shortName(a: MapAnswer): string {
  if (a.origin?.short_name) return a.origin.short_name;
  const loc = a.location;
  const label = loc?.label ?? '';
  if (loc?.kind === 'pin') {
    const m = label.match(/PIN\s*\d{6}/);
    if (m) return m[0];
  }
  const cut = label.split(/[,(]/)[0].trim();
  return cut || 'Searched place';
}

/** Window hours text: the server's, else formatted from window.minutes. Null for the older ranker. */
export function hoursText(a: MapAnswer): string | null {
  const w = a.window;
  if (!w) return null;
  if (w.hours_text) return w.hours_text;
  return typeof w.minutes === 'number' ? fmtHours(w.minutes) : null;
}

/** A card's time for a map label: time.short, else "about N min" from its minutes, else km. */
export function cardTime(c: MapCard, coarse = false): string | null {
  // coarse: the searched place is too large to drive from (origin.too_coarse), so its time
  // ranges ("2 h to 11 h 25 min") say nothing; the page shows km there, and so does the map.
  // a loose place's time.short is a wide spread ("1 min to 45 min"): the map names the point time,
  // as the headline does ("about 6 min from the middle of Jaipur")
  if (!coarse && typeof c.minutes === 'number') return `about ${fmtMinutes(c.minutes)}`;
  if (!coarse && c.time?.short) return c.time.short;
  if (typeof c.distance_km === 'number' && !c.position_approximate) return `${Math.round(c.distance_km)} km`;
  return null;
}

export function cardShortName(c: MapCard): string {
  return (c.name_short || c.name.split(',')[0].trim()).replace(/([A-Za-z])['\u2019]S\b/g, "$1's");
}
