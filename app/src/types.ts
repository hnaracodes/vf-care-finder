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
  state?: string | null;
  district?: string | null;
  query?: string | null;
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
  requirements?: string[];
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
  // v1 ranker fields already on the wire (src/vf/rank.py, D048)
  minutes?: number | null;
  minutes_low?: number | null;
  minutes_high?: number | null;
  minutes_label?: string | null;
  distance_label?: string | null;
  position_approximate?: boolean;
  window_min?: number | null;
  inside_window?: boolean;
  level_note?: string | null;
  // SPEC 3.1 enrichments (dbapp/server.py enrich_answer). All optional: the UI falls back when absent.
  name_short?: string;
  name_rest?: string | null;
  time?: TimeText | null;
  state_display?: string;
  crosses_state?: boolean;
  quotes?: Quote[];
  missing_phrases?: string[];
  questions?: Question[];
  call?: CallInfo;
  official_domain?: string | null;
  public_list?: PublicLine | null;
  outside_lines?: OutsideLine[];
  sim_index?: number | null;
  /** keyword search: the best-matching text names the words only inside a negation */
  match_negated?: boolean;
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
  // v1 ranker and SPEC 3.1 additions (all optional)
  further_away?: Card[];
  window?: Window;
  rank_version?: string;
  origin?: Origin;
  why_not?: WhyNot;
  funnel?: { steps: FunnelStep[]; consistency?: unknown };
  outside_nearby?: OutsideNearby[];
  demo_masked?: boolean;
  /** keyword search: the graded service the typed words name, if one does */
  did_you_mean?: { service: string; label: string } | null;
  /** set only on dev fixtures (app/src/fixtures/make_fixtures_v2.py); the UI shows a FIXTURE badge */
  fixture?: boolean;
  timing_ms?: number;
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
  /** demo=1 asks the server to mask names and phones; it can only turn masking on */
  demo?: string | null;
  /** reveal=1 asks for tel: links on Call (the server never gives them in demo mode) */
  reveal?: string | null;
}

// =====================================================================================
// SPEC section 3.4 (app/design/SPEC.md), verbatim. C produces these, A, B and D consume.
// =====================================================================================

export interface TimeText { short: string; range: string; loose: boolean }
export interface Quote { text: string; field: string; requirement_keys: string[]; marks: [number, number][];
  /** keyword search: the query's words appear in this text only inside a negation ("Excluding Snake Bite") */
  negated?: boolean }
export interface CallInfo { has_phone: boolean; phone?: string | null; tel?: string | null; note?: string | null }
export interface Question { question: string; why?: string }
export interface PublicLine { text: string; source?: string; validity?: string; current?: boolean; outside_data?: boolean }
export interface OutsideLine { source_id: string; source_name: string; status: 'current' | 'lapsed' | 'undated'; text: string }
// Card gains (all optional):
//   name_short, name_rest, time: TimeText | null, state_display, crosses_state, quotes: Quote[],
//   missing_phrases: string[], questions: Question[], call: CallInfo, official_domain: string | null,
//   public_list: PublicLine | null, outside_lines: OutsideLine[], sim_index: number | null,
//   minutes, minutes_low, minutes_high, minutes_label, distance_label, position_approximate,
//   window_min, inside_window, level_note   (v1 ranker fields already on the wire)
export interface WhyNotItem {
  letter: string; facility_id: string; name: string; name_masked: boolean; operator?: string | null;
  lat: number | null; lon: number | null; distance_km: number | null; distance_label: string;
  position_approximate: boolean; reason_code: string; reason_plain: string; tone: string; in_sim?: boolean;
  quote_or_absence?: { kind: 'quote' | 'absence'; text: string; field?: string; negated?: boolean } | null;
  /** server.py: estimated drive from the searched place (the middle of it, for a loose place) */
  minutes?: number | null; time_short?: string | null;
  /** server.py _why_not_extras: outside-list lines on this record, and the masked Call for public hospitals */
  outside_lines?: OutsideLine[]; call?: CallInfo | null;
}
export interface WhyNot { items: WhyNotItem[]; n_nearer: number; n_approx_nearer: number; heading: string; masked: boolean }
export interface FunnelStep { step: string; label: string; n: number; n_distinct?: number }
export interface Origin { short_name: string; label: string; precision_km: number | null; loose: boolean; too_coarse: boolean; state_display: string }
export interface Window { minutes: number; allowance: number; hours_text?: string; reach_text?: string; km_equivalent?: number;
  reach_km?: number; basis?: string; note?: string; origin_loose?: boolean; kind?: string;
  /** A's addition, already on the wire from the v1 ranker */
  reach_minutes?: number; factor?: number | null; state?: string | null }
