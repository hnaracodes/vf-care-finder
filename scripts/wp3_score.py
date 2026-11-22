#!/usr/bin/env python
"""WP3 part 2: score a fact tagger against the hand-labeled gold set.

Scores the regex baseline (src/vf/recipes.py, the current recipe, v0.1) on the
13 evidence tags, next to the original v0 patterns for comparison, and writes:

  labels/regex_scores.json (+ .meta.json)   per-tag precision, recall, F1,
                                            support, bootstrap CIs, population
                                            estimates, every error
  reports/model_a_results.md                the results table (regex row;
                                            teacher and student rows filled
                                            from labels/<method>_scores.json
                                            when those exist, else pending)
  reports/figures/wp3_regex_gold_scores.png precision and recall per tag

The gold set (labels/gold_facts.csv) is never used for training, prompt
tuning, or choosing thresholds. It only produces these numbers.

Two views of every metric:

* gold set: plain counts over the 429 gold texts. This is the results table.
  The sample over-draws regex matches and near misses on purpose, so these
  are not population rates.
* population-weighted: each gold text weighted by its stratum's
  population / sample size (labels/gold_sample.csv), which estimates the
  rates over all 275,095 distinct fact texts.

95% intervals come from a stratified bootstrap (resample within each stratum).

Other taggers: call ``score_tags(gold, predicted_tag_sets)`` and write the
result with ``write_scores(method, ...)``; the results page picks up
labels/teacher_scores.json and labels/student_scores.json automatically.

Run:  .venv/bin/python scripts/wp3_score.py
"""
from __future__ import annotations

import hashlib
import json
import os
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
TAGS = recipes.EVIDENCE_TAGS
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

#: The original tagger, scored next to the current one so the page shows what
#: the v0.1 fixes changed.
PREVIOUS_VERSION = "v0"

