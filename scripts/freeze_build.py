"""Freeze the final build before the sealed evaluation (D044 item 4, D053, D063).

Writes ``reports/FROZEN_BUILD.json``: the git commit, the bundle manifest sha256,
the sha256 of every file in ``dbapp/bundle``, of every input table in the data
folder's silver and gold layers (those the bundle was built from are marked),
of the two reference files and of the sealed evaluation's point and query lists,
and of every ``src/vf`` module. From the freeze on, nothing may change the
bundle; ``--check`` recomputes every hash and lists any difference.

    python scripts/freeze_build.py            # write the freeze (refuses to overwrite)
    python scripts/freeze_build.py --check    # compare the current build with the freeze
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vf import paths  # noqa: E402

OUT = ROOT / "reports" / "FROZEN_BUILD.json"
BUNDLE = ROOT / "dbapp" / "bundle"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def table_hashes() -> dict:
    """Every data table in silver and gold (not the .meta.json sidecars)."""
    out = {}
    for layer, d in (("silver", paths.silver_dir()), ("gold", paths.gold_dir())):
        for p in sorted(d.iterdir()):
            if p.is_file() and not p.name.endswith(".meta.json") and p.name != ".gitkeep":
                out[f"{layer}/{p.name}"] = {"sha256": sha256_file(p), "bytes": p.stat().st_size}
    return out


def bundle_hashes() -> dict:
    return {str(p.relative_to(BUNDLE)): {"sha256": sha256_file(p), "bytes": p.stat().st_size}
            for p in sorted(BUNDLE.rglob("*")) if p.is_file()}


def other_hashes() -> dict:
    files = [ROOT / "reference" / "terrain_multiplier.csv", ROOT / "reference" / "sea_pixel_post_offices.csv"]
    files += sorted((ROOT / "src" / "vf").glob("*.py"))
    out = {str(p.relative_to(ROOT)): sha256_file(p) for p in files}
    ev = paths.data_dir() / "labels" / "eval_sealed"
    for n in ("points.csv", "queries.csv"):
        out[f"data/labels/eval_sealed/{n}"] = sha256_file(ev / n)
    return out


def bundle_inputs(man: dict) -> list[str]:
    """The silver and gold tables the manifest says the bundle was built from."""
    found = set(man.get("upstream_row_counts") or {})
    for v in (man.get("files") or {}).values():
        if isinstance(v, dict):
            for tok in str(v.get("from") or "").replace("+", " ").replace("(", " ").replace(")", " ").replace(",", " ").split():
                if tok.startswith(("silver/", "gold/")):
                    found.add(tok)
    return sorted(found)


def build() -> dict:
    man_p = BUNDLE / "manifest.json"
    man = json.loads(man_p.read_text())
    tables = table_hashes()
    inputs = bundle_inputs(man)
    for k, v in tables.items():
        v["bundle_input"] = k in inputs or (k.startswith("silver/ext_") and not k.startswith("silver/ext_osm")
                                            and not k.startswith("silver/ext_district"))
    status = git("status", "--porcelain", "--untracked-files=no")
    return {
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "script": "scripts/freeze_build.py",
        "rule": "From this freeze on, nothing may change the bundle (D063; sealed evaluation protocol rule 2).",
        "git_commit": git("rev-parse", "HEAD"),
        "git_branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_uncommitted_tracked": status.splitlines(),
        "bundle_dir": "dbapp/bundle",
        "bundle_manifest_sha256": sha256_file(man_p),
        "bundle_manifest": {k: man.get(k) for k in ("bundle_version", "created_at_utc", "git_commit",
                                                    "code_uncommitted", "versions")},
        "bundle_files": bundle_hashes(),
        "bundle_manifest_files_match": all(
            sha256_file(BUNDLE / k) == v["sha256"] for k, v in (man.get("files") or {}).items()
            if isinstance(v, dict) and "sha256" in v),
        "bundle_inputs_named_in_manifest": inputs,
        "input_tables": tables,
        "code_and_reference": other_hashes(),
    }


def compare(frozen: dict, now: dict) -> list[str]:
    diffs = []
    if frozen["bundle_manifest_sha256"] != now["bundle_manifest_sha256"]:
        diffs.append("bundle manifest")
    for sect in ("bundle_files", "input_tables"):
        a, b = frozen[sect], now[sect]
        for k in sorted(set(a) | set(b)):
            if (a.get(k) or {}).get("sha256") != (b.get(k) or {}).get("sha256"):
                diffs.append(f"{sect}: {k}")
    a, b = frozen["code_and_reference"], now["code_and_reference"]
    diffs += [f"code_and_reference: {k}" for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
    return diffs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="compare the current build with the freeze")
    ap.add_argument("--deployed-manifest-sha256", default=None,
                    help="sha256 of the manifest.json the deployed app serves (recorded, compared)")
    ap.add_argument("--deployment-id", default=None)
    args = ap.parse_args()
    now = build()
    if args.check:
        if not OUT.exists():
            raise SystemExit(f"no freeze at {OUT}")
        frozen = json.loads(OUT.read_text())
        diffs = compare(frozen, now)
        print(f"[freeze check] frozen {frozen['frozen_at_utc']} at {frozen['git_commit'][:7]}; "
              f"{len(now['bundle_files'])} bundle files, {len(now['input_tables'])} tables, "
              f"{len(now['code_and_reference'])} code and reference files compared; "
              + ("no difference" if not diffs else f"{len(diffs)} differ: {diffs}"))
        raise SystemExit(1 if diffs else 0)
    if OUT.exists():
        raise SystemExit(f"{OUT} exists: the build is already frozen (use --check)")
    if args.deployed_manifest_sha256:
        now["deployed_app"] = {"app": "vf-care-finder", "deployment_id": args.deployment_id,
                               "manifest_sha256": args.deployed_manifest_sha256,
                               "matches_bundle": args.deployed_manifest_sha256 == now["bundle_manifest_sha256"]}
    body = {k: v for k, v in now.items()}
    body["freeze_sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, indent=1, sort_keys=False) + "\n")
    tmp.replace(OUT)
    n_in = sum(1 for v in now["input_tables"].values() if v["bundle_input"])
    print(f"[freeze] wrote {OUT.relative_to(ROOT)}: commit {now['git_commit'][:7]}, bundle manifest sha256 "
          f"{now['bundle_manifest_sha256'][:12]}..., {len(now['bundle_files'])} bundle files (manifest hashes match: "
          f"{now['bundle_manifest_files_match']}), {len(now['input_tables'])} tables ({n_in} bundle inputs), "
          f"uncommitted tracked files: {now['git_uncommitted_tracked'] or 'none'}")


if __name__ == "__main__":
    main()
