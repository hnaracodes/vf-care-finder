// Every word on screen that is not a quote or a server sentence is built here, as pure
// functions, so the wording can be checked in one place (SPEC 4.3 to 4.10). No grade
// numbers, service ids or banned words (SPEC 1) reach the screen from this file.
//
// When the server has not enriched an answer yet (SPEC 3.1 fields absent, or the v0
// ranker), normCard() derives the same fields from what is on the wire, so the screen
// still reads the same way. Every number still comes from the API.

import type { Answer, Card, Flag, FunnelStep, Origin, Quote, SimPayload, SimCounters, Window } from './types';

export function esc(s: unknown): string {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
}

export function capital(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

export function lowerFirst(s: string): string {
  // "The record's ..." -> "the record's ..."; leaves acronyms such as "ICU" or "PIN" alone
  return /^[A-Z][a-z]/.test(s) ? s[0].toLowerCase() + s.slice(1) : s;
}

export function count(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return 'n/a';
  return Math.round(n).toLocaleString('en-IN');
}

/** "a, b and c" */
export function joinAnd(words: string[], last = 'and'): string {
  const w = words.filter(Boolean);
  if (w.length <= 1) return w[0] ?? '';
  return `${w.slice(0, -1).join(', ')} ${last} ${w[w.length - 1]}`;
}

/** "Dialysis" -> "dialysis", "MRI scan" stays, "Emergency C-section ..." -> "emergency C-section ...". */
export function servicePhrase(label: string): string {
  if (label.length > 1 && /[A-Z]/.test(label[0]) && /[a-z]/.test(label[1])) return label[0].toLowerCase() + label.slice(1);
  return label;
}

/** The service as a phrase: "dialysis", or the typed words in quotes for the keyword fallback. */
export function serviceName(a: Answer): string {
  if (a.method === 'keyword') return `"${keywordText(a)}"`;
  return servicePhrase(a.service_label);
}

export function keywordText(a: Answer): string {
  const q = a.how_we_decided?.query;
  return typeof q === 'string' && q ? q : a.service_label.replace(/^Keyword search:\s*/i, '');
}

/** Short chip label for the care: "Emergency C-section", "Dialysis". */
export function careChip(a: Answer): string {
  if (a.method === 'keyword') return `"${keywordText(a)}"`;
  const l = a.service_label;
  return l.replace(/\s+and high-risk delivery$/i, '').replace(/\s*\([^)]*\)\s*$/, '');
}

// ------------------------------------------------------------------ banned words (SPEC 1)

/** Words that must never reach the screen. Checked by bannedIn() and by the acceptance grep. */
export const BANNED = [
  /\bprobabilit(y|ies)\b/i, /\blikely\b/i, /\bcalibrat/i, /\bchances?\b/i, /\bgrade \d/i, /\bemoc\b/i,
  /\bcorroborat/i, /\bsupported\b/i, /\bcapabilit(y|ies)\b/i, /\brecommendable\b/i, /\bsource trust\b/i,
  /\bscor(e|es|ed|ing)\b/i, /\bdoes not have\b/i, /\bcannot\b/i, /\bthere is no care\b/i,
  /—/,
];

export function bannedIn(text: string): string[] {
  return BANNED.filter((rx) => rx.test(text)).map((rx) => String(rx.exec(text)?.[0]));
}

/**
 * Server sentences shown as they are, apart from these plain-word swaps for words SPEC 1
 * bans on screen. Each swap keeps the meaning. The source wording is listed for the module
 * owners in the build report; when they change it, the swaps stop matching and do nothing.
 */
const SWAPS: [RegExp, string][] = [
  [/\bRecommendable, but it scored below the cards shown\b/g, 'Its record names it, but it came after the options shown'],
  [/\brecommendable\b/gi, 'kept by our rule'],
  [/\bscored below\b/gi, 'came after'],
  [/^Claimed:/, 'Lists it:'],
  [/^Supported:/, 'Names the service:'],
  [/^Corroborated:/, 'Names everything it needs:'],
  [/\bcorroborated\b/gi, 'complete'],
  [/\bsupported\b/gi, 'named'],
  [/\bNothing here is a probability\.?/g, 'No number here is a forecast.'],
  [/\bprobabilit(y|ies)\b/gi, 'forecast'],
  [/\blikely\b/gi, 'expected'],
  [/\bgrade (\d)\b/gi, 'level $1'],
  [/\bcapability\b/gi, 'services'],
  [/\bscore(s|d)?\b/gi, 'order'],
  [/—/g, ':'],
  // citations in brackets are for the technical detail, not the screen
  [/\s*\((?:PM-JAY|WHO|FBNC|FRU|ESC|PMNDP|NHM)\b[^)]*\)/g, ''],
  [/\s*\(the evidence allowance\)/gi, ''],
  [/\bthe evidence allowance\b/gi, 'the allowance'],
  [/\bcannot\b/g, 'is unable to'],
  [/\bestimated free-flow drive time\b/gi, 'estimated drive time with no traffic'],
  [/\bfree-flow drive time\b/gi, 'drive time with no traffic'],
  [/\bfree-flow car trips\b/gi, 'car trips with no traffic'],
  [/\bFree-flow means\b/g, 'That means'],
  [/\bfree-flow\b/gi, 'no-traffic'],
  [/newborn mechanical ventilation \(not CPAP alone\), a ventilator in a newborn fact, or a self-declared level III NICU/g, 'newborn ventilator care (not only CPAP)'],
];

export function plainServer(s: string | null | undefined): string {
  let t = String(s ?? '');
  for (const [rx, to] of SWAPS) t = t.replace(rx, to);
  return t;
}

// ------------------------------------------------------------------ words for record fields

/** SPEC 4.3 item 3: the list a quote came from. */
export function fieldWords(field: string | null | undefined): string {
  switch (field) {
    case 'procedure': return 'from its list of procedures';
    case 'equipment': return 'from its equipment list';
    case 'capability': return 'from its list of services';
    case 'specialties': case 'specialty': return 'from its list of specialties';
    case 'description': return 'from its description';
    default: return 'from its record';
  }
}

