"""What-if agent rule, same as the proposed browser simulator. Inputs: /api/trip and /api/answer JSON only."""
import json, math, sys
def hav(a, b):
    lon1, lat1 = map(math.radians, a); lon2, lat2 = map(math.radians, b)
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 2*6371.0088*math.asin(math.sqrt(h))
def mins(km, f): return f*2.30*km**0.842 if km > 0 else 0.0
def run(t, a, sup):
    f = a['window']['factor']; W = a['window']['minutes']
    F = t['facilities']; P = t['points']
    fac = [(x['lon'], x['lat']) for x in F]
    inwin = 0; tot = 0.0; firstbad = 0; extra = 0.0
    for p in P:
        d = [mins(hav(p, q), f) for q in fac]
        ev = [i for i in range(len(F)) if sup[i]]
        best = min((d[i] for i in ev), default=math.inf)
        if best <= W: inwin += 1
        tot += best
        # claims policy: nearest listing facility
        c = min(range(len(F)), key=lambda i: d[i])
        if not sup[c]:
            firstbad += 1
            onward = min((mins(hav(fac[c], fac[i]), f) for i in ev), default=math.inf)
            extra += d[c] + onward - best
    n = len(P)
    return dict(n=n, in_window=inwin, mean_min=tot/n, first_no_evidence=firstbad, extra_min_mean=(extra/firstbad if firstbad else 0))
out = {}
for k in ['lko', 'jpr', 'jok']:
    t = json.load(open(f't_{k}.json')); a = json.load(open(f'a_{k}.json'))
    F = t['facilities']; sup0 = [x['supported'] for x in F]
    base = run(t, a, sup0)
    tests = []
    for i, x in enumerate(F):
        if x['supported']: continue
        s = list(sup0); s[i] = True
        r = run(t, a, s)
        tests.append(dict(i=i, name=x['name'], reason=x['unsupported_reason'], gain=r['in_window']-base['in_window'], mean_min=r['mean_min'], saved=base['mean_min']-r['mean_min'], first_no_evidence=r['first_no_evidence']))
    tests.sort(key=lambda r: (-r['gain'], -r['saved']))
    out[k] = dict(window=a['window']['minutes'], factor=a['window']['factor'], base=base, top=tests[:5], n_tested=len(tests))
    print(k, json.dumps(out[k], indent=0)[:1500])
json.dump(out, open('sim.json', 'w'), indent=1)
