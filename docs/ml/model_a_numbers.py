#!/usr/bin/env python
"""Recompute the numbers quoted in docs/ml/model_a_design.md.

Read-only. It reads the committed silver and gold tables and
labels/gold_sample.csv, writes nothing, and prints one block per letter (A to M).
The design doc cites these letters. Every number in the doc that is not marked as
a reviewer measurement, an estimate, or a prior comes from this script.

Inputs (through vf.paths):
  silver/facts.parquet, silver/facilities.parquet, silver/post_offices.parquet,
  silver/nfhs.parquet, gold/fact_tags.parquet (regex v0 tags),
  gold/capability_grades.parquet (regex v0 grades, 9,989 facilities),
  labels/gold_sample.csv (the gold sample design, 429 rows) and labels/gold_facts.csv (block I)

Run:
  .venv/bin/python docs/ml/model_a_numbers.py          # everything, about 1 minute, about 2 GB peak
  .venv/bin/python docs/ml/model_a_numbers.py --fast   # no near-duplicate graph (skips K)
"""
from __future__ import annotations

import argparse
import hashlib
import re
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from vf import paths, recipes

warnings.filterwarnings("ignore")

DOC = Path(__file__).with_name("model_a_design.md")

GRADE_TAGS = ["ob_delivery", "csection", "blood", "anesthesia", "ot",
              "dialysis", "dialysis_unit", "icu", "ventilator"]
S1_TAGS = GRADE_TAGS + ["nicu", "scheme"]          # stratum S1: v0 hits on these
EV_TAGS = list(recipes.EVIDENCE_TAGS)

# Near-miss vocabulary (stratum S3) and negation or meta cues (stratum S4).
# Section 5 of the design doc points here. "closed" stays here for sampling only.
NEAR = (r"caesar|cesar|lscs|\bdeliver(y|ies)\b|labou?r room|labou?r ward|\bbirth|matern|blood cent|transfus|"
        r"blood component|blood storage|an(a)?esth|anasth|\bot\b|theat(re|er)|surgical suite|neonat|newborn|"
        r"\bsncu\b|\bscnu\b|incubator|kidney|renal|nephro|\bcrrt\b|\bsled\b|\bmicu\b|\bsicu\b|\bpicu\b|\bccu\b|"
        r"\biccu\b|critical care|\bhdu\b|high dependency|ventilat|bipap|pm-?\s?jay|esic|\bechs\b|aarogyasri|"
        r"arogyasri|mjpjay|chiranjeevi|cmchis|jan arogya")
META = (r"provided (content|text|information|data|images?|page|source)|not (explicitly )?(listed|specified|"
        r"mentioned|stated|provided|available)|no (specific |other )?(\w+ )?(listed|mentioned|specified)|"
        r"\blacks?\b|referr(ed|al)s? (to|out)|not functional|non-functional|closed")

# Candidate strings for the agent labels (no-key path), section 4.2.
CS_VARIANTS = r"caesar|cesar|cesear|ceaser|caeser|cesarian|\blscs\b|c-sec|c sec"
BLOOD_VARIANTS = r"blood cent|transfus|blood component|blood storage"

# Held-out states for the region test, section 5. Fixed before any training.
HELD_STATES = {"Kerala", "Rajasthan", "Odisha", "Orissa", "Assam"}   # Orissa is an old spelling in state_raw
TEST_PER_MILLE = 150          # 15% of signature groups go to the test split
S5_TRAIN_SAMPLE = 20_000      # no-cue texts sampled into the training pool
SEED = 20261121

STOP = {"the", "a", "an", "and", "of", "for", "with", "in", "at", "on", "to", "is", "are", "by", "its",
        "available", "facility", "facilities", "service", "services", "provides", "provided", "offers",
        "offered", "has", "have", "also", "hospital"}


def signature(text: str) -> str:
    """Grouping key for the test split: digits to '#', punctuation dropped,
    common filler words dropped, remaining word set sorted."""
    toks = re.findall(r"[a-z#]+", re.sub(r"\d+", "#", text))
    return " ".join(sorted({t for t in toks if t not in STOP}))


def in_test(sig: str) -> bool:
    return int(hashlib.sha1(sig.encode()).hexdigest()[:8], 16) % 1000 < TEST_PER_MILLE


def head(letter: str, title: str) -> None:
    print(f"\n[{letter}] {title}")


def pct(a, b) -> str:
    return f"{a:,} of {b:,} ({a / b:.1%})" if b else f"{a:,} of 0"


# --------------------------------------------------------------------------- load

def load():
    facts = pd.read_parquet(paths.silver_dir() / "facts.parquet")
    tags = pd.read_parquet(paths.gold_dir() / "fact_tags.parquet", columns=["fact_id", "tags"])
    f = facts.merge(tags, on="fact_id", how="left", validate="one_to_one")
    assert f.tags.notna().all(), "fact_tags does not cover every fact"
    tagstr = "|" + f.tags.map(lambda a: "|".join(a)) + "|"
    for t in EV_TAGS:
        f["rx_" + t] = tagstr.str.contains(f"|{t}|", regex=False)
    fac = pd.read_parquet(paths.silver_dir() / "facilities.parquet").set_index("facility_id")
    g = pd.read_parquet(paths.gold_dir() / "capability_grades.parquet")
    G = g.pivot(index="facility_id", columns="service_line", values="grade")
    d = (f.groupby("text_norm")
          .agg(n=("fact_id", "size"), n_fac=("facility_id", "nunique"),
               **{"rx_" + t: ("rx_" + t, "first") for t in EV_TAGS})
          .reset_index())
    gold = pd.read_csv(paths.labels_dir() / "gold_sample.csv", dtype=str, keep_default_na=False)
    return f, d, fac, G, gold