// Answer gains: origin?: Origin; window?: Window; why_not?: WhyNot; funnel?: { steps: FunnelStep[] };
//   further_away?: Card[]; outside_nearby?: unknown[]; demo_masked?: boolean; rank_version?: string

export type FacStatus = 'evidence' | 'claim_only' | 'held_back';
export interface SimFacility { i: number; facility_id: string; name: string; lat: number; lon: number; status: FacStatus;
  strong: boolean; grade: number; approximate: boolean; operator?: string | null; km_from_origin: number;
  card_rank: number | null; why_not_letter: string | null; name_masked: boolean }
export interface SimSummary { n_villages: number; within: number; avg_minutes: number | null; n_no_evidence_in_reach: number;
  first_no_evidence: number; first_claim_only: number; first_held_back: number; extra_minutes_mean: number | null;
  n_facilities: number; n_evidence: number; n_claim_only: number; n_held_back: number;
  n_listing_records: number; n_merged_duplicates: number }
export interface SimLabel { name: string; kind: 'district' | 'city' | 'post_office'; lat: number; lon: number; prio: number }
export interface SimPayload {
  status: 'ok' | 'unavailable'; reason?: string; version: string;
  service: string; service_label: string; service_phrase: string;
  origin: { lat: number; lon: number; short_name: string; label: string; precision_km: number | null; state_display: string; factor: number };
  window: { minutes: number; allowance: number; hours_text: string; km_equivalent: number; reach_km: number; reach_minutes: number; basis_short?: string };
  travel: { a: number; b: number; text: string };
  villages: { n: number; radius_km: number; lon: number[]; lat: number[]; pin: string[]; factor: number[] };
  facilities: SimFacility[]; summary: SimSummary; context: { labels: SimLabel[] }; notes: string[];
}

// =====================================================================================
// A's additions to the contract (not in SPEC 3.4). Ask A before changing them.
// =====================================================================================

/** An entry of answer.outside_nearby (vf.public_list.annotate_answer, v2, D055). Read defensively. */
export interface OutsideNearby {
  source_id?: string; source_name?: string; name?: string; district?: string | null; state?: string | null;
  lat?: number | null; lon?: number | null; position_precision_km?: number | null; position_note?: string | null;
  minutes_label?: string | null; km?: number | null; what?: string | null; citation?: string | null; text: string;
}

/** The three counters of SPEC 6 / 7.3, as D's rules compute them (same names as SimSummary). */
export interface SimCounters {
  /** villages (D's Counters name it n) */
  n?: number; within: number; avg_minutes: number | null; first_no_evidence: number;
  extra_minutes_mean?: number | null; n_no_evidence_in_reach?: number;
}

export type SimMode = 'evidence' | 'claims';
export type SimEdit =
  | { type: 'verify'; facility_id: string }
  | { type: 'close'; facility_id: string }
  | { type: 'add'; lon: number; lat: number };
export interface SimOpenOptions { mode?: SimMode; autoplay?: boolean; tool?: 'verify' | 'add' | 'close'; edit?: SimEdit }

/**
 * D's Simulator (app/src/sim/index.ts, SPEC 7 and 9), as main.ts calls it:
 *   const sim = new Simulator(map); sim.load(payload, answer); sim.open({...}); sim.previewAdd({lat, lon}); sim.close();
 * previewAdd returns the counters before and after a what-if unit at {lat, lon}, without changing the world.
 * open() autoplays unless autoplay is false.
 */
export interface SimulatorLike {
  load(payload: SimPayload, answer: Answer): void;
  open(opts?: SimOpenOptions): void;
  close(): void;
  previewAdd(at: { lat: number; lon: number }): { before: SimCounters; after: SimCounters } | null;
}
export type SimulatorCtor = new (map: MapHostLike) => SimulatorLike;

export interface MapExample { label: string; lat: number; lon: number }

/** B's MapHost (app/src/map/index.ts, SPEC 5.4): the part of its public API that A calls. */
export interface MapHostLike {
  showLanding(examples: MapExample[], onPick: (i: number) => void): void;
  showCandidates(c: Candidate[], onPick: (i: number) => void): void;
  showAnswer(a: Answer, sim: SimPayload | null): void;
  highlight(kind: 'result' | 'whynot', key: number | string | null): void;
  focus(kind: 'result' | 'further', idx: number): void;
  expand(opts?: { rail?: boolean }): unknown;
  collapse(): void;
  isExpanded(): boolean;
  onPick: (kind: 'result' | 'whynot' | 'further', key: number | string) => void;
}
export type MapHostCtor = new (inlineEl: HTMLElement) => MapHostLike;
