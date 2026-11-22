"""Ranking engine (WP5): service + resolved location -> top 3 cited cards, or an
honest abstention.

Pure Python at query time: ``load_index`` reads everything once, then
``rank``, ``search`` and ``simulate`` do no I/O. The same code runs in tests,
scripts and the app.

Score spec v1-final (D048), the shipped ranker (``rank_version = "v1"``):

* Gates. G1 a usable location; G2 grade 2+ (recipe v0.3) with no major WP11
  flag for the line; G3 estimated drive time (``vf.travel``: state factor x
  2.30 x km^0.842, free-flow) within the line's window (``vf.services.WINDOWS``).
* Order. Nearest first by estimated minutes, with one declared evidence
  allowance: a corroborated record may come first if it is at most the
  line's allowance (15 minutes, 30 for planned care) further. No score, no
  weights, nothing is a probability.
* Recommendable facilities beyond the window go on a separate ``further_away``
  line, never into ``results``. Islands (no terrain factor: sea travel is not
  modelled) fall back to the old km radius.

The legacy rankers stay behind a flag for the v0 against v1 comparison
(``rank(..., version="v0")``, ``VF_RANK_VERSION=v0``, or
``build_index(..., rank_version="v0")``):

* v0 (D027, superseded): gates, then
  ``score = 0.5 x evidence + 0.3 x distance + 0.2 x trust - penalties``.
* probability (D039, superseded, not shipped): when a ``service_probability``
  table (WP13) is loaded, the index defaults to the legacy path and lines it
  covers use ``score = p_offers x exp(-km / radius)``.

Inputs (no stubs, D041 WP5B):

* Grades: ``gold/capability_grades_v03.parquet`` (recipe v0.3, D048), else
  ``gold/capability_grades.parquet`` (v0.2) with a status saying so. Contract
  v2 (12 lines, ``evidence_items``, ``missing_for_next``,
  ``evidence_strength``). Older grades without ``evidence_items`` are refused:
  there would be no fact ids to cite.
* Trust checks: ``gold/facility_flags.parquet`` and
  ``gold/facility_duplicates.parquet`` (WP11). ``load_index`` refuses to run
  without them. ``build_index`` with ``flags=None`` means "no record is
  flagged" and says so in the panel (for tests and what-if runs only).
* Location: a ``vf.locate.Resolution`` (WP2 B), or a dict with the same fields.

Typical use::

    from vf import rank
    idx = rank.load_index()                  # data_dir() silver + gold
    from vf import locate
    ans = rank.rank(idx, "dialysis", locate.resolve("Jaipur"))
    ans.to_dict()
"""
from __future__ import annotations

import json
import math
import os
import pickle
import re
import tempfile
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from . import services as S
from . import travel as T
from .geo import haversine_km

KEYWORD_INDEX_VERSION = "kw_v1"
#: "v1" is score spec v1-final (D048), shipped; "v0" is the legacy ranker
#: (the D027 additive score, or D039 probability mode when a probability table
#: is loaded), kept for the v0 against v1 comparison.
RANK_VERSIONS = ("v1", "v0")
_ENV_VERSION = os.environ.get("VF_RANK_VERSION", "v1").strip().lower()
DEFAULT_RANK_VERSION = _ENV_VERSION if _ENV_VERSION in RANK_VERSIONS else "v1"
RANK_V1_VERSION = "rank_v1 (score spec v1-final, D048)"
#: Distance label for a record placed on a shared or PIN-centre point (D046).
APPROX_LABEL = "position approximate"
FACT_FIELDS = ("capability", "procedure", "equipment", "description")
FLAG_COLUMNS = ("facility_id", "flag", "service_line", "severity", "signal", "reason", "quote", "check_version")

POLICIES = ("nearest_claim", "nearest_evidence", "ranked")


# ---------------------------------------------------------------------------
# Output shapes
# ---------------------------------------------------------------------------

def _jsonable(v):
    if is_dataclass(v) and not isinstance(v, type):
        return {f.name: _jsonable(getattr(v, f.name)) for f in fields(v)}
    if isinstance(v, Mapping):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, np.ndarray)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    if v is pd.NA or v is pd.NaT:
        return None
    return v


@dataclass
class Card:
    facility_id: str
    name: str
    type: Optional[str]
    operator: Optional[str]
    city: Optional[str]
    state: Optional[str]
    lat: Optional[float]
    lon: Optional[float]
    distance_km: Optional[float]
    location_precision_km: Optional[float]
    location_note: str
    grade: Optional[int]
    grade_label: str
    #: "high" | "medium" | "low" under the legacy v0 ranker; None under v1, which
    #: never shows a confidence level (D048 (6)). The field is kept for compatibility.
    confidence: Optional[str]
    #: under v1, a neutral evidence note (no level): what the support rests on
    confidence_reason: str
    score: Optional[float]
    #: {evidence, distance, trust, penalties: [{name, value, reason, quote}], combine, ...}
    parts: dict
    #: [{requirement, quote, field, fact_id}], one row per satisfied recipe group
    evidence: list
    missing: list
    #: {n_domains, domains, official_website, also_listed_as}
    sources: dict
    why_here: str
    could_be_wrong: str
    #: ``strong`` | ``moderate`` | ``thin`` | ``none`` (grades v2), ``keyword`` for search cards
    evidence_strength: str = "none"
    #: every WP11 flag on the record that applies here: [{flag, severity, signal, reason, quote}]
    flags: list = field(default_factory=list)
    # --- v1-final (D048); defaults keep older callers working ---------------
    #: estimated free-flow drive minutes (point estimate) and its range; None where
    #: sea travel is not modelled. The range is the 95% band, widened by the
    #: origin's precision when the searched place is loose (``vf.travel``).
    minutes: Optional[float] = None
    minutes_low: Optional[float] = None
    minutes_high: Optional[float] = None
    #: "about 25 min estimated drive, free-flow; real trips take longer"
    minutes_label: str = ""
    #: "3.4 km straight-line", "about 12 km straight-line", or "position approximate"
    distance_label: str = ""
    #: the record sits on a point shared with other records, or at its PIN's centre
    position_approximate: bool = False
    #: the line's window (minutes) and whether this card is inside it
    window_min: Optional[float] = None
    inside_window: Optional[bool] = None
    #: v0.3 emoc FRU-level note (never changes the grade)
    level_note: Optional[str] = None
    #: v1: this card is the nearest recommendable facility inside the window (always shown)
    nearest_in_window: Optional[bool] = None
    #: v1: the searched place is loose and this card's drive-time range straddles the
    #: window, so whether it is inside depends on where the trip starts
    window_uncertain: Optional[bool] = None

    def recompute_score(self) -> Optional[float]:
        """The score rebuilt from ``parts``; equals ``score`` by construction."""
        p = self.parts or {}
        how = p.get("combine")
        pen = sum(x["value"] for x in p.get("penalties", []))
        if how == "sum":
            return p["evidence"] + p["distance"] + p["trust"] - pen
        if how == "product":
            return p["evidence"] * p["distance"] - pen
        if how == "match":
            return p["evidence"] - pen
        return None

    def to_dict(self) -> dict:
        return _jsonable(self)


@dataclass
class Answer:
    status: str  # "ok" | "no_reliable_option" | "needs_location_choice" | "location_not_found"
    service: str
    service_label: str
    radius_km: float
    method: str  # "recipe" | "keyword"
    location: dict
    results: list = field(default_factory=list)
    beyond_radius: Optional[Card] = None
    unverified_nearby: list = field(default_factory=list)
    how_we_decided: dict = field(default_factory=dict)
    # --- v1-final (D048) ------------------------------------------------------
    #: recommendable facilities beyond the window, nearest first; never top cards
    further_away: list = field(default_factory=list)
    #: this query's window: {minutes, allowance, kind, basis, km_equivalent, reach_minutes, factor, ...}
    window: dict = field(default_factory=dict)
    rank_version: str = "v0"

    def to_dict(self) -> dict:
        return _jsonable(self)

    def to_json(self, **kw) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, **kw)


# ---------------------------------------------------------------------------
# Resolution handling: a vf.locate.Resolution, or a dict with its fields
# ---------------------------------------------------------------------------

def _get(obj, name, default=None):
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _location_dict(res) -> dict:
    cands = _get(res, "candidates") or []
    out_c = []
    for c in cands:
        if isinstance(c, Mapping) or hasattr(c, "__dict__") or is_dataclass(c):
            out_c.append({k: _get(c, k) for k in ("place_id", "label", "name", "kind", "state", "district",
                                                  "lat", "lon", "precision_km", "score")})
        else:
            out_c.append({"label": str(c)})
    lat, lon = _get(res, "lat"), _get(res, "lon")
    return {
        "status": _get(res, "status", "ok"),
        "label": _get(res, "label"),
        "how": _get(res, "how"),
        "precision_km": _get(res, "precision_km"),
        "kind": _get(res, "kind"),
        "lat": None if lat is None or (isinstance(lat, float) and math.isnan(lat)) else float(lat),
        "lon": None if lon is None or (isinstance(lon, float) and math.isnan(lon)) else float(lon),
        "candidates": out_c,
        "state": _get(res, "state"),
        "district": _get(res, "district"),
        "place_id": _get(res, "place_id"),
        "query": _get(res, "query"),
    }


# ---------------------------------------------------------------------------
# Keyword index (D026): TF-IDF over distinct fact texts plus specialty names
# ---------------------------------------------------------------------------

_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")


def _specialty_words(s: str) -> str:
    return _CAMEL.sub(" ", s).replace("And", "and").lower()


@dataclass
class KeywordIndex:
    vectorizer: Any
    matrix: Any  # scipy csr, docs x vocab, rows L2-normalised
    docs: pd.DataFrame  # doc_id, text (verbatim quote), kind ("fact" | "specialty")
    postings: pd.DataFrame  # doc_id, facility_id, fact_id, field; sorted by doc_id
    version: str = KEYWORD_INDEX_VERSION

    def __post_init__(self):
        self.postings = self.postings.sort_values("doc_id", kind="stable").reset_index(drop=True)
        self._p_doc = self.postings["doc_id"].to_numpy(np.int64)
        self._offsets = np.searchsorted(self._p_doc, np.arange(len(self.docs) + 1))
        self._fac_pos: Optional[np.ndarray] = None
        self._doc_text = self.docs["text"].to_numpy(object)

    def save(self, out_dir) -> Path:
        """Persist into ``out_dir`` (WP12 bundle ``keyword_index/``)."""
        return save_keyword_index(self, out_dir)

    def bind(self, id_to_pos: Mapping[str, int]) -> None:
        self._fac_pos = np.array([id_to_pos.get(f, -1) for f in self.postings["facility_id"]], dtype=np.int64)

    def similarities(self, text: str) -> np.ndarray:
        """Cosine similarity of every doc to ``text``. Phrases separated by ";"
        are scored on their own and the best counts, so listing synonyms does
        not dilute the match. Specialty docs count ``SEARCH_SPECIALTY_WEIGHT``."""
        phrases = [p.strip() for p in str(text).split(";") if p.strip()] or [str(text)]
        q = self.vectorizer.transform(phrases)
        sims = np.asarray((self.matrix @ q.T).max(axis=1).todense()).ravel()
        if not hasattr(self, "_is_spec"):
            self._is_spec = (self.docs["kind"] == "specialty").to_numpy()
        return np.where(self._is_spec, sims * S.SEARCH_SPECIALTY_WEIGHT, sims)

    def matches(self, text: str, min_sim: float):
        """Posting rows whose doc matches ``text`` at ``min_sim`` or above: (rows, sims)."""
        sims = self.similarities(text)
        docs = np.flatnonzero(sims >= min_sim)
        if len(docs) == 0:
            return np.array([], dtype=np.int64), np.array([], dtype=float)
        starts, ends = self._offsets[docs], self._offsets[docs + 1]
        lens = ends - starts
        rows = np.repeat(starts - np.cumsum(np.r_[0, lens[:-1]]), lens) + np.arange(lens.sum())
        return rows, np.repeat(sims[docs], lens)