# --------------------------------------------------------------------------- blocks

def block_a(f, d, fac):
    head("A", "Facts")
    print("fact rows:", f"{len(f):,}", f.field.value_counts().to_dict())
    print("distinct text_norm:", f"{len(d):,}", "| appear once:", f"{(d.n == 1).mean():.1%}")
    L = d.text_norm.str.len()
    W = d.text_norm.str.split().str.len()
    print(f"length: mean {L.mean():.1f} chars, median {L.median():.0f}, p90 {L.quantile(.9):.0f}, "
          f"max {L.max():,}; median {W.median():.0f} words")
    per = f.groupby("facility_id").size()
    print(f"facilities with facts: {len(per):,} of {len(fac):,}; facts per facility with facts: "
          f"mean {per.mean():.1f}, median {per.median():.0f}; per facility over all {len(fac):,}: "
          f"{len(f) / len(fac):.1f}")


def block_b(f, d):
    head("B", "Regex v0 hits per tag (distinct texts, fact rows, facilities)")
    for t in EV_TAGS:
        m = f["rx_" + t]
        print(f"  {t:14s} {d['rx_' + t].sum():6,} {m.sum():7,} {f.loc[m, 'facility_id'].nunique():6,}")
    anyev = d[["rx_" + t for t in EV_TAGS]].any(axis=1)
    s1 = d[["rx_" + t for t in S1_TAGS]].any(axis=1)
    multi = d[["rx_" + t for t in EV_TAGS]].sum(axis=1) >= 2
    print(f"any evidence tag: {anyev.sum():,}; any S1 grade-relevant tag: {s1.sum():,}; 2+ tags: {multi.sum():,}")


def corr_texts(f, line, du_col="rx_dialysis_unit"):
    """Per facility: distinct texts carrying any tag of the line's corroborated rule."""
    rule = recipes.GRADE_RULES[line]
    tags = sorted(set().union(*rule.corroborated))
    cols = [("rx_" + t) if not (t == "dialysis_unit") else du_col for t in tags]
    m = f[cols].any(axis=1)
    return f[m].groupby("facility_id").text_norm.nunique()


def block_c(f, G):
    head("C", "Regex v0 grades over all facilities, and fragility")
    print("facilities:", f"{len(G):,}")
    for line in ("emoc", "dialysis", "icu"):
        print(f"  {line:8s}", G[line].value_counts().sort_index().to_dict())
    for line, tag in (("emoc", "csection"), ("dialysis", "dialysis"), ("icu", "icu")):
        nd = f[f["rx_" + tag]].groupby("facility_id").text_norm.nunique()
        ge2 = G.index[G[line] >= 2]
        one = int((nd.reindex(ge2).fillna(0) == 1).sum())
        print(f"  {line}: grade 2+ resting on one distinct '{tag}' text: {pct(one, len(ge2))}")
    print("two-distinct-fact rule for grade 3 (v0 tags): grade-3 facilities whose corroboration tags sit "
          "in a single distinct text")
    for line in ("emoc", "dialysis", "icu"):
        nd = corr_texts(f, line)
        g3 = G.index[G[line] == 3]
        one = int((nd.reindex(g3).fillna(0) <= 1).sum())
        print(f"  {line}: {pct(one, len(g3))}")
    # dialysis under v1 definitions: no nephrologist in dialysis_unit, no implication, two-fact rule
    f["du_v1"] = f.text_norm.str.contains(r"dialysis (unit|machine|centre|center)|h(a)?emodialysis")
    has_d = f[f.rx_dialysis].groupby("facility_id").size()
    has_u = f[f.du_v1].groupby("facility_id").size()
    nd = corr_texts(f, "dialysis", du_col="du_v1")
    both = has_d.index.intersection(has_u.index)
    g3_v1 = [i for i in both if nd.get(i, 0) >= 2]
    g3_v0 = set(G.index[G.dialysis == 3])
    print(f"  dialysis grade 3 with v1 unit words and two distinct facts: {len(g3_v1):,} "
          f"(v0 {len(g3_v0):,}; lost {len(g3_v0 - set(g3_v1)):,}, gained {len(set(g3_v1) - g3_v0):,})")


