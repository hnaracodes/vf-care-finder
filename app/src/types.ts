// API shapes, as dbapp/server.py returns them. Answer and Card are vf.rank's
// dataclasses (src/vf/rank.py); Resolution is vf.locate's; Trip is built in
// server.py; Simulate wraps vf.rank.simulate. Optional fields are read
// defensively: the server may omit them.

export interface Service {
  id: string;
  label: string;
  radius_km: number;
  description?: string;
}

export interface Candidate {
  label: string;
  place_id?: string | null;
  kind?: string | null;
  lat?: number | null;
  lon?: number | null;
  precision_km?: number | null;
}

export type ResolutionStatus = 'ok' | 'ambiguous' | 'not_found' | 'outside_india';

export interface Resolution {
  status: ResolutionStatus;
  label: string;
  how?: string | null;
  lat: number | null;
  lon: number | null;
  precision_km: number | null;
  kind?: string | null;
  place_id?: string | null;
  candidates?: Candidate[];
}

export interface Penalty {
  name: string;
  value: number;
  reason: string;
  quote?: string | null;
}

/** A WP11 trust flag on the record, as vf.rank puts it on a card. */
export interface Flag {
  flag: string;
  severity: 'minor' | 'major' | string;
  signal?: string | null;
  reason: string;
  quote?: string | null;
}

/**
 * Score parts. In the v0 sum (combine "sum") evidence, distance and trust are
 * the weighted contributions and raw holds the 0 to 1 values before weighting.
 * Keyword cards use combine "product" (match x reach) with trust null.
 */
export interface Parts {
  evidence: number;
  distance: number;
  trust: number | null;
  penalties: Penalty[];
  combine?: 'sum' | 'product' | 'match' | string;
  raw?: { evidence: number; distance: number; trust: number | null };
  weights?: Record<string, number>;
  labels?: Record<string, string>;
}

export interface EvidenceItem {
  requirement: string;
  quote: string;
  field: string;
  fact_id?: string | null;
}

export interface Card {
  facility_id: string;
  name: string;
  type?: string | null;
  operator?: string | null;
  city?: string | null;
  state?: string | null;
  lat: number | null;
  lon: number | null;
  distance_km: number | null;
  location_precision_km?: number | null;
  location_note?: string | null;
  grade?: number | null;
  grade_label?: string | null;
  confidence?: 'high' | 'medium' | 'low' | null;
  confidence_reason?: string | null;
  score?: number | null;
  parts?: Parts | Record<string, never> | null;
  evidence?: EvidenceItem[];
  missing?: string[];
  sources?: {
    n_domains: number;
    domains: string[];
    official_website?: string | null;
    also_listed_as?: (string | { facility_id?: string; name?: string })[];
  } | null;
  why_here?: string | null;
  could_be_wrong?: string | null;
  evidence_strength?: string | null;
  flags?: Flag[];
}

export interface Gate {
  gate: string;
  rule?: string | null;
  n?: number | null;
  of?: number | null;
  n_blocked?: number | null;
}

export interface HowWeDecided {
  mode?: 'v0' | 'probability' | 'keyword' | string;
  headline?: string;
  recipe?: string | string[];
  gates?: Gate[];
  formula?: string | null;
  weights?: Record<string, number> | null;
  penalties?: Record<string, number> | null;
  radius_km?: number;
  reach_km?: number;
  score_status?: string;
  tie_rule?: string;
  limits?: string[];
  notes?: string[];
  [k: string]: unknown;
}

export type AnswerStatus = 'ok' | 'no_reliable_option' | 'needs_location_choice' | 'location_not_found';

export interface Answer {
  status: AnswerStatus;
  service: string;
  service_label: string;
  radius_km: number;
  method: 'recipe' | 'keyword';
  location: Resolution;
  results: Card[];
  beyond_radius?: Card | null;
  unverified_nearby?: Card[];
  how_we_decided?: HowWeDecided;
}

export interface TripFacility {
  facility_id: string;
  name: string;
  lat: number;
  lon: number;
  kind: 'recommended' | 'claim_only';
  rank?: number | null;
  supported?: boolean;
  /** Why a claim target is not supported: it only claims the service (grade 1), or its evidence is
   * held back by a blocking warning (grade 2 or more). Null when supported. */
  unsupported_reason?: 'claim_only' | 'held_back' | null;
}

export interface Trip {
  status: 'ok' | 'unavailable';
  reason?: string;
  service: string;
  service_label?: string;
  radius_km: number;
  center?: { lat: number; lon: number };
  facilities: TripFacility[];
  /** [lon, lat] per post office */
  points: [number, number][];
  /** index into facilities per point, -1 when none */
  nearest_recommended: number[];
  nearest_claim: number[];
  summary?: {
    n_points: number;
    n_first_unsupported: number;
    /** n_first_unsupported = n_first_claim_only + n_first_held_back */
    n_first_claim_only?: number;
    n_first_held_back?: number;
    extra_km_mean: number | null;
    n_rerouted?: number;
    recommended_beyond_radius?: boolean;
  };
  notes?: string[];
}

export interface SimPolicy {
  policy: 'nearest_claim' | 'nearest_evidence' | 'ranked' | string;
  label?: string;
  expected_km: number | null;
  share_second_trip: number | null;
}

export interface Simulate {
  status: 'ok' | 'unavailable';
  reason?: string;
  service: string;
  n_origins?: number;
  policies: SimPolicy[];
  /** optional, for the animation: facilities with p_offers, origins, and per policy the visit order per origin */
  facilities?: { facility_id: string; lat: number; lon: number; p_offers: number }[];
  origins?: [number, number][];
  orders?: Record<string, number[][]>;
  notes?: string[];
}

export interface Query {
  service: string;
  /** free text for the "Something else" fallback */
  text?: string;
  q: string;
  place_id?: string | null;
}