def build_keyword_index(facts: pd.DataFrame, facilities: Optional[pd.DataFrame] = None) -> KeywordIndex:
    """Fit the fallback index. ``facts``: ``facility_id, fact_id, field, text``
    (``silver/facts.parquet``); ``facilities``: ``facility_id, specialties``.
    Distinct exact texts become documents, so every quote is verbatim for every
    facility that has it. WP12 persists it with ``save_keyword_index``."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    f = facts[["facility_id", "fact_id", "field", "text"]].dropna(subset=["text"])
    texts = pd.Index(f["text"].unique())
    docs = pd.DataFrame({"text": texts, "kind": "fact", "search_text": texts})
    post = pd.DataFrame({
        "doc_id": texts.get_indexer(f["text"]),
        "facility_id": f["facility_id"].to_numpy(),
        "fact_id": f["fact_id"].to_numpy(),
        "field": f["field"].to_numpy(),
    })
    if facilities is not None and "specialties" in facilities:
        sp = facilities[["facility_id", "specialties"]].explode("specialties").dropna()
        sp = sp[sp["specialties"].astype(str).str.len() > 0]
        names = pd.Index(sp["specialties"].unique())
        sdocs = pd.DataFrame({"text": names, "kind": "specialty",
                              "search_text": [_specialty_words(n) for n in names]})
        spost = pd.DataFrame({
            "doc_id": names.get_indexer(sp["specialties"]) + len(docs),
            "facility_id": sp["facility_id"].to_numpy(),
            "fact_id": None,
            "field": "specialties",
        })
        docs = pd.concat([docs, sdocs], ignore_index=True)
        post = pd.concat([post, spost], ignore_index=True)
    docs.insert(0, "doc_id", np.arange(len(docs)))
    vec = TfidfVectorizer(lowercase=True, token_pattern=r"(?u)\b\w+\b", ngram_range=(1, 2),
                          min_df=2, sublinear_tf=True, dtype=np.float32)
    X = vec.fit_transform(docs["search_text"].tolist()).tocsr()
    return KeywordIndex(vectorizer=vec, matrix=X, docs=docs[["doc_id", "text", "kind"]], postings=post)


#: Databricks Apps refuse any app file over 10 MB. The TF-IDF matrix is about
#: 20 MB compressed, so it is written as row blocks, each kept under this cap.
MATRIX_PART_MAX_BYTES = 8 * 1024 * 1024


def _save_matrix_parts(matrix, folder: Path, max_bytes: int = MATRIX_PART_MAX_BYTES) -> list[str]:
    """Write ``matrix`` as ``matrix.part-000.npz``, ``-001``, ... split by rows,
    doubling the number of parts until every file is under ``max_bytes``.
    Rows are independent documents, so ``vstack`` of the parts is the matrix."""
    from scipy import sparse

    m = matrix.tocsr()
    n_rows = m.shape[0]
    k = 1
    while True:
        for old in folder.glob("matrix.part-*.npz"):
            old.unlink()
        bounds = np.linspace(0, n_rows, k + 1).round().astype(int)
        names = []
        for i in range(k):
            name = f"matrix.part-{i:03d}.npz"
            sparse.save_npz(folder / name, m[bounds[i]:bounds[i + 1]], compressed=True)
            names.append(name)
        if all((folder / n).stat().st_size < max_bytes for n in names) or k >= max(n_rows, 1):
            return names
        k *= 2


def save_keyword_index(kw: KeywordIndex, out_dir) -> Path:
    """Write ``vectorizer.pkl``, ``matrix.part-NNN.npz`` (row blocks, each under
    ``MATRIX_PART_MAX_BYTES`` for the Databricks Apps file limit),
    ``docs.parquet``, ``postings.parquet``, ``meta.json`` into ``out_dir``
    atomically (temp dir, then rename)."""
    out = Path(out_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{out.name}.", dir=out.parent))
    with open(tmp / "vectorizer.pkl", "wb") as fh:
        pickle.dump(kw.vectorizer, fh)
    parts = _save_matrix_parts(kw.matrix, tmp)
    kw.docs.to_parquet(tmp / "docs.parquet", index=False)
    kw.postings.to_parquet(tmp / "postings.parquet", index=False)
    (tmp / "meta.json").write_text(json.dumps({
        "version": kw.version, "n_docs": int(len(kw.docs)), "n_postings": int(len(kw.postings)),
        "n_terms": int(kw.matrix.shape[1]), "matrix_parts": parts,
        "matrix_shape": [int(kw.matrix.shape[0]), int(kw.matrix.shape[1])],
    }, indent=2))
    if out.exists():
        old = out.with_name(out.name + ".old")
        if old.exists():
            import shutil
            shutil.rmtree(old)
        os.replace(out, old)
        os.replace(tmp, out)
        import shutil
        shutil.rmtree(old)
    else:
        os.replace(tmp, out)
    return out


def load_keyword_index(path) -> KeywordIndex:
    from scipy import sparse

    p = Path(path)
    with open(p / "vectorizer.pkl", "rb") as fh:  # our own bundle file only
        vec = pickle.load(fh)
    meta_path = p / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    parts = meta.get("matrix_parts") or sorted(x.name for x in p.glob("matrix.part-*.npz"))
    if parts:
        matrix = sparse.vstack([sparse.load_npz(p / n) for n in parts], format="csr")
        if "matrix_shape" in meta and list(matrix.shape) != meta["matrix_shape"]:
            raise ValueError(f"keyword index parts rebuild to {matrix.shape}, meta says {meta['matrix_shape']}")
    else:  # single-file indexes written before the split
        matrix = sparse.load_npz(p / "matrix.npz").tocsr()
    return KeywordIndex(vectorizer=vec, matrix=matrix,
                        docs=pd.read_parquet(p / "docs.parquet"),
                        postings=pd.read_parquet(p / "postings.parquet"))


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------

@dataclass
class LineData:
    grade: np.ndarray  # int8 per facility position
    strength: np.ndarray  # object
    n_support: np.ndarray  # int
    evidence: list  # per position: list of {requirement, quote, field, fact_id}
    missing: list  # per position: list of plain requirement names
    blocked: np.ndarray  # bool: a major flag applies to this line
    penalty: np.ndarray  # float: sum of minor-flag penalties
    minor_flags: dict  # pos -> list of flag dicts
    major_flags: dict  # pos -> list of flag dicts
    # probability mode (None when the line has no p_offers)
    p: Optional[np.ndarray] = None
    p_low: Optional[np.ndarray] = None
    p_high: Optional[np.ndarray] = None
    top_factors: Optional[list] = None
    n_labels: Optional[int] = None
    model_version: Optional[str] = None
    #: per position: the v0.3 level note (emoc) or None
    level_note: Optional[list] = None


@dataclass
class Index:
    fac: pd.DataFrame
    lat: np.ndarray
    lon: np.ndarray
    precision_km: np.ndarray
    id_to_pos: dict
    lines: dict  # service_line -> LineData
    record_major: dict  # pos -> list of whole-record major flags (for search)
    record_minor: dict
    #: duplicate groups (WP11 pairs joined into connected groups, so chains A-B, B-C are one group)
    dup_of: dict  # pos -> every other position in its group ("also listed as")
    dup_drop: set  # positions that are not their group's preferred record
    recipe_version: str
    grades_method: str
    flags_source: str
    keyword: Optional[KeywordIndex] = None
    facts_for_keyword: Optional[pd.DataFrame] = None
    post_offices: Optional[pd.DataFrame] = None
    probability_source: Optional[str] = None
    dup_canon: dict = field(default_factory=dict)  # pos -> the group's preferred position
    #: "v1" (D048, shipped) or "v0" (legacy: additive score, or probability mode)
    rank_version: str = "v1"
    #: which grades file was loaded, and a plain status (v0.3, or the v0.2 fallback)
    grades_source: str = ""
    grades_status: str = ""
    #: per position: the record sits on a shared point or at its PIN's centre
    approx: Optional[np.ndarray] = None
    #: PIN points with their state, to find an origin's state when the resolution has none
    pin_lat: Optional[np.ndarray] = None
    pin_lon: Optional[np.ndarray] = None
    pin_state: Optional[np.ndarray] = None

    def state_at(self, lat: float, lon: float) -> Optional[str]:
        """The PIN-directory state of the PIN point nearest (lat, lon), or None."""
        if self.pin_lat is None or not len(self.pin_lat):
            return None
        d = haversine_km(lat, lon, self.pin_lat, self.pin_lon)
        return str(self.pin_state[int(np.nanargmin(d))])

    @property
    def graded_lines(self) -> tuple:
        return tuple(l for l in S.SERVICE_LINES if l in self.lines)

    def is_probability(self, line: str) -> bool:
        ld = self.lines.get(line)
        return ld is not None and ld.p is not None

    def ensure_keyword(self) -> KeywordIndex:
        if self.keyword is None:
            if self.facts_for_keyword is None:
                raise RuntimeError("no keyword index in the bundle and no facts to build one")
            self.keyword = build_keyword_index(self.facts_for_keyword, self.fac)
        if self.keyword._fac_pos is None:
            self.keyword.bind(self.id_to_pos)
        return self.keyword


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, float) and math.isnan(v):
        return []
    if isinstance(v, (list, tuple, np.ndarray)):
        return list(v)
    return [v]


def _str_or_none(v) -> Optional[str]:
    if v is None or v is pd.NA:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    s = str(v)
    return s if s.strip() and s.strip().lower() != "null" else None


def _location_precision(fac: pd.DataFrame, places: Optional[pd.DataFrame]) -> tuple[np.ndarray, list]:
    if "location_precision_km" in fac.columns:
        prec = pd.to_numeric(fac["location_precision_km"], errors="coerce").to_numpy(float, copy=True)
        notes = [(_str_or_none(n) or "") for n in fac.get("location_note", pd.Series([""] * len(fac)))]
        return prec, notes
    cc = fac.get("coord_check", pd.Series(["ok"] * len(fac))).fillna("missing").astype(str)
    prec = cc.map(S.LOCATION_PRECISION_KM).fillna(2.0).to_numpy(float, copy=True)
    if places is not None and {"kind", "pin", "precision_km"} <= set(places.columns):
        pin_prec = places[places["kind"] == "pin"].drop_duplicates("pin").set_index("pin")["precision_km"]
        moved = (cc == "moved_to_pin").to_numpy()
        pp = fac["pin"].map(pin_prec).to_numpy(float)
        prec = np.where(moved & ~np.isnan(pp), np.maximum(pp, 2.0), prec)
    notes = [S.LOCATION_NOTES.get(c, "") for c in cc]
    return prec, notes


def build_index(
    facilities: pd.DataFrame,
    grades: pd.DataFrame,
    facts: Optional[pd.DataFrame] = None,
    flags: Optional[pd.DataFrame] = None,
    duplicates: Optional[pd.DataFrame] = None,
    service_probability: Optional[pd.DataFrame] = None,
    places: Optional[pd.DataFrame] = None,
    keyword: Optional[KeywordIndex] = None,
    post_offices: Optional[pd.DataFrame] = None,
    *,
    rank_version: Optional[str] = None,
    grades_source: Optional[str] = None,
    grades_status: Optional[str] = None,
) -> Index:
    """Build the in-memory index from frames (``load_index`` reads them).
    ``rank_version``: "v1" (default, D048) or "v0" (legacy). When a
    ``service_probability`` table is given and no version is, the legacy path
    is used: probability mode belongs to the superseded D039 ranker."""
    fac = facilities.reset_index(drop=True).copy()
    n = len(fac)
    id_to_pos = {fid: i for i, fid in enumerate(fac["facility_id"])}
    lat = pd.to_numeric(fac["lat"], errors="coerce").to_numpy(float, copy=True)
    lon = pd.to_numeric(fac["lon"], errors="coerce").to_numpy(float, copy=True)
    if "coord_check" in fac.columns:  # contract: excluded_unresolved has null lat/lon; enforce
        bad = (fac["coord_check"] == "excluded_unresolved").to_numpy()
        lat[bad] = np.nan
        lon[bad] = np.nan
    prec, notes = _location_precision(fac, places)
    fac["location_note"] = notes
    fac["location_precision_km"] = prec

    # source confidence: facility level, from the facilities table or the grades
    if "source_confidence" not in fac.columns:
        sc = grades.drop_duplicates("facility_id").set_index("facility_id")["source_confidence"] \
            if "source_confidence" in grades.columns else pd.Series(dtype=float)
        fac["source_confidence"] = fac["facility_id"].map(sc)
    if fac["source_confidence"].isna().any():
        from . import recipes
        miss = fac["source_confidence"].isna()
        fac.loc[miss, "source_confidence"] = [
            recipes.source_confidence(r.get("n_source_domains"), r.get("official_website"),
                                      r.get("page_updated"), r.get("staff_named"))
            for _, r in fac[miss].iterrows()]
    fac["source_confidence"] = fac["source_confidence"].astype(float)

    # trust flags (WP11). None means no record is flagged; the panel says so.
    if flags is None:
        flags = pd.DataFrame(columns=list(FLAG_COLUMNS))
        flags_source = "none"
    else:
        missing_cols = {"facility_id", "flag", "severity"} - set(flags.columns)
        if missing_cols:
            raise ValueError(f"flags table lacks {sorted(missing_cols)} (gold/facility_flags.parquet contract)")
        vers = sorted({str(v) for v in flags.get("check_version", pd.Series(dtype=str)).dropna().unique()})
        flags_source = ",".join(vers) if vers else "wp11"
    flags = flags[flags["facility_id"].isin(id_to_pos)]
    fpos = flags["facility_id"].map(id_to_pos).to_numpy()
    flag_recs = [dict(r, pos=int(p)) for r, p in zip(flags.to_dict("records"), fpos)]
    for r in flag_recs:
        r["service_line"] = _str_or_none(r.get("service_line"))
        r["quote"] = _str_or_none(r.get("quote"))
        r["reason"] = _str_or_none(r.get("reason"))
        r["signal"] = _str_or_none(r.get("signal"))
    record_major, record_minor = {}, {}
    for r in flag_recs:
        if r["service_line"] is None:
            (record_major if r["severity"] == "major" else record_minor).setdefault(r["pos"], []).append(r)

    # grades per line (contract v2 only: every cited quote needs its fact id)
    need = {"facility_id", "service_line", "grade", "evidence_items", "missing_for_next", "evidence_strength"}
    if not need <= set(grades.columns):
        raise ValueError(f"grades lack {sorted(need - set(grades.columns))}: capability_grades v2 "
                         "(recipe v0.2, docs/07_data_contracts.md) is required")
    g = grades[grades["facility_id"].isin(id_to_pos)].copy()
    g["pos"] = g["facility_id"].map(id_to_pos)
    recipe_version = str(g["recipe_version"].iloc[0]) if "recipe_version" in g and len(g) else "unknown"
    grades_method = str(g["method"].iloc[0]) if "method" in g and len(g) else "unknown"

    lines = {}
    for line, gl in g.groupby("service_line"):
        if line not in S.SERVICES:
            continue
        grade = np.zeros(n, dtype=np.int8)
        strength = np.full(n, "none", dtype=object)
        n_support = np.zeros(n, dtype=np.int64)
        evidence = [[] for _ in range(n)]
        missing = [[] for _ in range(n)]
        level = [None] * n
        has_level = "level_note" in gl.columns
        for r in gl.itertuples(index=False):
            p = r.pos
            grade[p] = int(r.grade)
            evidence[p] = [{"requirement": it.get("requirement"), "quote": it.get("text"),
                            "field": it.get("field"), "fact_id": _str_or_none(it.get("fact_id"))}
                           for it in _as_list(r.evidence_items)]
            missing[p] = [str(x) for x in _as_list(r.missing_for_next)]
            strength[p] = _str_or_none(r.evidence_strength) or "none"
            n_support[p] = int(getattr(r, "n_supporting_facts", 0) or 0)
            if has_level:
                level[p] = _str_or_none(r.level_note)
        blocked = np.zeros(n, dtype=bool)
        penalty = np.zeros(n, dtype=float)
        minor, major = {}, {}
        for r in flag_recs:
            if r["service_line"] not in (None, line):
                continue
            if r["severity"] == "major":
                blocked[r["pos"]] = True
                major.setdefault(r["pos"], []).append(r)
            else:
                penalty[r["pos"]] += S.PENALTIES.get(r["flag"], S.PENALTY_OTHER_MINOR)
                minor.setdefault(r["pos"], []).append(r)
        lines[line] = LineData(grade, strength, n_support, evidence, missing, blocked, penalty, minor, major,
                               level_note=level)

    # probability mode
    prob_source = None
    if service_probability is not None and len(service_probability):
        sp = service_probability[service_probability["facility_id"].isin(id_to_pos)]
        for line, pl in sp.groupby("service_line"):
            if line not in lines:
                continue
            ld = lines[line]
            pos = pl["facility_id"].map(id_to_pos).to_numpy()
            ld.p = np.zeros(n)
            ld.p[pos] = pl["p_offers"].astype(float).to_numpy()
            ld.p_low = np.zeros(n)
            ld.p_high = np.zeros(n)
            ld.p_low[pos] = pl.get("p_low", pl["p_offers"]).astype(float).to_numpy()
            ld.p_high[pos] = pl.get("p_high", pl["p_offers"]).astype(float).to_numpy()
            ld.top_factors = [[] for _ in range(n)]
            if "top_factors" in pl:
                for p_, tf in zip(pos, pl["top_factors"]):
                    ld.top_factors[p_] = [dict(x) for x in _as_list(tf)]
            ld.n_labels = int(pl["n_labels_line"].max()) if "n_labels_line" in pl else None
            ld.model_version = str(pl["model_version"].iloc[0]) if "model_version" in pl else None
        prob_source = "service_probability"

    # duplicates: pairs joined into groups (union-find). The group's preferred
    # record is a WP11 ``keep`` that no pair marks as the other side; when the
    # pairs disagree (cycles), the most often kept record, then the lowest id.
    dup_of, dup_drop, dup_canon = {}, set(), {}
    if duplicates is not None and len(duplicates):
        parent: dict = {}

        def find(x):
            while parent.setdefault(x, x) != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        kept, dropped = {}, set()
        for r in duplicates.itertuples(index=False):
            a, b = id_to_pos.get(r.facility_id_a), id_to_pos.get(r.facility_id_b)
            if a is None or b is None or a == b:
                continue
            keep = id_to_pos.get(_str_or_none(getattr(r, "keep", None)) or r.facility_id_a, a)
            keep = keep if keep in (a, b) else a
            kept[keep] = kept.get(keep, 0) + 1
            dropped.add(b if keep == a else a)
            parent[find(a)] = find(b)
        groups: dict = {}
        for x in list(parent):
            groups.setdefault(find(x), []).append(x)
        for members in groups.values():
            members = sorted(members)
            pref = [m for m in members if m in kept and m not in dropped] or \
                sorted((m for m in members if m in kept), key=lambda m: (-kept[m], fac["facility_id"].iat[m]))
            canon = pref[0]
            for m in members:
                dup_canon[m] = canon
                dup_of[m] = [o for o in members if o != m]
                if m != canon:
                    dup_drop.add(m)

    kw_facts = None
    if keyword is None and facts is not None:
        kw_facts = facts

    # approximate positions (D046): a point shared with another record, or a PIN centre
    usable_pt = ~np.isnan(lat) & ~np.isnan(lon)
    shared = np.zeros(n, dtype=bool)
    if usable_pt.any():
        pts = np.round(np.c_[lat[usable_pt], lon[usable_pt]], 6)
        _, inv, cnt = np.unique(pts, axis=0, return_inverse=True, return_counts=True)
        shared[np.flatnonzero(usable_pt)] = cnt[inv.ravel()] > 1
    moved = np.array(["centre of its PIN code area" in (x or "") for x in fac["location_note"]], dtype=bool)
    if "coord_check" in fac.columns:
        moved |= (fac["coord_check"].astype(str) == "moved_to_pin").to_numpy()
    approx = usable_pt & (shared | moved)

    # PIN points with their state, for an origin without one (coordinates typed into a test or the harness)
    pin_lat = pin_lon = pin_state = None
    if places is not None and {"kind", "lat", "lon", "state"} <= set(places.columns):
        pp = places[(places["kind"] == "pin") & places["lat"].notna() & places["state"].notna()]
        if len(pp):
            pin_lat, pin_lon = pp["lat"].to_numpy(float), pp["lon"].to_numpy(float)
            pin_state = pp["state"].astype(str).to_numpy(object)

    if rank_version is None:
        rank_version = "v0" if prob_source is not None else DEFAULT_RANK_VERSION
    if rank_version not in RANK_VERSIONS:
        raise ValueError(f"rank_version must be one of {RANK_VERSIONS}, not {rank_version!r}")
    idx = Index(fac=fac, lat=lat, lon=lon, precision_km=prec, id_to_pos=id_to_pos, lines=lines,
                record_major=record_major, record_minor=record_minor, dup_of=dup_of, dup_drop=dup_drop,
                dup_canon=dup_canon,
                recipe_version=recipe_version, grades_method=grades_method, flags_source=flags_source,
                keyword=keyword, facts_for_keyword=kw_facts, post_offices=post_offices,
                probability_source=prob_source, rank_version=rank_version,
                grades_source=grades_source or "frames", grades_status=grades_status or f"recipe {recipe_version}",
                approx=approx, pin_lat=pin_lat, pin_lon=pin_lon, pin_state=pin_state)
    if keyword is not None:
        keyword.bind(id_to_pos)
    return idx


#: Grades the v1-final ranker gates on (D048), and the fallback.
GRADES_V03 = "capability_grades_v03.parquet"
GRADES_V02 = "capability_grades.parquet"


def _grades_status(grades: pd.DataFrame, source: str) -> str:
    vers = sorted({str(v) for v in grades.get("recipe_version", pd.Series(dtype=str)).dropna().unique()})
    if vers == ["v0.3"]:
        return f"recipe v0.3 (rules aligned to WHO 2009, FBNC 2025, PM-JAY criteria, PMNDP; D048, D052), from {source}"
    return (f"recipe {', '.join(vers) or 'unknown'} from {source}: the v0.3 grades the v1-final spec gates on "
            "(D048) were not found, so the older rules are used. Rebuild with scripts/wp3_regrade_v03.py and "
            "scripts/wp12_bundle.py.")


def load_index(bundle_dir=None, service_probability: Optional[pd.DataFrame] = None,
               *, load_post_offices: bool = True, rank_version: Optional[str] = None) -> Index:
    """Load everything once. ``bundle_dir`` is a WP12 bundle (``facilities.parquet``,
    ``grades.parquet``, ``flags.parquet``, ``duplicates.parquet``, ...); ``None``
    reads ``vf.paths.data_dir()`` silver and gold. Grades: recipe v0.3
    (``gold/capability_grades_v03.parquet``, or a bundle ``grades_v03.parquet`` or
    ``grades.parquet`` written from it), else v0.2 with ``grades_status`` saying so.
    The trust checks (WP11) are required: without them a merged record or a
    wrong location would be recommended silently. A ``service_probability``
    frame (or the bundle's or gold's ``service_probability.parquet``) switches
    the index to the legacy probability mode (D039, superseded)."""
    from . import paths

    def opt(p: Path) -> Optional[pd.DataFrame]:
        return pd.read_parquet(p) if p.exists() else None

    def req(p: Path, what: str) -> pd.DataFrame:
        if not p.exists():
            raise FileNotFoundError(f"{p} is missing: {what}")
        return pd.read_parquet(p)

    if bundle_dir is not None and (Path(bundle_dir) / "facilities.parquet").exists():
        b = Path(bundle_dir)
        fac = pd.read_parquet(b / "facilities.parquet")
        gpath = b / "grades_v03.parquet" if (b / "grades_v03.parquet").exists() else b / "grades.parquet"
        grades = req(gpath, "rebuild the bundle (scripts/wp12_bundle.py)")
        gsrc = f"bundle {gpath.name}"
        flags = req(b / "flags.parquet", "the trust checks (WP11) are required; rebuild the bundle")
        dups = req(b / "duplicates.parquet", "the duplicate pairs (WP11) are required; rebuild the bundle")
        kw = load_keyword_index(b / "keyword_index") if (b / "keyword_index").exists() else None
        sp = service_probability if service_probability is not None else opt(b / "service_probability.parquet")
        return build_index(fac, grades, flags=flags, duplicates=dups, service_probability=sp,
                           places=opt(b / "places.parquet"), keyword=kw,
                           post_offices=opt(b / "post_offices.parquet") if load_post_offices else None,
                           rank_version=rank_version, grades_source=gsrc, grades_status=_grades_status(grades, gsrc))

    silver, gold = paths.silver_dir(), paths.gold_dir()
    fac = pd.read_parquet(silver / "facilities.parquet")
    gpath = gold / GRADES_V03 if (gold / GRADES_V03).exists() else gold / GRADES_V02
    grades = req(gpath, "run WP3 or WP4 first")
    gsrc = f"gold/{gpath.name}"
    flags = req(gold / "facility_flags.parquet", "run scripts/wp11_trust_checks.py first")
    dups = req(gold / "facility_duplicates.parquet", "run scripts/wp11_trust_checks.py first")
    facts = pd.read_parquet(silver / "facts.parquet", columns=["fact_id", "facility_id", "field", "text"])
    sp = service_probability if service_probability is not None else opt(gold / "service_probability.parquet")
    po = None
    if load_post_offices and (silver / "post_offices.parquet").exists():
        po = pd.read_parquet(silver / "post_offices.parquet", columns=["lat", "lon", "pin", "coord_valid"])
        po = po[po["coord_valid"]].drop_duplicates(["lat", "lon"])[["lat", "lon", "pin"]].reset_index(drop=True)
    return build_index(fac, grades, facts=facts, flags=flags, duplicates=dups, service_probability=sp,
                       places=opt(silver / "places.parquet"), post_offices=po,
                       rank_version=rank_version, grades_source=gsrc, grades_status=_grades_status(grades, gsrc))


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

def _fmt_km(km: float, precision: float) -> str:
    if km is None or math.isnan(km):
        return "unknown distance"
    if precision is not None and precision > 2:
        return f"about {int(round(km))} km"
    return f"{km:.1f} km"


def _round_km(km: float, precision: float) -> Optional[float]:
    if km is None or math.isnan(km):
        return None
    return float(round(km)) if precision is not None and precision > 2 else round(float(km), 1)


def _origin_precision(loc: Optional[Mapping]) -> Optional[float]:
    """The resolved place's precision (km), or None when unknown."""
    if not loc:
        return None
    p = loc.get("precision_km")
    try:
        p = float(p)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(p) else p