def block_d(f, d, fac, G):
    head("D", "Known regex errors")
    NEO_ANY = r"neonat|nicu|newborn"
    NEO_DEL = (r"neonatal intensive care( unit)?s?|neonatal icus?|neonatal critical care|newborn icu|"
               r"\bnicus?\b|\bsncus?\b|neo-?natal|newborn")
    ICU_RX = r"\bicu\b|intensive care|critical care unit|\bpicu\b|\bmicu\b|\bsicu\b|\bccu\b|\biccu\b|\bcticu\b"
    fi = f[f.rx_icu].copy()
    fi["neo"] = fi.text_norm.str.contains(NEO_ANY)
    fi["still"] = fi.text_norm.str.replace(NEO_DEL, " ", regex=True).str.contains(ICU_RX)
    g2 = set(G.index[G.icu >= 2])
    old = (set(fi.facility_id) - set(fi[~fi.neo].facility_id)) & g2
    new = (set(fi.facility_id) - set(fi[fi.still].facility_id)) & g2
    print(f"neonatal ICU, distinct icu texts with neonatal words: {fi[fi.neo].text_norm.nunique():,}")
    print(f"  ICU 2+ facilities whose every icu fact has a neonatal word: {len(old)} "
          f"{G.loc[list(old), 'icu'].value_counts().to_dict()}")
    print(f"  ...after deleting neonatal mentions and re-testing: {len(new)} "
          f"{G.loc[list(new), 'icu'].value_counts().to_dict()}")
    fv = f[f.rx_ventilator]
    g3 = set(G.index[G.icu == 3])
    v_non = set(fv[~fv.text_norm.str.contains(NEO_ANY)].facility_id)
    print(f"  ICU grade 3 with ventilator evidence only from neonatal facts: {len(g3 - v_non)} of {len(g3)}")

    CLAIM = (r"department|\bdept\b|course|diploma|degree|\bdnb\b|\bmd\b|\bms\b|mbbs|\bdgo\b|faculty|training|"
             r"trained|fellowship|experience|specialist|consultant|speciali[sz]|\bdr\.? ")
    for t in ("ob_delivery", "anesthesia"):
        x = d[d["rx_" + t]].text_norm
        print(f"claims-as-evidence wording in {t} hits: {pct(int(x.str.contains(CLAIM).sum()), len(x))}")

    du = f[f.rx_dialysis_unit].groupby("facility_id").size()
    dd = f[f.rx_dialysis].groupby("facility_id").size()
    du_only = du.index.difference(dd.index)
    x = f[f.facility_id.isin(du_only) & f.rx_dialysis_unit]
    print(f"nephrologist: facilities with dialysis_unit but no dialysis hit: {len(du_only)} "
          f"(current grades {G.loc[du_only, 'dialysis'].value_counts().to_dict()}); their dialysis_unit facts "
          f"naming a nephrologist: {int(x.text_norm.str.contains('nephrologist').sum())} of {len(x)}")
    unit_np = set(f[f.rx_dialysis_unit & ~f.text_norm.str.contains("nephrologist")].facility_id)
    g3d = set(G.index[G.dialysis == 3])
    print(f"  dialysis grade 3 whose dialysis_unit evidence is only nephrologist facts: {len(g3d - unit_np)} of {len(g3d)}")

    PERSON = (r"\bdr\.?\b|anaesthetist|anesthetist|anesthesiologist|anaesthesiologist|department|course|dnb|"
              r"diploma|faculty|md\b|consultant|specialist|anesthesiology$|anaesthesiology$")
    an = f[f.rx_anesthesia]
    an_ok = set(an[~an.text_norm.str.contains(PERSON)].facility_id)
    ot = set(f[f.rx_ot].facility_id)
    e3 = list(G.index[G.emoc == 3])
    risky = [i for i in e3 if i not in ot and i not in an_ok]
    via_an = sum(1 for i in e3 if i not in ot)
    print(f"EmOC grade 3: {len(e3)}; without ot (anesthesia carries it): {via_an}; of those, anesthesia only "
          f"from person, department or course facts: {len(risky)}")

    EVW = (r"icu|intensive|ventilat|dialys|caesar|cesar|c-section|blood bank|nicu|operation theat|operating theat|"
           r"anaesth|anesth|matern|deliver|emergency|trauma")
    NEG = (r"\bno\b|\bnot\b|without|unavailable|lack|absence|\bnone\b|refer(red|s)? (to|out)|does not|doesn't|"
           r"do not|isn't|is not|n't have|no longer|closed|discontinued|non-functional|not functional")
    ev = d.text_norm.str.contains(EVW)
    ng = d.text_norm.str.contains(NEG)
    print(f"evidence-word texts with a negation cue: {pct(int((ev & ng).sum()), int(ev.sum()))}")
    NEGF = (r"not available|not listed|not explicitly|not specified|no specific|provided content|referr|lacks|"
            r"does not|not functional|closed")
    anyrx = f[["rx_" + t for t in EV_TAGS]].any(axis=1)
    nf = f[f.text_norm.str.contains(NEGF) & anyrx]
    print(f"fact rows with an evidence hit and a negation or not-listed cue: {len(nf)} at {nf.facility_id.nunique()} facilities")
    anyrx_n = f[["rx_" + t for t in EV_TAGS]].sum(axis=1)
    for cue in ("closed", "referral", "referred to", "lacks"):
        z = f[f.text_norm.str.contains(cue) & (anyrx_n >= 1)]
        print(f"  cue '{cue}' on evidence-tagged facts: {z.text_norm.nunique()} distinct")
    mixed = f[f.text_norm.str.contains(NEGF) & (anyrx_n >= 2)]
    print(f"  negation-cue facts with 2+ evidence tags: {mixed.text_norm.nunique()} distinct")

    META_STMT = (r"provided (content|text|information|data|images?|page|source)|"
                 r"not (explicitly )?(listed|specified|mentioned|stated)")
    PHOTO = r"(visible|shown|seen|depicted) in (the |an |this )?(image|photo|picture)"
    ms = d.text_norm.str.contains(META_STMT)
    ph = d.text_norm.str.contains(PHOTO)
    anyd = d[["rx_" + t for t in EV_TAGS]].any(axis=1)
    print(f"meta-statements: {int(ms.sum()):,} distinct ({int((ms & anyd).sum())} with an evidence hit); "
          f"photo captions: {int(ph.sum()):,} distinct")

    print("vocabulary gaps (texts using the words; of those, missed by the tag's v0 regex):")
    for name, rx, tag in (("blood centre", r"blood cent", "blood"),
                          ("round-the-clock (hyphens)", r"round-the-clock", "h24"),
                          ("PM-JAY / PM JAY", r"pm-jay|pm jay", "scheme"),
                          ("ventilation or BiPAP", r"ventilation|bipap", "ventilator"),
                          ("MICU SICU PICU CCU ICCU", r"\bmicu\b|\bsicu\b|\bpicu\b|\bccu\b|\biccu\b", "icu"),
                          ("caesarean spelling variants", CS_VARIANTS, "csection")):
        m = d.text_norm.str.contains(rx)
        print(f"  {name:28s} {int(m.sum()):5,} {int((m & ~d['rx_' + tag]).sum()):5,}")

    desc = fac.description.fillna("").map(lambda s: set(recipes.tag_fact(s)))
    ftags = f.groupby("facility_id")[["rx_" + t for t in EV_TAGS]].any()
    ftags.columns = EV_TAGS
    spec = fac.specialties.map(lambda v: set(v) if isinstance(v, (list, np.ndarray)) else set())
    e3 = e2 = i2 = 0
    for fid in fac.index:
        t = set(ftags.columns[ftags.loc[fid].values]) if fid in ftags.index else set()
        t |= desc.get(fid, set())
        gr = recipes.grade_facility(t, spec.get(fid, set()))
        e3 += gr["emoc"] == 3
        e2 += gr["emoc"] >= 2
        i2 += gr["icu"] >= 2
    print(f"with description tags added: EmOC grade 3 {e3} (v0 {int((G.emoc == 3).sum())}), EmOC 2+ {e2} "
          f"(v0 {int((G.emoc >= 2).sum())}), ICU 2+ {i2} (v0 {int((G.icu >= 2).sum())})")


