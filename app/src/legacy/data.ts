// Loads the App data files described in docs/07_data_contracts.md ("App data").
// Every number the app shows comes from these files.

export type LineKey = 'emoc' | 'dialysis' | 'icu';
export type QuadrantKey = 'care_desert' | 'data_desert' | 'cost_barrier' | 'served';

export interface BinaryEntry {
  file: string;
  dtype: string;
  length: number;
}

export interface Meta {
  mock?: boolean;
  mock_note?: string;
  generated_at?: string;
  git_commit?: string;
  versions?: Record<string, string>;
  service_lines?: { key: LineKey; label: string; long?: string }[];
  grades?: { k: number; label: string }[];
  radii_km?: number[];
  simulator?: { min_grade?: number; upgrade_to_grade?: number; earth_radius_km?: number; steps?: number };
  no_index?: number;
  binary: BinaryEntry[];
  counts?: {
    facilities?: number;
    facilities_with_coords?: number;
    facilities_private_share?: number;
    post_offices_total?: number;
    post_offices_geocoded?: number;
    districts?: number;
    nfhs_districts?: number;
    districts_matched?: Record<string, number>;
  };
  models?: {
    fact_tagger?: {
      mock?: boolean;
      gold_set?: { n_facts?: number; labeled_by?: string };
      systems?: string[];
      per_tag?: { tag: string; [system: string]: { p?: number; r?: number; f1?: number } | string }[];
      cost_note?: string;
    };
    outcome_model?: {
      mock?: boolean;
      target?: string;
      cv_scheme?: string;
      metric?: string;
      without_access?: { mean: number; sd?: number };
      with_access?: { mean: number; sd?: number };
      n_districts?: number;
    };
  };
  caveats?: Record<string, string>;
  limits?: string[];
}

export interface Facility {
  id: string;
  name: string;
  lat: number | null;
  lon: number | null;
  type?: string | null;
  operator?: string | null;
  city?: string | null;
  state?: string | null;
  grades: Partial<Record<LineKey, number>>;
  evidence?: Partial<Record<LineKey, string[]>>;
  tags?: Partial<Record<LineKey, string[]>>;
  n_domains?: number;
}

export interface QuadrantInfo {
  quadrant_rule?: string | null;
  quadrant_model?: string | null;
  p_real_gap?: number | null;
  access_value?: number | null;
  outcome_value?: number | null;
  outcome_flag?: string | null;
  predicted_outcome?: number | null;
  residual?: number | null;
}

export interface District {
  key: string;
  state: string;
  name: string;
  quadrant?: Partial<Record<LineKey, QuadrantInfo | null>> | null;
  nfhs?: Record<string, number | null>;
  nfhs_flags?: Record<string, string>;
  access?: Partial<Record<LineKey, Record<string, number | null> | null>> | null;
  residual?: number | null;
  crosswalk_method?: string | null;
}

export interface ScenarioStep {
  step?: number;
  kind: 'upgrade' | 'new';
  facility_id?: string | null;
  lat?: number | null;
  lon?: number | null;
  marginal_gain: number;
  newly_covered?: number;
  covered?: number;
  cumulative_coverage: number;
  district_key?: string | null;
  quadrant_model?: string | null;
}

export interface ScenarioSetting {
  service_line: LineKey;
  radius_km: number;
  min_grade?: number;
  n_post_offices?: number;
  baseline_covered?: number;
  baseline_coverage?: number;
  naive: ScenarioStep[];
  desert_aware: ScenarioStep[];
}

export interface Scenarios {
  mock?: boolean;
  earth_radius_km?: number;
  upgrade_to_grade?: number;
  settings: ScenarioSetting[];
}

export interface Camera {
  longitude: number;
  latitude: number;
  zoom: number;
}

export interface Beat {
  id: number | string;
  time?: string;
  duration_s?: number;
  camera?: Camera;
  view?: 'distance' | 'quadrant' | 'swarm';
  line?: LineKey;
  grade?: number;
  radius_km?: number;
  caption?: string;
  pulse?: { lon: number; lat: number } | null;
  patient?: { lon: number; lat: number } | null;
  sweep?: number[] | null;
  show_counter_change?: { from_grade: number; to_grade: number } | null;
  highlight?: string | null;
  panel?: 'drawer' | 'lists' | 'limits' | null;
  scenario?: { plans: ('naive' | 'desert_aware')[]; steps: number } | null;
}

export interface Story {
  mock?: boolean;
  defaults?: { line?: LineKey; radius_km?: number; transition_s?: number };
  beats: Beat[];
}