def _eff_precision(fac_prec: Optional[float], loc: Optional[Mapping]) -> Optional[float]:
    """How well a card's distance is known: the looser of the facility's
    location and the searched place's (a district centre is good to tens of km)."""
    o = _origin_precision(loc)
    vals = [x for x in (fac_prec, o) if x is not None and not math.isnan(x)]
    return max(vals) if vals else None


def _sources(idx: Index, pos: int) -> dict:
    r = idx.fac.iloc[pos]
    doms = [str(d) for d in _as_list(r.get("source_domains"))]
    also = [{"facility_id": idx.fac.facility_id.iat[o], "name": idx.fac.name.iat[o]} for o in idx.dup_of.get(pos, [])]
    return {"n_domains": int(r.get("n_source_domains") or len(doms)), "domains": doms,
            "official_website": _str_or_none(r.get("official_website")), "also_listed_as": also}


def _flag_view(f: dict) -> dict:
    return {"flag": f.get("flag"), "severity": f.get("severity"), "signal": f.get("signal"),
            "reason": f.get("reason"), "quote": f.get("quote")}


def _card_flags(idx: Index, line: Optional[str], pos: int) -> list:
    """Every flag that applies to this facility for ``line`` (record-level ones
    included), major first. ``line=None``: record-level flags only (search)."""
    if line is not None and line in idx.lines:
        ld = idx.lines[line]
        fl = ld.major_flags.get(pos, []) + ld.minor_flags.get(pos, [])
    else:
        fl = idx.record_major.get(pos, []) + idx.record_minor.get(pos, [])
    return [_flag_view(f) for f in fl]


def _flag_penalties(flags: list) -> list:
    return [{"name": f["flag"], "value": S.PENALTIES.get(f["flag"], S.PENALTY_OTHER_MINOR),
             "reason": f.get("reason"), "quote": f.get("quote")} for f in flags]


def _confidence_v0(grade: int, strength: str, n_minor: int, precision: float, n_support: int) -> tuple[str, str]:
    if strength == "thin":
        return "low", "Low: the support is thin (one fact, or a record with one web source and no official website)."
    if precision is not None and precision > S.DISTRICT_PRECISION_KM:
        return "low", f"Low: the facility's location is known only to within about {precision:.0f} km."
    if grade == 3 and n_minor == 0:
        return "high", f"High: corroborated by {n_support} facts, {strength} evidence, no warnings, precise location."
    if grade == 3 and n_minor == 1:
        return "medium", "Medium: corroborated, but the record carries one warning."
    if grade == 2 and n_minor == 0:
        return "medium", "Medium: the record names the service, but not everything it needs."
    return "low", "Low: the record carries warnings that may affect this answer."


def _evidence_note_v1(grade: int, strength: str, n_minor: int, precision: float, n_support: int) -> str:
    """The v1 card's evidence note: the same facts as the legacy confidence
    reason, with no level (D048 (6): never "confidence")."""
    if strength == "thin":
        return "Thin support: one fact, or a record with one web source and no official website."
    if precision is not None and precision > S.DISTRICT_PRECISION_KM:
        return f"The facility's location is known only to within about {precision:.0f} km."
    if grade == 3 and n_minor == 0:
        return f"Corroborated by {n_support} facts, {strength} evidence, no warnings, precise location."
    if grade == 3 and n_minor == 1:
        return "Corroborated, but the record carries one warning."
    if grade == 2 and n_minor == 0:
        return "The record names the service, but not everything it needs."
    return "The record carries warnings that may affect this answer."


