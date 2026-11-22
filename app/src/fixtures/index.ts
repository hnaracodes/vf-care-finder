// FIXTURE router for development only. Every facility here is fictional and
// named "Fixture ...". Loaded through a dynamic import that only exists in dev
// builds (see api.ts), so none of this reaches dbapp/static in production.
// Regenerate the JSON with: .venv/bin/python app/scripts/make_fixtures.py

import services from './services.json';
import resolveJaipur from './resolve_jaipur.json';
import resolveFixturepur from './resolve_fixturepur.json';
import resolveAurangabad from './resolve_aurangabad.json';
import resolveNotFound from './resolve_not_found.json';
import resolveOutside from './resolve_outside.json';
import answerDialysisJaipur from './answer_dialysis_jaipur.json';
import answerKeywordJaipur from './answer_keyword_jaipur.json';
import answerDialysisFixturepur from './answer_dialysis_fixturepur.json';
import tripDialysisJaipur from './trip_dialysis_jaipur.json';
import simulateDialysisJaipur from './simulate_dialysis_jaipur.json';

/** What the fixtures can answer, for the "no fixture" message. */
export const FIXTURE_QUERIES = [
  'Dialysis, Jaipur (three results, trip view)',
  'Dialysis, Fixturepur (no reliable option)',
  'Something else "cataract surgery", Jaipur (keyword match)',
  'Aurangabad (asks which one)',
  'xyz (place not found), 51.5, -0.12 (outside India)',
];

function placeKey(q: string, placeId: string | null): string {
  if (placeId) return placeId;
  const s = q.trim().toLowerCase();
  if (s.includes('jaipur') || s === '302001') return 'jaipur';
  if (s.includes('fixturepur')) return 'fixturepur';
  if (s.includes('aurangabad')) return 'aurangabad';
  const m = s.match(/^\s*(-?\d+(\.\d+)?)\s*,\s*(-?\d+(\.\d+)?)\s*$/);
  if (m) {
    const a = Number(m[1]), b = Number(m[3]);
    const inIndia = (la: number, lo: number) => la > 6 && la < 37.5 && lo > 68 && lo < 97.5;
    if (inIndia(a, b) || inIndia(b, a)) return 'coords_india';
    return 'outside';
  }
  return 'not_found';
}

function resolveFixture(key: string): unknown {
  switch (key) {
    case 'jaipur':
    case 'city:jaipur|rajasthan':
      return resolveJaipur;
    case 'fixturepur':
    case 'post_office:fixture':
      return resolveFixturepur;
    case 'aurangabad':
      return resolveAurangabad;
    case 'outside':
      return resolveOutside;
    default:
      return resolveNotFound;
  }
}

export function fixtureFetch(path: string, p: URLSearchParams): unknown | null {
  const key = placeKey(p.get('q') ?? '', p.get('place_id'));
  const service = p.get('service') ?? '';
  if (path === 'services') return services;
  if (path === 'resolve') return resolveFixture(key);
  const isJaipur = key === 'jaipur' || key === 'city:jaipur|rajasthan';
  if (path === 'answer') {
    if (service === 'dialysis' && isJaipur) return answerDialysisJaipur;
    if (service === 'other' && isJaipur) return answerKeywordJaipur;
    if (service === 'dialysis' && (key === 'fixturepur' || key === 'post_office:fixture')) return answerDialysisFixturepur;
    return null;
  }
  if (path === 'trip') {
    if (service === 'dialysis' && isJaipur) return tripDialysisJaipur;
    return { status: 'unavailable', reason: 'No trip fixture for this query.', service, radius_km: 0, facilities: [], points: [], nearest_recommended: [], nearest_claim: [] };
  }
  if (path === 'simulate') {
    // Hidden by default, as in the real app before p_offers exists. ?fixture_sim shows the panel for testing.
    const on = new URLSearchParams(location.search).has('fixture_sim');
    if (on && service === 'dialysis' && isJaipur) return simulateDialysisJaipur;
    return null;
  }
  return null;
}