def block_e(d):
    head("E", "How concentrated each tag is in a few strings (facility incidences = sum of n_fac)")
    for t in ("csection", "blood", "anesthesia", "ot", "icu", "ventilator", "dialysis", "dialysis_unit"):
        x = d[d["rx_" + t]].sort_values("n_fac", ascending=False)
        tot = int(x.n_fac.sum())
        k50 = int((x.n_fac.cumsum() / tot < 0.5).sum()) + 1
        print(f"  {t:14s} distinct {len(x):5,} incidences {tot:5,} strings for half {k50:4,}; "
              f"top: {list(zip(x.text_norm.head(2), x.n_fac.head(2)))}")


def block_f(f, fac, G):
    head("F", "What-ifs on distance (post offices to nearest facility, haversine BallTree)")
    from sklearn.neighbors import BallTree
    po = pd.read_parquet(paths.silver_dir() / "post_offices.parquet")
    po = po[po.coord_valid]
    X = np.radians(po[["lat", "lon"]].values.astype(float))

    def dist(ids):
        ids = [i for i in ids if i in fac.index and bool(fac.at[i, "coord_valid"])]
        Y = np.radians(fac.loc[ids, ["lat", "lon"]].values.astype(float))
        return BallTree(Y, metric="haversine").query(X, k=1)[0][:, 0] * 6371.0, len(ids)

    def show(name, ids, base=None):
        dd, n = dist(ids)
        s = (f"  {name}: n={n} median {np.median(dd):.1f} km, p90 {np.percentile(dd, 90):.1f}, "
             f"beyond 60 km {np.mean(dd > 60):.1%}")
        if base is not None:
            s += f"; >10 km closer {(base - dd > 10).sum():,}, >10 km farther {(dd - base > 10).sum():,}"
        print(s)
        return dd

    print(f"post offices: {len(po):,}")
    has = f.groupby("facility_id")[["rx_" + t for t in EV_TAGS]].any()
    bc = f[f.text_norm.str.contains(r"blood cent|transfus|blood storage unit|blood component") & ~f.rx_blood]
    cand = [i for i in bc.facility_id.unique() if i in G.index and not has.at[i, "rx_blood"]
            and G.at[i, "emoc"] == 2 and (has.at[i, "rx_anesthesia"] or has.at[i, "rx_ot"])]
    kinds = {}
    for i in cand:
        texts = bc[bc.facility_id == i].text_norm.drop_duplicates()
        if texts.str.contains("exchange transfusion").all():
            kinds[i] = "exchange transfusion (neonatal)"
        elif texts.str.contains(r"diploma|vocational|course").all():
            kinds[i] = "job qualification text"
        else:
            kinds[i] = "plausible blood service"
    print(f"blood-centre or transfusion EmOC 2->3 candidates: {len(cand)}", pd.Series(kinds).value_counts().to_dict())
    g3 = list(G.index[G.emoc == 3])
    b = show("EmOC grade 3, v0", g3)
    show("+ all candidates", g3 + cand, b)
    show("+ exchange-transfusion only", g3 + [i for i in cand if kinds[i].startswith("exchange")], b)
    plaus = [i for i in cand if kinds[i] == "plausible blood service"]
    show("+ plausible blood services", g3 + plaus, b)
    clear = [i for i in plaus if bc[bc.facility_id == i].text_norm.str.contains("24x7 blood cent").any()]
    show("+ the clear on-site 24x7 blood centre", g3 + clear, b)

    NEO_ANY = r"neonat|nicu|newborn"
    NEO_DEL = (r"neonatal intensive care( unit)?s?|neonatal icus?|neonatal critical care|newborn icu|"
               r"\bnicus?\b|\bsncus?\b|neo-?natal|newborn")
    ICU_RX = r"\bicu\b|intensive care|critical care unit|\bpicu\b|\bmicu\b|\bsicu\b|\bccu\b|\biccu\b|\bcticu\b"
    fi = f[f.rx_icu]
    neo = fi.text_norm.str.contains(NEO_ANY)
    still = fi.text_norm.str.replace(NEO_DEL, " ", regex=True).str.contains(ICU_RX)
    g2 = list(G.index[G.icu >= 2])
    old = (set(fi.facility_id) - set(fi[~neo].facility_id)) & set(g2)
    new = (set(fi.facility_id) - set(fi[still].facility_id)) & set(g2)
    bi = show("ICU grade 2+, v0", g2)
    show(f"minus {len(old)} (every icu fact has a neonatal word)", [i for i in g2 if i not in old], bi)
    show(f"minus {len(new)} (neonatal mentions deleted, then re-tested)", [i for i in g2 if i not in new], bi)