def _confidence_prob(ld: LineData, pos: int, n_minor: int, precision: float, thr: float) -> tuple[str, str]:
    p, lo = float(ld.p[pos]), float(ld.p_low[pos])
    if ld.n_labels is not None and ld.n_labels < S.MIN_LABELS_LINE:
        return "low", "Low: the model for this service saw too few independent labels to be calibrated."
    if precision is not None and precision > S.DISTRICT_PRECISION_KM:
        return "low", f"Low: the facility's location is known only to within about {precision:.0f} km."
    if lo >= thr and n_minor == 0:
        return "high", f"High: even the low end of the estimate ({lo:.0%}) clears the bar of {thr:.0%}."
    if n_minor <= 1:
        return "medium", f"Medium: an estimated {p:.0%} chance an independent source confirms it (range {lo:.0%} to {float(ld.p_high[pos]):.0%})."
    return "low", "Low: the record carries several warnings."


def _could_be_wrong(idx: Index, line: Optional[str], pos: int, strength: str, missing: list,
                    minor: list, prob: Optional[float], keyword: bool = False) -> str:
    out = []
    for f in minor[:2]:
        if f.get("reason"):
            out.append(f["reason"])
    if keyword:
        out.append("This is a keyword match: no rule checked that the service is actually offered.")
    elif prob is not None:
        out.append(f"About {1 - prob:.0%} chance an independent source would not confirm this service.")
    elif strength == "thin":
        out.append("The support rests on very little: a single fact, or a record with one web source and no official website.")
    if len(out) < 2 and missing and not keyword:
        out.append(f"The record does not mention {missing[0]}.")
    if not out:
        out.append("Web pages can be stale: the record says what pages claimed, not what is available today.")
    return " ".join(out[:2])


def _make_card(idx: Index, pos: int, dist: float, *, line: Optional[str], score: Optional[float],
               parts: dict, confidence: tuple, evidence: list, missing: list, could: str,
               radius: Optional[float] = None, grade: Optional[int] = None, grade_label: Optional[str] = None,
               strength: Optional[str] = None, origin: Optional[Mapping] = None) -> Card:
    r = idx.fac.iloc[pos]
    prec = float(idx.precision_km[pos]) if not math.isnan(idx.precision_km[pos]) else None
    note = r["location_note"] or ""
    if prec is not None and prec > 2:
        note = (note + f" Position good to about {prec:.0f} km.").strip()
    if radius is not None and dist is not None and dist > radius:
        note = (note + f" Beyond the usual {radius:.0f} km for this service.").strip()
    note = (note + " Straight-line distance.").strip()
    o_prec = _origin_precision(origin)
    if o_prec is not None and o_prec > S.ORIGIN_FUZZY_KM:
        where = _str_or_none(origin.get("label")) or "the place searched"
        where = re.sub(r"(?i)^(the\s+)?centre of\s+", "", where)  # WP2 labels may already say "Centre of ..."
        note = (note + f" Measured from the centre of {where}, which is itself known only to within "
                       f"about {o_prec:.0f} km.").strip()
    g = grade if grade is not None else (int(idx.lines[line].grade[pos]) if line in idx.lines else None)
    gl = grade_label or (S.GRADE_PLAIN[S.GRADE_LABELS[g]] if g is not None else "")
    return Card(
        facility_id=r["facility_id"], name=_str_or_none(r.get("name")) or "(no name)",
        type=_str_or_none(r.get("facility_type")), operator=_str_or_none(r.get("operator_type")),
        city=_str_or_none(r.get("city_raw")), state=_str_or_none(r.get("state_raw")),
        lat=None if math.isnan(idx.lat[pos]) else float(idx.lat[pos]),
        lon=None if math.isnan(idx.lon[pos]) else float(idx.lon[pos]),
        distance_km=_round_km(dist, _eff_precision(prec, origin)), location_precision_km=prec, location_note=note,
        grade=g, grade_label=gl, confidence=confidence[0], confidence_reason=confidence[1],
        score=score, parts=parts, evidence=evidence, missing=[f"no fact in the record mentions {m}" for m in missing],
        sources=_sources(idx, pos), why_here="", could_be_wrong=could,
        evidence_strength=strength or (str(idx.lines[line].strength[pos]) if line in idx.lines else "none"),
        flags=_card_flags(idx, line, pos),
    )


def _why_here(a: Card, b: Optional[Card], n_rec: int, mode: str, *, radius: Optional[float] = None,
              a_in: bool = True, b_in: bool = True, origin: Optional[Mapping] = None) -> str:
    """One sentence on why ``a`` is above ``b``. ``a_in``/``b_in``: whether each
    is inside the service radius (inside always comes first, ``S.ORDER_RULE``)."""
    if b is None:
        return ("The only recommendable option within reach." if n_rec <= 1
                else f"Last of the {min(n_rec, 3)} options shown out of {n_rec} recommendable within reach.")
    da, db = a.distance_km, b.distance_km
    fa = _fmt_km(da, _eff_precision(a.location_precision_km, origin))
    fb = _fmt_km(db, _eff_precision(b.location_precision_km, origin))
    if radius is not None and a_in and not b_in:
        return (f"Listed above {b.name} because it is inside the {radius:.0f} km radius and {b.name} is not "
                f"({fa} against {fb}); facilities inside the radius always come first.")
    if a.score is not None and b.score is not None and abs(a.score - b.score) < S.TIE_EPS:
        return f"Tied with {b.name} (scores within {S.TIE_EPS}); listed first because it is closer ({fa} against {fb})."
    pa, pb = a.parts, b.parts
    if mode == "probability":
        # which factor of the product moved more: compare the log ratios
        e_ratio = math.log(max(pa["evidence"], 1e-9) / max(pb["evidence"], 1e-9))
        d_ratio = math.log(max(pa["distance"], 1e-9) / max(pb["distance"], 1e-9))
        if e_ratio >= d_ratio:
            return (f"Ranked above {b.name}: a higher chance an independent source confirms it "
                    f"({pa['evidence']:.0%} against {pb['evidence']:.0%}).")
        return f"Ranked above {b.name}: closer ({fa} against {fb}) with a similar chance of confirmation."
    diffs = {
        "evidence": pa["evidence"] - pb["evidence"],
        "distance": pa["distance"] - pb["distance"],
        "trust": pa["trust"] - pb["trust"],
        "penalty": sum(x["value"] for x in pb["penalties"]) - sum(x["value"] for x in pa["penalties"]),
    }
    k = max(diffs, key=diffs.get)
    if k == "evidence":
        return f"Ranked above {b.name}: stronger evidence ({a.grade_label.split(' (')[0]}, against {b.grade_label.split(' (')[0]})." \
            if a.grade != b.grade else f"Ranked above {b.name}: more evidence for the same level of support."
    if k == "distance":
        return f"Ranked above {b.name}: closer ({fa} against {fb})."
    if k == "trust":
        return (f"Ranked above {b.name}: higher source trust ({pa['raw']['trust']:.2f} against {pb['raw']['trust']:.2f}, "
                f"from web domains, official website, page recency and named staff).")
    names = ", ".join(x["name"].replace("_", " ") for x in pb["penalties"])
    return f"Ranked above {b.name}, which carries a warning this one does not ({names})."


def _dedupe(idx: Index, order: list, tier: Optional[Mapping] = None) -> list:
    """One card per duplicate group: the group's preferred record when it is a
    candidate in the same tier as the group's best-ranked candidate, else that
    best-ranked candidate. ``order`` is best first; ``tier`` (pos -> int, lower
    first, e.g. inside the radius 0, beyond 1) keeps a group from dropping out
    of the inside-radius tier just because its preferred record lies beyond."""
    cset = set(order)
    t = tier or {}
    seen, out = set(), []
    for p in order:
        c = idx.dup_canon.get(p)
        if c is None:
            out.append(p)
            continue
        if c in seen:
            continue
        if c != p and c in cset and t.get(c, 0) == t.get(p, 0):
            continue  # the preferred record comes later in the same tier
        seen.add(c)
        out.append(p)
    return out


def _n_distinct(idx: Index, positions) -> int:
    """Facilities counted once per WP11 duplicate group."""
    return len({idx.dup_canon.get(int(p), -1 - int(p)) for p in positions})


def _break_ties(order: list, score: dict, dist: dict, tier: Optional[Mapping] = None) -> list:
    """Stable pass: a candidate within TIE_EPS of the one above and closer moves
    up, never across tiers (``tier``: pos -> int, as in ``_dedupe``)."""
    order = list(order)
    t = tier or {}
    for _ in range(len(order)):
        swapped = False
        for i in range(len(order) - 1):
            a, b = order[i], order[i + 1]
            if t.get(a, 0) == t.get(b, 0) and abs(score[a] - score[b]) < S.TIE_EPS and dist[b] < dist[a]:
                order[i], order[i + 1] = b, a
                swapped = True
        if not swapped:
            break
    return order


# ---------------------------------------------------------------------------
# rank
# ---------------------------------------------------------------------------

def _distances(idx: Index, lat: float, lon: float) -> np.ndarray:
    return haversine_km(lat, lon, idx.lat, idx.lon)


def _score_parts(idx: Index, line: str, pos: int, dist: float, radius: float, mode: str) -> tuple[float, dict]:
    ld = idx.lines[line]
    reach = math.exp(-dist / radius)
    if mode == "probability":
        p = float(ld.p[pos])
        parts = {"evidence": p, "distance": reach, "trust": None, "penalties": [], "combine": "product",
                 "labels": {"evidence": "chance an independent source confirms it", "distance": "reach"},
                 "p_low": float(ld.p_low[pos]), "p_high": float(ld.p_high[pos]),
                 "top_factors": ld.top_factors[pos] if ld.top_factors else []}
        return p * reach, parts
    w = S.WEIGHTS
    g = int(ld.grade[pos])
    ev = min(max(S.GRADE_EVIDENCE.get(g, 0.0) + S.STRENGTH_ADJUST.get(ld.strength[pos], 0.0), 0.0), 1.0)
    tr = float(idx.fac["source_confidence"].iat[pos])
    pens = _flag_penalties(ld.minor_flags.get(pos, []))
    parts = {"evidence": w["evidence"] * ev, "distance": w["distance"] * reach, "trust": w["trust"] * tr,
             "penalties": pens, "combine": "sum",
             "raw": {"evidence": ev, "distance": reach, "trust": tr}, "weights": dict(w),
             "labels": {"evidence": "evidence in the record", "distance": "distance", "trust": "source trust"}}
    score = parts["evidence"] + parts["distance"] + parts["trust"] - sum(p["value"] for p in pens)
    return score, parts


def _rec_card(idx: Index, line: str, pos: int, dist: float, radius: float, mode: str,
              origin: Optional[Mapping] = None) -> Card:
    ld = idx.lines[line]
    score, parts = _score_parts(idx, line, pos, dist, radius, mode)
    minor = ld.minor_flags.get(pos, [])
    prec = float(idx.precision_km[pos])
    if mode == "probability":
        conf = _confidence_prob(ld, pos, len(minor), prec, S.p_threshold(line))
        prob = float(ld.p[pos])
    else:
        conf = _confidence_v0(int(ld.grade[pos]), ld.strength[pos], len(minor), prec, int(ld.n_support[pos]))
        prob = None
    could = _could_be_wrong(idx, line, pos, ld.strength[pos], ld.missing[pos], minor, prob)
    return _make_card(idx, pos, dist, line=line, score=score, parts=parts, confidence=conf,
                      evidence=ld.evidence[pos], missing=ld.missing[pos], could=could, radius=radius,
                      origin=origin)


def _unverified_card(idx: Index, line: str, pos: int, dist: float, radius: float, mode: str,
                     origin: Optional[Mapping] = None) -> Card:
    ld = idx.lines[line]
    major = ld.major_flags.get(pos, [])
    g = int(ld.grade[pos])
    if major:
        reason = "Not recommended: " + (major[0].get("reason") or "a blocking warning on the record.")
    elif mode == "probability" and ld.p is not None and g >= 2:
        reason = (f"Not recommended: an estimated {float(ld.p[pos]):.0%} chance an independent source confirms it, "
                  f"below the bar of {S.p_threshold(line):.0%}.")
    else:
        reason = "Claim not supported by the record: " + (
            f"no fact names {ld.missing[pos][0]}." if ld.missing[pos] else "no fact names the service.")
    could = " ".join(f["reason"] for f in (major + ld.minor_flags.get(pos, []))[:2] if f.get("reason")) or \
        "Only a claim: nothing in the record shows the service is offered."
    return _make_card(idx, pos, dist, line=line, score=None, parts={}, confidence=("low", reason),
                      evidence=ld.evidence[pos], missing=ld.missing[pos], could=could, radius=radius,
                      origin=origin)


def _base_answer(service: str, label: str, radius: float, method: str, res) -> Optional[Answer]:
    loc = _location_dict(res)
    st = loc["status"]
    if st == "ambiguous":
        return Answer("needs_location_choice", service, label, radius, method, loc,
                      how_we_decided={"headline": "That place name matches more than one place. Pick one to continue.",
                                      "notes": ["We never pick between same-named places silently."]})
    if st in ("not_found", "outside_india") or loc["lat"] is None or loc["lon"] is None:
        msg = loc.get("how") or "We could not find that place."
        return Answer("location_not_found", service, label, radius, method, loc,
                      how_we_decided={"headline": msg})
    return None