export function typeWord(t: string | null | undefined): string {
  if (!t) return 'Facility';
  const s = t.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/_/g, ' ').toLowerCase();
  return capital(s === 'nursing home' ? 'nursing home' : s);
}

export function operatorWord(op: string | null | undefined): string {
  if (!op) return 'ownership not stated';
  const s = op.toLowerCase();
  if (s === 'public' || s === 'government') return 'public';
  if (s === 'private') return 'private';
  return s.replace(/_/g, ' ');
}

/** The requirement each line is built on (vf.recipes GRADE_RULES supported_names[0]). */
export const MAIN_KEY: Record<string, string> = {
  emoc: 'caesarean section', nicu: 'newborn intensive care unit', dialysis: 'dialysis', icu: 'intensive care unit',
  cardiac_surgery: 'heart surgery', cath_lab: 'cath lab, angiography or angioplasty', mri: 'MRI scan', ct: 'CT scan',
  trauma: 'trauma care', burns: 'burn care', neurosurgery: 'brain or spine surgery', oncology: 'chemotherapy or radiotherapy',
};

/** SPEC 4.3 requirementWord: a short noun for each requirement key. Longest matching prefix wins. */
const REQ_WORD: Record<string, string> = {
  'caesarean section': 'C-section',
  'blood bank or transfusion': 'a blood bank',
  'anaesthesia or operating theatre': 'an operating theatre',
  'operating theatre': 'an operating theatre',
  'newborn intensive care unit': 'a newborn ICU (NICU)',
  'NICU or neonatal intensive care named': 'a newborn ICU (NICU)',
  'NICU': 'a newborn ICU (NICU)',
  'newborn breathing support or ventilator': 'newborn breathing support',
  'dialysis': 'dialysis',
  'dialysis unit': 'a dialysis unit',
  'dialysis unit, machines or nephrologist': 'a dialysis unit',
  'intensive care unit': 'an ICU (intensive care)',
  'ventilator': 'a ventilator',
  'heart surgery': 'heart surgery',
  'intensive or cardiac care unit': 'an intensive or cardiac care unit',
  'cardiac or intensive care unit': 'a cardiac or intensive care unit',
  'cath lab, angiography or angioplasty': 'a cath lab',
  '24-hour or emergency service': 'round-the-clock service',
  '24-hour service': 'round-the-clock service',
  'emergency service': 'an emergency service',
  'MRI scan': 'an MRI scan',
  'CT scan': 'a CT scan',
  'trauma care': 'trauma care',
  'blood bank or intensive care unit': 'a blood bank or an ICU',
  'burn care': 'burn care',
  'brain or spine surgery': 'brain or spine surgery',
  'chemotherapy or radiotherapy': 'chemotherapy or radiotherapy',
  'chemotherapy': 'chemotherapy',
  'radiotherapy': 'radiotherapy',
};

