// Plain-language helpers. Every word a health worker reads that is not a
// quote or a server string is built here, so the wording can be checked in
// one place. No grade numbers or service ids reach the screen.

import type { Answer } from './types';

export function esc(s: unknown): string {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
}

/** "Dialysis" -> "dialysis", "MRI scan" stays, "Emergency C-section ..." -> "emergency C-section ...". */
export function servicePhrase(label: string): string {
  if (label.length > 1 && /[A-Z]/.test(label[0]) && /[a-z]/.test(label[1])) return label[0].toLowerCase() + label.slice(1);
  return label;
}

const STEPS = [1, 2, 5, 10, 25, 50];

/**
 * A distance rounded to the location precision, so a PIN-centre location does
 * not read as "2.3 km". precision is the larger of the searched point's and
 * the facility's.
 */
export function kmText(d: number | null | undefined, precisionKm: number): string {
  if (d == null || !Number.isFinite(d)) return 'distance unknown';
  const p = Math.max(1, precisionKm || 1);
  if (d <= p) return `within about ${Math.ceil(p).toLocaleString('en-IN')} km`;
  const step = stepFor(p);
  return `about ${roundTo(d, step)} km`;
}

function stepFor(p: number): number {
  let s = 1;
  for (const x of STEPS) if (x <= p) s = x;
  return s;
}

function roundTo(x: number, step: number): string {
  const v = Math.max(step, Math.round(x / step) * step);
  return v.toLocaleString('en-IN');
}

export function capital(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

/** The service as a phrase: "dialysis", or the typed words in quotes for the keyword fallback. */
export function serviceName(a: Answer): string {
  if (a.method === 'keyword') {
    const q = a.how_we_decided?.query;
    const text = typeof q === 'string' && q ? q : a.service_label.replace(/^Keyword search:\s*/i, '');
    return `"${text}"`;
  }
  return servicePhrase(a.service_label);
}

/**
 * vf.services grade labels read "corroborated (the service and the things it
 * needs are named in the record)". Shown as "Corroborated: the service and ...".
 */
export function levelText(label: string | null | undefined): string {
  if (!label) return '';
  const m = /^([^(]+?)\s*\((.+)\)\s*$/.exec(label);
  return m ? `${capital(m[1])}: ${m[2]}.` : `${capital(label)}.`;
}

/** The server's confidence reason without its leading "High:" label, which the card already shows. */
export function plainReason(reason: string | null | undefined): string {
  return capital((reason ?? '').replace(/^(high|medium|low)\s*:\s*/i, ''));
}

/** Two numbers, as someone typing coordinates would. */
export function looksLikeCoords(typed: string): boolean {
  return /^\s*\(?\s*[-+]?\d{1,3}(\.\d+)?\s*[, ]\s*[-+]?\d{1,3}(\.\d+)?\s*\)?\s*$/.test(typed);
}

export const CONFIDENCE_WORDS: Record<string, string> = {
  high: 'High confidence',
  medium: 'Medium confidence',
  low: 'Low confidence',
};

export const FIELD_WORDS: Record<string, string> = {
  capability: 'capability',
  procedure: 'procedure',
  equipment: 'equipment',
  description: 'description',
  specialties: 'specialty',
  specialty: 'specialty',
  record: 'from the record',
};

export function operatorWord(op: string | null | undefined): string {
  if (!op) return 'ownership not stated';
  const s = op.toLowerCase();
  if (s === 'public' || s === 'government') return 'public';
  if (s === 'private') return 'private';
  return s;
}

export function typeWord(t: string | null | undefined): string {
  if (!t) return 'type not stated';
  return t.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/_/g, ' ').toLowerCase();
}

export const PART_WORDS: Record<string, string> = {
  evidence: 'Evidence',
  distance: 'Distance',
  trust: 'Source trust',
};

export function pct(x: number): string {
  return `${Math.round(x * 100)}%`;
}

export function fmt(x: number | null | undefined, digits = 2): string {
  if (x == null || !Number.isFinite(x)) return 'n/a';
  return x.toFixed(digits);
}

export function count(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return 'n/a';
  return n.toLocaleString('en-IN');
}