def _rank_v0(index: Index, service: str, resolution, k: int = 3) -> Answer:
    """Legacy ranker (D027 additive score, or D039 probability mode), kept for
    the v0 against v1 comparison. Top ``k`` recommendable facilities for
    ``service`` near ``resolution``, or an abstention."""
    if service not in S.SERVICES:
        raise ValueError(f"unknown service line {service!r}; free text goes to search()")
    svc = S.SERVICES[service]
    R = float(svc.radius_km)
    early = _base_answer(service, svc.label, R, "recipe", resolution)
    if early is not None:
        return early
    if service not in index.lines:
        ans = search(index, svc.keywords, resolution, k=k, radius_km=R)
        ans.service, ans.service_label = service, svc.label
        ans.how_we_decided.setdefault("notes", []).insert(
            0, f"No recipe grades are loaded for {svc.label.lower()} yet (recipe {index.recipe_version} covers "
               f"{', '.join(S.label(l).lower() for l in index.graded_lines)}). These are keyword matches only.")
        return ans

    ld = index.lines[service]
    mode = "probability" if ld.p is not None else "v0"
    loc = _location_dict(resolution)
    d = _distances(index, loc["lat"], loc["lon"])
    usable = ~np.isnan(d)
    reach_km = S.REACH_FACTOR * R
    if mode == "probability":
        # grade 2+ stays a gate: a claim-only record has no fact to quote (D027, D030)
        claim = (ld.grade >= 1) | (ld.p > 0)
        rec_mask = (ld.grade >= 2) & (ld.p >= S.p_threshold(service)) & ~ld.blocked
    else:
        claim = ld.grade >= 1
        rec_mask = (ld.grade >= 2) & ~ld.blocked
    in_reach = usable & (d <= reach_km) & claim
    rec = in_reach & rec_mask
    rec_idx = np.flatnonzero(rec)
    n_rec_records = int(len(rec_idx))
    n_rec = _n_distinct(index, rec_idx)  # one per duplicate group, uncapped
    rec_in_R_mask = rec & (d <= R)
    rec_in_R = int(rec_in_R_mask.sum())  # uncapped (the cap below only limits scoring)
    n_rec_in_R = _n_distinct(index, np.flatnonzero(rec_in_R_mask))
    rec_idx = rec_idx[np.argsort(d[rec_idx], kind="stable")][: S.MAX_CANDIDATES]
    capped = n_rec_records > len(rec_idx)

    # unverified nearby: claim-only or blocked inside the radius
    unv_mask = usable & (d <= R) & claim & ~rec_mask
    unv_idx = np.flatnonzero(unv_mask)
    unv_idx = [int(p) for p in unv_idx[np.argsort(d[unv_idx], kind="stable")]]

    def unverified_for(shown: list) -> list:
        """Nearest first, one per duplicate group, none from a group already shown as a card."""
        taken = {index.dup_canon.get(p, -1 - p) for p in shown}
        out = []
        for p in unv_idx:
            g_ = index.dup_canon.get(p, -1 - p)
            if g_ in taken:
                continue
            taken.add(g_)
            out.append(_unverified_card(index, service, p, float(d[p]), R, mode, origin=loc))
            if len(out) >= S.MAX_UNVERIFIED:
                break
        return out

    gates = [
        {"gate": "usable location", "rule": "the facility has a location we can use", "n": int(usable.sum()),
         "of": int(len(d))},
        {"gate": "within reach", "rule": f"claims the service and is within {reach_km:.0f} km (3 x the {R:.0f} km radius)",
         "n": int(in_reach.sum())},
        {"gate": "recommendable",
         "rule": ("supported or corroborated by facts in the record, estimated chance of independent "
                  f"confirmation at or above {S.p_threshold(service):.0%}, and no blocking warning") if mode == "probability"
         else "supported or corroborated by facts in the record, and no blocking warning",
         "n": n_rec_records, "n_distinct": n_rec, "n_blocked": int((in_reach & ld.blocked).sum()),
         "n_scored": int(len(rec_idx)), "max_scored": S.MAX_CANDIDATES},
        {"gate": "within radius", "rule": f"at least one recommendable facility within {R:.0f} km, else no reliable option nearby",
         "n": rec_in_R, "n_distinct": n_rec_in_R},
    ]
    how = {
        "mode": mode,
        "score_status": "calibrated" if mode == "probability" else "provisional",
        "service": svc.label, "radius_km": R, "reach_km": reach_km,
        "recipe": svc.recipe_text, "recipe_version": index.recipe_version, "grades_method": index.grades_method,
        "gates": gates,
        "formula": S.SCORE_FORMULA_PROB if mode == "probability" else S.SCORE_FORMULA_V0,
        "weights": None if mode == "probability" else dict(S.WEIGHTS),
        "penalties": None if mode == "probability" else dict(S.PENALTIES),
        "tie_rule": f"Scores within {S.TIE_EPS} are ties, broken by distance.",
        "order_rule": S.ORDER_RULE,
        "flags_source": index.flags_source,
        "limits": list(S.LIMITS),
        "notes": [],
    }
    if mode == "v0":
        how["notes"].append("The score is provisional: its weights, radii and penalties are stated defaults, not measured.")
    else:
        how["threshold"] = S.p_threshold(service)
        how["model_version"] = ld.model_version
    if index.flags_source == "none":
        how["notes"].append("No trust checks are loaded: no record is flagged, so merged records and location conflicts are not caught.")

    base = dict(service=service, service_label=svc.label, radius_km=R, method="recipe", location=loc)
    if rec_in_R == 0:
        far = np.flatnonzero(usable & rec_mask & (d > R))
        beyond = None
        if len(far):
            p = int(far[np.argmin(d[far])])
            beyond = _rec_card(index, service, p, float(d[p]), R, mode, origin=loc)
            beyond.why_here = (f"The nearest recommendable facility, "
                               f"{_fmt_km(d[p], _eff_precision(beyond.location_precision_km, loc))} away, "
                               f"beyond the {R:.0f} km radius.")
        unverified = unverified_for([])
        how["headline"] = (f"No reliable option nearby: no facility in this dataset shows reliable evidence of "
                           f"{svc.label.lower()} within {R:.0f} km. This does not mean there is none; the data may be missing it.")
        return Answer("no_reliable_option", results=[], beyond_radius=beyond, unverified_nearby=unverified,
                      how_we_decided=how, **base)

    # score, then one card per duplicate group (WP11 pairs joined into groups)
    cand = [int(p) for p in rec_idx]
    sc, parts, dist = {}, {}, {}
    for p in cand:
        sc[p], parts[p] = _score_parts(index, service, p, float(d[p]), R, mode)
        dist[p] = float(d[p])
    # inside the radius first, then by score (S.ORDER_RULE): a corroborated
    # record at 2.5 x the radius must not push every in-radius option off the cards
    tier = {p: 0 if dist[p] <= R else 1 for p in cand}
    order = sorted(cand, key=lambda p: (tier[p], -sc[p], dist[p]))
    order = _dedupe(index, order, tier)
    order = _break_ties(order, sc, dist, tier)
    shown = order[: k + 1]
    cards = [_rec_card(index, service, p, dist[p], R, mode, origin=loc) for p in shown]
    for i in range(min(k, len(cards))):
        nxt = shown[i + 1] if i + 1 < len(shown) else None
        cards[i].why_here = _why_here(cards[i], cards[i + 1] if nxt is not None else None, n_rec, mode,
                                      radius=R, a_in=tier[shown[i]] == 0,
                                      b_in=tier[nxt] == 0 if nxt is not None else True, origin=loc)
    n_show = min(k, len(order))
    inside = (f"{n_rec_in_R} of them {'is' if n_rec_in_R == 1 else 'are'} inside the {R:.0f} km radius "
              f"and {'comes' if n_rec_in_R == 1 else 'come'} first")
    how["headline"] = (f"{n_show} of {n_rec} recommendable facilities within {reach_km:.0f} km, best first; "
                       f"{inside}." + (f" Only the nearest {S.MAX_CANDIDATES} were scored." if capped else ""))
    unverified = unverified_for(shown[:k])
    return Answer("ok", results=cards[:k], beyond_radius=None, unverified_nearby=unverified, how_we_decided=how, **base)


# ---------------------------------------------------------------------------
# rank: score spec v1-final (D048)
# ---------------------------------------------------------------------------

@dataclass
class _V1Ctx:
    """Everything one v1 query needs, computed once (``_v1_context``)."""
    service: str
    W: float  # window, minutes
    A: float  # evidence allowance, minutes
    factor: T.Factor
    sea: bool  # no minutes: km fallback on the old radius
    d: np.ndarray  # straight-line km to every facility
    t: Optional[np.ndarray]  # estimated minutes (None when sea)
    R: float  # straight-line km equivalent of the window (or the old radius when sea)
    reach_km: float  # km equivalent of REACH_FACTOR_V1 x the window
    within: np.ndarray  # bool: inside the window
    in_reach_pt: np.ndarray  # bool: within reach (distance only)
    o_prec: Optional[float]
    loose: bool  # origin too loose for one number (D048)
    place: str
    #: the searched place is wider (2 x its precision) than the window reaches in
    #: straight-line km: which facilities are inside the window depends on where the
    #: trip starts, and a drive-time range would run from minutes to hours (fix round, Nov 22)
    too_loose: bool = False
    #: warnings that deny the allowance (``S.ALLOWANCE_DENY_FLAGS``; empty for the sensitivity check)
    deny: frozenset = S.ALLOWANCE_DENY_FLAGS


def _label_lc(service: str) -> str:
    """Display name mid-sentence: "emergency C-section and high-risk delivery", "MRI scan"."""
    lab = S.label(service)
    return lab if lab[1:2].isupper() else lab[:1].lower() + lab[1:]


def _place_name(loc: Mapping) -> str:
    """Short name of the searched place for sentences: 'Jaipur', 'PIN 302001'."""
    if loc.get("kind") == "coords":
        return "the point you gave"
    lab = _str_or_none(loc.get("label")) or _str_or_none(loc.get("query")) or "the place you searched"
    first = re.sub(r"\s*\([^)]*\)", "", lab).split(",")[0].strip()
    first = re.sub(r"(?i)^(the\s+)?centre of\s+", "", first)
    first = re.sub(r"(?i)\s+(district|city)$", "", first).strip()
    return first or lab


def _v1_context(index: Index, service: str, loc: Mapping, *, window_scale: float = 1.0,
                allowance: Optional[float] = None, terrain: bool = True,
                d: Optional[np.ndarray] = None, deny_flags: bool = True) -> _V1Ctx:
    w = S.WINDOWS[service]
    W = float(w.minutes) * float(window_scale)
    A = float(w.allowance if allowance is None else allowance)
    fac = T.origin_factor(loc, index.state_at if index.pin_lat is not None else None, terrain=terrain)
    if d is None:
        d = _distances(index, loc["lat"], loc["lon"])
    o_prec = _origin_precision(loc)
    if fac.sea:
        R = float(S.SERVICES[service].radius_km)
        reach = S.REACH_FACTOR * R
        t = None
        within = d <= R
        in_reach = d <= reach
        loose = False
    else:
        f = float(fac.value)
        R = T.window_km(W, factor=f)
        reach = T.window_km(S.REACH_FACTOR_V1 * W, factor=f)
        with np.errstate(invalid="ignore"):
            t = f * T.A_MIN * np.power(np.maximum(d, 0.0), T.B_EXP)
            within = t <= W
            in_reach = t <= S.REACH_FACTOR_V1 * W
        loose = T.origin_is_loose(o_prec, W, f)
    too_loose = bool(not fac.sea and o_prec is not None and 2.0 * float(o_prec) > float(R))
    return _V1Ctx(service, W, A, fac, fac.sea, d, t, float(R), float(reach), np.asarray(within, bool),
                  np.asarray(in_reach, bool), o_prec, loose, _place_name(loc), too_loose,
                  S.ALLOWANCE_DENY_FLAGS if deny_flags else frozenset())


def _v1_sets(index: Index, ctx: _V1Ctx) -> dict:
    """The gate masks and the candidate set, in the same dict shape the legacy
    ranker's mirror in ``vf.why_not`` uses. ``scored`` maps each candidate to its
    tier (0 inside the window, 1 beyond); empty when nothing recommendable is
    inside the window."""
    ld = index.lines[ctx.service]
    d = ctx.d
    usable = ~np.isnan(d)
    claim = ld.grade >= 1
    rec_mask = (ld.grade >= 2) & ~ld.blocked
    in_reach = usable & ctx.in_reach_pt & claim
    rec = in_reach & rec_mask
    rec_all = np.flatnonzero(rec)
    rec_all = rec_all[np.argsort(d[rec_all], kind="stable")]
    in_W = rec_all[ctx.within[rec_all]]
    # the nearest MAX_CANDIDATES only, as in v0 and the why-not strip; when a corroborated record
    # beyond the cap would have come first under the allowance, the answer says so
    # (``_cap_exception``; reports/ordering_behaviour.md: 16 of 18,000 line-origin queries)
    cand = [int(p) for p in rec_all[: S.MAX_CANDIDATES]]
    scored = {} if len(in_W) == 0 else {p: 0 if bool(ctx.within[p]) else 1 for p in cand}
    return {"R": ctx.R, "reach": ctx.reach_km, "usable": usable, "prob": False, "claim": claim,
            "rec_mask": rec_mask, "in_reach": in_reach, "rec": rec, "rec_all": rec_all, "in_R": in_W,
            "scored": scored, "version": "v1", "window_min": ctx.W, "sea": ctx.sea,
            "labels": _v1_funnel_labels(ctx)}


def _v1_funnel_labels(ctx: _V1Ctx) -> dict:
    """Wording for the why_not funnel's steps under v1."""
    label = _label_lc(ctx.service)
    if ctx.sea:
        return {
            "claim_within_reach": f"List or claim {label} and are within {ctx.reach_km:.0f} km",
            "beyond_reach": f"Lists or claims {label}, but more than {ctx.reach_km:.0f} km away "
                            f"(3 x the {ctx.R:.0f} km radius; sea travel is not modelled).",
            "within_radius": f"Within the {ctx.R:.0f} km radius (sea travel is not modelled)",
            "beyond_radius": f"Recommendable, but more than {ctx.R:.0f} km away.",
        }
    reach_m = S.REACH_FACTOR_V1 * ctx.W
    return {
        "claim_within_reach": f"List or claim {label} and are within about {T.fmt_minutes(reach_m)} estimated drive",
        "beyond_reach": f"Lists or claims {label}, but more than about {T.fmt_minutes(reach_m)} estimated drive "
                        f"away (3 x the {ctx.W:.0f}-minute window).",
        "within_radius": f"Within the {ctx.W:.0f}-minute window (estimated drive, free-flow; about "
                         f"{ctx.R:.0f} km straight-line from here)",
        "beyond_radius": f"Recommendable, but more than about {T.fmt_minutes(ctx.W)} estimated drive away "
                         f"(the {ctx.W:.0f}-minute window).",
    }


