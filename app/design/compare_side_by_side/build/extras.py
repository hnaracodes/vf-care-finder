import sys, json
sys.path.insert(0, "/Users/hrudaynara/Projects/CMU Datathon/src")
from pathlib import Path
from vf import rank, locate, why_not, questions, public_list
B = Path("/Users/hrudaynara/Projects/CMU Datathon/dbapp/bundle")
idx = rank.load_index(B)
R = locate.Resolver(path=B/"places.parquet")
PUB = public_list.load()
infos = questions.load_record_info()
out = {}
for key, line, q in [("lko","emoc","226001"),("jok","dialysis","Jokihat"),("jpr","dialysis","Jaipur")]:
    res = R.resolve(q)
    ans = json.load(open(f"a_{key}.json"))
    PUB.annotate(ans)
    wn = why_not.why_not_strip(idx, line, res, ans, mask_names=False)
    fn = why_not.funnel(idx, line, res, answer_dict=ans)
    cards = list(ans.get("results") or [])
    br = ans.get("beyond_radius")
    if br: cards += br if isinstance(br, list) else [br]
    qs, ci, pl = {}, {}, {}
    for c in cards:
        qs[c["name"]] = questions.questions_for(c, line, infos.get(c["facility_id"]))
        ci[c["name"]] = questions.call_info(c["facility_id"])
        pl[c["name"]] = c.get("public_list")
    out[key] = {"why_not": wn, "funnel_counts": why_not.funnel_counts(fn), "funnel_steps": [{k: s.get(k) for k in ("step","label","n","n_dropped")} for s in fn["steps"]], "consistency": fn.get("consistency"),
                "questions": qs, "call_info": ci, "public_list": pl}
json.dump(out, open("extras.json", "w"), indent=1, default=str)
print("ok")