#: Why the three worst regex tags (recipe v0.1) fail, written after reading
#: every error on the gold set (Nov 21, 2026). Each example must still be an
#: error of the stated kind; guard_notes() stops the page from going stale if
#: labels or patterns change.
WORST_TAG_NOTES: dict[str, dict] = {
    "ob_delivery": {
        "plain": "obstetric",
        "why": (
            "Precision: the bare word 'obstetric' fires on specialty lists, department names and "
            "doctors' qualifications that never say the facility delivers babies. {fp_obstetric} of "
            "{fp} false positives are texts where 'obstetric' is the only trigger. Recall: the gold "
            "set counts a caesarean as a delivery, and this pattern has no caesarean branch, so "
            "{fn_caesarean} of {fn} misses are caesarean texts. The plural 'normal deliveries' also "
            "slips past 'normal delivery'. The caesarean misses change no EmOC grade, because a "
            "caesarean fact already gives grade 2. The false positives can lift a facility to grade 1 "
            "(claimed) with no delivery statement."
        ),
        "examples": [
            ("fp", "obstetrics and gynecology department includes infertility treatment and "
                   "gynecological laparoscopy."),
            ("fn", "facilitates normal deliveries"),
        ],
    },
    "icu": {
        "plain": "ICU",
        "why": (
            "Recall: ICU types outside the pattern are missed: intensive cardiac care units (ICCU), "
            "intensive therapy units (ITU), PICU, and 'critical care' stated as a service offered. "
            "{fn_icu_type} of {fn} misses are of this kind. Precision: 'intensive care' fires on a "
            "neonatal intensive care unit, which is nicu and not icu, and 'ICU' fires on an ICU ambulance "
            "and on a research statistic about ICU transfers ({fp} false positives in all, {fp_nicu} of "
            "them NICU texts)."
        ),
        "examples": [
            ("fn", "has 14 itu beds"),
            ("fp", "neonatal intensive care unit (nicu) level iii with 16 beds"),
        ],
    },
    "ventilator": {
        "plain": "ventilation",
        "why": (
            "Recall: the pattern needs the word 'ventilator' (or 'ventilatory'), so 'mechanical "
            "ventilation' and 'non-invasive ventilation' are missed ({fn_ventilation} of {fn} misses). "
            "Precision: it fires when ventilators are a stated need rather than equipment on hand, and "
            "on an ambiguous photo caption."
        ),
        "examples": [
            ("fn", "non-invasive mechanical ventilation"),
            ("fp", "need for monitors, ventilators, infusion pumps, and defibrillators to augment "
                   "icu facilities"),
        ],
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

#: Other recurring regex errors, each with one real gold example (guarded too).
OTHER_ERRORS: list[tuple[str, str, str, str]] = [
    ("dialysis_unit", "fn", "Unit words outside the list ('dialysis ward', 'dialysis clinic')", "dialysis ward"),
    ("h24", "fn", "The v0.1 negation cue drops a hedged statement the gold set counts",
     "24x7 helpline (not listed? will not mention)"),
    ("emergency", "fp", "Trauma rehabilitation is not emergency care", "major trauma rehabilitation"),
    ("anesthesia", "fp", "Local or topical anaesthesia for a minor procedure",
     "cataract surgery performed under topical anesthesia with a 2.2 mm corneal incision"),
    ("scheme", "fp", "'Ayushman' as a hospital name", "ayushman hospital offers ivf and surrogacy services"),
    ("emergency", "fn", "Ambulance services have no emergency word", "provides ambulance facilities"),
    ("h24", "fn", "Hyphenated 'round-the-clock'", "round-the-clock community cancer care center"),
]

#: The regex tag each failure-mode stratum targets (for the stratum table).
FM_TARGET = {
    "fm_csection_wordpart": "csection",
    "fm_negation": None,  # any evidence tag
    "fm_haemodialysis_only": "dialysis_unit",
    "fm_neonatal_icu": "icu",
    "fm_obstetric_only": "ob_delivery",
}

#: Labeling rules, summarized for the results page (full text in the gold sidecar notes).
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
    """Gold labels joined to the sample design (stratum, weight)."""
    lab = paths.labels_dir()
    need = [lab / GOLD_FILE, lab / SAMPLE_FILE]
    missing = [str(p) for p in need if not p.exists()]
    if missing:
        raise SystemExit(f"missing input(s): {missing}. Run scripts/wp3_gold_sample.py first.")
    gold = pd.read_csv(need[0], dtype=str, keep_default_na=False)
    design = pd.read_csv(need[1], dtype=str, keep_default_na=False)
    if list(gold.columns) != ["text_norm", "tags", "labeled_by", "note"]:
        raise SystemExit(f"{need[0]} columns {list(gold.columns)} do not match the contract")
    df = gold.merge(design[["text_norm", "stratum", "phase", "stratum_size", "stratum_n", "weight"]],
                    on="text_norm", how="left", validate="one_to_one")
    if df["stratum"].isna().any():
        raise SystemExit("gold texts missing from gold_sample.csv")
    df["gold"] = df["tags"].map(split_tags)
    df["weight"] = df["weight"].astype(float)
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

    Returns per-tag and summary metrics on the gold set, population-weighted
    estimates, and stratified-bootstrap 95% intervals. Only ``tags`` are scored.
    """
    G = np.array([[t in s for t in tags] for s in gold["gold"]], dtype=bool)
    P = np.array([[t in set(s) for t in tags] for s in pred], dtype=bool)
    ones = np.ones(len(gold))
    w = gold["weight"].to_numpy(dtype=float)

    tp, fp, fn = _counts(G, P, ones)
    p, r, f = _prf(tp, fp, fn)
    wtp, wfp, wfn = _counts(G, P, w)
    wp, wr, wf = _prf(wtp, wfp, wfn)
    mp, mr, mf = _prf(tp.sum(), fp.sum(), fn.sum())
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
            "weighted": {
                "precision": r4(wp[j]), "recall": r4(wr[j]), "f1": r4(wf[j]),
                "f1_ci95": ci(bwf[:, j]),
                "est_distinct_texts_with_tag": int(round(wtp[j] + wfn[j])),
                "est_distinct_texts_predicted": int(round(wtp[j] + wfp[j])),
            },
        }
    ev_gold = [s & set(tags) for s in gold["gold"]]
    ev_pred = [set(s) & set(tags) for s in pred]
    exact = np.array([a == b for a, b in zip(ev_gold, ev_pred)])
    return {
        "per_tag": per_tag,
        "summary": {
            "rows": int(len(gold)),
            "macro": {"precision": r4(p.mean()), "recall": r4(r.mean()), "f1": r4(f.mean()),
                      "f1_ci95": ci(bmacro)},
            "micro": {"precision": r4(mp), "recall": r4(mr), "f1": r4(mf), "f1_ci95": ci(bmicro)},
            "exact_match_evidence_tags": r4(exact.mean()),
            "weighted": {
                "macro_f1": r4(wf.mean()), "macro_f1_ci95": ci(bwmacro),
                "micro_precision": r4(wmp), "micro_recall": r4(wmr), "micro_f1": r4(wmf),
                "exact_match_evidence_tags": r4((w * exact).sum() / w.sum()),
            },
        },
        "_matrices": (G, P),
    }


def sklearn_crosscheck(G, P, per_tag) -> None:
    """Point estimates must equal scikit-learn's."""
    from sklearn.metrics import precision_recall_fscore_support
    sp, sr, sf, ss = precision_recall_fscore_support(G, P, average=None, zero_division=0)
    for j, t in enumerate(TAGS):
        m = per_tag[t]
        assert abs(m["precision"] - sp[j]) < 1e-4 and abs(m["recall"] - sr[j]) < 1e-4, t
        assert abs(m["f1"] - sf[j]) < 1e-4 and m["support"] == ss[j], t


def error_lists(gold: pd.DataFrame, G, P) -> dict:
    out = {}
    for j, t in enumerate(TAGS):
        fp = gold[~G[:, j] & P[:, j]]
        fn = gold[G[:, j] & ~P[:, j]]
        item = lambda r: {"text_norm": r.text_norm, "stratum": r.stratum, "note": r.note}
        out[t] = {"false_positives": [item(r) for r in fp.itertuples()],
                  "false_negatives": [item(r) for r in fn.itertuples()]}
    return out


def failure_mode_table(gold: pd.DataFrame, pred: list) -> dict:
    out = {}
    for fm, target in FM_TARGET.items():
        rows = gold[gold["stratum"] == fm]
        idx = rows.index
        if target is None:
            fired = sum(len(set(pred[i]) & set(TAGS)) for i in idx)
            right = sum(len(set(pred[i]) & set(TAGS) & rows.at[i, "gold"]) for i in idx)
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


def note_counts(errors: dict) -> dict:
    """Numbers quoted in WORST_TAG_NOTES, counted from the errors."""
    import re
    ob = errors["ob_delivery"]
    du = errors["dialysis_unit"]
    ve = errors["ventilator"]
    ic = errors["icu"]
    caes = re.compile(r"c[- ]?section|caesarean|cesarean|\blscs\b")
    return {
        "ob_delivery": {
            "fp": len(ob["false_positives"]), "fn": len(ob["false_negatives"]),
            "fp_obstetric": sum(e["stratum"] == "fm_obstetric_only" for e in ob["false_positives"]),
            "fn_caesarean": sum(bool(caes.search(e["text_norm"])) for e in ob["false_negatives"]),
        },
        "dialysis_unit": {
            "fp": len(du["false_positives"]), "fn": len(du["false_negatives"]),
            "fp_haemo": sum(not re.search(r"dialysis (unit|machine|centre|center)|nephrologist", e["text_norm"])
                            for e in du["false_positives"]),
        },
        "icu": {
            "fp": len(ic["false_positives"]), "fn": len(ic["false_negatives"]),
            "fp_nicu": sum(bool(re.search(r"\bnicu\b|neonatal", e["text_norm"])) for e in ic["false_positives"]),
            "fn_icu_type": sum(bool(re.search(r"\b(iccu|itu|picu|micu|sicu|ccu)\b|critical care", e["text_norm"]))
                               for e in ic["false_negatives"]),
        },
        "ventilator": {
            "fp": len(ve["false_positives"]), "fn": len(ve["false_negatives"]),
            "fn_ventilation": sum(bool(re.search(r"ventilation|ventilatory", e["text_norm"]))
                                  and "ventilator " not in e["text_norm"] for e in ve["false_negatives"]),
        },
    }


def guard_notes(scores: dict, errors: dict, errors_prev: dict | None = None) -> list[str]:
    """Problems that would make the hand-written failure notes stale."""
    problems = []
    if errors_prev is not None:
        for tag, kind, _, text in FIXED_IN_V01:
            key = "false_positives" if kind == "fp" else "false_negatives"
            if text not in {e["text_norm"] for e in errors_prev[tag][key]}:
                problems.append(f"{tag}: fixed example is not a {PREVIOUS_VERSION} {kind}: {text!r}")
            if text in {e["text_norm"] for e in errors[tag][key]}:
                problems.append(f"{tag}: fixed example is still a {recipes.RECIPE_VERSION} {kind}: {text!r}")
    worst = sorted(TAGS, key=lambda t: (scores["per_tag"][t]["f1"], t))[:3]
    if set(worst) != set(WORST_TAG_NOTES):
        problems.append(f"three worst tags are now {worst}, notes cover {sorted(WORST_TAG_NOTES)}")
    examples = [(tag, kind, text) for tag, spec in WORST_TAG_NOTES.items() for kind, text in spec["examples"]]
    examples += [(tag, kind, text) for tag, kind, _, text in OTHER_ERRORS]
    for tag, kind, text in examples:
        key = "false_positives" if kind == "fp" else "false_negatives"
        if text not in {e["text_norm"] for e in errors[tag][key]}:
            problems.append(f"{tag}: example is no longer a {kind}: {text!r}")
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


def f2(x) -> str:
    return "" if x is None else f"{x:.2f}"


def ci_str(v, ci) -> str:
    return f"{v:.2f} ({ci[0]:.2f} to {ci[1]:.2f})"


def make_figure(res: dict) -> Path:
    ps.use()
    pt = res["per_tag"]
    order = sorted(TAGS, key=lambda t: (pt[t]["f1"], t))  # worst first, drawn on top
    macro = res["summary"]["macro"]
    plain = [WORST_TAG_NOTES[t]["plain"] for t in order[:3]]
    fig, gs = ps.figure(
        7.4,
        f"The regex tagger is precise on most tags but misreads {plain[0]}, {plain[1]} and {plain[2]} wording",
        f"Precision and recall of the recipe {recipes.RECIPE_VERSION} regexes per evidence tag, on "
        f"{res['summary']['rows']} hand-labeled gold facts. Tags sorted by F1, worst at the top. "
        f"Macro F1 {macro['f1']:.2f}.",
        note="Gold set: stratified sample of distinct fact texts from silver/facts.parquet, labeled by an "
             "agent (not a human). Lines show the gap between precision and recall; where the two are equal "
             "the dots overlap (" + ", ".join(t for t in sorted(TAGS) if pt[t]["precision"] == pt[t]["recall"])
             + "). Source: scripts/wp3_score.py, labels/regex_scores.json.",
        legend_rows=1, left_in=1.6, right_in=1.2,
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
    ax.set_yticks(y, order)
    ax.set_ylim(len(order) - 0.5, -0.6)
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(50, 102)
    ax.set_xticks([50, 60, 70, 80, 90, 100])
    ps.pct_axis(ax, "x")
    ps.clean_axes(ax, grid="x", keep=("bottom",))
    ax.set_xlabel("Score on the gold set")
    # F1 printed in a column at the right edge
    for yi, t in zip(y, order):
        ax.annotate(f"F1 {pt[t]['f1']:.2f}", xy=(1.0, yi), xycoords=("axes fraction", "data"),
                    xytext=(ps.px(10), 0), textcoords="offset points", va="center", ha="left",
                    fontsize=10.5, color=ps.INK_2)
    ps.panel_title(ax, "Precision and recall per tag",
                   [("Precision", ps.BLUE, "dot"), ("Recall", ps.ORANGE, "dot")])
    return ps.save(fig, FIGURE)


def render_md(res: dict, others: dict, fm: dict, errors: dict, counts: dict, gold: pd.DataFrame,
              gold_meta: dict, sample_meta: dict, prev: dict) -> str:
    s = res["summary"]
    pt = res["per_tag"]
    worst = sorted(TAGS, key=lambda t: (pt[t]["f1"], t))[:3]
    pos = {t: pt[t]["support"] for t in TAGS}
    n_phase2 = int((gold["phase"] == "2").sum())
    n_high = sum(pt[t]["f1"] >= 0.85 for t in TAGS)
    # tags whose whole F1 interval lies below every other tag's interval
    clearly_worse = [t for t in TAGS
                     if pt[t]["f1_ci95"][1] < min(pt[u]["f1_ci95"][0] for u in TAGS if u != t)]
    zero_err = [t for t in TAGS if pt[t]["fp"] == 0 and pt[t]["fn"] == 0]
    wob = pt["ob_delivery"]["weighted"]
    wve = pt["ventilator"]["weighted"]
    wt = dict(zip(gold["text_norm"], gold["weight"]))
    ob_fp = errors["ob_delivery"]["false_positives"]
    ob_fp_w = sum(wt[e["text_norm"]] for e in ob_fp)
    ob_fp_obst = sum(wt[e["text_norm"]] for e in ob_fp if e["stratum"] == "fm_obstetric_only")
    rnd = gold[gold["stratum"] == "random"]
    rnd_pos = int(rnd["gold"].map(lambda g: bool(g & set(TAGS))).sum())
    rnd_pop = int(rnd["stratum_size"].iloc[0]) if len(rnd) else 0
    L = []
    L.append("# Model A results: fact tagger\n")
    L.append(f"Model A gives each fact text zero or more evidence tags (recipe {recipes.RECIPE_VERSION}, "
             "`docs/06_capability_recipes.md`). This page scores each tagger against the hand-labeled gold set "
             "(`data/labels/gold_facts.csv`).\n")
    L.append("**The gold set is never used for training, prompt tuning, or choosing thresholds.** It only "
             "produces the numbers on this page. Teacher prompts and student models must be built without "
             "looking at it.\n")
    L.append("## Summary\n")
    L.append(
        f"The regex baseline reaches a macro F1 of {s['macro']['f1']:.2f} (95% interval "
        f"{s['macro']['f1_ci95'][0]:.2f} to {s['macro']['f1_ci95'][1]:.2f}) over the 13 evidence tags on "
        f"{s['rows']} gold facts. {n_high} of 13 tags score 0.85 or more. The three weakest are "
        + ", ".join(f"`{t}` ({pt[t]['f1']:.2f})" for t in worst)
        + ". Their errors have plain causes (below) that a learned tagger should fix. The gold set over-draws "
        "regex matches and near misses on purpose, so these are rates on hard cases, not on a typical fact.\n")
    L.append("| Method | What it is | Macro precision | Macro recall | Macro F1 (95% CI) | Micro F1 | Exact match | Status |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    L.append(f"| regex | {METHODS['regex']} | {s['macro']['precision']:.2f} | {s['macro']['recall']:.2f} | "
             f"{ci_str(s['macro']['f1'], s['macro']['f1_ci95'])} | {s['micro']['f1']:.2f} | "
             f"{s['exact_match_evidence_tags']:.2f} | done |")
    ps_ = prev["summary"]
    L.append(f"| regex {PREVIOUS_VERSION} | original patterns, before the v0.1 fixes | "
             f"{ps_['macro']['precision']:.2f} | {ps_['macro']['recall']:.2f} | "
             f"{ci_str(ps_['macro']['f1'], ps_['macro']['f1_ci95'])} | {ps_['micro']['f1']:.2f} | "
             f"{ps_['exact_match_evidence_tags']:.2f} | superseded |")
    for m in ("teacher", "student"):
        if m in others:
            o = others[m]["summary"]
            L.append(f"| {m} | {METHODS[m]} | {o['macro']['precision']:.2f} | {o['macro']['recall']:.2f} | "
                     f"{ci_str(o['macro']['f1'], o['macro']['f1_ci95'])} | {o['micro']['f1']:.2f} | "
                     f"{o['exact_match_evidence_tags']:.2f} | done |")
        else:
            L.append(f"| {m} | {METHODS[m]} | pending | pending | pending | pending | pending | pending |")
    L.append("")
    L.append("Macro averages weight each of the 13 evidence tags equally. Micro F1 pools every (fact, tag) "
             "decision. Exact match is the share of gold facts whose full evidence-tag set is predicted "
             "exactly. Intervals come from a stratified bootstrap (2,000 resamples within each sampling "
             "stratum).\n")
    L.append(f"![Regex precision and recall per tag](figures/{FIGURE.name})\n")

    L.append("## Per tag\n")
    L.append(f"| Tag | Gold positives | Regex precision | Regex recall | Regex F1 (95% CI) | Regex {PREVIOUS_VERSION} F1 "
             "| Teacher F1 | Student F1 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for t in TAGS:
        m = pt[t]
        other = []
        for o in ("teacher", "student"):
            other.append(f"{others[o]['per_tag'][t]['f1']:.2f}" if o in others else "pending")
        L.append(f"| `{t}` | {m['support']} | {m['precision']:.2f} ({m['tp']}/{m['predicted']}) | "
                 f"{m['recall']:.2f} ({m['tp']}/{m['support']}) | {ci_str(m['f1'], m['f1_ci95'])} | "
                 f"{prev['per_tag'][t]['f1']:.2f} | {other[0]} | {other[1]} |")
    L.append("")
    sep = ("Only " + ", ".join(f"`{t}`" for t in clearly_worse) + " has an F1 interval entirely below "
           "every other tag's.") if clearly_worse else "No tag's F1 interval lies entirely below all the others."
    one = len(zero_err) == 1
    zero = (" " + " and ".join(f"`{t}`" for t in zero_err) + " made no errors on the gold set, so "
            + ("its interval has" if one else "their intervals have") + " zero width. That does not make "
            + ("it" if one else "them") + " perfect; it means the sample found no error.") if zero_err else ""
    L.append("Precision and recall show their counts in brackets. Most F1 intervals overlap, so the order "
             f"of the middle tags is not reliable. {sep}{zero}\n")

    L.append(f"## What recipe {recipes.RECIPE_VERSION} changed\n")
    pv = prev["summary"]["macro"]
    changed = [t for t in TAGS if abs(pt[t]["f1"] - prev["per_tag"][t]["f1"]) > 1e-9]
    L.append(
        f"Recipe {recipes.RECIPE_VERSION} fixes four failure modes of {PREVIOUS_VERSION} that changed facility "
        "grades (`docs/06_capability_recipes.md`, \"Changes in v0.1\"): a word boundary before \"c section\", "
        "VBAC wording ignored for `csection`, no `dialysis_unit` from \"haemodialysis\" alone, and no evidence "
        f"tag on a fact with a negation or absence cue. On the gold set macro F1 moves from {pv['f1']:.2f} to "
        f"{s['macro']['f1']:.2f}, and the tags whose F1 changed are "
        + ", ".join(f"`{t}` ({prev['per_tag'][t]['f1']:.2f} to {pt[t]['f1']:.2f})" for t in changed) + ". "
        "Read this gain as optimistic: the failure modes were found in WP3 part 1 before the gold set was drawn, "
        "and the patterns were written from the full fact table, not tuned on gold scores, but the gold set "
        "over-samples exactly these failure modes, so it rewards their fix more than a random sample would. "
        f"The negation cue costs `h24` recall: hedged statements such as \"24x7 helpline (not listed? will not "
        "mention)\" lose their tag. `h24` is a bonus tag and never changes a grade.\n")
    L.append(f"Errors of {PREVIOUS_VERSION} that {recipes.RECIPE_VERSION} fixes, with a gold example of each:\n")
    L.append("| Tag | Error fixed | Kind | Example from the gold set |")
    L.append("| --- | --- | --- | --- |")
    for tag, kind, what, text in FIXED_IN_V01:
        L.append(f"| `{tag}` | {what} | {'false positive' if kind == 'fp' else 'missed'} | \"{text}\" |")
    L.append("")
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
            label = "False positive (regex tags it, gold does not)" if kind == "fp" else \
                    "Missed (gold tags it, regex does not)"
            L.append(f"- {label}: \"{text}\"")
        L.append("")
    L.append("Other recurring errors (every error is listed in `data/labels/regex_scores.json`):\n")
    L.append("| Tag | Error | Kind | Example from the gold set |")
    L.append("| --- | --- | --- | --- |")
    for tag, kind, what, text in OTHER_ERRORS:
        L.append(f"| `{tag}` | {what} | {'false positive' if kind == 'fp' else 'missed'} | \"{text}\" |")
    L.append("")

    L.append("## Known failure modes, measured\n")
    L.append("Five strata were built from failure modes found in WP3 part 1. Inside each one, this is how "
             f"often the targeted regex tag is right, for {recipes.RECIPE_VERSION} and for {PREVIOUS_VERSION}. "
             "A stratum where the regex no longer fires shows no precision.\n")
    L.append(f"| Stratum | Distinct texts in data | Sampled | Tag checked | Regex fired | Right | Precision | "
             f"{PREVIOUS_VERSION} fired | {PREVIOUS_VERSION} right | {PREVIOUS_VERSION} precision |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    desc = sample_meta.get("failure_modes", {})
    for k, v in fm.items():
        prec = "" if v["precision"] is None else f"{v['precision']:.2f}"
        o = prev["failure_mode_strata"][k]
        oprec = "" if o["precision"] is None else f"{o['precision']:.2f}"
        L.append(f"| `{k}` | {v['population']:,} | {v['texts']} | {v['target']} | {v['regex_tags_fired']} | "
                 f"{v['correct']} | {prec} | {o['regex_tags_fired']} | {o['correct']} | {oprec} |")
    L.append("")
    for k in fm:
        if k in desc:
            L.append(f"- `{k}`: {desc[k]}")
    L.append("")

    L.append("## Population estimates (regex)\n")
    w = s["weighted"]
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
    for t in TAGS:
        m = pt[t]["weighted"]
        L.append(f"| `{t}` | {m['precision']:.2f} | {m['recall']:.2f} | {m['f1']:.2f} | "
                 f"{m['est_distinct_texts_with_tag']:,} | {m['est_distinct_texts_predicted']:,} |")
    L.append("")

    L.append("## Gold set\n")
    L.append(
        f"- {len(gold)} distinct `text_norm` values from `silver/facts.parquet`, sampled by "
        f"`scripts/wp3_gold_sample.py` (seed {sample_meta['seed']}). There are 30 strata: five known "
        "failure modes, texts the regex tags (grouped by their rarest tag), regex-untagged near misses "
        "(\"kidney\", \"critical\", \"round-the-clock\", ...), and a random draw from everything else.")
    L.append(
        f"- Phase 2: after phase 1 (396 texts) was labeled, six tags had fewer than 25 positives. "
        f"{n_phase2} more texts were drawn at random from those tags' strata (seed {sample_meta['seed_phase2']}). "
        "Stratum weights use the combined sample sizes.")
    L.append(
        f"- Labeled by an agent (`labeled_by = agent`), one text at a time, from a blind worksheet. An agent-labeled "
        "gold set is weaker evidence than a human one. Hruday should spot-check it before the numbers go in "
        "the video, and the methods note must say who labeled it.")
    L.append("- Positives per evidence tag (all at least 25): "
             + ", ".join(f"`{t}` {pos[t]}" for t in TAGS) + ".")
    noise = gold_meta.get("positives_per_noise_tag", {})
    L.append("- Noise tags are labeled too, for scoring the teacher and student later: "
             + ", ".join(f"`{t}` {n}" for t, n in noise.items())
             + f". {gold_meta.get('texts_with_no_tag', 0)} texts have no tag at all.")
    L.append("")
    L.append("Labeling rules:\n")
    for rule in LABEL_RULES:
        L.append(f"- {rule}")
    L.append("")

    L.append("## Caveats\n")
    L.append("- These are fact-level scores on distinct texts. They are not facility-level grade accuracy: "
             "one wrong fact can change a grade, and many grade-relevant errors cancel out across a "
             "facility's other facts.")
    L.append("- The gold set cannot see contamination (a fact about a different organization filed under "
             "this facility). That is a facility-linkage problem, not a text-tagging one.")
    L.append("- Several labels are judgment calls; each has a note in `gold_facts.csv`. The rules above are "
             "the ones the teacher prompt should state, so teacher and gold agree on definitions.")
    L.append("")
    L.append("## Reproduce\n")
    L.append("```bash")
    L.append(".venv/bin/python scripts/wp3_gold_sample.py sample   # design only; labels come from the hand-labeled TSV")
    L.append(".venv/bin/python scripts/wp3_score.py")
    L.append("```\n")
    L.append(f"Generated by `{SCRIPT}` on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.")
    return "\n".join(L) + "\n"


def main() -> int:
    gold = load_gold()
    pred = [recipes.tag_fact(t) for t in gold["text_norm"]]
    res = score_tags(gold, pred)
    G, P = res.pop("_matrices")
    sklearn_crosscheck(G, P, res["per_tag"])
    errors = error_lists(gold, G, P)
    fm = failure_mode_table(gold, pred)
    counts = note_counts(errors)

    # the original v0 patterns, for the comparison rows
    pred_prev = [recipes.tag_fact(t, version=PREVIOUS_VERSION) for t in gold["text_norm"]]
    res_prev = score_tags(gold, pred_prev)
    Gp, Pp = res_prev.pop("_matrices")
    errors_prev = error_lists(gold, Gp, Pp)
    prev = {
        "recipe_version": PREVIOUS_VERSION,
        "summary": res_prev["summary"],
        "per_tag": {t: {k: res_prev["per_tag"][t][k] for k in ("precision", "recall", "f1", "tp", "fp", "fn")}
                    for t in TAGS},
        "failure_mode_strata": failure_mode_table(gold, pred_prev),
    }

    lab = paths.labels_dir()
    gold_meta = json.loads((lab / (GOLD_FILE + ".meta.json")).read_text())
    sample_meta = json.loads((lab / (SAMPLE_FILE + ".meta.json")).read_text())
    worst = sorted(TAGS, key=lambda t: (res["per_tag"][t]["f1"], t))[:3]
    result = {
        "method": "regex",
        "model_version": recipes.RECIPE_VERSION,
        "recipe_version": recipes.RECIPE_VERSION,
        "tags_scored": list(TAGS),
        "gold": {"rows": int(len(gold)), "labeled_by": sorted(gold["labeled_by"].unique().tolist()),
                 "positives": {t: res["per_tag"][t]["support"] for t in TAGS}},
        "summary": res["summary"],
        "per_tag": res["per_tag"],
        "worst_tags_by_f1": worst,
        "failure_mode_strata": fm,
        "errors": errors,
        "previous_version": prev,
        "bootstrap": {"replicates": N_BOOT, "seed": BOOT_SEED,
                      "scheme": "rows resampled with replacement within each gold_sample stratum"},
        "conventions": {
            "zero_division": 0,
            "weighted": "each gold row weighted by stratum population / stratum sample size "
                        "(labels/gold_sample.csv), estimating rates over all distinct fact texts",
            "unit": "distinct text_norm",
        },
        "use": "the gold set is never used for training, prompt tuning, or choosing thresholds",
    }
    meta = {
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **git_info(),
        "recipe_version": recipes.RECIPE_VERSION,
        "model_version": recipes.RECIPE_VERSION,
        "method": "regex",
        "script": SCRIPT,
        "inputs": {f"labels/{n}": {"sha256": sha256(lab / n)} for n in (GOLD_FILE, SAMPLE_FILE)},
    }
    out = write_scores("regex", result, meta)

    problems = guard_notes(res, errors, errors_prev)
    if problems:
        print("STALE FAILURE NOTES, update WORST_TAG_NOTES in scripts/wp3_score.py:")
        for p in problems:
            print("  -", p)
        print(f"scores written to {out}; results page and figure NOT rewritten")
        return 1

    fig_path = make_figure(res)
    others = load_other_scores()
    write_atomic(REPORT, render_md(res, others, fm, errors, counts, gold, gold_meta, sample_meta, prev))

    # ---------------------------------------------------------------- summary
    s = res["summary"]
    print(f"regex vs gold ({s['rows']} facts) -> {out}")
    print(f"macro P {s['macro']['precision']:.3f}  R {s['macro']['recall']:.3f}  "
          f"F1 {s['macro']['f1']:.3f} {s['macro']['f1_ci95']}  micro F1 {s['micro']['f1']:.3f}  "
          f"exact {s['exact_match_evidence_tags']:.3f}")
    print(f"weighted macro F1 {s['weighted']['macro_f1']:.3f} {s['weighted']['macro_f1_ci95']}")
    pv = prev["summary"]
    print(f"{PREVIOUS_VERSION} for comparison: macro F1 {pv['macro']['f1']:.3f} {pv['macro']['f1_ci95']}  "
          f"micro F1 {pv['micro']['f1']:.3f}  exact {pv['exact_match_evidence_tags']:.3f}")
    print(f"{'tag':14s} {'sup':>4s} {'P':>6s} {'R':>6s} {'F1':>6s}  F1 95% CI      wP     wR     wF1  {PREVIOUS_VERSION} F1")
    for t in TAGS:
        m = res["per_tag"][t]
        wm = m["weighted"]
        print(f"{t:14s} {m['support']:>4d} {m['precision']:>6.3f} {m['recall']:>6.3f} {m['f1']:>6.3f}  "
              f"[{m['f1_ci95'][0]:.2f}, {m['f1_ci95'][1]:.2f}]  {wm['precision']:.3f}  {wm['recall']:.3f}  {wm['f1']:.3f}"
              f"  {prev['per_tag'][t]['f1']:.3f}")
    print("worst by F1:", ", ".join(worst))
    under = [t for t in TAGS if res["per_tag"][t]["support"] < 25]
    print("tags under 25 positives:", ", ".join(under) if under else "none")
    print(f"report -> {REPORT}\nfigure -> {fig_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