def gate_masks(index: Index, line: str, d: np.ndarray, resolution=None) -> dict:
    """The ranker's gates for one query, as ``vf.why_not`` mirrors them:
    ``{R, reach, usable, prob, claim, rec_mask, in_reach, rec, rec_all, in_R,
    scored, version, ...}``. Under v1, ``R`` and ``reach`` are the straight-line
    km equivalents of the window and of 3 x the window for this origin."""
    if index.rank_version == "v1":
        loc = _location_dict(resolution) if resolution is not None else {}
        return _v1_sets(index, _v1_context(index, line, loc, d=d))
    ld = index.lines[line]
    R = float(S.SERVICES[line].radius_km)
    reach = float(S.REACH_FACTOR) * R
    usable = ~np.isnan(d)
    prob = ld.p is not None
    claim = ((ld.grade >= 1) | (ld.p > 0)) if prob else (ld.grade >= 1)
    rec_mask = (ld.grade >= 2) & ~ld.blocked
    if prob:
        rec_mask &= ld.p >= S.p_threshold(line)
    in_reach = usable & (d <= reach) & claim
    rec = in_reach & rec_mask
    rec_all = np.flatnonzero(rec)
    rec_all = rec_all[np.argsort(d[rec_all], kind="stable")]
    in_R = rec_all[d[rec_all] <= R]
    scored = {} if len(in_R) == 0 else {int(p): 0 if d[p] <= R else 1 for p in rec_all[: S.MAX_CANDIDATES]}
    return {"R": R, "reach": reach, "usable": usable, "prob": prob, "claim": claim, "rec_mask": rec_mask,
            "in_reach": in_reach, "rec": rec, "rec_all": rec_all, "in_R": in_R, "scored": scored, "version": "v0"}


def _allowance_denied_by(index: Index, line: str, p: int, deny: Optional[frozenset] = None) -> list:
    """The warnings on this record (for this line, or the whole record) that deny it
    the evidence allowance: possible merge, location conflict (``S.ALLOWANCE_DENY_FLAGS``)."""
    deny = S.ALLOWANCE_DENY_FLAGS if deny is None else deny
    out = []
    for f in index.lines[line].minor_flags.get(p, []):
        name = str(f.get("flag") or "")
        if name in deny and name.replace("_", " ") not in out:
            out.append(name.replace("_", " "))
    return out


def _gets_allowance(index: Index, line: str, p: int, deny: Optional[frozenset] = None) -> bool:
    return int(index.lines[line].grade[p]) >= 3 and not _allowance_denied_by(index, line, p, deny)


def _order_key(index: Index, ctx: _V1Ctx, p: int) -> tuple:
    """Nearest first, with the allowance taken off a corroborated record's minutes
    (not for a record with a possible-merge or location-conflict warning)."""
    g3 = _gets_allowance(index, ctx.service, p, ctx.deny)
    if ctx.sea:
        return (float(ctx.d[p]), 0, float(ctx.d[p]), float(ctx.d[p]))
    t = float(ctx.t[p])
    # "at most the allowance further": at exactly the allowance the corroborated record comes first
    return (round(t - (ctx.A if g3 else 0.0), 9), 0 if g3 else 1, t, float(ctx.d[p]))


def order_v1(index: Index, ctx: _V1Ctx, sets: Optional[dict] = None) -> list:
    """Window candidates in card order, one per duplicate group."""
    sets = sets or _v1_sets(index, ctx)
    cand = [p for p, tier in sets["scored"].items() if tier == 0]
    order = sorted(cand, key=lambda p: _order_key(index, ctx, p))
    return _dedupe(index, order)


def _cap_exception(index: Index, ctx: _V1Ctx, sets: dict, shown: list) -> list:
    """In-window records beyond the MAX_CANDIDATES cap whose order key beats the
    last shown card's: the allowance would have listed them, but the cap left them
    out. Usually empty."""
    in_W = sets["in_R"]
    if len(in_W) <= S.MAX_CANDIDATES or not shown or ctx.sea or ctx.A <= 0:
        return []
    bar = max(_order_key(index, ctx, p)[0] for p in shown)
    return [int(p) for p in in_W[S.MAX_CANDIDATES:] if _order_key(index, ctx, int(p))[0] < bar]


def _canon(index: Index, p: int):
    return index.dup_canon.get(int(p), -1 - int(p))


def shown_v1(index: Index, order: list, in_W, k: int) -> tuple[list, Optional[int], bool]:
    """The cards shown from ``order``: the first ``k``, except that the nearest
    recommendable facility in the window is always shown (D048 Order). When the
    allowance pushes it past ``k``, it takes the last place. ``k`` = 1 asks for the
    top pick alone (evaluation, sensitivity), so nothing is reserved there.
    Returns ``(shown, nearest_pos, reserved)``."""
    if not order or len(in_W) == 0:
        return list(order[:k]), None, False
    g0 = _canon(index, in_W[0])  # in_W is nearest first
    near = next((p for p in order if _canon(index, p) == g0), None)
    top = list(order[:k])
    if near is None or near in top or k < 2:
        return top, near, False
    return top[: k - 1] + [near], near, True


def pick_v1(index: Index, service: str, lat: float, lon: float, *, state: Optional[str] = None,
            window_scale: float = 1.0, allowance: Optional[float] = None, terrain: bool = True,
            d: Optional[np.ndarray] = None, deny_flags: bool = True) -> dict:
    """The v1 top pick for one origin without building cards (sensitivity runs):
    ``{top, nearest, n_in_window, top_minutes, nearest_minutes, sea}``; ``top`` is
    None when the answer would abstain."""
    loc = {"lat": lat, "lon": lon, "state": state, "precision_km": None, "label": ""}
    ctx = _v1_context(index, service, loc, window_scale=window_scale, allowance=allowance, terrain=terrain, d=d,
                      deny_flags=deny_flags)
    sets = _v1_sets(index, ctx)
    order = order_v1(index, ctx, sets)
    in_W = sets["in_R"]
    top = order[0] if order else None
    near = int(in_W[0]) if len(in_W) else None
    shown3, _, reserved = shown_v1(index, order, in_W, 3)
    mins = (lambda p: None if p is None or ctx.t is None else float(ctx.t[p]))
    return {"top": top, "nearest": near, "n_in_window": int(len(in_W)), "top_minutes": mins(top),
            "nearest_minutes": mins(near), "sea": ctx.sea, "factor": ctx.factor.value,
            "shown3": [int(p) for p in shown3], "nearest_reserved": reserved}


def _merge_evidence(items: list) -> list:
    """One row per quoted fact: a fact that meets two requirements is shown once,
    with both requirements and the note "one fact covers both requirements" (D046)."""
    out, by_key = [], {}
    for it in items:
        key = (it.get("fact_id"), it.get("quote"), it.get("field"))
        if it.get("quote") is None or key not in by_key:
            row = dict(it)
            row["requirements"] = [it.get("requirement")]
            by_key.setdefault(key, row)
            out.append(row)
            continue
        row = by_key[key]
        if it.get("requirement") not in row["requirements"]:
            row["requirements"].append(it.get("requirement"))
    for row in out:
        reqs = [r for r in row["requirements"] if r]
        if len(reqs) > 1:
            row["requirement"] = "; ".join(reqs)
            row["note"] = ("one fact covers both requirements" if len(reqs) == 2
                           else f"one fact covers all {len(reqs)} requirements")
    return out


def _v1_distance_label(index: Index, pos: int, km: float, o_prec: Optional[float]) -> tuple[str, bool]:
    approx = bool(index.approx[pos]) if index.approx is not None else False
    if km is None or math.isnan(km):
        return "unknown distance", approx
    if approx:
        return APPROX_LABEL, True
    if o_prec is not None and o_prec > S.ORIGIN_FUZZY_KM:
        r = int(round(km))
        return ("under 1 km straight-line" if r < 1 else f"about {r} km straight-line"), False
    if km < 0.05:  # never "0.0 km" (D046)
        return "less than 0.1 km straight-line", False
    return f"{km:.1f} km straight-line", False


def _v1_fill(card: Card, index: Index, ctx: _V1Ctx, pos: int) -> Card:
    """The v1 fields on a card built by ``_make_card``."""
    km = float(ctx.d[pos])
    card.distance_label, card.position_approximate = _v1_distance_label(index, pos, km, ctx.o_prec)
    card.window_min = None if ctx.sea else ctx.W
    card.inside_window = bool(ctx.within[pos])
    if ctx.sea:
        card.minutes = card.minutes_low = card.minutes_high = None
        card.minutes_label = T.minutes_label(None, None, None, sea=True)
    else:
        t, lo, hi = T.minutes_range(km, float(ctx.factor.value), ctx.o_prec, ctx.loose)
        card.minutes = round(t, 1)
        card.minutes_low, card.minutes_high = round(lo, 1), round(hi, 1)
        card.minutes_label = T.minutes_label(t, lo, hi, loose=ctx.loose, place=ctx.place,
                                             approximate=card.position_approximate,
                                             span_km=(2 * ctx.o_prec) if ctx.too_loose else None)
        card.window_uncertain = bool(ctx.loose and lo <= ctx.W < hi)
    if not card.inside_window:
        card.location_note = (card.location_note + (
            f" Beyond the {ctx.R:.0f} km radius used where sea travel is not modelled." if ctx.sea else
            f" Beyond the {ctx.W:.0f}-minute window for this service.")).strip()
    if card.position_approximate and index.approx is not None:
        card.location_note = (card.location_note + " Its map point is shared with other records or is its PIN "
                              "code's centre, so the distance is approximate.").strip()
    card.evidence = _merge_evidence(card.evidence)
    ld = index.lines.get(ctx.service)
    if ld is not None and ld.level_note is not None:
        card.level_note = ld.level_note[pos]
    return card


def _v1_parts(index: Index, ctx: _V1Ctx, pos: int) -> dict:
    g3 = _gets_allowance(index, ctx.service, pos, ctx.deny)
    denied = _allowance_denied_by(index, ctx.service, pos, ctx.deny) \
        if int(index.lines[ctx.service].grade[pos]) >= 3 else []
    key = _order_key(index, ctx, pos)
    return {"combine": "v1", "penalties": [], "allowance_denied_by": denied,
            "minutes": None if ctx.sea else round(float(ctx.t[pos]), 2),
            "order_minutes": None if ctx.sea else round(key[0], 2),
            "allowance_min": 0.0 if ctx.sea else ctx.A, "allowance_applies": bool(g3 and not ctx.sea and ctx.A > 0),
            "km": round(float(ctx.d[pos]), 2),
            "labels": {"minutes": "estimated drive time, free-flow", "order_minutes":
                       "minutes used for the order (minus the allowance for a corroborated record)"}}


def _v1_card(index: Index, ctx: _V1Ctx, pos: int, loc: Mapping) -> Card:
    ld = index.lines[ctx.service]
    minor = ld.minor_flags.get(pos, [])
    prec = float(index.precision_km[pos])
    conf = (None, _evidence_note_v1(int(ld.grade[pos]), ld.strength[pos], len(minor), prec, int(ld.n_support[pos])))
    could = _could_be_wrong(index, ctx.service, pos, ld.strength[pos], ld.missing[pos], minor, None)
    card = _make_card(index, pos, float(ctx.d[pos]), line=ctx.service, score=None, parts=_v1_parts(index, ctx, pos),
                      confidence=conf, evidence=ld.evidence[pos], missing=ld.missing[pos], could=could,
                      radius=None, origin=loc)
    return _v1_fill(card, index, ctx, pos)


def _v1_unverified_card(index: Index, ctx: _V1Ctx, pos: int, loc: Mapping) -> Card:
    card = _unverified_card(index, ctx.service, pos, float(ctx.d[pos]), ctx.R, "v0", origin=loc)
    card.location_note = re.sub(r"\s*Beyond the usual \d+ km for this service\.", "", card.location_note)
    card.confidence = None
    return _v1_fill(card, index, ctx, pos)


def _short_grade(c: Card) -> str:
    return (c.grade_label or "").split(" (")[0]


def _round_min(m: float) -> int:
    """Minutes as ``vf.travel.fmt_minutes`` shows them (1 min under 10, else 5)."""
    m = float(m)
    return max(int(round(m)) if m < 10 else int(5 * round(m / 5)), 1)


def _denied_phrase(c: Card) -> str:
    flags = (c.parts or {}).get("allowance_denied_by") or []
    return " and ".join(flags) + (" warning" if len(flags) == 1 else " warnings")