def strata(d):
    s1 = d[["rx_" + t for t in S1_TAGS]].any(axis=1)
    s2 = (d.rx_h24 | d.rx_emergency) & ~s1
    s3 = d.text_norm.str.contains(NEAR) & ~s1 & ~s2
    s4 = d.text_norm.str.contains(META) & ~s1 & ~s2 & ~s3
    st = np.select([s1, s2, s3, s4], ["S1", "S2", "S3", "S4"], default="S5")
    return pd.Series(st, index=d.index)


def near_dup_graph(texts, chunk=400):
    """Adjacency of char 3-5-gram TF-IDF cosine >= 0.9 among ``texts``, built in row chunks."""
    from scipy import sparse
    from sklearn.feature_extraction.text import TfidfVectorizer
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, dtype=np.float32)
    X = vec.fit_transform(texts).tocsr()
    rows, cols = [], []
    for i in range(0, X.shape[0], chunk):
        S = (X[i:i + chunk] @ X.T).tocoo()
        k = (S.data >= 0.9) & (S.row + i != S.col)
        rows.append(S.row[k] + i)
        cols.append(S.col[k])
    r, c = np.concatenate(rows), np.concatenate(cols)
    A = sparse.coo_matrix((np.ones(len(r), np.float32), (r, c)), shape=(X.shape[0],) * 2).tocsr()
    return A, vec, X


