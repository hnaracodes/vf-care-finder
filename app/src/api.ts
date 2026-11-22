// Talks to dbapp/server.py. Every endpoint takes the same query parameters:
//   service   one of the 12 service ids, or "other" for the keyword fallback
//   text      the free-text service, only with service=other
//   q         what the user typed in "Where is the patient?"
//   place_id  optional, a candidate the user picked after an ambiguous place
//
// There is no fixture or mock path: every answer comes from the server, which
// runs vf.rank and vf.locate on the bundle. If the server does not answer, the
// screen says so.

import type { Answer, Query, Resolution, Service, Simulate, Trip } from './types';

export class ApiError extends Error {
  constructor(message: string, readonly status = 0) {
    super(message);
  }
}

function params(q: Partial<Query>): URLSearchParams {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v != null && v !== '') p.set(k, String(v));
  return p;
}

async function get<T>(path: string, p: URLSearchParams): Promise<T> {
  const qs = p.toString();
  let res: Response;
  try {
    res = await fetch(`./api/${path}${qs ? `?${qs}` : ''}`, { headers: { Accept: 'application/json' } });
  } catch {
    throw new ApiError('The server did not answer. Check the connection and try again.');
  }
  if (!res.ok) {
    let msg = `The server returned an error (${res.status}).`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === 'string') msg = body.detail;
    } catch { /* not JSON */ }
    throw new ApiError(msg, res.status);
  }
  return (await res.json()) as T;
}

/** The service list, and whether /api/simulate can answer (only once WP13 probabilities exist). */
export async function boot(): Promise<{ services: Service[]; simulateAvailable: boolean }> {
  const r = await get<{ services: Service[]; simulate_available?: boolean }>('services', new URLSearchParams());
  const list = r.services;
  if (!Array.isArray(list) || list.length === 0) throw new ApiError('The server returned no services.');
  return { services: list, simulateAvailable: r.simulate_available === true };
}

export const resolve = (q: string) => get<Resolution>('resolve', params({ q }));
export const answer = (q: Query) => get<Answer>('answer', params(q));
export const trip = (q: Query) => get<Trip>('trip', params(q));

/** null when simulation is not available (no p_offers yet, or the endpoint is missing). */
export async function simulate(q: Query): Promise<Simulate | null> {
  try {
    const s = await get<Simulate>('simulate', params(q));
    if (!s || s.status !== 'ok' || !Array.isArray(s.policies) || s.policies.length === 0) return null;
    return s;
  } catch {
    return null;
  }
}
