#!/usr/bin/env python
"""WP3 part 2: score a fact tagger against the hand-labeled gold set.

Scores the regex baseline (src/vf/recipes.py, the current recipe, v0.2) on all
24 evidence tags over the merged gold set, and keeps the earlier rounds as
history. Writes:

  labels/regex_scores.json (+ .meta.json)   per-tag precision, recall, F1,
                                            support, bootstrap CIs, every
                                            error, and the history runs
  reports/model_a_results.md                the results page (regex rows;
                                            teacher and student rows filled
                                            from labels/<method>_scores.json
                                            when those exist, else pending)
  reports/figures/wp3_regex_gold_scores.png precision and recall per tag

The gold set (labels/gold_facts.csv) is never used for training, prompt
tuning, regex writing, or choosing thresholds. It only produces these numbers.

The gold set has two parts:

* v0.1 rows (429): the original stratified sample (labels/gold_sample.csv),
  with design weights. Relabeled for the 11 v0.2 tags; their v0.1 tags are
  unchanged.
* v0.2 rows (348): drawn for the new tags (labels/gold_v02_candidates.csv):
  starter-pattern hits, near misses, and a random draw. These strata are not a
  partition of the population, so they have no design weights.

Runs scored:

* regex v0.2, 24 tags, all 777 rows: the results table. Plain counts.
* regex v0.2, the 13 v0.1 tags and the 11 new tags separately (macro only).
* v0.2 starter patterns (docs/06_capability_recipes.md), 11 new tags, all rows.
* history: regex v0.1 and v0 on the 429 v0.1 rows and 13 v0.1 tags, exactly
  as the v0.1 page reported them, with population-weighted estimates.

95% intervals come from a stratified bootstrap (resample within each stratum;
the v0.2 rows use their candidate strata).

Other taggers: call ``score_tags(gold, predicted_tag_sets)`` and write the
result with ``write_scores(method, ...)``; the results page picks up
labels/teacher_scores.json and labels/student_scores.json automatically.

Run:  .venv/bin/python scripts/wp3_score.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vf import paths, recipes  # noqa: E402
from vf import plotstyle as ps  # noqa: E402

SCRIPT = "scripts/wp3_score.py"
GOLD_FILE = "gold_facts.csv"
SAMPLE_FILE = "gold_sample.csv"
CAND_FILE = "gold_v02_candidates.csv"
RELABEL_FILE = "gold_v02_relabel_old.csv"
TAGS = recipes.EVIDENCE_TAGS
TAGS_V01 = recipes.EVIDENCE_TAGS_V01
NEW_TAGS = recipes.NEW_TAGS_V02
N_BOOT = 2000
BOOT_SEED = 20261121
REPORT = paths.code_root() / "reports" / "model_a_results.md"
FIGURE = paths.code_root() / "reports" / "figures" / "wp3_regex_gold_scores.png"
#: Methods shown in the results table, in order, with what produces them.
METHODS = {
    "regex": f"recipe {recipes.RECIPE_VERSION} regexes (src/vf/recipes.py)",
    "teacher": "LLM teacher labels (WP3 part 3)",
    "student": "trained student tagger (WP4)",
}
#: The release before this one, scored on its own rows and tags as history.
HISTORY_VERSION = "v0.1"
#: The original tagger, scored next to v0.1 (history).
PREVIOUS_VERSION = "v0"

#: Why the three worst regex tags (recipe v0.2) fail, written after reading
#: every error on the merged gold set (Nov 21, 2026). Each example must still
#: be an error of the stated kind; guard_notes() stops the page from going
#: stale if labels or patterns change.
WORST_TAG_NOTES: dict[str, dict] = {
    "ob_delivery": {
        "plain": "obstetric",
        "why": (
            "The pattern is the v0.1 one, unchanged in v0.2, and it scores lower than in v0.1 (0.61 on the 429 "
            "v0.1 rows) because the 348 new rows hold many department and specialty lists. Precision: the bare "
            "word 'obstetric' fires on lists, department names and doctors' qualifications that never say the "
            "facility delivers babies. {fp_obstetric} of {fp} false positives have 'obstetric' as the only "
            "trigger, and {fp_added} of the {fp} come from the new rows, {fp_added_list} of those department or "
            "specialty lists. Recall: the gold set counts a caesarean as a delivery, and this pattern has no "
            "caesarean branch, so {fn_caesarean} of {fn} misses are caesarean texts. The caesarean misses change "
            "no EmOC grade, because a caesarean fact already gives grade 2. The false positives can lift a "
            "facility to grade 1 (claimed) with no delivery statement."
        ),
        "examples": [
            ("fp", "multispecialty hospital with departments including ophthalmology, orthopedics, neurosurgery, "
                   "obstetrics and gynecology, ent, general medicine, and pulmonology"),
            ("fp", "gynecology and obstetrics services provided at shri hari hospital"),
            ("fn", "performs caesarean delivery"),
        ],
    },
    "neurosurgery": {
        "plain": "neurosurgery",
        "why": (
            "Recall only: the regex made no false positive and missed {fn} of {support}. It treats "
            "'neurosurgery' and 'spine surgery' as specialty names, which tag only outside a list of departments "
            "and outside a staff biography. {fn_list} misses are lists, {fn_staff} are facts the staff cue "
            "blocks (it includes 'specializes in', which also catches a facility's own 'specializes in "
            "neurosurgical care'), and {fn_none} use wording no pattern covers ('spinal fracture fixation'). "
            "Part of this is the gold set, not the regex. The written rule (TAGGING_RULES in vf.recipes) says "
            "a specialty word in a list is a claim, not a service, yet of the {list_total} gold texts where "
            "neurosurgery appears only inside a list, {list_pos} are labeled neurosurgery. The four labelers "
            "split on this: {list_by_chunk}. So this F1 mixes regex misses with label disagreement, and the "
            "true recall on texts that follow the rule is higher."
        ),
        "examples": [
            ("fn", "specializes in neurosurgical care"),
            ("fn", "specializes in minimally invasive spine surgery"),
            ("fn", "spinal fracture fixation"),
        ],
    },
    "ventilator": {
        "plain": "ventilation",
        "why": (
            "Recall: the pattern needs the word 'ventilator' (or 'ventilatory'), so 'mechanical ventilation', "
            "'non-invasive ventilation' and 'neonatal ventilation' are missed ({fn_ventilation} of {fn} misses). "
            "The new rows add {fn_added} misses, {fn_neonatal} of them 'neonatal ventilation', which is why the "
            "tag falls from 0.84 on the v0.1 rows. The regex tags {fn_neonatal_caught} of those {fn_neonatal} "
            "`neonatal_resp`, which the `nicu` line accepts in place of `ventilator`, so they cost no NICU grade. "
            "The other misses can cost the `icu` line its corroboration, which needs `icu` and `ventilator`. One "
            "gold row labels bare 'bipap' as ventilator, which the v0.1 labeling rule excludes (CPAP, BiPAP or "
            "high-flow oxygen alone is not). Precision: it fires when "
            "ventilators are a stated need rather than equipment on hand, and on an ambiguous photo caption "
            "({fp} false positives)."
        ),
        "examples": [
            ("fn", "provides neonatal ventilation support"),
            ("fn", "non-invasive mechanical ventilation"),
            ("fp", "need for monitors, ventilators, infusion pumps, and defibrillators to augment "
                   "icu facilities"),
        ],
    },
}

#: Tags within 0.01 F1 of the third worst: one paragraph each, guarded the same way.
NEAR_TIE_NOTES: dict[str, dict] = {
    "burns": {
        "why": (
            "{fn} misses: {fn_list} are lists where 'burns management' or 'burn care' is a list item, and "
            "{fn_staff} is a fact the staff cue blocks through 'specializes in'; {fn_none} ('dedicated burns "
            "operation theatre') matches no pattern. The regex treats that wording as "
            "a specialty name; the labelers read it as a service, and the written rule does not settle it. This "
            "is why `burns` scores below its starter pattern ({starter_f1:.2f}), which had no list rule."
        ),
        "example": ("fn", "specializes in burns management"),
    },
    "icu": {
        "why": (
            "{fn_ccu} of {fn} misses are coronary or cardiac care units (CCU, ICCU), which the gold set counts "
            "as intensive care. The v0.1 `icu` pattern has no CCU branch; v0.2 tags them `ccu` (F1 1.00) but "
            "not `icu`, so a CCU-only facility gets no `icu` grade."
        ),
        "example": ("fn", "coronary care unit (ccu)"),
    },
}

#: The v0.1 notes on its three worst tags, kept as history and still guarded
#: against the v0.1 run on the 429 v0.1 rows.
WORST_TAG_NOTES_V01: dict[str, dict] = {
    "ob_delivery": {
        "why": (
            "Precision: the bare word 'obstetric' fires on specialty lists, department names and doctors' "
            "qualifications that never say the facility delivers babies. {fp_obstetric} of {fp} false positives "
            "are texts where 'obstetric' is the only trigger. Recall: no caesarean branch, so {fn_caesarean} of "
            "{fn} misses are caesarean texts. The plural 'normal deliveries' also slips past 'normal delivery'."
        ),
        "examples": [("fn", "facilitates normal deliveries")],
    },
    "icu": {
        "why": (
            "Recall: ICU types outside the pattern (ICCU, ITU, PICU, 'critical care' stated as a service) are "
            "missed, {fn_icu_type} of {fn} misses. Precision: 'intensive care' fires on a neonatal intensive "
            "care unit, and 'ICU' on an ICU ambulance and a research statistic ({fp} false positives)."
        ),
        "examples": [("fn", "has 14 itu beds"),
                     ("fp", "neonatal intensive care unit (nicu) level iii with 16 beds")],
    },
    "ventilator": {
        "why": (
            "Recall: 'mechanical ventilation' and 'non-invasive ventilation' have no 'ventilator' word "
            "({fn_ventilation} of {fn} misses). Precision: stated needs and an ambiguous photo caption."
        ),
        "examples": [("fn", "non-invasive mechanical ventilation")],
    },
}

#: Errors of v0 that v0.1 fixed, each with one real gold example. Guarded: each
#: must be an error of v0 and not of v0.1.
FIXED_IN_V01: list[tuple[str, str, str, str]] = [
    ("csection", "fp", "Word-internal 'c section'", "dedicated paediatric section"),
    ("csection", "fp", "VBAC is a vaginal birth after a past caesarean",
     "provides vaginal birth after cesarean (vbac) services as a supported option"),
    ("dialysis_unit", "fp", "'Haemodialysis' alone counted as a named unit", "provides hemodialysis treatment"),
    ("emergency", "fp", "Negation was not handled", "does not operate an emergency department"),
]

#: Other recurring v0.2 regex errors, each with one real gold example (guarded too).
OTHER_ERRORS: list[tuple[str, str, str, str]] = [
    ("radiotherapy", "fp", "Planned equipment worded as 'will include'",
     "phase ii will include truebeam radiation therapy system"),
    ("radiotherapy", "fp", "Techniques listed in a doctor's profile",
     "radiotherapy techniques listed in dr babita singh's profile include imrt, igrt, rapic arc, sbrt, srs, "
     "and image-guided brachytherapy."),
    ("chemo", "fp", "A general medical statement, not a service",
     "chemotherapy can boost the effect of radiation therapy"),
    ("cath_lab", "fn", "Coronary angiography in another word order", "angiography of coronary arteries"),
    ("cath_lab", "fn", "Transcatheter valve procedures and 'interventional cardiology'",
     "interventional cardiology services including tavi and cabg"),
    ("trauma", "fn", "Casualty handling trauma, with no trauma-unit word", "has a 24/7 casualty block to handle trauma"),
    ("burns", "fp", "A research award, not a burn service",
     "awarded the young investigator award at the international society of burn injuries (isbi)."),
    ("dialysis_unit", "fn", "Unit words outside the list ('dialysis ward', 'dialysis clinic')", "dialysis ward"),
    ("h24", "fn", "The v0.1 negation cue drops a hedged statement the gold set counts",
     "24x7 helpline (not listed? will not mention)"),
    ("emergency", "fp", "Trauma rehabilitation is not emergency care", "major trauma rehabilitation"),
    ("anesthesia", "fp", "Local or topical anaesthesia for a minor procedure",
     "cataract surgery performed under topical anesthesia with a 2.2 mm corneal incision"),
    ("scheme", "fp", "'Ayushman' as a hospital name", "ayushman hospital offers ivf and surrogacy services"),
]

#: The regex tag each v0.1 failure-mode stratum targets (for the stratum table).
FM_TARGET = {
    "fm_csection_wordpart": "csection",
    "fm_negation": None,  # any evidence tag
    "fm_haemodialysis_only": "dialysis_unit",
    "fm_neonatal_icu": "icu",
    "fm_obstetric_only": "ob_delivery",
}

#: v0.1 labeling rules, summarized for the results page (full text in the gold sidecar notes).
LABEL_RULES = [
    "Each text is labeled by its meaning, from a blind worksheet that showed only the text (no stratum, no regex output).",
    "Negated, absent, referred-out, planned, historical, hypothetical and 'not specified in the content' statements get no evidence tag.",
    "A specialty word is not a service: 'Nephrology department' is not dialysis, 'Obstetrics and gynaecology' is not ob_delivery, and 'critical care' in a specialty or department list is not icu. The exception is anesthesiology, where the specialty is the service.",
    "ob_delivery needs a delivery, childbirth, labour or maternity word. A caesarean is a delivery, so csection texts also get ob_delivery.",
    "dialysis_unit needs a named dialysis unit, centre, ward or clinic, dialysis machines or equipment, or a nephrologist. 'Haemodialysis' alone is dialysis only.",
    "icu covers ICU, MICU, SICU, CCU, ICCU, PICU, RICU, ITU, intensive care, and critical care beds or services stated as provided. A NICU alone is nicu, not icu. An HDU is neither.",
    "ventilator covers ventilators and mechanical ventilation, invasive or non-invasive, when named as ventilation. CPAP, BiPAP or high-flow oxygen alone is not.",
    "anesthesia excludes local or topical anaesthesia as part of a minor, dental or eye procedure, dental sedation, and epidural steroid injections for pain.",
    "emergency covers emergency, casualty, trauma and ambulance services, of any kind. scheme covers public schemes only (Ayushman Bharat/PM-JAY, CGHS, ECHS, ESI, state schemes), not private insurers, TPAs, or 'Ayushman' as a hospital name.",
    "A name counts as a claim ('Sai Clinic & Maternity' gets ob_delivery). Photo captions get photo_artifact, plus an evidence tag only when the unit is unambiguous.",
    "Noise tags (reputation, location, staff_name, accessibility, photo_artifact, web_presence, other_clinical) are labeled too, but the regex has none, so they are not scored here.",
]


# --------------------------------------------------------------------------
# io helpers
# --------------------------------------------------------------------------

def git_info() -> dict:
    root = paths.code_root()
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        commit = None
    try:
        dirty = bool(subprocess.run(
            ["git", "--no-optional-locks", "status", "--porcelain", "--", SCRIPT,
             "src/vf/recipes.py", "src/vf/clean.py"],
            cwd=root, capture_output=True, text=True, check=True).stdout.strip())
    except Exception:
        dirty = None
    return {"git_commit": commit, "code_uncommitted": dirty}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_atomic(path: Path, text: str) -> None:
    paths.ensure(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def split_tags(s: str) -> frozenset:
    return frozenset(t for t in s.split("|") if t)


def load_gold() -> pd.DataFrame:
    """Gold labels joined to the sample design (stratum, weight) and labeler chunk.

    ``part`` is ``v0.1`` for the original 429 rows and ``v0.2`` for the rows
    added for the new tags. v0.2 rows have weight NaN (no design weights) and
    stratum ``v02_<candidate stratum>``.
    """
    lab = paths.labels_dir()
    need = [lab / GOLD_FILE, lab / SAMPLE_FILE, lab / CAND_FILE, lab / RELABEL_FILE]
    missing = [str(p) for p in need if not p.exists()]
    if missing:
        raise SystemExit(f"missing input(s): {missing}. Run the WP3 gold scripts first.")
    gold, design, cand, relab = (pd.read_csv(p, dtype=str, keep_default_na=False) for p in need)
    if list(gold.columns) != ["text_norm", "tags", "labeled_by", "note"]:
        raise SystemExit(f"{need[0]} columns {list(gold.columns)} do not match the contract")
    if not gold["text_norm"].is_unique:
        raise SystemExit(f"{need[0]} has duplicate text_norm")
    df = gold.merge(design[["text_norm", "stratum", "phase", "stratum_size", "stratum_n", "weight"]],
                    on="text_norm", how="left", validate="one_to_one")
    df["part"] = np.where(df["stratum"].isna(), "v0.2", "v0.1")
    c = cand.set_index("text_norm")
    new = df["part"] == "v0.2"
    if not df.loc[new, "text_norm"].isin(c.index).all():
        raise SystemExit("gold texts missing from both gold_sample.csv and gold_v02_candidates.csv")
    df.loc[new, "stratum"] = "v02_" + df.loc[new, "text_norm"].map(c["stratum"])
    df.loc[new, "phase"] = "v0.2"
    chunk = pd.concat([cand[["text_norm", "chunk"]], relab[["text_norm", "chunk"]]]).drop_duplicates("text_norm")
    df["chunk"] = df["text_norm"].map(chunk.set_index("text_norm")["chunk"]).fillna("")
    df["gold"] = df["tags"].map(split_tags)
    df["weight"] = pd.to_numeric(df["weight"], errors="coerce")
    bad = {t for s in df["gold"] for t in s} - set(recipes.ALL_TAGS)
    if bad:
        raise SystemExit(f"unknown gold tags: {sorted(bad)}")
    return df


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------

def _prf(tp, fp, fn):
    """Precision, recall, F1 with 0 for an empty denominator (sklearn's zero_division=0)."""
    tp, fp, fn = (np.asarray(x, dtype=float) for x in (tp, fp, fn))
    with np.errstate(invalid="ignore", divide="ignore"):
        p = np.where(tp + fp > 0, tp / (tp + fp), 0.0)
        r = np.where(tp + fn > 0, tp / (tp + fn), 0.0)
        f = np.where(2 * tp + fp + fn > 0, 2 * tp / (2 * tp + fp + fn), 0.0)
    return p, r, f


def _counts(G, P, c):
    """tp, fp, fn per tag given per-row multiplicities c (counts or weights)."""
    return c @ (G & P), c @ (~G & P), c @ (G & ~P)


def _ci(a, axis=0):
    lo, hi = np.percentile(a, [2.5, 97.5], axis=axis)
    return lo, hi


def score_tags(gold: pd.DataFrame, pred: list, *, tags=TAGS, n_boot: int = N_BOOT,
               seed: int = BOOT_SEED) -> dict:
    """Score predicted tag sets (one per gold row, same order) against gold.

    Returns per-tag and summary metrics on the gold set and stratified-bootstrap
    95% intervals. Only ``tags`` are scored. Population-weighted estimates are
    added only when every row has a design weight (the v0.1 rows alone).
    """
    tags = tuple(tags)
    G = np.array([[t in s for t in tags] for s in gold["gold"]], dtype=bool)
    P = np.array([[t in set(s) for t in tags] for s in pred], dtype=bool)
    ones = np.ones(len(gold))
    w = gold["weight"].to_numpy(dtype=float)
    weighted = bool(np.isfinite(w).all())

    tp, fp, fn = _counts(G, P, ones)
    p, r, f = _prf(tp, fp, fn)
    mp, mr, mf = _prf(tp.sum(), fp.sum(), fn.sum())
    if weighted:
        wtp, wfp, wfn = _counts(G, P, w)
        wp, wr, wf = _prf(wtp, wfp, wfn)
        wmp, wmr, wmf = _prf(wtp.sum(), wfp.sum(), wfn.sum())

    # stratified bootstrap: resample rows within each stratum
    rng = np.random.default_rng(seed)
    groups = [np.flatnonzero(gold["stratum"].to_numpy() == s) for s in sorted(gold["stratum"].unique())]
    B = n_boot
    bp, br, bf = (np.empty((B, len(tags))) for _ in range(3))
    bwf = np.empty((B, len(tags)))
    bmacro, bmicro, bwmacro = np.empty(B), np.empty(B), np.empty(B)
    for b in range(B):
        c = np.zeros(len(gold))
        for idx in groups:
            np.add.at(c, rng.choice(idx, size=len(idx), replace=True), 1)
        btp, bfp, bfn = _counts(G, P, c)
        bp[b], br[b], bf[b] = _prf(btp, bfp, bfn)
        bmacro[b] = bf[b].mean()
        bmicro[b] = _prf(btp.sum(), bfp.sum(), bfn.sum())[2]
        if weighted:
            cw = c * w
            wtp_b, wfp_b, wfn_b = _counts(G, P, cw)
            bwf[b] = _prf(wtp_b, wfp_b, wfn_b)[2]
            bwmacro[b] = bwf[b].mean()

    def r4(x):
        return None if x is None else round(float(x), 4)

    def ci(a):
        lo, hi = _ci(a)
        return [r4(lo), r4(hi)]

    per_tag = {}
    for j, t in enumerate(tags):
        per_tag[t] = {
            "support": int(tp[j] + fn[j]),
            "predicted": int(tp[j] + fp[j]),
            "tp": int(tp[j]), "fp": int(fp[j]), "fn": int(fn[j]),
            "precision": r4(p[j]), "recall": r4(r[j]), "f1": r4(f[j]),
            "precision_ci95": ci(bp[:, j]), "recall_ci95": ci(br[:, j]), "f1_ci95": ci(bf[:, j]),
        }
        if weighted:
            per_tag[t]["weighted"] = {
                "precision": r4(wp[j]), "recall": r4(wr[j]), "f1": r4(wf[j]),
                "f1_ci95": ci(bwf[:, j]),
                "est_distinct_texts_with_tag": int(round(wtp[j] + wfn[j])),
                "est_distinct_texts_predicted": int(round(wtp[j] + wfp[j])),
            }
    ev_gold = [s & set(tags) for s in gold["gold"]]
    ev_pred = [set(s) & set(tags) for s in pred]
    exact = np.array([a == b for a, b in zip(ev_gold, ev_pred)])
    summary = {
        "rows": int(len(gold)),
        "tags": len(tags),
        "macro": {"precision": r4(p.mean()), "recall": r4(r.mean()), "f1": r4(f.mean()),
                  "f1_ci95": ci(bmacro)},
        "micro": {"precision": r4(mp), "recall": r4(mr), "f1": r4(mf), "f1_ci95": ci(bmicro)},
        "exact_match_evidence_tags": r4(exact.mean()),
    }
    if weighted:
        summary["weighted"] = {
            "macro_f1": r4(wf.mean()), "macro_f1_ci95": ci(bwmacro),
            "micro_precision": r4(wmp), "micro_recall": r4(wmr), "micro_f1": r4(wmf),
            "exact_match_evidence_tags": r4((w * exact).sum() / w.sum()),
        }
    return {"per_tag": per_tag, "summary": summary, "_matrices": (G, P)}


def sklearn_crosscheck(G, P, per_tag, tags) -> None:
    """Point estimates must equal scikit-learn's."""
    from sklearn.metrics import precision_recall_fscore_support
    sp, sr, sf, ss = precision_recall_fscore_support(G, P, average=None, zero_division=0)
    for j, t in enumerate(tags):
        m = per_tag[t]
        assert abs(m["precision"] - sp[j]) < 1e-4 and abs(m["recall"] - sr[j]) < 1e-4, t
        assert abs(m["f1"] - sf[j]) < 1e-4 and m["support"] == ss[j], t


def error_lists(gold: pd.DataFrame, G, P, tags) -> dict:
    out = {}
    for j, t in enumerate(tags):
        fp = gold[~G[:, j] & P[:, j]]
        fn = gold[G[:, j] & ~P[:, j]]
        item = lambda r: {"text_norm": r.text_norm, "stratum": r.stratum, "part": r.part,
                          "chunk": r.chunk, "note": r.note}
        out[t] = {"false_positives": [item(r) for r in fp.itertuples()],
                  "false_negatives": [item(r) for r in fn.itertuples()]}
    return out


def failure_mode_table(gold: pd.DataFrame, pred: list, tags=TAGS_V01) -> dict:
    out = {}
    for fm, target in FM_TARGET.items():
        rows = gold[gold["stratum"] == fm]
        idx = rows.index
        if target is None:
            fired = sum(len(set(pred[i]) & set(tags)) for i in idx)
            right = sum(len(set(pred[i]) & set(tags) & rows.at[i, "gold"]) for i in idx)
            out[fm] = {"texts": int(len(rows)), "target": "any evidence tag",
                       "regex_tags_fired": int(fired), "correct": int(right),
                       "precision": round(right / fired, 4) if fired else None}
        else:
            fired = sum(target in pred[i] for i in idx)
            right = sum(target in pred[i] and target in rows.at[i, "gold"] for i in idx)
            out[fm] = {"texts": int(len(rows)), "target": target,
                       "regex_tags_fired": int(fired), "correct": int(right),
                       "precision": round(right / fired, 4) if fired else None}
        out[fm]["population"] = int(rows["stratum_size"].iloc[0]) if len(rows) else 0
    return out


_CAES = re.compile(r"c[- ]?section|caesarean|cesarean|\blscs\b")
_OB_OTHER = re.compile(r"maternity|labou?r (room|ward)|normal delivery|childbirth")
_LIST = re.compile(recipes.SPECIALTY_LIST_CUE)
_CCU = re.compile(r"\b(a?ccu|iccu)\b|coronary care|cardiac care unit|cardiac intensive")


def note_counts(errors: dict, errors_v01: dict, gold: pd.DataFrame, per_tag: dict,
                starter_f1: dict) -> dict:
    """Numbers quoted in the hand-written notes, counted from the errors."""
    ob, ns, ve, bu, ic = (errors[t] for t in ("ob_delivery", "neurosurgery", "ventilator", "burns", "icu"))

    def reason(e, tag):
        return recipes.tag_reasons(e["text_norm"]).get(tag)

    # neurosurgery as a name inside a list, and how each labeler chunk labeled it
    neo = [e for e in ve["false_negatives"] if e["part"] == "v0.2" and "neonat" in e["text_norm"]
           and "ventilation" in e["text_norm"] and "ventilator" not in e["text_norm"]]
    lst = gold[[recipes.tag_reasons(t).get("neurosurgery") == "specialty_list" for t in gold["text_norm"]]]
    lpos = lst["gold"].map(lambda g: "neurosurgery" in g)
    by_chunk = []
    for ch in sorted(lst["chunk"].unique()):
        m = lst["chunk"] == ch
        by_chunk.append(f"labeler {ch} marked {int(lpos[m].sum())} of {int(m.sum())}")
    counts = {
        "ob_delivery": {
            "fp": len(ob["false_positives"]), "fn": len(ob["false_negatives"]),
            "fp_obstetric": sum(not _OB_OTHER.search(e["text_norm"]) for e in ob["false_positives"]),
            "fp_added": sum(e["part"] == "v0.2" for e in ob["false_positives"]),
            "fp_added_list": sum(e["part"] == "v0.2" and bool(_LIST.search(e["text_norm"]))
                                 for e in ob["false_positives"]),
            "fn_caesarean": sum(bool(_CAES.search(e["text_norm"])) and "vbac" not in e["text_norm"]
                                for e in ob["false_negatives"]),
        },
        "neurosurgery": {
            "fp": len(ns["false_positives"]), "fn": len(ns["false_negatives"]),
            "support": per_tag["neurosurgery"]["support"],
            "fn_list": sum(reason(e, "neurosurgery") == "specialty_list" for e in ns["false_negatives"]),
            "fn_staff": sum(reason(e, "neurosurgery") == "staff_bio" for e in ns["false_negatives"]),
            "fn_none": sum(reason(e, "neurosurgery") is None for e in ns["false_negatives"]),
            "list_total": int(len(lst)), "list_pos": int(lpos.sum()),
            "list_by_chunk": ", ".join(by_chunk),
        },
        "ventilator": {
            "fp": len(ve["false_positives"]), "fn": len(ve["false_negatives"]),
            "fn_ventilation": sum("ventilation" in e["text_norm"] and "ventilator" not in e["text_norm"]
                                  for e in ve["false_negatives"]),
            "fn_added": sum(e["part"] == "v0.2" for e in ve["false_negatives"]),
            "fn_neonatal": len(neo),
            "fn_neonatal_caught": sum("neonatal_resp" in recipes.tag_fact(e["text_norm"]) for e in neo),
        },
        "burns": {
            "fp": len(bu["false_positives"]), "fn": len(bu["false_negatives"]),
            "fn_list": sum(reason(e, "burns") == "specialty_list" for e in bu["false_negatives"]),
            "fn_staff": sum(reason(e, "burns") == "staff_bio" for e in bu["false_negatives"]),
            "fn_none": sum(reason(e, "burns") is None for e in bu["false_negatives"]),
            "starter_f1": starter_f1.get("burns", float("nan")),
        },
        "icu": {
            "fp": len(ic["false_positives"]), "fn": len(ic["false_negatives"]),
            "fn_ccu": sum(bool(_CCU.search(e["text_norm"])) for e in ic["false_negatives"]),
        },
    }
    # history: the v0.1 notes, counted on the v0.1 run
    ob1, ic1, ve1 = (errors_v01[t] for t in ("ob_delivery", "icu", "ventilator"))
    counts["v0.1"] = {
        "ob_delivery": {
            "fp": len(ob1["false_positives"]), "fn": len(ob1["false_negatives"]),
            "fp_obstetric": sum(e["stratum"] == "fm_obstetric_only" for e in ob1["false_positives"]),
            "fn_caesarean": sum(bool(_CAES.search(e["text_norm"])) for e in ob1["false_negatives"]),
        },
        "icu": {
            "fp": len(ic1["false_positives"]), "fn": len(ic1["false_negatives"]),
            "fn_icu_type": sum(bool(re.search(r"\b(iccu|itu|picu|micu|sicu|ccu)\b|critical care", e["text_norm"]))
                               for e in ic1["false_negatives"]),
        },
        "ventilator": {
            "fp": len(ve1["false_positives"]), "fn": len(ve1["false_negatives"]),
            "fn_ventilation": sum(bool(re.search(r"ventilation|ventilatory", e["text_norm"]))
                                  and "ventilator " not in e["text_norm"] for e in ve1["false_negatives"]),
        },
    }
    return counts


def _worst(per_tag: dict, tags, k: int = 3) -> list[str]:
    return sorted(tags, key=lambda t: (per_tag[t]["f1"], t))[:k]


def near_ties(per_tag: dict, tags=TAGS) -> list[str]:
    worst = _worst(per_tag, tags)
    cut = per_tag[worst[-1]]["f1"] + 0.01
    return [t for t in sorted(tags, key=lambda t: (per_tag[t]["f1"], t))
            if t not in worst and per_tag[t]["f1"] <= cut]


def guard_notes(scores: dict, errors: dict, v01: dict, errors_v01: dict, errors_prev: dict) -> list[str]:
    """Problems that would make the hand-written failure notes stale."""
    problems = []

    def is_error(errs, tag, kind, text):
        key = "false_positives" if kind == "fp" else "false_negatives"
        return text in {e["text_norm"] for e in errs[tag][key]}

    for tag, kind, _, text in FIXED_IN_V01:
        if not is_error(errors_prev, tag, kind, text):
            problems.append(f"{tag}: fixed example is not a {PREVIOUS_VERSION} {kind}: {text!r}")
        if is_error(errors_v01, tag, kind, text):
            problems.append(f"{tag}: fixed example is still a {HISTORY_VERSION} {kind}: {text!r}")
    worst = _worst(scores["per_tag"], TAGS)
    if set(worst) != set(WORST_TAG_NOTES):
        problems.append(f"three worst tags are now {worst}, notes cover {sorted(WORST_TAG_NOTES)}")
    ties = near_ties(scores["per_tag"])
    if set(ties) != set(NEAR_TIE_NOTES):
        problems.append(f"near-tie tags are now {ties}, notes cover {sorted(NEAR_TIE_NOTES)}")
    worst1 = _worst(v01["per_tag"], TAGS_V01)
    if set(worst1) != set(WORST_TAG_NOTES_V01):
        problems.append(f"v0.1 three worst tags are now {worst1}, history notes cover {sorted(WORST_TAG_NOTES_V01)}")
    examples = [(tag, kind, text) for tag, spec in WORST_TAG_NOTES.items() for kind, text in spec["examples"]]
    examples += [(tag, *spec["example"]) for tag, spec in NEAR_TIE_NOTES.items()]
    examples += [(tag, kind, text) for tag, kind, _, text in OTHER_ERRORS]
    for tag, kind, text in examples:
        if not is_error(errors, tag, kind, text):
            problems.append(f"{tag}: example is no longer a {recipes.RECIPE_VERSION} {kind}: {text!r}")
    for tag, spec in WORST_TAG_NOTES_V01.items():
        for kind, text in spec["examples"]:
            if not is_error(errors_v01, tag, kind, text):
                problems.append(f"{tag}: history example is no longer a {HISTORY_VERSION} {kind}: {text!r}")
    return problems


# --------------------------------------------------------------------------
# outputs
# --------------------------------------------------------------------------

def write_scores(method: str, result: dict, meta: dict) -> Path:
    path = paths.labels_dir() / f"{method}_scores.json"
    write_atomic(path, json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    sidecar = dict(meta, output=f"labels/{path.name}", rows=result["summary"]["rows"])
    write_atomic(path.with_name(path.name + ".meta.json"),
                 json.dumps(sidecar, indent=2, ensure_ascii=False, default=str) + "\n")
    return path


def load_other_scores() -> dict:
    out = {}
    for m in METHODS:
        if m == "regex":
            continue
        p = paths.labels_dir() / f"{m}_scores.json"
        if p.exists():
            out[m] = json.loads(p.read_text())
    return out


def ci_str(v, ci) -> str:
    return f"{v:.2f} ({ci[0]:.2f} to {ci[1]:.2f})"


def make_figure(res: dict) -> Path:
    ps.use()
    pt = res["per_tag"]
    order = sorted(TAGS, key=lambda t: (pt[t]["f1"], t))  # worst first, drawn on top
    macro = res["summary"]["macro"]
    plain = [WORST_TAG_NOTES[t]["plain"] for t in order[:3]]
    lo = min(min(pt[t]["precision"], pt[t]["recall"]) for t in TAGS) * 100
    x0 = max(0, int(np.floor((lo - 5) / 10) * 10))
    fig, gs = ps.figure(
        10.6,
        f"The regex tagger is precise on most of its {len(TAGS)} tags but misreads {plain[0]}, "
        f"{plain[1]} and {plain[2]} wording",
        f"Precision and recall of the recipe {recipes.RECIPE_VERSION} regexes per evidence tag, on "
        f"{res['summary']['rows']} hand-labeled gold facts. {len(NEW_TAGS)} tags are new in "
        f"{recipes.RECIPE_VERSION}. Tags sorted by F1, worst at the top. Macro F1 {macro['f1']:.2f}.",
        note="Gold set: stratified samples of distinct fact texts from silver/facts.parquet, labeled by agents "
             "(not a human); hard cases are over-drawn. Lines show the gap between precision and recall; where "
             "the two are equal the dots overlap. Source: scripts/wp3_score.py, labels/regex_scores.json.",
        legend_rows=1, left_in=1.7, right_in=1.2,
    )
    ax = fig.add_subplot(gs[0, 0])
    y = np.arange(len(order))
    P = np.array([pt[t]["precision"] for t in order]) * 100
    R = np.array([pt[t]["recall"] for t in order]) * 100
    for yi, a, b in zip(y, P, R):
        ax.plot([min(a, b), max(a, b)], [yi, yi], color=ps.AXIS, lw=ps.px(2), zorder=1,
                solid_capstyle="round")
    kw = dict(s=ps.px(9) ** 2 * 1.6, edgecolors=ps.SURFACE, linewidths=ps.px(1.5), zorder=3)
    ax.scatter(P, y, color=ps.BLUE, **kw)
    ax.scatter(R, y, color=ps.ORANGE, **kw)
    ax.set_yticks(y, [f"{t} (new)" if t in NEW_TAGS else t for t in order])
    ax.set_ylim(len(order) - 0.5, -0.6)
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(x0, 102)
    ax.set_xticks(list(range(x0, 101, 10)))
    ps.pct_axis(ax, "x")
    ps.clean_axes(ax, grid="x", keep=("bottom",))
    ax.set_xlabel("Score on the gold set")
    for yi, t in zip(y, order):
        ax.annotate(f"F1 {pt[t]['f1']:.2f}", xy=(1.0, yi), xycoords=("axes fraction", "data"),
                    xytext=(ps.px(10), 0), textcoords="offset points", va="center", ha="left",
                    fontsize=10.5, color=ps.INK_2)
    ps.panel_title(ax, "Precision and recall per tag",
                   [("Precision", ps.BLUE, "dot"), ("Recall", ps.ORANGE, "dot")])
    return ps.save(fig, FIGURE)


def _ex_label(kind: str) -> str:
    return "False positive (regex tags it, gold does not)" if kind == "fp" else \
        "Missed (gold tags it, regex does not)"


def render_md(res: dict, groups: dict, starter: dict, v01: dict, prev: dict, others: dict,
              fm: dict, fm_prev: dict, errors_v01: dict, counts: dict, gold: pd.DataFrame,
              gold_meta: dict, sample_meta: dict, cand_meta: dict) -> str:
    s = res["summary"]
    pt = res["per_tag"]
    worst = _worst(pt, TAGS)
    ties = near_ties(pt)
    pos = {t: pt[t]["support"] for t in TAGS}
    n_new_rows = int((gold["part"] == "v0.2").sum())
    n_old_rows = int((gold["part"] == "v0.1").sum())
    n_phase2 = int((gold["phase"] == "2").sum())
    n_high = sum(pt[t]["f1"] >= 0.85 for t in TAGS)
    under = [t for t in TAGS if pos[t] < 25]
    clearly_worse = [t for t in TAGS
                     if pt[t]["f1_ci95"][1] < min(pt[u]["f1_ci95"][0] for u in TAGS if u != t)]
    zero_err = [t for t in TAGS if pt[t]["fp"] == 0 and pt[t]["fn"] == 0]
    g01, gnew = groups["v0.1_tags"]["summary"], groups["new_tags"]["summary"]
    v1s, v1t = v01["summary"], v01["per_tag"]
    sts = starter["summary"]
    ver = recipes.RECIPE_VERSION

    L = []
    L.append("# Model A results: fact tagger\n")
    L.append(f"Model A gives each fact text zero or more evidence tags (recipe {ver}, "
             "`docs/06_capability_recipes.md`). This page scores each tagger against the hand-labeled gold set "
             "(`data/labels/gold_facts.csv`).\n")
    L.append("**The gold set is never used for training, prompt tuning, regex writing, or choosing thresholds.** "
             "It only produces the numbers on this page. Teacher prompts and student models must be built "
             "without looking at it.\n")

    # ---------------------------------------------------------------- summary
    L.append("## Summary\n")
    L.append(
        f"The recipe {ver} regex reaches a macro F1 of {s['macro']['f1']:.2f} (95% interval "
        f"{s['macro']['f1_ci95'][0]:.2f} to {s['macro']['f1_ci95'][1]:.2f}) over all {len(TAGS)} evidence tags "
        f"on {s['rows']} gold facts. {n_high} of {len(TAGS)} tags score 0.85 or more. The three weakest are "
        + ", ".join(f"`{t}` ({pt[t]['f1']:.2f})" for t in worst)
        + (", with " + " and ".join(f"`{t}` ({pt[t]['f1']:.2f})" for t in ties) + " close behind"
           if ties else "")
        + ". The weak tags fail for plain reasons (below). Some of it is regex wording, and some is gold labels "
        "that disagree with the written rules. "
        f"The {len(NEW_TAGS)} new tags score a macro F1 of {gnew['macro']['f1']:.2f}; the {len(TAGS_V01)} v0.1 "
        f"tags score {g01['macro']['f1']:.2f} on the full set. On the original {v1s['rows']} rows the v0.1 tags "
        f"score {v1s['macro']['f1']:.2f}, exactly as recipe v0.1 reported, because v0.2 does not change their "
        "patterns. They score lower on the full set because the added rows over-draw department lists and "
        "cardiac care units and neonatal ventilation, which those patterns handle badly. The gold set over-draws regex matches and near "
        "misses on purpose, so these are rates on hard cases, not on a typical fact.\n")
    L.append("| Method | What it is | Tags | Gold rows | Macro precision | Macro recall | Macro F1 (95% CI) | "
             "Micro F1 | Exact match | Status |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")

    def row(name, what, sm, status):
        # other taggers' score files may name the exact-match key differently
        ex = sm.get("exact_match_evidence_tags", sm.get("exact_match"))
        ex = "" if ex is None else f"{ex:.2f}"
        return (f"| {name} | {what} | {sm.get('tags', '')} | {sm['rows']} | {sm['macro']['precision']:.2f} | "
                f"{sm['macro']['recall']:.2f} | {ci_str(sm['macro']['f1'], sm['macro']['f1_ci95'])} | "
                f"{sm['micro']['f1']:.2f} | {ex} | {status} |")

    L.append(row(f"regex {ver}", METHODS["regex"], s, "done"))
    L.append(row(f"regex {ver}, v0.1 tags", f"the same run, the {len(TAGS_V01)} v0.1 tags only", g01, "done"))
    L.append(row(f"regex {ver}, new tags", f"the same run, the {len(NEW_TAGS)} new tags only", gnew, "done"))
    L.append(row(f"{ver} starter patterns", "loose patterns from the recipes doc, before reading facts "
                 "(new tags only)", sts, "comparison"))
    L.append(row(f"regex {HISTORY_VERSION}", "history: recipe v0.1, on the v0.1 gold rows", v1s, "superseded"))
    L.append(row(f"regex {PREVIOUS_VERSION}", "history: original patterns, before the v0.1 fixes",
                 prev["summary"], "superseded"))
    for m in ("teacher", "student"):
        if m in others:
            o = others[m]
            what = METHODS[m] + (f", {o['model']}" if o.get("model") else "")
            L.append(row(m, what, {"tags": len(o.get("tags_scored", [])) or "", **o["summary"]},
                         "scored"))
        else:
            L.append(f"| {m} | {METHODS[m]} | | | pending | pending | pending | pending | pending | pending |")
    L.append("")
    L.append("Macro averages weight each evidence tag equally. Micro F1 pools every (fact, tag) decision. Exact "
             "match is the share of gold facts whose full evidence-tag set (over the tags scored) is predicted "
             "exactly. Intervals come from a stratified bootstrap (2,000 resamples within each sampling stratum). "
             f"The v0.1 and v0 rows are history: the same numbers the v0.1 page reported, recomputed here on "
             f"the {v1s['rows']} rows that existed then.\n")
    L.append(f"![Regex precision and recall per tag](figures/{FIGURE.name})\n")

    # ---------------------------------------------------------------- per tag
    L.append("## Per tag\n")
    L.append(f"| Tag | New in {ver} | Gold positives | Regex precision | Regex recall | Regex F1 (95% CI) | "
             f"Regex {HISTORY_VERSION} F1 ({v1s['rows']} rows) | Starter F1 | Teacher F1 | Student F1 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for t in TAGS:
        m = pt[t]
        other = []
        for o in ("teacher", "student"):
            ot = others.get(o, {}).get("per_tag", {}).get(t)
            other.append(f"{ot['f1']:.2f}" if ot else "pending")
        hist = f"{v1t[t]['f1']:.2f}" if t in v1t else ""
        st = f"{starter['per_tag'][t]['f1']:.2f}" if t in starter["per_tag"] else ""
        L.append(f"| `{t}` | {'yes' if t in NEW_TAGS else ''} | {m['support']} | "
                 f"{m['precision']:.2f} ({m['tp']}/{m['predicted']}) | {m['recall']:.2f} ({m['tp']}/{m['support']}) | "
                 f"{ci_str(m['f1'], m['f1_ci95'])} | {hist} | {st} | {other[0]} | {other[1]} |")
    L.append("")
    sep = ("Only " + ", ".join(f"`{t}`" for t in clearly_worse) + " has an F1 interval entirely below "
           "every other tag's.") if clearly_worse else "No tag's F1 interval lies entirely below all the others."
    one = len(zero_err) == 1
    zero = (" " + ", ".join(f"`{t}`" for t in zero_err) + " made no errors on the gold set, so "
            + ("its interval has" if one else "their intervals have") + " zero width. That does not make "
            + ("it" if one else "them") + " perfect; it means the sample found no error.") if zero_err else ""
    small = (" " + ", ".join(f"`{t}` ({pos[t]})" for t in under) + " have fewer than 25 gold positives, so "
             "their intervals are wide.") if under else ""
    L.append("Precision and recall show their counts in brackets. Most F1 intervals overlap, so the order "
             f"of the middle tags is not reliable. {sep}{zero}{small}\n")

    # ---------------------------------------------------------------- v0.2 changes
    L.append(f"## What recipe {ver} changed\n")
    better = [t for t in NEW_TAGS if pt[t]["f1"] > starter["per_tag"][t]["f1"] + 1e-9]
    worse = [t for t in NEW_TAGS if pt[t]["f1"] < starter["per_tag"][t]["f1"] - 1e-9]
    L.append(
        f"Recipe {ver} adds {len(NEW_TAGS)} evidence tags for the nine new service lines and leaves the "
        f"{len(TAGS_V01)} v0.1 patterns alone: on all {s['rows']} gold rows the {ver} tagger gives exactly the "
        f"v0.1 output on the v0.1 tags. Each new tag started from a loose pattern in the recipes doc, and its "
        "final regex was written by reading matches and near misses in `silver/facts.parquet`, not gold "
        f"labels. Against the starter patterns, the new tags' macro F1 moves from {sts['macro']['f1']:.2f} to "
        f"{gnew['macro']['f1']:.2f}"
        + (f"; F1 rises on " + ", ".join(f"`{t}` ({starter['per_tag'][t]['f1']:.2f} to {pt[t]['f1']:.2f})"
                                          for t in better) if better else "")
        + (f", and falls on " + ", ".join(f"`{t}` ({starter['per_tag'][t]['f1']:.2f} to {pt[t]['f1']:.2f})"
                                           for t in worse) if worse else "")
        + ". Read this comparison with care: the added gold rows were drawn from starter-pattern hits and near "
        "misses, so the sample is built around the starter's decisions, and the starter has no negation, list "
        "or referral handling at all. The drops are where the list and staff rules suppress names the labelers "
        "counted (next section).\n")

    # ---------------------------------------------------------------- worst tags
    L.append("## Where the regex fails: the three worst tags\n")
    for t in worst:
        spec = WORST_TAG_NOTES.get(t)
        m = pt[t]
        L.append(f"### `{t}`: F1 {m['f1']:.2f}, precision {m['precision']:.2f}, recall {m['recall']:.2f}\n")
        if spec is None:
            L.append("No notes written for this tag yet. See `false_positives` and `false_negatives` in "
                     "`data/labels/regex_scores.json`.\n")
            continue
        L.append(spec["why"].format(**counts.get(t, {})) + "\n")
        for kind, text in spec["examples"]:
            L.append(f"- {_ex_label(kind)}: \"{text}\"")
        L.append("")
    if ties:
        L.append("### Close behind\n")
        for t in ties:
            spec = NEAR_TIE_NOTES.get(t)
            m = pt[t]
            body = spec["why"].format(**counts.get(t, {})) if spec else "No notes written for this tag yet."
            L.append(f"`{t}` (F1 {m['f1']:.2f}, precision {m['precision']:.2f}, recall {m['recall']:.2f}): {body}")
            if spec:
                kind, text = spec["example"]
                L.append(f"{_ex_label(kind)}: \"{text}\".")
            L.append("")
    L.append("Other recurring errors (every error is listed in `data/labels/regex_scores.json`):\n")
    L.append("| Tag | Error | Kind | Example from the gold set |")
    L.append("| --- | --- | --- | --- |")
    for tag, kind, what, text in OTHER_ERRORS:
        L.append(f"| `{tag}` | {what} | {'false positive' if kind == 'fp' else 'missed'} | \"{text}\" |")
    L.append("")

    # ---------------------------------------------------------------- history
    L.append(f"## History: recipe {HISTORY_VERSION}\n")
    L.append(f"Recipe {HISTORY_VERSION} was scored on {v1s['rows']} gold rows and {len(TAGS_V01)} tags. Those numbers "
             f"are unchanged and recomputed here: macro F1 {ci_str(v1s['macro']['f1'], v1s['macro']['f1_ci95'])}, "
             f"micro F1 {v1s['micro']['f1']:.2f}. Its three weakest tags were "
             + ", ".join(f"`{t}` ({v1t[t]['f1']:.2f})" for t in _worst(v1t, TAGS_V01)) + ".\n")
    c1 = counts["v0.1"]
    for t in _worst(v1t, TAGS_V01):
        spec = WORST_TAG_NOTES_V01[t]
        m = v1t[t]
        L.append(f"- `{t}` (F1 {m['f1']:.2f}, precision {m['precision']:.2f}, recall {m['recall']:.2f}): "
                 + spec["why"].format(**c1[t]) + " Example: "
                 + "; ".join(f"{'false positive' if k == 'fp' else 'missed'} \"{x}\"" for k, x in spec["examples"])
                 + ".")
    L.append("")
    L.append(f"### What recipe {HISTORY_VERSION} changed\n")
    pv = prev["summary"]["macro"]
    changed = [t for t in TAGS_V01 if abs(v1t[t]["f1"] - prev["per_tag"][t]["f1"]) > 1e-9]
    L.append(
        f"Recipe {HISTORY_VERSION} fixes four failure modes of {PREVIOUS_VERSION} that changed facility "
        "grades (`docs/06_capability_recipes.md`, \"Changes in v0.1\"): a word boundary before \"c section\", "
        "VBAC wording ignored for `csection`, no `dialysis_unit` from \"haemodialysis\" alone, and no evidence "
        f"tag on a fact with a negation or absence cue. On the {v1s['rows']} v0.1 gold rows macro F1 moves from "
        f"{pv['f1']:.2f} to {v1s['macro']['f1']:.2f}, and the tags whose F1 changed are "
        + ", ".join(f"`{t}` ({prev['per_tag'][t]['f1']:.2f} to {v1t[t]['f1']:.2f})" for t in changed) + ". "
        "Read this gain as optimistic: the failure modes were found in WP3 part 1 before the gold set was drawn, "
        "and the patterns were written from the full fact table, not tuned on gold scores, but the gold set "
        "over-samples exactly these failure modes, so it rewards their fix more than a random sample would. "
        "The negation cue costs `h24` recall: hedged statements such as \"24x7 helpline (not listed? will not "
        "mention)\" lose their tag. `h24` is a bonus tag and never changes a grade.\n")
    L.append(f"Errors of {PREVIOUS_VERSION} that {HISTORY_VERSION} fixes, with a gold example of each:\n")
    L.append("| Tag | Error fixed | Kind | Example from the gold set |")
    L.append("| --- | --- | --- | --- |")
    for tag, kind, what, text in FIXED_IN_V01:
        L.append(f"| `{tag}` | {what} | {'false positive' if kind == 'fp' else 'missed'} | \"{text}\" |")
    L.append("")

    L.append("### Known v0.1 failure modes, measured\n")
    L.append("Five strata of the v0.1 gold rows were built from failure modes found in WP3 part 1. Inside each "
             f"one, this is how often the targeted regex tag is right, for {ver} (the same as {HISTORY_VERSION} "
             f"on these tags) and for {PREVIOUS_VERSION}. A stratum where the regex no longer fires shows no "
             "precision.\n")
    L.append(f"| Stratum | Distinct texts in data | Sampled | Tag checked | Regex fired | Right | Precision | "
             f"{PREVIOUS_VERSION} fired | {PREVIOUS_VERSION} right | {PREVIOUS_VERSION} precision |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    desc = sample_meta.get("failure_modes", {})
    for k, v in fm.items():
        prec = "" if v["precision"] is None else f"{v['precision']:.2f}"
        o = fm_prev[k]
        oprec = "" if o["precision"] is None else f"{o['precision']:.2f}"
        L.append(f"| `{k}` | {v['population']:,} | {v['texts']} | {v['target']} | {v['regex_tags_fired']} | "
                 f"{v['correct']} | {prec} | {o['regex_tags_fired']} | {o['correct']} | {oprec} |")
    L.append("")
    for k in fm:
        if k in desc:
            L.append(f"- `{k}`: {desc[k]}")
    L.append("")

    # ---------------------------------------------------------------- population
    L.append(f"### Population estimates (v0.1 tags, v0.1 rows)\n")
    w = v1s["weighted"]
    wob = v1t["ob_delivery"]["weighted"]
    wve = v1t["ventilator"]["weighted"]
    wt = dict(zip(gold["text_norm"], gold["weight"]))
    ob_fp = errors_v01["ob_delivery"]["false_positives"]
    ob_fp_w = sum(wt[e["text_norm"]] for e in ob_fp)
    ob_fp_obst = sum(wt[e["text_norm"]] for e in ob_fp if e["stratum"] == "fm_obstetric_only")
    old = gold[gold["part"] == "v0.1"]
    rnd = old[old["stratum"] == "random"]
    rnd_pos = int(rnd["gold"].map(lambda g: bool(g & set(TAGS_V01))).sum())
    rnd_pop = int(rnd["stratum_size"].iloc[0]) if len(rnd) else 0
    L.append(
        f"Only the v0.1 gold rows have design weights, so population estimates exist only for the "
        f"{len(TAGS_V01)} v0.1 tags. The {n_new_rows} rows added for {ver} were drawn from overlapping strata "
        "(starter hits, near misses, a random draw) with no inclusion probabilities, so there are no population "
        f"estimates for the new tags. Since {ver} leaves the v0.1 patterns unchanged, these hold for {ver} too.\n")
    L.append(
        f"Over all fact texts, the `ob_delivery` regex is mostly wrong: weighted precision is "
        f"{wob['precision']:.2f}, so about {1 - wob['precision']:.0%} of the distinct texts it tags have no "
        f"delivery statement, and {ob_fp_obst / ob_fp_w:.0%} of those are texts where 'obstetric' (a "
        "specialty, department or doctor's title) is the only trigger. The ventilator regex "
        f"finds an estimated {wve['recall']:.0%} of ventilation texts. "
        "Weighting each gold fact by its stratum's population over sample size estimates the rates over all "
        f"{sample_meta['population_distinct_texts']:,} distinct fact texts. Weighted macro F1 is "
        f"{w['macro_f1']:.2f} (95% interval {w['macro_f1_ci95'][0]:.2f} to {w['macro_f1_ci95'][1]:.2f}). "
        f"Treat these as rough. The random stratum has {len(rnd)} texts standing for {rnd_pop:,} texts with "
        f"no regex tag and no near-miss word. It held {rnd_pos} evidence positives"
        + (", which bounds misses there only loosely. So weighted recall is an optimistic estimate, and the "
           "bootstrap cannot show that.\n" if rnd_pos == 0 else
           ". Each one carries a large weight, so weighted recall is unstable.\n"))
    L.append("| Tag | Weighted precision | Weighted recall | Weighted F1 | Est. texts with the tag | Est. texts the regex tags |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    for t in TAGS_V01:
        m = v1t[t]["weighted"]
        L.append(f"| `{t}` | {m['precision']:.2f} | {m['recall']:.2f} | {m['f1']:.2f} | "
                 f"{m['est_distinct_texts_with_tag']:,} | {m['est_distinct_texts_predicted']:,} |")
    L.append("")

    # ---------------------------------------------------------------- gold set
    L.append("## Gold set\n")
    L.append(f"- {len(gold)} distinct `text_norm` values from `silver/facts.parquet`, in two parts.")
    L.append(
        f"- v0.1 part, {n_old_rows} texts, sampled by `scripts/wp3_gold_sample.py` (seed {sample_meta['seed']}). "
        "There are 30 strata: five known failure modes, texts the regex tags (grouped by their rarest tag), "
        "regex-untagged near misses (\"kidney\", \"critical\", \"round-the-clock\", ...), and a random draw from "
        f"everything else. Phase 2: after phase 1 (396 texts) was labeled, six tags had fewer than 25 positives, "
        f"and {n_phase2} more texts were drawn at random from those tags' strata (seed {sample_meta['seed_phase2']}). "
        f"These rows were later relabeled for the {len(NEW_TAGS)} new tags only; their v0.1 tags are unchanged, "
        f"and {gold_meta.get('old_rows_gaining_v02_tags', 0)} of them gained a new tag.")
    nstrata = len(cand_meta.get("strata_order", []))
    L.append(
        f"- {ver} part, {n_new_rows} texts, sampled by `scripts/wp3_gold_v02_sample.py` (seed "
        f"{cand_meta.get('seed')}) from texts not already in the gold set, in {nstrata} strata: for each new "
        "tag, 20 texts its starter pattern matches and 8 near misses (a related word, no starter match), plus "
        "40 at random. Merged by `scripts/wp3_gold_v02_merge.py`.")
    L.append(
        "- Labeled by agents (`labeled_by = agent`), from blind worksheets that showed no regex output. The "
        f"{ver} labels came from four agent labelers, one per chunk of rows. An agent-labeled gold set is weaker "
        "evidence than a human one, and the four labelers do not always agree (see `neurosurgery` above). "
        "Hruday should spot-check it before the numbers go in the video, and the methods note must say who "
        "labeled it.")
    L.append("- Positives per evidence tag: " + ", ".join(f"`{t}` {pos[t]}" for t in TAGS) + ".")
    if under:
        L.append("- Under the target of 25 positives: " + ", ".join(f"`{t}` ({pos[t]})" for t in under)
                 + ". The merge sidecar lists them under `evidence_tags_under_25`.")
    noise = gold_meta.get("positives_per_noise_tag", {})
    L.append("- Noise tags are labeled too, for scoring the teacher and student later: "
             + ", ".join(f"`{t}` {n}" for t, n in noise.items())
             + f". {gold_meta.get('texts_with_no_tag', 0)} texts have no tag at all.")
    L.append("")
    L.append("Labeling rules for the v0.1 tags:\n")
    for rule in LABEL_RULES:
        L.append(f"- {rule}")
    L.append("")
    L.append(f"Rules for every evidence tag in {ver} (`TAGGING_RULES` in `vf.recipes`; tag definitions are "
             "`TAG_DEFINITIONS` there):\n")
    for rule in recipes.TAGGING_RULES:
        L.append(f"- {rule}")
    L.append("")

    L.append("## Caveats\n")
    L.append("- These are fact-level scores on distinct texts. They are not facility-level grade accuracy: "
             "one wrong fact can change a grade, and many grade-relevant errors cancel out across a "
             "facility's other facts.")
    L.append("- The gold set cannot see contamination (a fact about a different organization filed under "
             "this facility). That is a facility-linkage problem, not a text-tagging one.")
    L.append("- Several labels are judgment calls; each has a note in `gold_facts.csv`. Where labelers split on "
             "a rule (specialty names in lists, 'burns management' as a list item, BiPAP as ventilation), the "
             "score mixes regex error with label disagreement. Adjudicating those rows would change "
             "`neurosurgery` and `burns` most.")
    if under:
        L.append(f"- {len(under)} tags have fewer than 25 gold positives; their scores rest on "
                 f"{min(pos[t] for t in under)} to {max(pos[t] for t in under)} positives each.")
    L.append("")
    L.append("## Reproduce\n")
    L.append("```bash")
    L.append(".venv/bin/python scripts/wp3_gold_sample.py sample   # v0.1 design; labels come from the hand-labeled TSV")
    L.append(".venv/bin/python scripts/wp3_gold_v02_merge.py       # merge the v0.2 labeled chunks")
    L.append(".venv/bin/python scripts/wp3_score.py")
    L.append("```\n")
    L.append(f"Generated by `{SCRIPT}` on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.")
    return "\n".join(L) + "\n"


def _slim(res: dict, tags) -> dict:
    return {"summary": res["summary"],
            "per_tag": {t: {k: res["per_tag"][t][k] for k in
                            ("support", "predicted", "tp", "fp", "fn", "precision", "recall", "f1", "f1_ci95")}
                        for t in tags}}


def main() -> int:
    gold = load_gold()
    texts = gold["text_norm"].tolist()

    # ------------------------------------------------ current recipe, all rows, all tags
    pred = recipes.tag_facts(texts)
    res = score_tags(gold, pred, tags=TAGS)
    G, P = res.pop("_matrices")
    sklearn_crosscheck(G, P, res["per_tag"], TAGS)
    errors = error_lists(gold, G, P, TAGS)
    groups = {}
    for name, tg in (("v0.1_tags", TAGS_V01), ("new_tags", NEW_TAGS)):
        r = score_tags(gold, pred, tags=tg)
        r.pop("_matrices")
        groups[name] = _slim(r, tg)

    # ------------------------------------------------ starter patterns (new tags)
    lab = paths.labels_dir()
    cand_meta = json.loads((lab / (CAND_FILE + ".meta.json")).read_text())
    starter_rx = {t: re.compile(cand_meta["starter_patterns"][t]) for t in NEW_TAGS}
    pred_st = [[t for t in NEW_TAGS if starter_rx[t].search(recipes.normalize_text(x))] for x in texts]
    r = score_tags(gold, pred_st, tags=NEW_TAGS)
    r.pop("_matrices")
    starter = _slim(r, NEW_TAGS)

    # ------------------------------------------------ history: v0.1 and v0 on the v0.1 rows
    old = gold[gold["part"] == "v0.1"].reset_index(drop=True)
    otexts = old["text_norm"].tolist()
    pred_v01 = recipes.tag_facts(otexts, version=HISTORY_VERSION)
    n_diff = sum(set(a) & set(TAGS_V01) != set(b) for a, b in zip(recipes.tag_facts(texts), recipes.tag_facts(texts, version=HISTORY_VERSION)))
    res_v01 = score_tags(old, pred_v01, tags=TAGS_V01)
    G1, P1 = res_v01.pop("_matrices")
    sklearn_crosscheck(G1, P1, res_v01["per_tag"], TAGS_V01)
    errors_v01 = error_lists(old, G1, P1, TAGS_V01)
    fm = failure_mode_table(old, pred_v01)
    pred_prev = recipes.tag_facts(otexts, version=PREVIOUS_VERSION)
    res_prev = score_tags(old, pred_prev, tags=TAGS_V01)
    Gp, Pp = res_prev.pop("_matrices")
    errors_prev = error_lists(old, Gp, Pp, TAGS_V01)
    fm_prev = failure_mode_table(old, pred_prev)
    prev = {
        "recipe_version": PREVIOUS_VERSION,
        "summary": res_prev["summary"],
        "per_tag": {t: {k: res_prev["per_tag"][t][k] for k in ("precision", "recall", "f1", "tp", "fp", "fn")}
                    for t in TAGS_V01},
        "failure_mode_strata": fm_prev,
    }

    counts = note_counts(errors, errors_v01, gold, res["per_tag"],
                         {t: starter["per_tag"][t]["f1"] for t in NEW_TAGS})
    gold_meta = json.loads((lab / (GOLD_FILE + ".meta.json")).read_text())
    sample_meta = json.loads((lab / (SAMPLE_FILE + ".meta.json")).read_text())
    worst = _worst(res["per_tag"], TAGS)
    result = {
        "method": "regex",
        "model_version": recipes.RECIPE_VERSION,
        "recipe_version": recipes.RECIPE_VERSION,
        "tags_scored": list(TAGS),
        "gold": {"rows": int(len(gold)), "rows_v01": int(len(old)), "rows_v02_added": int(len(gold) - len(old)),
                 "labeled_by": sorted(gold["labeled_by"].unique().tolist()),
                 "positives": {t: res["per_tag"][t]["support"] for t in TAGS}},
        "summary": res["summary"],
        "summary_by_tag_group": {"v0.1_tags": groups["v0.1_tags"]["summary"],
                                 "new_tags": groups["new_tags"]["summary"]},
        "per_tag": res["per_tag"],
        "worst_tags_by_f1": worst,
        "near_ties": near_ties(res["per_tag"]),
        "errors": errors,
        "starter_patterns": {"what": "v0.2 starter patterns from docs/06_capability_recipes.md, applied raw "
                                     "to text_norm (no negation, list or referral handling)", **starter},
        "history": {
            HISTORY_VERSION: {
                "what": "recipe v0.1 on the 429 v0.1 gold rows and 13 v0.1 tags, as first reported",
                "summary": res_v01["summary"], "per_tag": res_v01["per_tag"],
                "worst_tags_by_f1": _worst(res_v01["per_tag"], TAGS_V01),
                "failure_mode_strata": fm, "errors": errors_v01,
            },
            PREVIOUS_VERSION: prev,
        },
        "checks": {"rows_where_v02_and_v01_differ_on_v01_tags": int(n_diff)},
        "bootstrap": {"replicates": N_BOOT, "seed": BOOT_SEED,
                      "scheme": "rows resampled with replacement within each stratum: gold_sample strata for "
                                "the v0.1 rows, v02_<candidate stratum> for the rows added in v0.2"},
        "conventions": {
            "zero_division": 0,
            "weighted": "only in history.v0.1: each v0.1 gold row weighted by stratum population / stratum "
                        "sample size (labels/gold_sample.csv). The v0.2 rows have no design weights.",
            "unit": "distinct text_norm",
        },
        "use": "the gold set is never used for training, prompt tuning, regex writing, or choosing thresholds",
    }
    meta = {
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **git_info(),
        "recipe_version": recipes.RECIPE_VERSION,
        "model_version": recipes.RECIPE_VERSION,
        "method": "regex",
        "script": SCRIPT,
        "inputs": {f"labels/{n}": {"sha256": sha256(lab / n)} for n in (GOLD_FILE, SAMPLE_FILE, CAND_FILE, RELABEL_FILE)},
    }
    out = write_scores("regex", result, meta)

    problems = guard_notes(res, errors, res_v01, errors_v01, errors_prev)
    if n_diff:
        problems.append(f"v0.2 and v0.1 differ on the v0.1 tags for {n_diff} gold rows")
    if problems:
        print("STALE FAILURE NOTES, update the notes in scripts/wp3_score.py:")
        for p in problems:
            print("  -", p)
        print(f"scores written to {out}; results page and figure NOT rewritten")
        return 1

    fig_path = make_figure(res)
    others = load_other_scores()
    write_atomic(REPORT, render_md(res, groups, starter, res_v01, prev, others, fm, fm_prev, errors_v01,
                                   counts, gold, gold_meta, sample_meta, cand_meta))

    # ---------------------------------------------------------------- summary
    s = res["summary"]
    print(f"regex {recipes.RECIPE_VERSION} vs gold ({s['rows']} facts, {len(TAGS)} tags) -> {out}")
    print(f"macro P {s['macro']['precision']:.3f}  R {s['macro']['recall']:.3f}  "
          f"F1 {s['macro']['f1']:.3f} {s['macro']['f1_ci95']}  micro F1 {s['micro']['f1']:.3f}  "
          f"exact {s['exact_match_evidence_tags']:.3f}")
    for name in ("v0.1_tags", "new_tags"):
        g = groups[name]["summary"]
        print(f"  {name:10s} macro F1 {g['macro']['f1']:.3f} {g['macro']['f1_ci95']}  micro F1 {g['micro']['f1']:.3f}")
    print(f"  starter    macro F1 {starter['summary']['macro']['f1']:.3f} (new tags)")
    v = res_v01["summary"]
    print(f"history {HISTORY_VERSION} ({v['rows']} rows, {len(TAGS_V01)} tags): macro F1 {v['macro']['f1']:.3f} "
          f"{v['macro']['f1_ci95']}  micro F1 {v['micro']['f1']:.3f}  weighted macro F1 {v['weighted']['macro_f1']:.3f}")
    pv = prev["summary"]
    print(f"history {PREVIOUS_VERSION}: macro F1 {pv['macro']['f1']:.3f} {pv['macro']['f1_ci95']}")
    print(f"rows where v0.2 and v0.1 differ on v0.1 tags: {n_diff}")
    print(f"{'tag':16s} {'sup':>4s} {'P':>6s} {'R':>6s} {'F1':>6s}  F1 95% CI     v0.1 F1  starter F1")
    for t in TAGS:
        m = res["per_tag"][t]
        h = f"{res_v01['per_tag'][t]['f1']:.3f}" if t in res_v01["per_tag"] else "     "
        st = f"{starter['per_tag'][t]['f1']:.3f}" if t in starter["per_tag"] else ""
        print(f"{t:16s} {m['support']:>4d} {m['precision']:>6.3f} {m['recall']:>6.3f} {m['f1']:>6.3f}  "
              f"[{m['f1_ci95'][0]:.2f}, {m['f1_ci95'][1]:.2f}]  {h}    {st}")
    print("worst by F1:", ", ".join(worst), "| close behind:", ", ".join(near_ties(res["per_tag"])) or "none")
    under = [t for t in TAGS if res["per_tag"][t]["support"] < 25]
    print("tags under 25 positives:", ", ".join(under) if under else "none")
    print(f"report -> {REPORT}\nfigure -> {fig_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