def block_g(f, d, fac, gold, fast):
    head("G", "Strata, the test split, the region holdout, and the training pool")
    from scipy.sparse.csgraph import connected_components
    d["stratum"] = strata(d)
    d["sig"] = d.text_norm.map(signature)
    d["gold"] = d.text_norm.isin(set(gold.text_norm))
    print("strata (distinct texts):", d.stratum.value_counts().sort_index().to_dict())
    print(f"gold sample texts found in facts: {int(d.gold.sum())} of {len(gold)}")
    # region holdout: texts that appear only at facilities in the held-out states
    st = fac.state_raw.astype(str).str.strip()
    held_fac = set(fac.index[st.isin(HELD_STATES)])
    fr = f.facility_id.isin(held_fac)
    print(f"region holdout {sorted(HELD_STATES)}: {len(held_fac):,} facilities ({len(held_fac) / len(fac):.1%}), "
          f"{pct(int(fr.sum()), len(f))} fact rows")
    in_held = set(f[fr].text_norm)
    excl = in_held - set(f[~fr].text_norm)
    d["region"] = d.text_norm.isin(excl)
    print(f"  distinct texts at held-out facilities: {len(in_held):,}; only there: {len(excl):,}")
    r = d[d.region & ~d.gold]
    print("  region-only texts with a v0 grade-tag hit:",
          {tg: int(r['rx_' + tg].sum()) for tg in ("csection", "blood", "anesthesia", "ot", "icu",
                                                    "ventilator", "dialysis", "dialysis_unit", "nicu")})
    # S5 (no cue): test by signature group; training sample from the rest
    s5 = d.stratum.eq("S5")
    d["test"] = False
    d.loc[s5, "test"] = d.loc[s5, "sig"].map(in_test)
    s5_rest = d[s5 & ~d.test & ~d.gold & ~d.region]
    take = s5_rest.sample(min(S5_TRAIN_SAMPLE, len(s5_rest)), random_state=SEED).index
    d["s5_train"] = False
    d.loc[take, "s5_train"] = True
    q = len(take) / len(s5_rest)
    # S1 to S4: test by near-duplicate component (signature in --fast mode)
    g14 = d[~s5 & ~d.gold]
    graph = None
    if fast:
        d.loc[g14.index, "test"] = g14.sig.map(in_test)
        print("  (fast mode: S1-S4 test split by signature group, not by component)")
    else:
        t0 = time.time()
        idx = np.r_[g14.index.values, take.values]
        A, vec, X = near_dup_graph(d.loc[idx, "text_norm"].tolist())
        ncomp, lab = connected_components(A, directed=False)
        sizes = np.bincount(lab)
        key = pd.Series(d.loc[idx, "sig"].values).groupby(lab).min()
        comp_test = key.map(in_test)
        n14 = len(g14)
        d.loc[g14.index, "test"] = comp_test.reindex(lab[:n14]).values
        graph = dict(idx=idx, A=A, vec=vec, X=X, lab=lab, n14=n14)
        print(f"  near-duplicate graph over {len(idx):,} texts (S1-S4 {n14:,} + S5 training sample "
              f"{len(take):,}) in {time.time() - t0:.0f} s: {ncomp:,} components, largest {sizes.max()}, "
              f"texts in a component of 2+: {(sizes[lab] >= 2).mean():.1%}")
    t = d[~d.gold]
    print("test split (texts per stratum):", t[t.test].stratum.value_counts().sort_index().to_dict(),
          "total", f"{int(t.test.sum()):,}")
    for tag in ("csection", "blood", "icu", "dialysis_unit"):
        print(f"  v0 '{tag}' texts in test: {int(t[t.test]['rx_' + tag].sum())} of {int(t['rx_' + tag].sum())}")
    pool = d[(~s5 & ~d.gold & ~d.test & ~d.region) | d.s5_train]
    print(f"training pool: {len(pool):,} texts", pool.stratum.value_counts().sort_index().to_dict(),
          f"S5 inclusion probability {q:.4f} (weight {1 / q:.1f})")
    return d, pool, graph


def block_h(d, gold):
    head("H", "Agent-label worklist for the no-key path")
    gs = set(gold.text_norm)
    cs = d.rx_csection | d.text_norm.str.contains(CS_VARIANTS)
    bl = d.rx_blood | d.text_norm.str.contains(BLOOD_VARIANTS)
    s4 = d.stratum == "S4"
    ng = ~d.text_norm.isin(gs)
    NEG = (r"\bno\b|\bnot\b|without|unavailable|lack|absence|\bnone\b|refer(red|s)? (to|out)|does not|doesn't|"
           r"do not|isn't|is not|n't have|no longer|discontinued|non-functional|not functional")
    anyev = d[["rx_" + t for t in EV_TAGS]].any(axis=1)
    negev = anyev & d.text_norm.str.contains(NEG) & ~cs & ~bl
    print(f"evidence-tagged texts with a negation cue, outside the C-section and blood candidates: "
          f"{int((negev & ng).sum()):,}")
    print(f"C-section candidates: {int((cs & ng).sum()):,}; blood candidates: {int((bl & ng).sum()):,}; "
          f"S4 negation and meta pool: {int((s4 & ng).sum()):,}; union {int(((cs | bl | s4) & ng).sum()):,} "
          f"(gold sample texts excluded)")
    x = d[(cs | bl) & ng]
    print(f"  fact rows covered by the C-section and blood candidates: {int(x.n.sum()):,}; "
          f"characters to read: {int(x.text_norm.str.len().sum()):,}")


def block_i(gold):
    head("I", "Gold sample (labels/gold_sample.csv) and v0-to-v1 definition differences")
    print("rows:", len(gold), "| strata kinds:",
          gold.stratum.str.extract(r"^(fm|re|nm|random)")[0].value_counts().to_dict())
    t = gold.text_norm
    checks = {
        "names a nephrologist": t.str.contains("nephrologist"),
        "anaesthesiology as a department or specialty word": t.str.contains(r"an(a)?esthesiology"),
        "BiPAP or CPAP": t.str.contains(r"bipap|cpap"),
        "exchange transfusion": t.str.contains("exchange transfusion"),
        "neonatal and ICU words": t.str.contains(r"neonat|nicu") & t.str.contains(r"\bicu\b|intensive"),
        "fm_negation stratum (needs the negated tag)": gold.stratum.eq("fm_negation"),
    }
    for k, m in checks.items():
        print(f"  {k}: {int(m.sum())}")
    gp = paths.labels_dir() / "gold_facts.csv"
    if not gp.exists():
        print("  labels/gold_facts.csv not written yet")
        return
    gf = pd.read_csv(gp, dtype=str, keep_default_na=False)
    tg = "|" + gf.tags + "|"
    has = lambda t: tg.str.contains(f"|{t}|", regex=False)
    grade_any = np.column_stack([has(t) for t in GRADE_TAGS]).any(axis=1)
    person = gf.text_norm.str.contains(r"\bdr\.? |\bdr\b|\bdoctor [a-z]+ [a-z]+ is\b")
    print(f"gold_facts.csv: {len(gf)} rows, labeled_by {gf.labeled_by.value_counts().to_dict()}")
    print(f"  v1 reconciliation: rows naming a person (Dr) that carry a grade tag: {int((person & grade_any).sum())}; "
          f"of them dialysis_unit: {int((person & has('dialysis_unit')).sum())}, anesthesia: "
          f"{int((person & has('anesthesia')).sum())}")
    neg_note = gf.note.str.contains(r"negat|not available|absent|referred|not offered|unavailable", case=False)
    print(f"  rows whose note says negated or absent (candidates for the new negated tag): {int(neg_note.sum())}")
    name_note = gf.note.str.contains(r"\bname\b", case=False)
    print(f"  rows whose note mentions a name: {int(name_note.sum())}; of them with a grade tag other than "
          f"ob_delivery: {int((name_note & np.column_stack([has(t) for t in GRADE_TAGS[1:]]).any(axis=1)).sum())}")
    bip = gf.text_norm.str.contains(r"bipap|cpap")
    print(f"  BiPAP or CPAP rows tagged ventilator: {int((bip & has('ventilator')).sum())} of {int(bip.sum())}")


