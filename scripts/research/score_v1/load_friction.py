import sys, numpy as np, tifffile
p = sys.argv[1]
with tifffile.TiffFile(p) as t:
    pg = t.pages[0]
    for tag in pg.tags.values():
        v = tag.value
        s = str(v)
        print(tag.name, s[:200])
    a = pg.asarray()
print(a.dtype, a.shape)
a = a.astype(np.float32)
print('nan', np.isnan(a).sum(), 'min', np.nanmin(a), 'max', np.nanmax(a))
for q in [0.01, 0.1, 0.5, 0.9, 0.99]:
    print(q, np.nanquantile(a[a > 0], q))
print('neg', (a < 0).sum(), 'zero', (a == 0).sum())
np.save(sys.argv[2], a)
