"""WP11: trust checks. Flags records that should not be trusted at face value.

Reads ``silver/facilities.parquet``, ``silver/facts.parquet``,
``gold/capability_grades.parquet``, ``silver/pin_crosswalk.parquet`` and the raw
PIN directory (through ``vf.geo.PinReference``, as WP1 does). Writes, each with a
``.meta.json`` sidecar:

  gold/facility_flags.parquet       one row per facility per flag (contract v2)
  gold/facility_duplicates.parquet  likely duplicate pairs

and, from the hand review in ``data/labels/flag_review.csv``,
``reports/trust_checks.md``.

    python scripts/wp11_trust_checks.py              # flags, duplicates, report
    python scripts/wp11_trust_checks.py --sample     # also (re)draw the review sample, keeping marks
    python scripts/wp11_trust_checks.py --no-scenarios   # skip the scenario-suite impact run

Checks and rules: ``vf.trust``. The hand review is the source of every
precision number; the script never marks a row itself.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vf import geo, io, paths, trust  # noqa: E402

SCRIPT = "scripts/wp11_trust_checks.py"
SEED = 11
REVIEW_PER_SIGNAL = 15      # flagged records drawn per signal (fewer if it fires less)
REVIEW_UNFLAGGED = 20
REVIEW_STRENGTH_PER_LEVEL = 10

FLAG_SCHEMA = pa.schema([
    ("facility_id", pa.string()), ("flag", pa.string()), ("service_line", pa.string()),
    ("severity", pa.string()), ("signal", pa.string()), ("reason", pa.string()),
    ("quote", pa.string()), ("check_version", pa.string()),
])
DUP_SCHEMA = pa.schema([
    ("facility_id_a", pa.string()), ("facility_id_b", pa.string()), ("distance_km", pa.float64()),
    ("name_score", pa.float64()), ("signal", pa.string()), ("keep", pa.string()),
])
REVIEW_COLUMNS = ["review_id", "round", "check", "signal", "sample", "facility_id", "name", "service_line",
                  "severity", "shown", "mark", "note"]
#: Round 2 re-tests the rules revised after round 1 on rows not read before.
ROUND2_PER_GROUP = 20


def log(*a):
    print(*a, flush=True)


# --------------------------------------------------------------------------
# io helpers
# --------------------------------------------------------------------------

def write_atomic(path: Path, write) -> None:
    paths.ensure(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_table(df: pd.DataFrame, schema: pa.Schema, path: Path, meta: dict) -> None:
    table = pa.Table.from_pandas(df[schema.names], schema=schema, preserve_index=False)
    write_atomic(path, lambda p: pq.write_table(table, p))
    side = dict(meta, rows=table.num_rows, columns=schema.names)
    write_atomic(path.with_name(path.name + ".meta.json"),
                 lambda p: p.write_text(json.dumps(side, indent=2, default=str) + "\n"))


def git_info() -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "--no-optional-locks", "status", "--porcelain", "--", SCRIPT,
                                     "src/vf/trust.py", "src/vf/geo.py"], cwd=ROOT, capture_output=True,
                                    text=True, check=True).stdout.strip())
    except Exception:
        commit, dirty = None, None
    return {"git_commit": commit, "code_uncommitted": dirty}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _list(v) -> list:
    if v is None:
        return []
    if isinstance(v, float) and np.isnan(v):
        return []
    return list(v)


def _s(v):
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA:
        return None
    return str(v)


# --------------------------------------------------------------------------
# geography lookups
# --------------------------------------------------------------------------

class Places:
    """Name and district lookups built once from ``geo.PinReference``."""

    def __init__(self, ref: geo.PinReference, crosswalk: pd.DataFrame):
        self.ref = ref
        po = ref.offices
        cw = crosswalk.copy()
        cw["st"] = cw["state_pin"].map(trust.pin_state)
        self.states = {s for s in cw["st"].unique() if s and s != "NA"}
        self.pin_states = cw[cw["st"] != "NA"].groupby("pin")["st"].agg(set).to_dict()
        self.pin_groups = po.dropna(subset=["group"]).groupby("pin")["group"].agg(set).to_dict()
        self.pin_main = ref.pins["district"].to_dict()
        # place key -> districts: district names, and head/sub offices placed near their district centre
        pg: dict[str, set] = defaultdict(set)
        for d, g in zip(po["district"], po["group"]):
            if isinstance(g, str) and d:
                pg[d].add(g)
        for o, g, town, placed in zip(po["office"], po["group"], po["town"], po["placed"]):
            if isinstance(g, str) and town and placed and o:
                pg[o].add(g)
        self.place_groups = dict(pg)
        # district -> all names of its offices (any type), its own name and divisions
        gn: dict[str, set] = defaultdict(set)
        for o, d, v, g in zip(po["office"], po["district"], po["division"], po["group"]):
            if isinstance(g, str):
                gn[g].update(x for x in (o, d, v) if x)
        self.group_names = dict(gn)
        self.district_words = {w.lower() for d in po["district"].dropna().unique() for w in d.split() if len(w) >= 4}
        # place words for duplicate names: district, division and state names. Town and office names are
        # not used (WP11 fix round): globally they are 19,551 words that include brands also found as a place
        # ("Yashoda", "Amrita", "Manipal", "Ganga": 10 true pairs lost), and per district they strip deity and
        # person names ("Durga", "Vijaya") and made "Sri Vijaya Durga" and "Vijaya Super Speciality" score 100.
        words = lambda names: {w.lower() for n in names if isinstance(n, str)
                               for w in geo.place_key(n).split() if len(w) >= 4}
        self.dup_place_words = (self.district_words | words(po["division"].dropna().unique())
                                | words(self.states))

    def pin_loc(self, pin):
        if pin is None or pin not in self.ref.pins.index:
            return None
        r = self.ref.pins.loc[pin]
        return None if pd.isna(r["lat"]) else (float(r["lat"]), float(r["lon"]))

    def city_groups(self, keys: set, text_state) -> set:
        g = set().union(*[self.place_groups.get(k, set()) for k in keys]) if keys else set()
        if text_state:
            g = {x for x in g if trust.pin_state(x.split("|")[0]) == text_state}
        return g


# --------------------------------------------------------------------------
# the checks over the table
# --------------------------------------------------------------------------

def run_checks(fac: pd.DataFrame, facts: pd.DataFrame, grades: pd.DataFrame, pl: Places,
               merge_alone=trust.MERGE_SIGNALS_ALONE):
    """Returns (flags frame, per-facility signal table for the review, context dict)."""
    ref = pl.ref
    texts = defaultdict(list)
    for fid, field, text in zip(facts["facility_id"], facts["field"], facts["text"]):
        texts[fid].append((field, text))
    for fid, d in zip(fac["facility_id"], fac["description"]):
        if isinstance(d, str):
            texts[fid].append(("description", d))

    gp = grades.pivot(index="facility_id", columns="service_line", values="grade").fillna(0).astype(int)
    limits = trust.specialty_breadth_limits(fac["facility_type"], fac["specialties"].map(len))

    # district of each record, for the shared website check: its PIN's, else its point's
    rec_district, point_district = {}, {}
    for r in fac.itertuples(index=False):
        pd_ = pl.pin_main.get(r.pin) if r.pin else None
        pt = None
        if not pd.isna(r.lat_raw) and not pd.isna(r.lon_raw):
            pt = ref.district_at(r.lat_raw, r.lon_raw)
        point_district[r.facility_id] = pt
        rec_district[r.facility_id] = pd_ if isinstance(pd_, str) else pt
    sites = fac["official_website"].map(trust.website_key)
    shared = trust.shared_websites(fac["facility_id"], sites, [rec_district[f] for f in fac["facility_id"]])
    name_of = dict(zip(fac["facility_id"], fac["name"]))
    city_of = dict(zip(fac["facility_id"], fac["city_raw"]))

    rows, sig_rows = [], []
    for r, site in zip(fac.itertuples(index=False), sites):
        fid = r.facility_id
        pin = r.pin if isinstance(r.pin, str) else None
        pin_district = pl.pin_main.get(pin) if pin else None
        pin_district = pin_district if isinstance(pin_district, str) else None
        text_state = trust.state_of_text(r.state_raw, pl.states)
        keys = geo.city_keys(r.city_raw)
        loc = pl.pin_loc(pin)
        pin_names = set()
        if pin:
            pin_names = ref.names_of_pin(pin) | (ref.names_near(*loc) if loc else set())
            if pin_district:
                pin_names |= pl.group_names.get(pin_district, set())
        city_agrees = geo.names_match(keys, pin_names) if keys and pin_names else None
        city_groups = pl.city_groups(keys, text_state)
        if city_agrees is None and keys and pin_district in city_groups:
            city_agrees = True
        lc = trust.location_conflict(
            r.coord_check, r.coord_pin_km, pin, _s(r.state_raw), _s(r.city_raw), text_state,
            pl.pin_states.get(pin, set()) if pin else set(), city_agrees,
            city_groups if city_agrees is False else (), pin_district)
        if lc:
            rows.append(dict(lc, facility_id=fid))

        # merges
        msig: dict[str, tuple] = {}
        place_words = pl.district_words | {w.lower() for k in keys | pin_names for w in k.split()}
        j = trust.name_joins_two(r.name, place_words)
        if j:
            msig["name_joins_two"] = (f"The record's name joins two facility names (\"{j.strip()}\" is the "
                                      "second), so its details may belong to more than one place.", r.name)
        own_groups = {g for g in (pin_district, point_district.get(fid)) if g} | city_groups
        own_names = set(keys) | pin_names
        if not pd.isna(r.lat_raw) and not pd.isna(r.lon_raw):
            own_names |= ref.names_near(r.lat_raw, r.lon_raw)
        for g in own_groups:
            own_names.add(geo.place_key(g.split("|")[1]))
        if own_groups:
            oa = trust.other_address(texts.get(fid, []), own_groups, own_names, pl.place_groups,
                                     pl.pin_groups, pin, pl.states)
            if oa:
                t, other = oa
                where = " or ".join(trust.nice_place(o) for o in other[:2])
                home = trust.nice_place(pin_district or sorted(own_groups)[0])
                msig["text_other_address"] = (
                    f"The record's own text places it in {where}, but its PIN code and city put it in {home}: "
                    "it may mix facts from a facility of the same or a similar name elsewhere.", t)
        if fid in shared:
            others = shared[fid]
            cities = sorted({str(city_of[o]) for o in others if isinstance(city_of[o], str)})[:3]
            msig["shared_website"] = (
                f"Its website ({site}) is also given by {len(others)} record(s) in other districts"
                + (f" ({', '.join(cities)})" if cities else "")
                + "; some details may describe another branch.", r.official_website)
        ns = len(r.specialties)
        lim = limits.get(r.facility_type if isinstance(r.facility_type, str) else "unknown")
        if lim is not None and ns > lim:
            msig["specialty_breadth"] = (
                f"It lists {ns} specialties, more than 99% of {r.facility_type} records (limit {lim:.0f}); "
                "it may combine several facilities.", None)
        mf = trust.merge_flag(msig, merge_alone)
        if mf:
            rows.append(dict(mf, facility_id=fid))

        g = gp.loc[fid].to_dict() if fid in gp.index else {}
        for ic in trust.implausible_claims(r.facility_type, r.name, r.n_beds, g, r.description):
            rows.append(dict(ic, facility_id=fid))

        sig_rows.append({"facility_id": fid, "merge_signals": sorted(msig), "merge_detail": msig,
                         "city_agrees_pin": city_agrees, "text_state": text_state, "pin_district": pin_district})

    flags = pd.DataFrame(rows)
    if len(flags):
        flags["check_version"] = trust.CHECK_VERSION
        flags = flags[trust.FLAG_COLUMNS].sort_values(["facility_id", "flag", "service_line"],
                                                      na_position="first").reset_index(drop=True)
    else:
        flags = pd.DataFrame(columns=trust.FLAG_COLUMNS)
    ctx = {"limits": limits, "shared": shared, "sites": sites, "gp": gp,
           "signals": pd.DataFrame(sig_rows).set_index("facility_id")}
    return flags, ctx


def build_duplicates(fac: pd.DataFrame, grades: pd.DataFrame, sites: pd.Series, pl: Places,
                     strict: bool = True) -> pd.DataFrame:
    g2 = grades[grades["grade"] >= 2].groupby("facility_id").size()
    gsum = grades.groupby("facility_id")["grade"].sum()
    rank_key = (fac["facility_id"].map(g2).fillna(0) * 1000 + fac["facility_id"].map(gsum).fillna(0) * 10
                + fac["n_source_domains"].clip(upper=9))
    df = pd.DataFrame({"facility_id": fac["facility_id"], "name": fac["name"], "lat": fac["lat"],
                       "lon": fac["lon"], "pin": fac["pin"], "site": sites, "rank_key": rank_key})
    return trust.find_duplicates(df, place_words=pl.dup_place_words, strict=strict)


# --------------------------------------------------------------------------
# review sample
# --------------------------------------------------------------------------

def shown_text(fid: str, fac_i: pd.DataFrame, flag_row=None, extra: str = "") -> str:
    r = fac_i.loc[fid]
    parts = [f"type={r.facility_type}", f"city={r.city_raw}", f"state={r.state_raw}", f"pin={r.pin}",
             f"coord_check={r.coord_check}", f"pin_km={'' if pd.isna(r.coord_pin_km) else round(r.coord_pin_km)}"]
    if flag_row is not None:
        parts.append(f"reason={flag_row['reason']}")
        if flag_row.get("quote"):
            parts.append(f"quote={str(flag_row['quote'])[:200]}")
    if extra:
        parts.append(extra)
    return " | ".join(parts)


def draw_sample(flags: pd.DataFrame, dups: pd.DataFrame, fac: pd.DataFrame, grades: pd.DataFrame,
                ctx: dict, previous: pd.DataFrame | None) -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    fac_i = fac.set_index("facility_id")
    out = []

    def take(df, n):
        if len(df) <= n:
            return df
        return df.iloc[sorted(rng.choice(len(df), n, replace=False))]

    # flagged, per check and primary signal (signals reviewed one by one so each can be weakened)
    sig_all = ctx["signals"]
    for flag in ["location_conflict", "possible_merge", "implausible_claim"]:
        f = flags[flags["flag"] == flag]
        if flag == "possible_merge":
            # review each merge signal on its own, over every record where it fired (flag or not)
            for sig in ["name_joins_two", "text_other_address", "shared_website", "specialty_breadth"]:
                ids = [i for i, s in sig_all["merge_signals"].items() if sig in s]
                for fid in take(pd.Series(ids, dtype=object), REVIEW_PER_SIGNAL):
                    reason, quote = sig_all.loc[fid, "merge_detail"][sig]
                    out.append(dict(check=flag, signal=sig, sample="flagged", facility_id=fid,
                                    service_line=None, severity="",
                                    shown=shown_text(fid, fac_i, {"reason": reason, "quote": quote})))
            continue
        f = f.assign(primary=f["signal"].str.split("+").str[0])
        for sig, fs in f.groupby("primary"):
            if flag == "implausible_claim":
                fs = fs.drop_duplicates("facility_id")
            for _, fr in take(fs, REVIEW_PER_SIGNAL).iterrows():
                extra = ""
                if flag == "implausible_claim":
                    gr = grades[(grades.facility_id == fr.facility_id) & (grades.service_line == fr.service_line)]
                    if len(gr):
                        ev = [it["text"][:120] for it in _list(gr.iloc[0]["evidence_items"])][:2]
                        extra = f"grade={int(gr.iloc[0]['grade'])} evidence={ev} beds={fac_i.loc[fr.facility_id].n_beds}"
                out.append(dict(check=flag, signal=fr["signal"], sample="flagged", facility_id=fr.facility_id,
                                service_line=fr["service_line"], severity=fr["severity"],
                                shown=shown_text(fr.facility_id, fac_i, fr, extra)))
    # duplicates
    name = fac_i["name"]
    for _, d in take(dups, 30).iterrows():
        out.append(dict(check="duplicate_of", signal=d["signal"], sample="flagged",
                        facility_id=f"{d.facility_id_a}|{d.facility_id_b}", service_line=None, severity="",
                        shown=f"A={name[d.facility_id_a]} ({fac_i.loc[d.facility_id_a].city_raw}) | "
                              f"B={name[d.facility_id_b]} ({fac_i.loc[d.facility_id_b].city_raw}) | "
                              f"km={d.distance_km:.2f} score={d.name_score:.0f} | "
                              f"sites={fac_i.loc[d.facility_id_a].official_website} / "
                              f"{fac_i.loc[d.facility_id_b].official_website}"))
    # unflagged, any check
    unflagged = fac[~fac["facility_id"].isin(flags["facility_id"])]
    for fid in take(unflagged["facility_id"], REVIEW_UNFLAGGED):
        out.append(dict(check="unflagged", signal="", sample="unflagged", facility_id=fid, service_line=None,
                        severity="", shown=shown_text(fid, fac_i)))
    # evidence strength: 10 per level at grade 2+
    g2 = grades[grades["grade"] >= 2]
    for lvl in ["thin", "moderate", "strong"]:
        for _, gr in take(g2[g2["evidence_strength"] == lvl], REVIEW_STRENGTH_PER_LEVEL).iterrows():
            r = fac_i.loc[gr.facility_id]
            ev = [f"{it['field']}: {it['text'][:110]}" for it in _list(gr["evidence_items"])]
            out.append(dict(check="evidence_strength", signal=lvl, sample="graded", facility_id=gr.facility_id,
                            service_line=gr.service_line, severity="",
                            shown=f"grade={gr.grade_label} n_support={gr.n_supporting_facts} "
                                  f"domains={r.n_source_domains} site={r.official_website} | " + " || ".join(ev)))
    s = pd.DataFrame(out)
    s["name"] = [name.get(f.split("|")[0], "") for f in s["facility_id"]]
    s["review_id"] = [hashlib.sha1(f"{c}|{g}|{f}|{l}".encode()).hexdigest()[:10]
                      for c, g, f, l in zip(s["check"], s["signal"], s["facility_id"], s["service_line"])]
    s["mark"], s["note"], s["round"] = "", "", "1"
    if previous is not None and len(previous):
        keep = previous.set_index("review_id")[["mark", "note"]]
        m = s["review_id"].isin(keep.index)
        s.loc[m, "mark"] = s.loc[m, "review_id"].map(keep["mark"]).fillna("")
        s.loc[m, "note"] = s.loc[m, "review_id"].map(keep["note"]).fillna("")
    return s[REVIEW_COLUMNS]


# --------------------------------------------------------------------------
# impact
# --------------------------------------------------------------------------

def touched_grade2(flags: pd.DataFrame, grades: pd.DataFrame, dups: pd.DataFrame) -> dict:
    """Facility-line pairs graded 2+ that each check touches (record-level flags touch every line)."""
    g2 = grades[grades["grade"] >= 2][["facility_id", "service_line"]]
    out = {}
    for (flag, sev), f in flags.groupby(["flag", "severity"]):
        rec = f[f["service_line"].isna()]["facility_id"].unique()
        line = f[f["service_line"].notna()][["facility_id", "service_line"]]
        hit = g2[g2["facility_id"].isin(rec)]
        hit = pd.concat([hit, g2.merge(line, on=["facility_id", "service_line"])]).drop_duplicates()
        out[f"{flag}/{sev}"] = {"facilities": int(hit["facility_id"].nunique()), "facility_lines": int(len(hit))}
    drop = set(dups["facility_id_a"]) | set(dups["facility_id_b"])
    drop -= set(dups["keep"])
    hit = g2[g2["facility_id"].isin(drop)]
    out["duplicate_of/hidden"] = {"facilities": int(hit["facility_id"].nunique()), "facility_lines": int(len(hit))}
    return out


def scenario_impact(flags: pd.DataFrame, dups: pd.DataFrame) -> dict | None:
    """Run the scenario suite with no flags, then each check alone, then all; count result changes."""
    from vf import rank
    spec = importlib.util.spec_from_file_location("wp5_scenarios", ROOT / "scripts" / "wp5_scenarios.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    silver, gold = paths.silver_dir(), paths.gold_dir()
    fac = pd.read_parquet(silver / "facilities.parquet")
    grades = pd.read_parquet(gold / "capability_grades.parquet")
    places = pd.read_parquet(silver / "places.parquet") if (silver / "places.parquet").exists() else None
    resolve, rname = mod.get_resolver()
    sc = pd.read_csv(paths.labels_dir() / "scenarios.csv", dtype=str, keep_default_na=False)
    sc = sc[~sc["service"].str.startswith("search:")]
    res = {r.id: resolve(r.place_text, r.state_hint or None) for r in sc.itertuples(index=False)}
    empty = flags.iloc[0:0]
    no_dups = dups.iloc[0:0]

    def answers(fl, du):
        idx = rank.build_index(fac, grades, flags=fl, duplicates=du, places=places)
        out = {}
        for r in sc.itertuples(index=False):
            a = rank.rank(idx, r.service, res[r.id])
            out[r.id] = (a.status, [c.facility_id for c in a.results])
        return out

    base = answers(empty, no_dups)
    variants = {f: (flags[flags["flag"] == f], no_dups) for f in flags["flag"].unique()}
    variants["duplicate_of"] = (empty, dups)
    variants["all"] = (flags, dups)
    out = {"scenarios": len(sc), "resolver": rname, "checks": {}}
    for name, (fl, du) in variants.items():
        ans = answers(fl, du)
        left = sum(len(set(base[k][1]) - set(ans[k][1])) for k in base)
        changed = [k for k in base if base[k][1] != ans[k][1]]
        status = [k for k in base if base[k][0] != ans[k][0]]
        out["checks"][name] = {"results_left": left, "scenarios_changed": len(changed),
                               "scenarios_status_changed": len(status), "changed_ids": changed}
    return out


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def draw_round2(flags: pd.DataFrame, dups: pd.DataFrame, fac: pd.DataFrame, grades: pd.DataFrame,
                ctx: dict, previous: pd.DataFrame) -> pd.DataFrame:
    """Fresh rows for the rules revised after round 1 (never a row read before):
    duplicate pairs, ``type_cannot_offer`` at grade 2+, every ``name_joins_two``
    hit, and ``possible_merge`` flags that are ``major``."""
    rng = np.random.default_rng(SEED + 2)
    fac_i = fac.set_index("facility_id")
    seen = set(zip(previous["check"], previous["facility_id"]))
    name = fac_i["name"]
    out = []

    def take(df, n):
        return df if len(df) <= n else df.iloc[sorted(rng.choice(len(df), n, replace=False))]

    d = dups[np.array([("duplicate_of", f"{a}|{b}") not in seen for a, b in zip(dups.facility_id_a, dups.facility_id_b)], dtype=bool)]
    for _, x in take(d, ROUND2_PER_GROUP).iterrows():
        out.append(dict(check="duplicate_of", signal=x["signal"], sample="flagged_r2",
                        facility_id=f"{x.facility_id_a}|{x.facility_id_b}", service_line=None, severity="",
                        shown=f"A={name[x.facility_id_a]} ({fac_i.loc[x.facility_id_a].city_raw}) | "
                              f"B={name[x.facility_id_b]} ({fac_i.loc[x.facility_id_b].city_raw}) | "
                              f"km={x.distance_km:.2f} score={x.name_score:.0f} | "
                              f"sites={fac_i.loc[x.facility_id_a].official_website} / "
                              f"{fac_i.loc[x.facility_id_b].official_website}"))
    ic = flags[flags["flag"] == "implausible_claim"].merge(
        grades[["facility_id", "service_line", "grade", "evidence_items"]], on=["facility_id", "service_line"])
    ic = ic[(ic["grade"] >= 2) & np.array([("implausible_claim", f) not in seen for f in ic["facility_id"]], dtype=bool)]
    for _, x in take(ic.drop_duplicates("facility_id"), ROUND2_PER_GROUP).iterrows():
        ev = [it["text"][:110] for it in _list(x["evidence_items"])][:2]
        r = fac_i.loc[x.facility_id]
        out.append(dict(check="implausible_claim", signal=x["signal"], sample="flagged_grade2_r2",
                        facility_id=x.facility_id, service_line=x["service_line"], severity=x["severity"],
                        shown=f"type={r.facility_type} | grade={x.grade} evidence={ev} | beds={r.n_beds} | "
                              f"desc={str(r.description)[:150]}"))
    pm = flags[flags["flag"] == "possible_merge"]
    for _, x in pm[pm["signal"].str.startswith("name_joins_two")].iterrows():
        if ("possible_merge", x.facility_id) in seen:
            continue
        out.append(dict(check="possible_merge", signal=x["signal"], sample="flagged_r2", facility_id=x.facility_id,
                        service_line=None, severity=x["severity"], shown=shown_text(x.facility_id, fac_i, x)))
    major = pm[(pm["severity"] == "major") & np.array([("possible_merge", f) not in seen for f in pm["facility_id"]], dtype=bool)]
    for _, x in take(major, 15).iterrows():
        out.append(dict(check="possible_merge_major", signal=x["signal"], sample="flagged_r2",
                        facility_id=x.facility_id, service_line=None, severity=x["severity"],
                        shown=shown_text(x.facility_id, fac_i, x)))
    s = pd.DataFrame(out)
    s["name"] = [name.get(f.split("|")[0], "") for f in s["facility_id"]]
    s["review_id"] = [hashlib.sha1(f"r2|{c}|{g}|{f}|{l}".encode()).hexdigest()[:10]
                      for c, g, f, l in zip(s["check"], s["signal"], s["facility_id"], s["service_line"])]
    s["mark"], s["note"], s["round"] = "", "", "2"
    return s[REVIEW_COLUMNS]


def draw_round4(dups_loose: pd.DataFrame, dups: pd.DataFrame, fac: pd.DataFrame,
                previous: pd.DataFrame) -> pd.DataFrame:
    """Fix round: every ``near_same_name`` pair scoring under 100 under the looser
    round-2 rule or the final rule, not read before (a census of the weak band,
    not a sample)."""
    fac_i = fac.set_index("facility_id")
    name = fac_i["name"]
    seen = set(previous.loc[previous["check"] == "duplicate_of", "facility_id"])
    band = pd.concat([dups_loose, dups]).drop_duplicates(["facility_id_a", "facility_id_b"])
    band = band[band["signal"].str.contains("near_same_name") & (band["name_score"] < 100)]
    out = []
    for _, x in band.iterrows():
        fid = f"{x.facility_id_a}|{x.facility_id_b}"
        if fid in seen:
            continue
        out.append(dict(check="duplicate_of", signal=x["signal"], sample="band_census_r4", facility_id=fid,
                        service_line=None, severity="",
                        shown=f"A={name[x.facility_id_a]} ({fac_i.loc[x.facility_id_a].city_raw}, "
                              f"{fac_i.loc[x.facility_id_a].pin}) | "
                              f"B={name[x.facility_id_b]} ({fac_i.loc[x.facility_id_b].city_raw}, "
                              f"{fac_i.loc[x.facility_id_b].pin}) | "
                              f"km={x.distance_km:.2f} score={x.name_score:.0f} | "
                              f"sites={fac_i.loc[x.facility_id_a].official_website} / "
                              f"{fac_i.loc[x.facility_id_b].official_website}"))
    s = pd.DataFrame(out, columns=[c for c in REVIEW_COLUMNS if c not in ("name", "review_id", "mark", "note", "round")])
    s["name"] = [name.get(f.split("|")[0], "") for f in s["facility_id"]]
    s["review_id"] = [hashlib.sha1(f"r4|{c}|{f}".encode()).hexdigest()[:10]
                      for c, f in zip(s["check"], s["facility_id"])]
    s["mark"], s["note"], s["round"] = "", "", "4"
    return s[REVIEW_COLUMNS]


def dup_review_under_final(review: pd.DataFrame, dups: pd.DataFrame) -> pd.DataFrame:
    """Every reviewed ``near_same_name`` pair (any round), with whether the final
    rule keeps it and its score band: the honest view of the revised rule."""
    r = review[(review["check"] == "duplicate_of") & review["mark"].isin(["correct", "wrong"])
               & review["signal"].str.contains("near_same_name")].copy()
    final = {f"{a}|{b}": s for a, b, s in zip(dups.facility_id_a, dups.facility_id_b, dups.signal)}
    r["kept"] = [("near_same_name" in final.get(f, "")) for f in r["facility_id"]]
    # the score shown to the reviewer, at the time of the review
    score = r["shown"].str.extract(r"score=(\d+)")[0].astype(float)
    r["band"] = np.where(score >= 100, "100", "under 100")
    t = r.groupby(["band", "kept"]).agg(reviewed=("mark", "size"),
                                        correct=("mark", lambda s: int((s == "correct").sum())))
    return t.reset_index()


def precision_table(review: pd.DataFrame) -> pd.DataFrame:
    r = review[review["mark"].isin(["correct", "wrong"])].copy()
    if not len(r):
        return pd.DataFrame(columns=["round", "check", "signal", "sample", "reviewed", "correct", "precision"])
    r["primary"] = r["signal"].fillna("").astype(str).str.split("+").str[0]
    t = r.groupby(["round", "check", "primary", "sample"]).agg(
        reviewed=("mark", "size"), correct=("mark", lambda s: int((s == "correct").sum())))
    t["precision"] = (t["correct"] / t["reviewed"]).round(3)
    return t.reset_index().rename(columns={"primary": "signal"})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="draw the round-1 review sample, keeping existing marks")
    ap.add_argument("--round2", action="store_true", help="append a round-2 sample for the revised rules")
    ap.add_argument("--round4", action="store_true",
                    help="append the fix-round census of near_same_name pairs scoring under 100")
    ap.add_argument("--no-scenarios", action="store_true")
    ap.add_argument("--stats", type=Path, default=None, help="write the numbers behind the report as JSON here")
    args = ap.parse_args(argv)
    t0 = time.time()
    silver, gold, labels = paths.silver_dir(), paths.gold_dir(), paths.labels_dir()
    fac = pd.read_parquet(silver / "facilities.parquet")
    facts = pd.read_parquet(silver / "facts.parquet", columns=["facility_id", "field", "text"])
    grades = pd.read_parquet(gold / "capability_grades.parquet")
    cw = pd.read_parquet(silver / "pin_crosswalk.parquet")
    raw = io.read_pincodes_raw()
    coords, _ = geo.post_office_coords(raw)
    ref = geo.PinReference(raw, coords)
    pl = Places(ref, cw)
    log(f"loaded {len(fac):,} facilities, {len(facts):,} facts, {len(grades):,} grade rows "
        f"({grades['recipe_version'].iloc[0]}) in {time.time() - t0:.0f}s")

    flags, ctx = run_checks(fac, facts, grades, pl)
    dups = build_duplicates(fac, grades, ctx["sites"], pl)
    dups_loose = build_duplicates(fac, grades, ctx["sites"], pl, strict=False)

    # counts
    log("\nflags per check and severity:")
    cnt = flags.groupby(["flag", "severity"]).agg(rows=("facility_id", "size"),
                                                  facilities=("facility_id", "nunique"))
    log(cnt.to_string())
    log("\nflag rows per signal:")
    log(flags.groupby(["flag", "signal"]).size().to_string())
    sig = ctx["signals"]["merge_signals"]
    merge_sig_counts = {s: int(sum(s in x for x in sig)) for s in
                        ["name_joins_two", "text_other_address", "shared_website", "specialty_breadth"]}
    log(f"\nmerge signals fired (before combining): {merge_sig_counts}")
    log(f"duplicate pairs: {len(dups)} ({dups['signal'].value_counts().to_dict()})")

    meta = {"script": SCRIPT, "check_version": trust.CHECK_VERSION,
            "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **git_info(),
            "recipe_version": str(grades["recipe_version"].iloc[0]),
            "inputs": {p: {"sha256": sha256(paths.data_dir() / p)} for p in
                       ["silver/facilities.parquet", "silver/facts.parquet", "gold/capability_grades.parquet",
                        "silver/pin_crosswalk.parquet"]}}
    write_table(flags, FLAG_SCHEMA, gold / "facility_flags.parquet", dict(
        meta, output="gold/facility_flags.parquet",
        counts={f"{a}/{b}": int(v) for (a, b), v in cnt["rows"].items()},
        facilities_flagged=int(flags["facility_id"].nunique()),
        signals={k: int(v) for k, v in flags.groupby("signal").size().items()},
        merge_signals_fired=merge_sig_counts,
        merge_signals_alone=sorted(trust.MERGE_SIGNALS_ALONE),
        merge_signals_corroborate=sorted(trust.MERGE_SIGNALS_CORROBORATE),
        merge_signals_unused=["specialty_breadth"],
        few_beds_enabled=trust.USE_BEDS,
        specialty_breadth_limits=ctx["limits"],
        rules="vf.trust; review and precision in reports/trust_checks.md"))
    write_table(dups, DUP_SCHEMA, gold / "facility_duplicates.parquet", dict(
        meta, output="gold/facility_duplicates.parquet",
        rule=f"within {trust.DUP_KM} km and distinctive-name score >= {trust.DUP_NAME_SCORE} (near_same_name; under "
             f"{trust.DUP_SURE_SCORE} the official websites must not differ, and the words-inside match needs the "
             "same website), "
             "or same official website (platforms excluded) and PIN (same_site_and_pin); keep = more lines "
             "graded 2+, then higher grade sum, then more source domains",
        signals={k: int(v) for k, v in dups["signal"].value_counts().items()}))

    review_path = labels / "flag_review.csv"
    previous = pd.read_csv(review_path, dtype=str, keep_default_na=False) if review_path.exists() else None
    if args.sample or args.round2 or args.round4 or previous is None:
        if args.round4:
            review = pd.concat([previous, draw_round4(dups_loose, dups, fac, previous)], ignore_index=True)
        elif args.round2:
            review = pd.concat([previous, draw_round2(flags, dups, fac, grades, ctx, previous)], ignore_index=True)
        else:
            review = draw_sample(flags, dups, fac, grades, ctx, previous)
        write_atomic(review_path, lambda p: review[REVIEW_COLUMNS].to_csv(p, index=False))
        write_atomic(review_path.with_name(review_path.name + ".meta.json"), lambda p: p.write_text(json.dumps(
            dict(meta, output="labels/flag_review.csv", rows=len(review), seed=SEED,
                 reviewed_by="agent (Claude), by reading each row's shown text and, where needed, the "
                             "record's facts"), indent=2) + "\n"))
        log(f"\nreview sample: {len(review)} rows -> {review_path}")
    else:
        review = previous
    prec = precision_table(review)
    n_marked = int(review["mark"].isin(["correct", "wrong"]).sum())
    log(f"\nreview: {n_marked} of {len(review)} rows marked")
    if len(prec):
        log(prec.to_string(index=False))
    dup_final = dup_review_under_final(review, dups)
    log(f"\nreviewed near_same_name pairs under the final rule (loose rule: {len(dups_loose)} pairs, "
        f"{int(dups_loose['signal'].str.contains('near_same_name').sum())} near_same_name):")
    log(dup_final.to_string(index=False))

    touched = touched_grade2(flags, grades, dups)
    log("\ngraded 2+ touched per check:")
    log(json.dumps(touched))
    impact = None
    if not args.no_scenarios:
        t1 = time.time()
        impact = scenario_impact(flags, dups)
        log(f"\nscenario impact ({time.time() - t1:.0f}s): "
            + json.dumps({k: {kk: vv for kk, vv in v.items() if kk != 'changed_ids'}
                          for k, v in impact['checks'].items()}))
    stats = {"counts": cnt.reset_index().to_dict("records"),
             "signals": flags.groupby(["flag", "signal"]).size().reset_index(name="n").to_dict("records"),
             "merge_signals_fired": merge_sig_counts, "dups": dups["signal"].value_counts().to_dict(),
             "n_dups": len(dups), "touched": touched, "impact": impact,
             "precision": prec.to_dict("records"), "limits": ctx["limits"],
             "dup_review_final": dup_final.to_dict("records"), "n_dups_loose": len(dups_loose),
             "strength": grades[grades.grade >= 2].groupby(["service_line", "evidence_strength"]).size()
             .unstack(fill_value=0).to_dict("index")}
    if args.stats:
        args.stats.parent.mkdir(parents=True, exist_ok=True)
        args.stats.write_text(json.dumps(stats, indent=1, default=str))
    log(f"\ndone in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