def block_j(d, gold):
    head("J", "Teacher prompt examples (Appendix A) against facts and gold")
    txt = DOC.read_text()
    a = txt.split("## Appendix A", 1)[1].split("## Appendix B", 1)[0]
    ex = sorted({e.strip().lower().rstrip(".") for e in re.findall(r'"([^"]{8,})"', a)})
    norm = lambda s: re.sub(r"\s+", " ", s.strip().lower()).rstrip(".")
    facts = set(d.text_norm.map(norm))
    gset = set(gold.text_norm.map(norm))
    exact = [e for e in ex if norm(e) in facts]
    print(f"quoted examples: {len(ex)}; exact matches to a fact: {len(exact)} {exact}; "
          f"to a gold row: {sum(norm(e) in gset for e in ex)}")
    from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
    cv = CountVectorizer(binary=True, token_pattern=r"[a-z0-9]+")
    Xf = cv.fit_transform(d.text_norm)
    Xe = cv.transform(ex)
    share = (Xf @ Xe.T).toarray() / np.maximum(Xe.sum(axis=1).A1, 1)
    cand = np.where((share >= 0.5).any(axis=1))[0]
    pool = d.text_norm.iloc[cand].tolist()
    tv = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True).fit(pool + ex)
    S = (tv.transform(ex) @ tv.transform(pool).T).toarray() if pool else np.zeros((len(ex), 1))
    best = S.max(axis=1)
    order = np.argsort(-best)[:5]
    print(f"candidate facts sharing half an example's words: {len(pool):,}; examples with a fact at char "
          f"TF-IDF cosine >= 0.9: {int((best >= 0.9).sum())}; highest: "
          f"{[(ex[i], pool[int(S[i].argmax())], round(float(best[i]), 2)) for i in order]}")
    gl = gold.text_norm.tolist()
    Sg = (tv.transform(ex) @ tv.transform(gl).T).toarray()
    print(f"gold rows within cosine 0.9 of any example: {int((Sg.max(axis=0) >= 0.9).sum())}")


def block_k(d, pool, graph):
    head("K", "Near-duplicate leakage (char 3-5-gram TF-IDF cosine >= 0.9)")
    A, lab, n14, idx, vec = graph["A"], graph["lab"], graph["n14"], graph["idx"], graph["vec"]
    pos = pd.Series(np.arange(len(idx)), index=idx)
    p = pos.reindex(pool.index).dropna().astype(int).values      # pool rows inside the graph
    Ap = A[p][:, p]
    rng = np.random.default_rng(SEED)
    val = rng.random(len(p)) < 0.1
    leak_text = (Ap[val][:, ~val].sum(axis=1).A1 > 0).mean()
    sig = d.loc[idx[p], "sig"].values
    sval = np.array([in_test(s + "#val") for s in sig])
    leak_sig = (Ap[sval][:, ~sval].sum(axis=1).A1 > 0).mean()
    comp = lab[p]
    uc = np.unique(comp)
    cval = np.isin(comp, uc[rng.random(len(uc)) < 0.1])
    leak_comp = (Ap[cval][:, ~cval].sum(axis=1).A1 > 0).mean()
    print(f"pool, 10% validation split by text: {leak_text:.1%} of validation texts have a near duplicate in "
          f"train; by signature group: {leak_sig:.1%}; by component: {leak_comp:.1%}")
    t14 = pos.reindex(d.index[d.test & ~d.gold & d.stratum.ne("S5")]).dropna().astype(int).values
    print(f"test S1-S4 texts with a near duplicate in the pool: {(A[t14][:, p].sum(axis=1).A1 > 0).mean():.1%}")
    s5t = d[d.test & d.stratum.eq("S5") & ~d.gold].sample(5000, random_state=SEED).text_norm
    Q = vec.transform(s5t)
    Xp = graph["X"][p]
    best = np.concatenate([(Q[i:i + 500] @ Xp.T).max(axis=1).toarray().ravel() for i in range(0, Q.shape[0], 500)])
    print(f"test S5 texts (sample of 5,000) with a near duplicate in the pool: {(best >= 0.9).mean():.1%}")