export interface AppData {
  meta: Meta;
  n: number;
  lon: Float32Array;
  lat: Float32Array;
  district: Uint16Array;
  /** key `${line}_g${k}` for k = 1..3 */
  dist: Record<string, Float32Array>;
  near: Record<string, Uint16Array>;
  facilities: Facility[];
  facIndexById: Map<string, number>;
  districts: District[];
  districtIndexByKey: Map<string, number>;
  scenarios: Scenarios | null;
  story: Story | null;
  noIndex: number;
  lines: { key: LineKey; label: string; long: string }[];
}

const DTYPES: Record<string, (b: ArrayBuffer) => ArrayLike<number>> = {
  float32: (b) => new Float32Array(b),
  uint16: (b) => new Uint16Array(b),
  uint32: (b) => new Uint32Array(b),
  int32: (b) => new Int32Array(b),
  uint8: (b) => new Uint8Array(b),
};

async function fetchJSON<T>(url: string, optional = false): Promise<T | null> {
  const res = await fetch(url);
  if (!res.ok) {
    if (optional) return null;
    throw new Error(`Could not load ${url} (${res.status})`);
  }
  return (await res.json()) as T;
}

async function fetchBinary(base: string, entry: BinaryEntry): Promise<ArrayLike<number>> {
  const res = await fetch(base + entry.file);
  if (!res.ok) throw new Error(`Could not load ${entry.file} (${res.status})`);
  const make = DTYPES[entry.dtype];
  if (!make) throw new Error(`Unknown dtype ${entry.dtype} for ${entry.file}`);
  const arr = make(await res.arrayBuffer());
  if (arr.length !== entry.length) {
    throw new Error(`${entry.file}: expected ${entry.length} values, got ${arr.length}`);
  }
  return arr;
}

export async function loadData(base = './data/', onProgress?: (msg: string) => void): Promise<AppData> {
  onProgress?.('Loading meta.json');
  const meta = await fetchJSON<Meta>(base + 'meta.json');
  if (!meta || !Array.isArray(meta.binary)) throw new Error('meta.json has no "binary" list');

  onProgress?.(`Loading ${meta.binary.length} binary columns`);
  const [bins, facilities, districts, scenarios, story] = await Promise.all([
    Promise.all(meta.binary.map((e) => fetchBinary(base, e).then((a) => [e.file, a] as const))),
    fetchJSON<Facility[]>(base + 'facilities.json'),
    fetchJSON<District[]>(base + 'districts.json'),
    fetchJSON<Scenarios>(base + 'scenarios.json', true),
    fetchJSON<Story>(base + 'story.json', true),
  ]);
  const byFile = new Map<string, ArrayLike<number>>(bins);
  const need = (f: string) => {
    const a = byFile.get(f);
    if (!a) throw new Error(`meta.json does not list ${f}`);
    return a;
  };
  const lon = need('po_lon.f32') as Float32Array;
  const lat = need('po_lat.f32') as Float32Array;
  const district = need('po_district.u16') as Uint16Array;
  const n = lon.length;

  const dist: Record<string, Float32Array> = {};
  const near: Record<string, Uint16Array> = {};
  for (const [file, arr] of byFile) {
    let m = /^po_dist_(\w+)_g(\d)\.f32$/.exec(file);
    if (m) dist[`${m[1]}_g${m[2]}`] = arr as Float32Array;
    m = /^po_near_(\w+)_g(\d)\.u16$/.exec(file);
    if (m) near[`${m[1]}_g${m[2]}`] = arr as Uint16Array;
  }

  const lines = (meta.service_lines ?? [
    { key: 'emoc', label: 'Obstetrics', long: 'Emergency obstetric care' },
    { key: 'dialysis', label: 'Dialysis', long: 'Dialysis' },
    { key: 'icu', label: 'ICU', long: 'Intensive care with ventilation' },
  ]).filter((l) => dist[`${l.key}_g1`] !== undefined).map((l) => ({ key: l.key, label: l.label, long: l.long ?? l.label }));

  const facIndexById = new Map<string, number>();
  (facilities ?? []).forEach((f, i) => facIndexById.set(f.id, i));
  const districtIndexByKey = new Map<string, number>();
  (districts ?? []).forEach((d, i) => districtIndexByKey.set(d.key, i));

  return {
    meta,
    n,
    lon,
    lat,
    district,
    dist,
    near,
    facilities: facilities ?? [],
    facIndexById,
    districts: districts ?? [],
    districtIndexByKey,
    scenarios,
    story,
    noIndex: meta.no_index ?? 65535,
    lines,
  };
}