export function requirementWord(key: string): string {
  if (REQ_WORD[key]) return REQ_WORD[key];
  let best = '';
  for (const k of Object.keys(REQ_WORD)) if (key.startsWith(k) && k.length > best.length) best = k;
  return best ? REQ_WORD[best] : key.replace(/\s*\(.*$/, '');
}

/** "caesarean section (WHO 2009 ...)" -> "caesarean section"; a joined "a; b" gives both. */
export function requirementKeys(req: string): string[] {
  return req.split(';').map((r) => r.replace(/\s*\(.*$/, '').trim()).filter(Boolean);
}

/** A phone question without the standard citation in brackets: "(WHO 2009 Table 4, signal function 9)". */
/** Questions whose recipe wording reads badly when asked on the phone: the plain question to ask. */
const QUESTION_PLAIN: [RegExp, string][] = [
  [/^Do you have newborn mechanical ventilation.*$/i, 'Can you put a newborn on a ventilator, not only CPAP?'],
  [/^Do you have dialysis unit, machines or haemodialysis named.*$/i, 'Do you have your own dialysis machines?'],
];

export function plainQuestion(q: string): string {
  const t = q.replace(/\s*\((WHO|PM-JAY|FBNC|FRU|ESC|PMNDP|NHM)[^)]*\)/g, '')
    .replace(/\bmentions blood bank or transfusion, not necessarily for obstetric patients\b/i, 'mentions a blood bank or transfusion. A C-section can need blood')
    .replace(/,\s*not necessarily for obstetric patients\b/gi, '');
  for (const [rx, to] of QUESTION_PLAIN) if (rx.test(t)) return to;
  // "Do you have blood bank or transfusion, not necessarily for obstetric patients?" names a
  // recipe requirement verbatim; ask it in the plain word the rest of the card uses.
  const m = /^Do you have (.+)\?$/.exec(t);
  if (m) {
    const k = requirementKeys(m[1])[0] ?? m[1];
    const w = requirementWord(k.replace(/,\s*not necessarily.*$/i, ''));
    if (w !== k) return `Do you have ${w}?`;
  }
  return plainServer(t);
}

/** "a caesarean section" style phrase for a missing requirement (fallback for missing_phrases). */
export function missingPhrase(req: string): string {
  const k = requirementKeys(req.replace(/^no fact in the record mentions /i, ''))[0] ?? req;
  const w = requirementWord(k);
  return w === k ? `a ${k}` : w;
}

// ------------------------------------------------------------------ the normalised card

export interface CardView {
  c: Card;
  nameShort: string;
  nameRest: string | null;
  /** short: what a card shows; point: "about 6 min" from the searched point (the middle of a loose place);
   *  range: the honest spread ("under 5 min to 45 min" for a loose place, the drive-time band otherwise) */
  time: { short: string; range: string; loose: boolean; point: string } | null;
  approx: boolean;
  distanceLabel: string;
  stateDisplay: string;
  crossesState: boolean;
  quotes: Quote[];
  missingPhrases: string[];
}

function fmtMinutes(m: number): string {
  // mirrors vf.travel.fmt_minutes, used only when the server has not sent card.time
  const r = Math.max(1, m < 10 ? Math.round(m) : 5 * Math.round(m / 5));
  if (r < 60) return `${r} min`;
  const h = Math.floor(r / 60), mm = r % 60;
  return mm === 0 ? `${h} h` : `${h} h ${mm} min`;
}

function normText(s: string): string {
  return s.toLowerCase().replace(/\s+/g, ' ').replace(/[ .]+$/, '').trim();
}

export function normCard(c: Card, a: Answer): CardView {
  const name = c.name ?? '';
  const comma = name.indexOf(',');
  const nameShort = fixName(c.name_short ?? (comma > 0 ? name.slice(0, comma).trim() : name));
  const nameRest0 = c.name_rest !== undefined ? c.name_rest : (comma > 0 ? name.slice(comma + 1).trim() || null : null);
  const nameRest = nameRest0 ? fixName(nameRest0) : nameRest0;
  const loose = !!(a.origin?.loose ?? a.window?.origin_loose);
  let time: CardView['time'] = null;
  if (c.minutes != null && Number.isFinite(c.minutes)) {
    const lo = c.minutes_low, hi = c.minutes_high;
    // "1 min" as the low end of a spread reads as a real figure: anything under 5 minutes is "under 5 min"
    // a low end under 5 minutes says nothing ("1 min" reads as a real figure): the range is "up to X"
    const range = lo != null && hi != null ? (hi < 5 ? 'under 5 min' : lo < 5 ? `up to ${fmtMinutes(hi)}` : `${fmtMinutes(lo)} to ${fmtMinutes(hi)}`)
      : (c.time?.range ?? '');
    const point = `about ${fmtMinutes(c.minutes)}`;
    const isLoose = loose && !!range;
    time = { short: isLoose ? point : (c.time?.short ?? point), range, loose: isLoose, point };
  } else if (c.time) {
    time = { ...c.time, point: c.time.short };
  }
  // A place too large to start a drive from (origin.too_coarse): a range like "1 min to 5 h" says
  // nothing, so cards fall back to the straight-line distance from the place's middle.
  if (a.origin?.too_coarse) time = null;
  const approx = !!c.position_approximate;
  const distanceLabel = approx ? 'position approximate' : (c.distance_label || kmShort(c));
  const originState = a.origin?.state_display ?? titleCase(a.location?.state ?? '');
  const stateDisplay = c.state_display ?? (c.state ? titleCase(c.state) : '');
  const crossesState = c.crosses_state ?? (!!stateDisplay && !!originState && stateDisplay.toLowerCase() !== originState.toLowerCase());
  let quotes = c.quotes;
  if (!quotes) {
    const seen = new Map<string, Quote>();
    quotes = [];
    for (const e of c.evidence ?? []) {
      const keys = (e.requirements?.length ? e.requirements : [e.requirement]).flatMap(requirementKeys);
      const k = normText(e.quote);
      const prev = seen.get(k);
      if (prev) { for (const x of keys) if (!prev.requirement_keys.includes(x)) prev.requirement_keys.push(x); continue; }
      const q: Quote = { text: e.quote, field: e.field, requirement_keys: keys, marks: [] };
      seen.set(k, q);
      quotes.push(q);
    }
  }
  const missingPhrases = c.missing_phrases ?? (c.missing ?? []).map(missingPhrase);
  return { c, nameShort, nameRest, time, approx, distanceLabel, stateDisplay, crossesState, quotes, missingPhrases };
}

/** "Pawar'S Clinic" -> "Pawar's Clinic": a capital after an apostrophe is a scraping artefact. */
export function fixName(s: string): string {
  return String(s ?? '').replace(/([A-Za-z])['\u2019]S\b/g, "$1's");
}

export function titleCase(s: string): string {
  return s.toLowerCase().replace(/\b([a-z])/g, (m) => m.toUpperCase());
}

/** A straight-line distance for cards without minutes (keyword search, sea origins). */
export function kmShort(c: Card): string {
  const d = c.distance_km;
  if (d == null || !Number.isFinite(d)) return 'distance unknown';
  const p = Math.max(1, c.location_precision_km ?? 1);
  if (d <= p) return `less than ${Math.ceil(p)} km`;
  return d < 10 ? `about ${d.toFixed(0)} km` : `about ${(5 * Math.round(d / 5)).toLocaleString('en-IN')} km`;
}

// ------------------------------------------------------------------ the note (SPEC 4.3)

const CLAIM_SENTENCE: Record<string, string> = {
  emoc: 'Its record says it performs C-sections',
  dialysis: 'Its record names a dialysis service',
  trauma: 'Its record names emergency trauma care',
  nicu: 'Its record names a newborn intensive care unit (NICU)',
};

export function claimSentence(a: Answer, v: CardView): string {
  if (a.method === 'keyword') {
    return v.c.match_negated
      ? `Its best-matching text names ${serviceName(a)} only to exclude it, so call before going`
      : `No rule checked that it offers ${serviceName(a)}: call first`;
  }
  if (!v.quotes.length) return `Its record lists ${serviceName(a)}`;
  if (CLAIM_SENTENCE[a.service]) return CLAIM_SENTENCE[a.service];
  const k = leadQuote(a, v)?.requirement_keys[0];
  return `Its record names ${k ? requirementWord(k) : serviceName(a)}`;
}

export function headline(a: Answer, v: CardView): string {
  const claim = claimSentence(a, v);
  // keyword search is a word match, not a recipe check: never "Go to"
  if (a.method === 'keyword') {
    const coarse = (a.origin?.precision_km ?? 0) > (a.radius_km || 100) / 2;
    if (coarse) return `Closest word match: ${v.nameShort}, ${kmShort(v.c)} in a straight line from the middle of ${shortPlace(a)}, which is itself known only to about ${count(a.origin!.precision_km)} km. ${claim}.`;
    return `Closest word match: ${v.nameShort}, ${kmShort(v.c)} away in a straight line. ${claim}.`;
  }
  if (v.time && v.time.loose) return `Go to ${v.nameShort}, ${v.time.point} from the middle of ${shortPlace(a)}. ${claim}.`;
  if (v.time) return `Go to ${v.nameShort}, ${v.time.short} away. ${claim}.`;
  if (v.approx) return `Go to ${v.nameShort}. ${claim}.`;
  if (a.origin?.too_coarse) {
    const from = a.origin.short_name === 'the point you gave'
      ? `from the point you gave (read to about ${count(a.origin.precision_km)} km)` : `from the middle of ${a.origin.short_name}`;
    return `Go to ${v.nameShort}, ${kmShort(v.c)} in a straight line ${from}. ${claim}.`;
  }
  return `Go to ${v.nameShort}, ${kmShort(v.c)} away in a straight line. ${claim}.`;
}

/** SPEC 4.3 item 2, under the headline. The headline already gives the time, so this line does not
 * repeat it: it gives the spread, the straight-line distance and what the place is. */
export function distanceLine(v: CardView, a?: Answer): { lead: string; rest: string } {
  const c = v.c;
  const where = [typeWord(c.type), operatorWord(c.operator), c.city].filter(Boolean).join(', ');
  const cross = v.crossesState && v.stateDisplay
    ? (a && isIsland(a) ? ` In ${v.stateDisplay}: across the sea; sea or air travel is not modelled.` : ` In ${v.stateDisplay}: across the state border.`) : '';
  if (!v.time) {
    const d = v.approx ? 'position approximate' : `${kmShort(c)} in a straight line`;
    return { lead: '', rest: `${capital(d)}. ${where}.${cross}` };
  }
  if (v.time.loose) {
    const place = a ? shortPlace(a) : 'the place';
    const km = v.approx ? 'Its position is approximate.' : `${capital(kmShort(c))} in a straight line from the middle of ${place}.`;
    return { lead: '', rest: `Estimated drive ${v.time.range}, depending on where in ${place} the patient is. ${km} ${where}.${cross}` };
  }
  // "range 1 min to 1 min" says nothing: show a range only when its two ends differ.
  const ends = v.time.range ? v.time.range.split(' to ') : [];
  const range = v.time.range && !(ends.length === 2 && ends[0] === ends[1]) && v.time.range !== v.time.point ? `; it could be ${v.time.range}` : '';
  const point = ` ${v.time.point}`;
  // a PIN or post office area known only to several km: the time is from its middle (the circle on the map)
  const prec = a?.origin?.precision_km ?? 0;
  const fromMid = a && prec >= 5 ? ` Times are from the middle of ${shortPlace(a)}, which is known to about ${count(prec)} km, so the patient's own trip may differ.` : '';
  if (v.approx) return { lead: '', rest: `Estimated drive${point}${range}. Its map position is approximate, so the time is rough.${fromMid} ${where}.${cross}` };
  return { lead: '', rest: `Estimated drive${point}${range}. ${capital(v.distanceLabel)}.${fromMid} ${where}.${cross}` };
}

export function mainKey(a: Answer): string | null {
  return MAIN_KEY[a.service] ?? null;
}

export function leadQuote(a: Answer, v: CardView): Quote | null {
  if (!v.quotes.length) return null;
  const k = mainKey(a);
  // The strongest fact naming the service leads: one naming the service itself over a staff mention
  // ("Head of Trauma Unit identified among listed doctors"), then one that also names more of what the care needs.
  const names = (q: Quote) => !!k && q.requirement_keys.some((x) => x === k || x.startsWith(k));
  const staff = (q: Quote) => /\b(head of|doctors?|dr\.?|consultants?|specialists?|surgeons? (?:is|are)|identified among)\b/i.test(q.text);
  const score = (q: Quote) => (names(q) ? 10 : 0) - (staff(q) ? 4 : 0) + Math.min(3, q.requirement_keys.length)
    + (q.field === 'procedure' || q.field === 'equipment' ? 1 : 0);
  let best = v.quotes[0], bs = -Infinity;
  for (const q of v.quotes) { const sc = score(q); if (sc > bs) { bs = sc; best = q; } }
  return best;
}

export function otherQuotes(a: Answer, v: CardView, max = 2): Quote[] {
  const lead = leadQuote(a, v);
  return v.quotes.filter((q) => q !== lead).slice(0, max);
}

/** The quote as HTML, marks wrapped, trimmed to about 60 characters around the first mark when long. */
export function quoteHtml(q: Quote, maxLen = 140): string {
  let text = q.text;
  let marks = (q.marks ?? []).filter(([s, e]) => s >= 0 && e > s && e <= text.length).sort((x, y) => x[0] - y[0]);
  let pre = '', post = '';
  if (text.length > maxLen) {
    const m0 = marks[0]?.[0] ?? 0;
    let s = Math.max(0, m0 - 60), e = Math.min(text.length, (marks[0]?.[1] ?? m0) + 60);
    if (s > 0) { const sp = text.indexOf(' ', s); if (sp > 0 && sp < m0) s = sp + 1; pre = '...'; }
    if (e < text.length) { const sp = text.lastIndexOf(' ', e); if (sp > (marks[0]?.[1] ?? 0)) e = sp; post = '...'; }
    text = text.slice(s, e);
    marks = marks.map(([a, b]) => [a - s, b - s] as [number, number]).filter(([a, b]) => a >= 0 && b <= text.length);
  }
  let out = '', i = 0;
  for (const [s, e] of marks) {
    if (s < i) continue;
    out += esc(text.slice(i, s)) + `<mark class="ev">${esc(text.slice(s, e))}</mark>`;
    i = e;
  }
  out += esc(text.slice(i));
  return `${pre}${out}${post}`;
}

// ------------------------------------------------------------------ how sure (SPEC 4.3 sureLine)

/** v1-final sends no level (D048 (6): confidence is null). The level then mirrors the same record
 * facts the old rule used (vf.rank._confidence_v0): thin support, a position known only to within
 * 15 km, the level of the record (names everything the care needs, or the service only) and minor
 * warnings. These are facts about the record's text, not a forecast. */
export function recordLevel(c: Card): 'high' | 'medium' | 'low' {
  if (c.confidence === 'high' || c.confidence === 'medium' || c.confidence === 'low') return c.confidence;
  const minor = (c.flags ?? []).filter((f) => f.severity !== 'major').length;
  const major = (c.flags ?? []).some((f) => f.severity === 'major');
  if (c.evidence_strength === 'thin' || major) return 'low';
  if (c.location_precision_km != null && c.location_precision_km > 15) return 'low';
  const g = c.grade ?? 0;
  if (g >= 3 && minor === 0) return 'high';
  if ((g >= 3 && minor === 1) || (g === 2 && minor === 0)) return 'medium';
  return 'low';
}

export function sureLevel(c: Card): { word: string; bars: number; cls: string } {
  switch (recordLevel(c)) {
    case 'high': return { word: 'Strong record evidence.', bars: 3, cls: 'c-high' };
    case 'medium': return { word: 'Some record evidence.', bars: 2, cls: 'c-med' };
    default: return { word: 'Limited record evidence.', bars: 1, cls: 'c-low' };
  }
}

function firstSentence(s: string): string {
  const t = String(s ?? '').trim();
  const m = /^(.+?[.!?])(\s|$)/.exec(t);
  return (m ? m[1] : t).trim();
}

/** Runs of 8 or more digits become "[phone number]" (vf.questions.mask_numbers). */
const DATE_RX = /^\d{1,2}[-./]\d{1,2}[-./]\d{2,4}$|^(19|20)\d\d ?- ?(19|20)\d\d$|^(19|20)\d\d[-./](0?[1-9]|1[0-2])[-./](0?[1-9]|[12]\d|3[01])$/;
export function maskNumbers(s: string): string {
  return s.replace(/\+?\d[\d \-().]{6,}\d/g, (m) => (m.replace(/\D/g, '').length >= 8 && !DATE_RX.test(m.trim()) ? '[phone number]' : m));
}

/** What the card's quotes name, in plain words, the service first: ["dialysis", "a dialysis unit"]. */
export function namedWords(a: Answer, v: CardView): string[] {
  const k = mainKey(a);
  const lead = k ? requirementWord(k) : serviceName(a);
  const words: string[] = [];
  for (const q of v.quotes) for (const key of q.requirement_keys) {
    const w = requirementWord(key);
    if (!words.includes(w)) words.push(w);
  }
  if (k && !words.includes(lead)) words.unshift(lead);
  return words.length ? words : [lead];
}

export function sureLine(a: Answer, v: CardView): string {
  const c = v.c;
  if (a.method === 'keyword') return 'The quoted text matches your words. No rule checked that the service is offered.';
  const k = mainKey(a);
  const lead = k ? requirementWord(k) : serviceName(a);
  const flags = c.flags ?? [];
  const major = flags.find((f) => f.severity === 'major');
  const merge = flags.find((f) => /place|merge/.test(f.flag));
  const prec = c.location_precision_km ?? 0;
  if (v.approx && prec >= 10) return `Its record names ${lead}, but its position is known only to within about ${Math.round(prec)} km.`;
  if (v.approx) return `Its record names ${lead}, but its map position is approximate.`;
  if (major) return `Its record names ${lead}, but ${lowerFirst(plainServer(maskNumbers(firstSentence(major.reason)))).replace(/\.$/, '')}.`;
  if (merge) return `Its record names ${lead}, but its own text also names another place, so it may mix two facilities.`;
  if (c.evidence_strength === 'thin') {
    const n = c.sources?.n_domains ?? 0;
    if (n <= 1 && !c.sources?.official_website) return `Its record names ${lead}, but it comes from one website and no official site.`;
    // one fact may name several things (a dialysis unit and dialysis): say what it names, so this line
    // never contradicts the order line ("its record names dialysis and a dialysis unit")
    const w = namedWords(a, v);
    return v.quotes.length <= 1 ? `Only one fact in its record names ${joinAnd(w)}.` : `Only a few facts in its record name ${joinAnd(w)}.`;
  }
  if (v.quotes.length === 1 && v.quotes[0].requirement_keys.length <= 1) return `Only one sentence in its record names ${lead}.`;
  if (v.missingPhrases.length) return `Its record names ${lead}, but not ${joinAnd(v.missingPhrases, 'or')}.`;
  const words: string[] = [];
  for (const q of v.quotes) for (const key of q.requirement_keys) {
    const w = requirementWord(key);
    if (!words.includes(w)) words.push(w);
  }
  if (k && !words.includes(lead)) words.unshift(lead);
  const pin = /consistent/i.test(c.location_note ?? '') ? ', and its map position matches its PIN' : '';
  return `Its record names ${joinAnd(words)}${pin}.`;
}

// ------------------------------------------------------------------ call, sources, cautions

export function callCaption(a: Answer): string {
  return a.demo_masked ? 'Number from its record. Digits hidden for the demo.' : 'Number from its record. It may be out of date.';
}

export function sourcesLine(c: Card): string {
  const n = c.sources?.n_domains ?? c.sources?.domains?.length ?? 0;
  if (!n) return '';
  const sites = n === 1 ? '1 website' : `${count(n)} websites`;
  const dom = c.official_domain !== undefined ? c.official_domain : officialDomain(c.sources?.official_website);
  const caveat = n > 1 ? ' Directories and listing sites count, so this is not independent confirmation.' : '';
  return dom ? `Found on ${sites}, including its own (${dom}).${caveat}` : `Found on ${sites}.${caveat}`;
}

/** Fallback for official_domain: the host without www., null for link shorteners and maps. */
export function officialDomain(url: string | null | undefined): string | null {
  if (!url) return null;
  let host = url.replace(/^[a-z]+:\/\//i, '').split('/')[0].toLowerCase();
  const path = url.replace(/^[a-z]+:\/\//i, '').toLowerCase();
  if (host.startsWith('www.')) host = host.slice(4);
  if (/(^|\.)(goo\.gl|bit\.ly|tinyurl\.com)$/.test(host) || host.startsWith('maps.google') || path.startsWith('google.com/maps')
    || path.startsWith('www.google.com/maps')) return null;
  return host || null;
}

export function cautions(v: CardView): string[] {
  const c = v.c;
  const out: string[] = [];
  if (v.approx) {
    out.push(/centre of its PIN/i.test(c.location_note ?? '')
      ? 'Its map position is approximate: we placed it at the centre of its PIN area.'
      : 'Its map position is approximate: other records share its map point, or it sits at the centre of its PIN area.');
  }
  for (const f of c.flags ?? []) {
    if (v.approx && f.flag === 'location_conflict') continue;
    const s = plainServer(maskNumbers(firstSentence(f.reason)));
    if (s && !out.includes(s)) out.push(s);
  }
  if (v.missingPhrases.length) out.push(`Not found in its record: ${joinAnd(v.missingPhrases, 'or')}.`);
  return out;
}

// ------------------------------------------------------------------ origin line (SPEC 4.1)

export function originText(a: Answer): { text: string; tint: boolean } {
  const o: Origin | undefined = a.origin;
  const loc = a.location;
  if (loc?.kind === 'coords' || o?.short_name === 'the point you gave') return { text: 'From the point you gave.', tint: false };
  const label = o?.label ?? loc?.label ?? '';
  const short = o?.short_name ?? label.split(',')[0];
  const prec = o?.precision_km ?? loc?.precision_km ?? null;
  if (o?.too_coarse) return { text: `${short} covers a large area, so we show straight-line distance from its middle, not drive times. If you know the patient's PIN, type it for a drive time.`, tint: true };
  if (o?.loose) return {
    text: `${short} is large: the patient could be anywhere within about ${count(prec)} km of its middle, so times are from the middle. If you know the patient's PIN, type it for one drive time.`,
    tint: true,
  };
  const good = prec != null && Number.isFinite(prec) ? ` (location accurate to about ${count(Math.max(1, prec))} km)` : '';
  const m = /^Centre of (.+?), about (\d[\d,]*) km across$/.exec(label);
  if (m) return { text: `From the centre of ${m[1]} (about ${m[2]} km across; location accurate to about ${count(Math.max(1, prec ?? 0))} km).`, tint: false };
  return { text: `From the middle of ${label}${good}.`, tint: false };
}

export function shortPlace(a: Answer): string {
  return a.origin?.short_name ?? (a.location?.label ?? '').split(',')[0].replace(/\s*\(.*$/, '');
}

/** "PIN 226001, Lucknow": the chip for the place. */
export function placeChip(a: Answer): string {
  const label = (a.location?.label ?? a.origin?.label ?? '').replace(/\s*\([^)]*\)/g, '');
  const parts = label.split(',').map((x) => x.replace(/\s+(district|city)$/i, '').trim())
    .filter((x) => x && !/^about \d/.test(x));
  const s = shortPlace(a);
  if (parts.length < 2) return s;
  const pick = /^PIN \d/.test(s) ? parts[1] : parts[parts.length - 1];
  return pick.toLowerCase() === s.toLowerCase() ? s : `${s}, ${pick}`;
}

// ------------------------------------------------------------------ backups (SPEC 4.4)

/** Names of nearer options this card is listed before (later cards, and why-not records the order put lower). */
function nearerAfter(a: Answer, c: Card): string[] {
  const i = a.results.indexOf(c);
  if (c.minutes == null) return [];
  const later = a.results.slice(i + 1).filter((x) => x.minutes != null && x.minutes < c.minutes!).map((x) => normCard(x, a).nameShort);
  const wn = (a.why_not?.items ?? []).filter((w) => w.reason_code === 'ranked_lower' && w.distance_km != null && c.distance_km != null
    && w.distance_km < c.distance_km).map((w) => fixName(w.name.split(',')[0]));
  return [...later, ...wn];
}

const allows = (c: Card) => !!((c.parts ?? {}) as { allowance_applies?: boolean }).allowance_applies;

/** The first card's order line, when a nearer option is listed after it (SPEC 4.4 for card 1). */
export function topOrderLine(a: Answer, v: CardView): string {
  const allow = a.window?.allowance;
  const nearer = nearerAfter(a, v.c);
  if (!nearer.length || !allows(v.c) || allow == null) return '';
  return `Listed first, though ${joinAnd(nearer)} ${nearer.length === 1 ? 'is' : 'are'} nearer: ${possessive(v.nameShort)} record names ${joinAnd(namedWords(a, v))}, and it is at most ${count(allow)} minutes further.`;
}

export function orderSentence(a: Answer, prev: CardView, v: CardView): string {
  const c = v.c, p = prev.c;
  const allow = a.window?.allowance;
  const nearer = nearerAfter(a, c);
  if (allows(c) && nearer.length && allow != null) {
    return `Listed before ${joinAnd(nearer)}, which ${nearer.length === 1 ? 'is' : 'are'} nearer: ${possessive(v.nameShort)} record names ${joinAnd(namedWords(a, v))}, and it is at most ${count(allow)} minutes further.`;
  }
  // every earlier card that is further than this one and came first by the allowance
  const i = a.results.indexOf(c);
  const before = (i > 0 ? a.results.slice(0, i) : [p]).filter((x) => allows(x) && c.minutes != null && x.minutes != null
    && Math.round(c.minutes) < Math.round(x.minutes));
  if (before.length && allow != null) {
    const names = before.map((x) => normCard(x, a).nameShort);
    const one = names.length === 1;
    // a record whose position is approximate or disputed is only maybe nearer
    const unsure = v.approx || (c.flags ?? []).some((f) => /location|merge|place/.test(f.flag));
    const lead = unsure ? `Listed after ${joinAnd(names)}, and may be nearer (its position or record is uncertain)`
      : `Listed after ${joinAnd(names)}, though nearer`;
    const such = joinAnd(namedWords(a, normCard(before[0], a)).slice(1, 3)) || serviceName(a);
    return one
      ? `${lead}: ${possessive(names[0])} record names more of what the care needs (such as ${such}), and ${names[0]} is at most ${count(allow)} minutes further.`
      : `${lead}: their records name more of what the care needs (such as ${such}), and they are at most ${count(allow)} minutes further.`;
  }
  const same = (c.minutes != null && p.minutes != null && Math.round(c.minutes) === Math.round(p.minutes))
    || (c.minutes == null && c.distance_km != null && p.distance_km != null && Math.round(c.distance_km) === Math.round(p.distance_km));
  if (same && prev.nameShort !== v.nameShort) {
    return (c.grade ?? 0) < (p.grade ?? 0)
      ? `About the same distance as ${prev.nameShort}; listed after it because its record shows less.`
      : `About the same distance as ${prev.nameShort}.`;
  }
  if (same) return 'About the same distance as the one above.';
  return 'Next nearest with evidence in its record.';
}

/** "BNK Hospital's", "Pawar's Clinic's". */
export function possessive(name: string): string {
  return /s$/i.test(name) ? `${name}'` : `${name}'s`;
}

/** Abstention: when a card a little further has stronger record evidence than the nearest one, say so. */
export function strongerNearby(a: Answer, near: CardView, others: Card[]): string {
  const rank = (c: Card) => ({ high: 3, medium: 2, low: 1 }[recordLevel(c)]);
  const allow = a.window?.allowance ?? 15;
  const better = others.find((c) => rank(c) > rank(near.c) && c.minutes != null && near.c.minutes != null
    && c.minutes - near.c.minutes <= allow);
  if (!better) return '';
  const b = normCard(better, a);
  // from the unrounded minutes (parts.minutes), not the rounded card values
  const um = (c: Card) => Number(((c.parts ?? {}) as { minutes?: number }).minutes ?? c.minutes);
  const dt = Math.round(um(better) - um(near.c));
  const gapTxt = dt <= 1 ? 'about the same drive' : `about ${dt} min further`;
  return `${b.nameShort} is about as far (${gapTxt}) and has stronger record evidence. If you can, call both and send the patient to the one that confirms it can help.`;
}

// ------------------------------------------------------------------ further away (SPEC 4.5)

export function hoursText(a: Answer): string {
  const w: Window | undefined = a.window;
  if (w?.hours_text) return w.hours_text;
  if (w?.minutes) {
    const h = w.minutes / 60;
    return h === 1 ? '1 hour' : Number.isInteger(h) ? `${h} hours` : `${h.toFixed(1)} hours`;
  }
  return `${count(a.radius_km)} km`;
}

export function reachText(a: Answer): string {
  const w = a.window;
  if (w?.reach_text) return w.reach_text;
  if (w?.reach_minutes) return fmtMinutes(w.reach_minutes);
  return `${count(a.how_we_decided?.reach_km ?? a.radius_km * 3)} km`;
}

// ------------------------------------------------------------------ how we decided (SPEC 4.7)

export function ruleParagraph(a: Answer): string {
  if (a.method === 'keyword') {
    return 'Word match only: we look for facts and listed specialties whose words match yours (a text similarity of at least 0.25; a listed specialty counts half as much as a fact), then weigh the match by straight-line distance, so a closer place with a slightly weaker match can come first. No rule checked that the service is offered.';
  }
  const allow = a.window?.allowance;
  const one = allow != null
    ? ` One exception: a record that names both the service and what it needs (for example the equipment or the unit) can go first if it is at most ${count(allow)} minutes further.`
    : '';
  return `We keep a facility only if a sentence in its own record names the service and nothing serious is wrong with the record. We list them nearest first by estimated drive.${one}`;
}

export function windowLine(a: Answer): string {
  if (a.method === 'keyword') return `Search radius: ${count(a.radius_km || 100)} km in a straight line (a design choice). Word search gives no drive time.`;
  const w = a.window;
  if (!w) return '';
  const basis = w.basis ? ` ${firstSentenceKeepCite(w.basis)}` : '';
  return `Window: ${hoursText(a)} estimated drive.${basis}`;
}

/** The first sentence of the window basis, not cut inside a parenthesis or a citation. */
function firstSentenceKeepCite(s: string): string {
  let depth = 0;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (ch === '(') depth++;
    else if (ch === ')') depth = Math.max(0, depth - 1);
    else if (ch === '.' && depth === 0 && (i + 1 === s.length || s[i + 1] === ' ') && !/\b(al|et|e\.g|i\.e|Dr|vs)$/.test(s.slice(0, i))) {
      return s.slice(0, i + 1);
    }
  }
  return s;
}

export const CITATION_LINE = 'Every quote is exact text from a fact in the facility\'s record, with the list it came from; long facts are trimmed where you see "...". The data does not say which web page each fact came from.';
export const FOOTER_LINE = 'Times are estimated drive times from straight-line distance, with no traffic: real trips take longer. Records come from web pages, which can be out of date. Most records here are private facilities; public hospitals are often thinly described online.';
export const WHYNOT_CLOSING = 'Grey means only that our rule did not pick it from its record. It does not mean the place is unable to help.';
export const INTRO_LINE = 'Pick the care and the place. We show up to 3 facilities, quote their own records, and say when nothing reliable is near.';
export const HONESTY_LINE = 'This is not proof that no care exists nearby. Most records here are private facilities, and public hospitals are often thinly described online.';

/** Plain labels for the funnel steps (the server labels use words SPEC 1 bans). Numbers are the server's. */
export function funnelLabel(step: FunnelStep, a: Answer, last: boolean): string {
  void last;
  const phrase = serviceName(a);
  switch (step.step) {
    case 'all_records': return 'records in this dataset';
    case 'usable_location': return 'have a map position we can use';
    case 'claim_within_reach': return `list or claim ${phrase} in a wider search, up to about ${reachText(a)} away`;
    case 'recommendable': return 'have a sentence naming it, and nothing serious wrong';
    case 'within_radius': return `within ${hoursText(a)} estimated drive`;
    case 'shown': return 'shown here';
    default: return plainServer(step.label);
  }
}

export function funnelN(a: Answer, step: string): number | null {
  const s = a.funnel?.steps.find((x) => x.step === step);
  return s ? s.n : null;
}

// ------------------------------------------------------------------ abstention (SPEC 4.9)

export function abstainHeadline(a: Answer): string {
  const prec = a.origin?.precision_km ?? 0;
  if (a.method === 'keyword') {
    const r = a.radius_km || 100;
    // D058 (3) for word search: a place wider than the search radius makes the negative uncertain
    if (prec > r / 2) return `Uncertain: ${shortPlace(a)} spans about ${count(2 * prec)} km, and no record with a word match for ${serviceName(a)} is within ${count(r)} km of its middle in this dataset.`;
    return `No record with a word match for ${serviceName(a)} within ${count(r)} km of ${shortPlace(a)} in this dataset.`;
  }
  return `No clear evidence of ${serviceName(a)} within ${hoursText(a)} of ${shortPlace(a)} in this dataset.`;
}

export function isIsland(a: Answer): boolean {
  return !!a.window && (a.window as { sea_travel_not_modelled?: boolean }).sea_travel_not_modelled === true;
}

export function abstainNextStep(a: Answer): string {
  if (isIsland(a)) return `On the islands, ask the nearest public hospital or the district health office how patients needing ${serviceName(a)} are referred or moved to the mainland.`;
  return `Call the district hospital, or your ANM or block health officer, and ask where ${serviceName(a)} is available nearer to ${shortPlace(a)}.`;
}

/** The free emergency transport numbers, for the emergency services. */
export function emergencyLine(a: Answer): string {
  if (a.method === 'keyword') return '';
  if (a.service === 'emoc' || a.service === 'nicu') return 'In an emergency, call 108 for an ambulance. For a pregnant woman or a sick newborn, 102 is free transport in most states.';
  if (['trauma', 'burns', 'cath_lab', 'neurosurgery', 'icu'].includes(a.service)) return 'In an emergency, call 108 for an ambulance (free in most states).';
  return '';
}

export function gapLine(a: Answer): string {
  const nReach = funnelN(a, 'claim_within_reach'), nRec = funnelN(a, 'recommendable'), nIn = funnelN(a, 'within_radius');
  if (nReach == null || nRec == null || nIn == null) return '';
  const b = a.beyond_radius;
  if (nReach === 0 && b) {
    const v = normCard(b, a);
    const where = isIsland(a) && v.crossesState ? `, across the sea in ${v.stateDisplay}` : v.crossesState && v.stateDisplay ? `, in ${v.stateDisplay}` : '';
    return `No record within about ${reachText(a)} lists or claims ${serviceName(a)}. The nearest record that names it is ${kmShort(b)} away in a straight line${where}.`;
  }
  return `In a wider search, up to about ${reachText(a)} away, ${count(nReach)} ${nReach === 1 ? 'record lists or claims' : 'records list or claim'} ${serviceName(a)}; ${count(nRec)} ${nRec === 1 ? 'has' : 'have'} a sentence naming it; ${nIn === 0 ? 'none' : count(nIn)} ${nIn === 1 ? 'is' : 'are'} within ${hoursText(a)}.`;
}

// ------------------------------------------------------------------ the trip teaser (SPEC 4.8, 4.9)

export function teaserSentence(s: SimPayload): string {
  const m = s.summary;
  if (m.first_no_evidence > 0 && m.first_held_back > 0) {
    return `${count(m.within)} of ${count(m.n_villages)} nearby villages are within ${s.window.hours_text} of a place whose record shows ${s.service_phrase}. But if people went by a hospital's listed specialties alone, ${count(m.first_no_evidence)} villages would go first to a place with no usable evidence of it (no evidence in its record, or evidence held back by a warning).`;
  }
  if (m.first_no_evidence === 0) {
    return `${count(m.within)} of ${count(m.n_villages)} nearby villages are within ${s.window.hours_text} of a place whose record shows ${s.service_phrase}.`;
  }
  return `${count(m.within)} of ${count(m.n_villages)} nearby villages are within ${s.window.hours_text} of a place whose record shows ${s.service_phrase}. But if people went by a hospital's listed specialties alone, ${count(m.first_no_evidence)} villages would go first to a place whose record shows no evidence of it.`;
}

export function previewAddSentence(s: SimPayload, short: string, before: SimCounters, after: SimCounters): string {
  return `Imagine ${/^[aeiou]/i.test(s.service_phrase) ? 'an' : 'a'} ${s.service_phrase} unit in ${short} (not real: a planning what-if). ${count(after.within)} of ${count(s.summary.n_villages)} nearby villages would be within ${s.window.hours_text} of one, up from ${count(before.within)}.`;
}

// ------------------------------------------------------------------ small helpers

export function looksLikeCoords(typed: string): boolean {
  return /^\s*\(?\s*[-+]?\d{1,3}(\.\d+)?\s*[, ]\s*[-+]?\d{1,3}(\.\d+)?\s*\)?\s*$/.test(typed);
}

export function flagWords(f: Flag): string {
  return plainServer(maskNumbers(firstSentence(f.reason)));
}