def _why_here_v1(a: Card, b: Optional[Card], ctx: _V1Ctx, n_in: int) -> str:
    """One sentence on why ``a`` is above ``b``. Times are compared as the cards
    show them (rounded), so the stated difference matches the two shown times."""
    if b is None:
        if n_in <= 1:
            return ("The only recommendable facility within the radius." if ctx.sea else
                    f"The only recommendable facility within the {ctx.W:.0f}-minute window.")
        return f"Last of the {min(n_in, 3)} shown; {n_in} recommendable facilities are within the window."
    if ctx.sea or a.minutes is None or b.minutes is None:
        return f"Listed above {b.name}: closer ({a.distance_label} against {b.distance_label})."
    ta, tb = float(a.parts["minutes"]), float(b.parts["minutes"])
    if ctx.too_loose:  # the cards show no minutes here: compare in km from the resolved point
        frm = (f"from the point {ctx.place} resolved to ({_km_phrase(a)} against {_km_phrase(b)}, "
               "straight-line)")
        if a.parts.get("allowance_applies") and not b.parts.get("allowance_applies") and ta > tb + 0.5:
            return (f"Listed above {b.name}: corroborated, and within the {ctx.A:.0f}-minute evidence allowance "
                    f"measured {frm}.")
        return f"Listed above {b.name}: closer {frm}."
    fa, fb = T.fmt_minutes(ta), T.fmt_minutes(tb)
    diff = _round_min(ta) - _round_min(tb)
    a_allow, b_allow = bool(a.parts.get("allowance_applies")), bool(b.parts.get("allowance_applies"))
    b_denied = (b.grade or 0) >= 3 and not b_allow
    if a_allow and not b_allow and ta > tb + 0.5:
        how_far = (f"only about {T.fmt_minutes(diff)} further (about {fa} against {fb})" if diff > 0 else
                   f"only slightly further (both about {fa})")
        s = f"Listed above {b.name}: corroborated, and {how_far}, within the {ctx.A:.0f}-minute evidence allowance."
        if b_denied:
            s += f" The allowance does not apply to {b.name}, which carries a {_denied_phrase(b)}."
        return s
    if abs(ta - tb) <= 0.5:
        if a_allow and not b_allow and ta > tb:
            return (f"Same estimated drive time as {b.name} (about {fa}); listed first because its record is "
                    f"corroborated, within the {ctx.A:.0f}-minute evidence allowance.")
        if a.distance_km is not None and b.distance_km is not None and a.distance_km <= b.distance_km:
            return f"Same estimated drive time as {b.name} (about {fa}); listed first because it is closer in straight-line km."
        if ta <= tb:
            return f"Same estimated drive time as {b.name} (about {fa}); listed first because it is slightly nearer."
        return f"Same estimated drive time as {b.name} (about {fa}); listed first by the order rule."
    nearer = f"slightly nearer (both about {fa})" if diff == 0 else f"nearer (about {fa} against {fb})"
    if b_denied:
        return (f"Listed above {b.name}: {nearer}. The evidence allowance does not apply to "
                f"{b.name}, which carries a {_denied_phrase(b)}.")
    if b_allow and not a_allow:
        return (f"Listed above {b.name}: more than {ctx.A:.0f} minutes nearer (about {fa} against {fb}), so the "
                f"evidence allowance does not move the corroborated record ahead.")
    return (f"Listed above {b.name}: slightly nearer (both about {fa} estimated drive)." if diff == 0 else
            f"Listed above {b.name}: nearer (about {fa} against {fb} estimated drive).")


def _km_phrase(c: Card) -> str:
    lab = c.distance_label or ""
    return "an approximate position" if lab == APPROX_LABEL else lab.replace(" straight-line", "")


def _why_nearest_v1(c: Card, ctx: _V1Ctx) -> str:
    what = (f"about {T.fmt_minutes(c.minutes)} estimated drive" if c.minutes is not None and not ctx.too_loose
            else c.distance_label)
    return (f"The nearest recommendable facility ({what}), always shown. The cards above it are corroborated "
            f"records the {ctx.A:.0f}-minute evidence allowance lists first.")


def _too_loose_text(ctx: _V1Ctx) -> str:
    return (f"Uncertain: {ctx.place} spans about {2 * ctx.o_prec:.0f} km, more than the {ctx.W:.0f}-minute window "
            f"reaches (about {ctx.R:.0f} km straight-line), so which facilities are inside the window depends on "
            "where you start. Search a PIN code, post office or town for a firm answer.")


def _hours_phrase(W: float) -> str:
    """'2 hours'', '1 hour's', '1.5 hours'' (possessive, before "estimated drive")."""
    h = T.fmt_hours(W)
    if h.endswith("hours"):
        return h + "'"
    if h.endswith("hour"):
        return h + "'s"
    return h


def _rank_v1(index: Index, service: str, resolution, k: int = 3, *, window_scale: float = 1.0,
             allowance: Optional[float] = None, terrain: bool = True) -> Answer:
    svc = S.SERVICES[service]
    loc = _location_dict(resolution)
    ctx = _v1_context(index, service, loc, window_scale=window_scale, allowance=allowance, terrain=terrain)
    sets = _v1_sets(index, ctx)
    ld = index.lines[service]
    d = ctx.d
    usable, claim, rec_mask = sets["usable"], sets["claim"], sets["rec_mask"]
    in_reach, rec, rec_all, in_W = sets["in_reach"], sets["rec"], sets["rec_all"], sets["in_R"]
    n_rec = _n_distinct(index, rec_all)
    n_in = _n_distinct(index, in_W)
    w = S.WINDOWS[service]
    hours = _hours_phrase(ctx.W)
    win = {**w.as_dict(), "minutes": ctx.W, "allowance": ctx.A, "km_equivalent": round(ctx.R, 1),
           "reach_minutes": S.REACH_FACTOR_V1 * ctx.W, "reach_km": round(ctx.reach_km, 1),
           "factor": ctx.factor.value, "factor_text": ctx.factor.text, "state": ctx.factor.state,
           "sea_travel_not_modelled": ctx.sea, "origin_loose": ctx.loose, "origin_too_loose": ctx.too_loose,
           "origin_precision_km": None if ctx.o_prec is None else round(float(ctx.o_prec), 1),
           "note": S.KM_FALLBACK_NOTE if ctx.sea else S.WINDOW_NOTE}
    if ctx.sea:
        g3_rule = (f"within the {ctx.R:.0f} km straight-line radius: sea travel is not modelled here, so no drive "
                   "time is estimated")
        reach_rule = f"claims the service and is within {ctx.reach_km:.0f} km (3 x the {ctx.R:.0f} km radius)"
    else:
        g3_rule = (f"estimated drive time within the {ctx.W:.0f}-minute window ({w.kind}; about {ctx.R:.0f} km "
                   f"straight-line from here at {ctx.factor.text})")
        reach_rule = (f"claims the service and is within about {T.fmt_minutes(S.REACH_FACTOR_V1 * ctx.W)} estimated "
                      f"drive (3 x the window), the search limit for the further-away line")
    gates = [
        {"gate": "usable location", "id": "G1", "rule": "the facility has a location we can use",
         "n": int(usable.sum()), "of": int(len(d))},
        {"gate": "within reach", "rule": reach_rule, "n": int(in_reach.sum())},
        {"gate": "recommendable", "id": "G2",
         "rule": f"grade 2 or 3 under recipe {index.recipe_version} (a fact in the record names the service) and no "
                 "major warning for this service",
         "n": int(rec.sum()), "n_distinct": n_rec, "n_blocked": int((in_reach & ld.blocked).sum()),
         "n_scored": int(len(sets["scored"])), "max_scored": S.MAX_CANDIDATES},
        {"gate": "within radius", "id": "G3", "rule": g3_rule, "n": int(len(in_W)), "n_distinct": n_in,
         "window_min": None if ctx.sea else ctx.W, "km_equivalent": round(ctx.R, 1)},
    ]
    order_rule = S.ORDER_RULE_V1.format(allowance=ctx.A)
    how = {
        "mode": "v1", "rank_version": "v1", "ranker": RANK_V1_VERSION,
        "score_status": "v1-final: gates and an order rule, no score (D048)",
        "service": svc.label, "radius_km": round(ctx.R, 1), "reach_km": round(ctx.reach_km, 1),
        "window": win,
        "windows": {l: S.WINDOWS[l].as_dict() for l in S.SERVICE_LINES},
        "allowance_min": ctx.A,
        "travel_model": {"text": T.MODEL_TEXT, "a": T.A_MIN, "b": T.B_EXP, "log_sd": T.LOG_SD,
                         "band_log": T.BAND_LOG, "band_coverage": T.BAND_COVERAGE, "n_trips": T.N_TRIPS,
                         "factor": ctx.factor.value, "factor_text": ctx.factor.text, "version": T.TRAVEL_VERSION},
        "recipe": S.recipe_text(service, index.recipe_version), "recipe_version": index.recipe_version,
        "recipe_citations": dict(S.RECIPE_V03_CITATIONS) if index.recipe_version == "v0.3" else {},
        "grades_method": index.grades_method, "grades_source": index.grades_source,
        "grades_status": index.grades_status, "tagger": S.TAGGER_MEASURED,
        "gates": gates,
        "formula": order_rule, "order_rule": order_rule,
        "tie_rule": "Equal estimated minutes are ties, listed by straight-line km; never by warnings.",
        "weights": None, "penalties": None,
        "probability": S.PROBABILITY_NOTE,
        "flags_source": index.flags_source,
        "limits": list(S.LIMITS_V1),
        "notes": [],
    }
    if ctx.too_loose:
        how["notes"].append(_too_loose_text(ctx))
    elif ctx.loose:
        how["notes"].append(f"{ctx.place} is known only to within about {ctx.o_prec:.0f} km, so each drive time is "
                            f"a range: it depends on where in {ctx.place} you start.")
    if ctx.factor.source.startswith("state unknown"):
        how["notes"].append("The searched point's state is not known, so no terrain factor is applied (1.00).")
    if index.recipe_version != "v0.3":
        how["notes"].append(index.grades_status)
    if index.flags_source == "none":
        how["notes"].append("No trust checks are loaded: no record is flagged, so merged records and location conflicts are not caught.")

    base = dict(service=service, service_label=svc.label, radius_km=round(ctx.R, 1), method="recipe", location=loc)
    order = order_v1(index, ctx, sets)
    shown, near_pos, reserved = shown_v1(index, order, in_W, k)
    cards = [_v1_card(index, ctx, p, loc) for p in shown]
    nxt = None
    if not reserved and len(order) > len(shown):
        nxt = _v1_card(index, ctx, order[len(shown)], loc)  # only for the last card's comparison
    for i, c in enumerate(cards):
        c.nearest_in_window = shown[i] == near_pos
        if reserved and i == len(cards) - 1:
            c.why_here = _why_nearest_v1(c, ctx)
        else:
            c.why_here = _why_here_v1(c, cards[i + 1] if i + 1 < len(cards) else nxt, ctx, n_in)
    if reserved:
        how["notes"].append("The nearest recommendable facility is always shown: the evidence allowance listed "
                            "corroborated records ahead of it, so it takes the last place.")
    capped = _cap_exception(index, ctx, sets, shown)
    if capped:
        how["notes"].append(f"Only the {S.MAX_CANDIDATES} nearest recommendable records are put in order. "
                            f"{len(capped)} corroborated record(s) further out inside the window would have been "
                            "listed under the evidence allowance, but lie beyond that limit.")
        how["cap_exception"] = [index.fac["facility_id"].iat[p] for p in capped]

    def further(shown_pos: list, n_max: int, limit_reach: bool) -> list:
        taken = {index.dup_canon.get(p, -1 - p) for p in shown_pos}
        m = usable & rec_mask & ~ctx.within
        if limit_reach:
            m &= ctx.in_reach_pt
        far = np.flatnonzero(m)
        far = far[np.argsort(d[far], kind="stable")]
        out = []
        for p in far:
            p = int(p)
            g_ = index.dup_canon.get(p, -1 - p)
            if g_ in taken:
                continue
            taken.add(g_)
            c = _v1_card(index, ctx, p, loc)
            what = c.minutes_label.split(";")[0] if c.minutes is not None and not ctx.too_loose else c.distance_label
            c.why_here = (f"Further away: {what}, beyond the " +
                          (f"{ctx.R:.0f} km radius." if ctx.sea else f"{ctx.W:.0f}-minute window."))
            out.append(c)
            if len(out) >= n_max:
                break
        return out

    unv_mask = usable & ctx.within & claim & ~rec_mask
    unv_idx = np.flatnonzero(unv_mask)
    unv_idx = [int(p) for p in unv_idx[np.argsort(d[unv_idx], kind="stable")]]

    def unverified_for(shown_pos: list) -> list:
        taken = {index.dup_canon.get(p, -1 - p) for p in shown_pos}
        out = []
        for p in unv_idx:
            g_ = index.dup_canon.get(p, -1 - p)
            if g_ in taken:
                continue
            taken.add(g_)
            out.append(_v1_unverified_card(index, ctx, p, loc))
            if len(out) >= S.MAX_UNVERIFIED:
                break
        return out

    within_txt = (f"within {ctx.R:.0f} km (straight-line; sea travel is not modelled)" if ctx.sea else
                  f"within about {hours} estimated drive (free-flow) of {ctx.place}")
    if not shown:
        fa = further([], k, limit_reach=False)
        how["headline"] = (f"No reliable option nearby. No facility in this dataset shows clear evidence of "
                           f"{_label_lc(service)} {within_txt}. This is a gap in our data, not proof that no care exists.")
        if fa:
            how["headline"] += f" The nearest one with evidence is further away: {fa[0].name}, " + (
                fa[0].minutes_label.split(";")[0] if fa[0].minutes is not None and not ctx.too_loose
                else fa[0].distance_label) + "."
        if ctx.too_loose:
            how["headline"] = _too_loose_text(ctx) + " " + how["headline"]
        return Answer("no_reliable_option", results=[], beyond_radius=fa[0] if fa else None,
                      unverified_nearby=unverified_for([]), how_we_decided=how, further_away=fa,
                      window=win, rank_version="v1", **base)

    fa = further(shown, k - len(shown), limit_reach=True) if len(shown) < k else []
    how["headline"] = (f"{len(shown)} of {n_in} recommendable {'facility' if n_in == 1 else 'facilities'} "
                       f"{within_txt}, nearest first" + ("" if ctx.sea else
                                                         f" (a corroborated record may move up to {ctx.A:.0f} minutes)")
                       + "." + (" The last card is the nearest one, always shown." if reserved else "")
                       + (f" {len(fa)} more with evidence {'is' if len(fa) == 1 else 'are'} further away."
                          if fa else ""))
    if ctx.too_loose:
        how["headline"] = _too_loose_text(ctx) + " " + how["headline"]
    return Answer("ok", results=cards, beyond_radius=None, unverified_nearby=unverified_for(shown),
                  how_we_decided=how, further_away=fa, window=win, rank_version="v1", **base)