def block_l(fac, G):
    head("L", "Does graded access measure more than web visibility? (705 districts)")
    from scipy.stats import spearmanr
    from sklearn.linear_model import LinearRegression
    from sklearn.neighbors import BallTree
    po = pd.read_parquet(paths.silver_dir() / "post_offices.parquet")
    po = po[po.coord_valid & po.district_key.notna()].copy()
    n = pd.read_parquet(paths.silver_dir() / "nfhs.parquet").set_index("district_key")
    X = np.radians(po[["lat", "lon"]].values.astype(float))

    def dist(ids):
        ids = [i for i in ids if bool(fac.at[i, "coord_valid"])]
        Y = np.radians(fac.loc[ids, ["lat", "lon"]].values.astype(float))
        return BallTree(Y, metric="haversine").query(X, k=1)[0][:, 0] * 6371

    e2 = list(G.index[G.emoc >= 2])
    print("EmOC 2+ by operator:", fac.loc[e2, "operator_type"].value_counts(dropna=False).to_dict())
    po["d_e2"] = dist(e2)
    po["d_any"] = dist(list(fac.index[fac.coord_valid]))
    dd = po.groupby("district_key")[["d_e2", "d_any"]].median()
    cs = "births_delivered_by_csection_5y_pct"
    dev = ["women_age_15_49_with_10_or_more_years_of_schooling_pct", "hh_electricity_pct",
           "households_using_clean_fuel_for_cooking_pct"]
    dd = dd.join(n[[cs, "institutional_birth_in_public_facility_5y_pct"] + dev], how="inner").dropna()
    dd["not_public"] = 100 - dd.institutional_birth_in_public_facility_5y_pct
    print(f"districts: {len(dd)}; spearman(distance to EmOC 2+, C-section rate) "
          f"{spearmanr(dd.d_e2, dd[cs]).correlation:+.2f}; spearman(distance to any facility, C-section rate) "
          f"{spearmanr(dd.d_any, dd[cs]).correlation:+.2f}")
    Z = dd[dev].values
    res = lambda v, Z=Z: v - LinearRegression().fit(Z, v).predict(Z)
    le, la = np.log1p(dd.d_e2.values), np.log1p(dd.d_any.values)
    print(f"  partial, given development: graded vs C-section {spearmanr(res(le), res(dd[cs].values)).correlation:+.2f}; "
          f"graded vs share not in public facilities {spearmanr(res(le), res(dd.not_public.values)).correlation:+.2f}; "
          f"any-facility vs that share {spearmanr(res(la), res(dd.not_public.values)).correlation:+.2f}")
    Z2 = np.column_stack([Z, la])
    res2 = lambda v: v - LinearRegression().fit(Z2, v).predict(Z2)
    print(f"  partial, given development and any-facility distance: graded vs C-section "
          f"{spearmanr(res2(le), res2(dd[cs].values)).correlation:+.2f}")


def block_m(d, pool):
    head("M", "Teacher job sizes and cost (estimate: characters measured, tokens assumed)")
    txt = DOC.read_text()
    a = txt.split("## Appendix A", 1)[1].split("## Appendix B", 1)[0]
    sysp = a.split("```text", 1)[1].split("```", 1)[0] if "```text" in a else ""
    ng = ~d.gold
    core = ng & (d.index.isin(pool.index) | d.test | d.region)
    rest = ng & ~core
    ntag = d[["rx_" + t for t in EV_TAGS]].sum(axis=1)
    print(f"system prompt: {len(sysp):,} characters, {len(sysp.split()):,} words")
    B = 40
    for name, m in (("core (pool + test + region-only)", core), ("everything else", rest)):
        n = int(m.sum())
        R = -(-n // B)
        user = float((d.text_norm[m].str.len() + 5).sum())
        outc = float((20 + 16 * (ntag[m] + 0.8)).sum())
        line = f"  {name}: {n:,} texts, {R:,} requests;"
        for cpt in (2.4, 3.4):
            sys_t = len(sysp) / cpt + 150
            for think in (0, 1500):
                usd = (user / cpt * 0.10 + R * sys_t * 0.01 + sys_t * 0.125 + (outc / cpt + R * think) * 0.50) / 1e6
                line += f" {cpt} ch/tok think {think}: ${usd:.2f} (batch ${usd / 2:.2f});"
        print(line)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fast", action="store_true", help="skip the near-duplicate graph (block K; G splits S1-S4 by signature)")
    args = ap.parse_args()
    t0 = time.time()
    f, d, fac, G, gold = load()
    block_a(f, d, fac)
    block_b(f, d)
    block_c(f, G)
    block_d(f, d, fac, G)
    block_e(d)
    block_f(f, fac, G)
    d, pool, graph = block_g(f, d, fac, gold, args.fast)
    block_h(d, gold)
    block_i(gold)
    block_j(d, gold)
    if graph is not None:
        block_k(d, pool, graph)
    block_l(fac, G)
    block_m(d, pool)
    print(f"\ndone in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
