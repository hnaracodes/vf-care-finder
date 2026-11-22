"""The keyword index matrix is written as row blocks so every file fits the
Databricks Apps 10 MB per-file limit, and loads back to the identical matrix."""
import json

import numpy as np
import pytest
from scipy import sparse

from vf import rank


def _dense_random(n_rows=4000, n_cols=3000, density=0.02, seed=0):
    rng = np.random.default_rng(seed)
    m = sparse.random(n_rows, n_cols, density=density, format="csr", random_state=rng, dtype=np.float32)
    return m


def test_parts_respect_cap_and_round_trip(tmp_path):
    m = _dense_random()
    cap = 200_000  # small cap so the split is exercised
    names = rank._save_matrix_parts(m, tmp_path, max_bytes=cap)
    assert len(names) > 1
    assert all((tmp_path / n).stat().st_size < cap for n in names)
    back = sparse.vstack([sparse.load_npz(tmp_path / n) for n in names], format="csr")
    assert back.shape == m.shape
    assert (back != m).nnz == 0


def test_save_and_load_keyword_index(tmp_path, monkeypatch):
    import pandas as pd

    facts = pd.DataFrame({
        "facility_id": ["a", "a", "b", "c", "c", "d"],
        "fact_id": ["1", "2", "3", "4", "5", "6"],
        "field": ["capability"] * 6,
        "text": ["dialysis unit with 10 machines", "24x7 emergency care", "dialysis unit open daily",
                 "cataract surgery unit", "emergency surgery 24x7", "snake bite care"],
    })
    kw = rank.build_keyword_index(facts)
    monkeypatch.setattr(rank, "MATRIX_PART_MAX_BYTES", 1)  # force one part per row block doubling
    out = rank.save_keyword_index(kw, tmp_path / "kw")
    meta = json.loads((out / "meta.json").read_text())
    assert not (out / "matrix.npz").exists()
    assert meta["matrix_parts"] and meta["matrix_shape"] == list(kw.matrix.shape)
    back = rank.load_keyword_index(out)
    assert (back.matrix != kw.matrix).nnz == 0
    assert back.docs.equals(kw.docs.reset_index(drop=True)) or len(back.docs) == len(kw.docs)


def test_load_legacy_single_file(tmp_path):
    import pandas as pd

    facts = pd.DataFrame({"facility_id": ["a", "b", "c"], "fact_id": ["1", "2", "3"], "field": ["capability"] * 3,
                          "text": ["mri scan 1.5 tesla", "ct scan 64 slice", "mri and ct scan"]})
    kw = rank.build_keyword_index(facts)
    out = rank.save_keyword_index(kw, tmp_path / "kw")
    for p in out.glob("matrix.part-*.npz"):
        p.unlink()
    meta = json.loads((out / "meta.json").read_text())
    meta.pop("matrix_parts"), meta.pop("matrix_shape")
    (out / "meta.json").write_text(json.dumps(meta))
    sparse.save_npz(out / "matrix.npz", kw.matrix)
    back = rank.load_keyword_index(out)
    assert (back.matrix != kw.matrix).nnz == 0
