// Browser counters vs the server's summary (the parity check, SPEC 3.2).

import type { SimPayload } from './contract';
import type { Counters } from './rules';

/** Browser counters vs the server's summary (the parity check, SPEC 3.2). */
export function paritySummary(c: Counters, p: SimPayload): { ok: boolean; diff: Record<string, [unknown, unknown]> } {
  const s = p.summary;
  const diff: Record<string, [unknown, unknown]> = {};
  const exact: (keyof Counters & keyof typeof s)[] = ['within', 'n_no_evidence_in_reach', 'first_no_evidence', 'first_claim_only', 'first_held_back'];
  if (c.n !== s.n_villages) diff.n_villages = [c.n, s.n_villages];
  for (const k of exact) if (c[k] !== s[k]) diff[k] = [c[k], s[k]];
  for (const k of ['avg_minutes', 'extra_minutes_mean'] as const) {
    const a = c[k], b = s[k];
    if ((a === null) !== (b === null) || (a !== null && b !== null && Math.abs(a - b) > 0.051)) diff[k] = [a, b];
  }
  return { ok: Object.keys(diff).length === 0, diff };
}