def rank(index: Index, service: str, resolution, k: int = 3, *, version: Optional[str] = None,
         window_scale: float = 1.0, allowance: Optional[float] = None, terrain: bool = True) -> Answer:
    """Top ``k`` recommendable facilities for ``service`` near ``resolution``,
    or an abstention. Lines with no grades loaded fall back to ``search``.

    ``version``: "v1" (score spec v1-final, D048; the index default) or "v0"
    (the legacy additive score, or probability mode when loaded). The v1
    sensitivity knobs (``reports/ordering_behaviour.md``): ``window_scale``
    multiplies the window, ``allowance`` replaces the line's allowance (minutes),
    ``terrain=False`` turns the state factor off."""
    version = version or index.rank_version
    if version not in RANK_VERSIONS:
        raise ValueError(f"version must be one of {RANK_VERSIONS}, not {version!r}")
    if version == "v0":
        return _rank_v0(index, service, resolution, k=k)
    if service not in S.SERVICES:
        raise ValueError(f"unknown service line {service!r}; free text goes to search()")
    svc = S.SERVICES[service]
    early = _base_answer(service, svc.label, float(svc.radius_km), "recipe", resolution)
    if early is not None:
        early.rank_version = "v1"
        return early
    if service not in index.lines:
        ans = search(index, svc.keywords, resolution, k=k, radius_km=float(svc.radius_km))
        ans.service, ans.service_label = service, svc.label
        ans.how_we_decided.setdefault("notes", []).insert(
            0, f"No recipe grades are loaded for {svc.label.lower()} yet (recipe {index.recipe_version} covers "
               f"{', '.join(S.label(l).lower() for l in index.graded_lines)}). These are keyword matches only.")
        return ans
    return _rank_v1(index, service, resolution, k=k, window_scale=window_scale, allowance=allowance, terrain=terrain)


# ---------------------------------------------------------------------------
# search: keyword fallback (D026). Confidence never above low.
# ---------------------------------------------------------------------------

def search(index: Index, text: str, resolution, k: int = 3, radius_km: Optional[float] = None) -> Answer:
    R = float(radius_km or S.SEARCH_RADIUS_KM)
    label = f"Keyword search: {text}"
    early = _base_answer("search", label, R, "keyword", resolution)
    if early is not None:
        return early
    kw = index.ensure_keyword()
    v1 = index.rank_version == "v1"  # D048 (6): no confidence levels or "validated" wording under v1
    loc = _location_dict(resolution)
    d = _distances(index, loc["lat"], loc["lon"])
    rows, sims = kw.matches(text, S.SEARCH_MIN_SIMILARITY)
    n = len(index.fac)
    best = np.zeros(n)
    fpos = kw._fac_pos[rows] if len(rows) else np.array([], dtype=np.int64)
    ok = fpos >= 0
    rows, sims, fpos = rows[ok], sims[ok], fpos[ok]
    np.maximum.at(best, fpos, sims)
    usable = ~np.isnan(d)
    matched = best >= S.SEARCH_MIN_SIMILARITY
    blocked = np.zeros(n, dtype=bool)
    for p in index.record_major:
        blocked[p] = True
    cand_mask = usable & matched & (d <= R)
    good = cand_mask & ~blocked
    how = {
        "mode": "keyword", "score_status": "keyword", "query": text, "radius_km": R,
        "formula": (f"score = best keyword match x reach, where the match is the TF-IDF cosine similarity of the "
                    f"facility's best fact (at least {S.SEARCH_MIN_SIMILARITY}; a specialty name counts "
                    f"{S.SEARCH_SPECIALTY_WEIGHT} of a fact) and reach = exp(-km / {R:.0f}); "
                    f"ties within {S.TIE_EPS} broken by distance."),
        "recipe": ["Keyword match, no service recipe: the quoted text matched your words, but no rule checked that the service is offered."],
        "gates": [
            {"gate": "usable location", "rule": "the facility has a location we can use", "n": int(usable.sum()), "of": n},
            {"gate": "keyword match", "rule": f"a fact or specialty matches the words (similarity {S.SEARCH_MIN_SIMILARITY} or more)", "n": int((usable & matched).sum())},
            {"gate": "within radius", "rule": f"within {R:.0f} km", "n": int(cand_mask.sum())},
            {"gate": "no blocking warning", "rule": "no blocking warning on the whole record", "n": int(good.sum()),
             "n_distinct": _n_distinct(index, np.flatnonzero(good))},
        ],
        "flags_source": index.flags_source,
        "limits": list(S.LIMITS_SEARCH_V1 if v1 else S.LIMITS), "notes": [],
    }
    if index.flags_source == "none":
        how["notes"].append("No trust checks are loaded: no record is flagged, so merged records and location conflicts are not caught.")

    def card(p: int) -> Card:
        sel = fpos == p
        rr, ss = rows[sel], sims[sel]
        o = np.argsort(-ss, kind="stable")[:3]
        ev, seen = [], set()
        for j in o:
            pr = kw.postings.iloc[int(rr[j])]
            q = kw._doc_text[int(pr["doc_id"])]
            if q in seen:
                continue
            seen.add(q)
            ev.append({"requirement": f"matches \"{text}\"", "quote": q, "field": pr["field"],
                       "fact_id": _str_or_none(pr["fact_id"]), "similarity": round(float(ss[j]), 3)})
        dist = float(d[p])
        reach = math.exp(-dist / R)
        parts = {"evidence": float(best[p]), "distance": reach, "trust": None, "penalties": [], "combine": "product",
                 "labels": {"evidence": "keyword match strength", "distance": "reach"}}
        minor = index.record_minor.get(p, [])
        could = _could_be_wrong(index, None, p, "thin", [], minor, None, keyword=True)
        reason = ("Keyword match, no service recipe. The quoted text matched your words; "
                  "no rule checked that the service is offered.")
        return _make_card(index, p, dist, line=None, score=float(best[p]) * reach, parts=parts,
                          confidence=(None if v1 else "low", reason if v1 else "Low: " + reason), evidence=ev, missing=[], could=could, radius=R,
                          grade=None, grade_label="keyword match, no service recipe", strength="keyword",
                          origin=loc)

    base = dict(service="search", service_label=label, radius_km=R, method="keyword", location=loc)
    gi = np.flatnonzero(good)
    unv_all = np.flatnonzero(cand_mask & blocked)
    unv_all = [int(p) for p in unv_all[np.argsort(d[unv_all], kind="stable")]]

    def unverified_for(shown: list) -> list:
        """Blocked matches, nearest first, one per duplicate group, none from a group shown as a card."""
        taken = {index.dup_canon.get(p, -1 - p) for p in shown}
        out = []
        for p in unv_all:
            g_ = index.dup_canon.get(p, -1 - p)
            if g_ in taken:
                continue
            taken.add(g_)
            c = card(p)
            c.score, c.parts = None, {}
            c.confidence_reason = "Not recommended: " + (index.record_major[p][0].get("reason") or "a blocking warning.")
            out.append(c)
            if len(out) >= S.MAX_UNVERIFIED:
                break
        return out

    if len(gi) == 0:
        far = np.flatnonzero(usable & matched & ~blocked & (d > R))
        beyond = None
        if len(far):
            p = int(far[np.argmin(d[far])])
            beyond = card(p)
            beyond.why_here = (f"The nearest keyword match, "
                               f"{_fmt_km(d[p], _eff_precision(beyond.location_precision_km, loc))} away, beyond {R:.0f} km.")
        how["headline"] = f"No facility in this dataset has a fact matching \"{text}\" within {R:.0f} km."
        return Answer("no_reliable_option", results=[], beyond_radius=beyond, unverified_nearby=unverified_for([]),
                      how_we_decided=how, **base)
    dist = {int(p): float(d[p]) for p in gi}
    sc = {int(p): float(best[p]) * math.exp(-dist[int(p)] / R) for p in gi}
    # one card per WP11 duplicate group, as in rank()
    order = _dedupe(index, sorted(sc, key=lambda p: (-sc[p], dist[p])))
    order = _break_ties(order, sc, dist)
    cards = [card(p) for p in order[:k]]
    unverified = unverified_for(order[:k])
    for i, c in enumerate(cards):
        nxt = order[i + 1] if i + 1 < len(order) else None
        if nxt is None:
            c.why_here = "The only keyword match within the radius." if len(order) == 1 else "Last of the matches shown."
        elif abs(sc[order[i]] - sc[nxt]) < S.TIE_EPS:
            c.why_here = f"Tied with {index.fac.name.iat[nxt]} (scores within {S.TIE_EPS}); listed first because it is closer."
        elif math.log(max(best[order[i]], 1e-9) / max(best[nxt], 1e-9)) >= math.log(
                math.exp(-dist[order[i]] / R) / math.exp(-dist[nxt] / R)):
            c.why_here = f"Ranked above {index.fac.name.iat[nxt]}: its facts match the words more closely."
        else:
            c.why_here = (f"Ranked above {index.fac.name.iat[nxt]}: closer "
                          f"({_fmt_km(dist[order[i]], _eff_precision(c.location_precision_km, loc))} against "
                          f"{_fmt_km(dist[nxt], _eff_precision(float(index.precision_km[nxt]), loc))}) with a similar match.")
    how["headline"] = (f"{len(cards)} of {len(order)} keyword matches within {R:.0f} km. " +
                       ("Keyword matches are not recommendations: no rule checked that the service is offered."
                        if v1 else "Keyword matches are never more than low confidence."))
    return Answer("ok", results=cards, beyond_radius=None, unverified_nearby=unverified, how_we_decided=how, **base)


# ---------------------------------------------------------------------------
# simulate (D038): expected straight-line km to real care, closed form
# ---------------------------------------------------------------------------

def expected_trip_km(legs: Sequence[float], probs: Sequence[float]) -> tuple[float, float]:
    """A patient visits facilities in order; leg i is the km to reach facility i
    from the previous stop (or the origin). Facility i offers the service with
    probability ``probs[i]``. Returns (expected km travelled until care is found
    or the list ends, probability the list ends without care)."""
    survive, km = 1.0, 0.0
    for leg, p in zip(legs, probs):
        km += survive * float(leg)
        survive *= 1.0 - float(p)
    return km, survive


def _policy_order(idx: Index, line: str, d_o: np.ndarray, policy: str, R: float, L: int) -> np.ndarray:
    ld = idx.lines[line]
    usable = ~np.isnan(d_o)
    if policy == "nearest_claim":
        m = usable & (ld.grade >= 1)
        c = np.flatnonzero(m)
        return c[np.argsort(d_o[c], kind="stable")][:L]
    if policy == "nearest_evidence":
        m = usable & (ld.grade >= 2)
        c = np.flatnonzero(m)
        return c[np.argsort(d_o[c], kind="stable")][:L]
    if policy == "ranked":
        m = usable & (ld.p > 0) & ~ld.blocked
        c = np.flatnonzero(m)
        rec = (ld.p[c] >= S.p_threshold(line)) & (ld.grade[c] >= 2)
        score = ld.p[c] * np.exp(-d_o[c] / R)
        inside = d_o[c] <= R
        # as rank() lists them (S.ORDER_RULE): recommendable first, inside the
        # radius before beyond, then by score, then distance
        o = np.lexsort((d_o[c], -score, ~inside, ~rec))
        return c[o][:L]
    raise ValueError(f"unknown policy {policy!r}; one of {POLICIES}")


def simulate(index: Index, service: str, resolution, policy: Optional[str] = None,
             n_origins: int = S.SIM_DEFAULT_ORIGINS, max_visits: int = S.SIM_MAX_VISITS) -> dict:
    """Expected straight-line km until a patient reaches a facility that offers
    ``service``, for origins at the ``n_origins`` post office points nearest the
    resolution, under ``policy`` (or all of ``POLICIES``). Each visited facility
    offers the service with probability ``p_offers``; no random draws. Only in
    probability mode: without ``p_offers`` there is no honest number to show."""
    if not index.is_probability(service):
        raise RuntimeError(f"simulate needs p_offers for {service!r} (probability mode, D038); none is loaded")
    if index.post_offices is None or not len(index.post_offices):
        raise RuntimeError("simulate needs post office points in the index")
    loc = _location_dict(resolution)
    if loc["lat"] is None:
        raise ValueError("simulate needs a resolved location")
    ld = index.lines[service]
    R = float(S.SERVICES[service].radius_km)
    po = index.post_offices
    dpo = haversine_km(loc["lat"], loc["lon"], po["lat"].to_numpy(float), po["lon"].to_numpy(float))
    oi = np.argsort(dpo, kind="stable")[:n_origins]
    olat, olon = po["lat"].to_numpy(float)[oi], po["lon"].to_numpy(float)[oi]
    pols = [policy] if policy else list(POLICIES)
    out = {"service": service, "n_origins": int(len(oi)), "max_visits": max_visits,
           "origin_radius_km": float(dpo[oi].max()) if len(oi) else None,
           "model_version": ld.model_version, "policies": {}}
    for pol in pols:
        kms, ends, second = [], [], []
        for a, b in zip(olat, olon):
            d_o = haversine_km(a, b, index.lat, index.lon)
            order = _policy_order(index, service, d_o, pol, R, max_visits)
            if len(order) == 0:
                kms.append(np.nan)
                ends.append(1.0)
                second.append(1.0)
                continue
            legs = [float(d_o[order[0]])]
            if len(order) > 1:
                legs += list(haversine_km(index.lat[order[:-1]], index.lon[order[:-1]],
                                          index.lat[order[1:]], index.lon[order[1:]]))
            probs = ld.p[order]
            km, end = expected_trip_km(legs, probs)
            kms.append(km)
            ends.append(end)
            second.append(1.0 - float(probs[0]))
        kms = np.asarray(kms, dtype=float)
        out["policies"][pol] = {
            "expected_km_mean": float(np.nanmean(kms)) if np.isfinite(kms).any() else None,
            "expected_km_median": float(np.nanmedian(kms)) if np.isfinite(kms).any() else None,
            "share_second_trip": float(np.mean(second)),
            "share_no_care_after_list": float(np.mean(ends)),
        }
    return out if policy is None else {**{k: v for k, v in out.items() if k != "policies"}, "policy": policy,
                                       **out["policies"][policy]}
