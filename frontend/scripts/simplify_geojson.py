import json
import os
import sys

src, dst, tol, prec = sys.argv[1], sys.argv[2], float(sys.argv[3]), int(sys.argv[4])


def dp(pts, tol):
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L = (dx * dx + dy * dy) ** 0.5
        best, idx = -1, None
        for i in range(a + 1, b):
            px, py = pts[i]
            d = (
                abs(dy * px - dx * py + bx * ay - by * ax) / L
                if L
                else ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            )
            if d > best:
                best, idx = d, i
        if idx is not None and best > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


def ring(r):
    r = [[round(x, prec), round(y, prec)] for x, y in r]
    out = []
    for p in r:
        if not out or out[-1] != p:
            out.append(p)
    out = dp(out, tol)
    if out[0] != out[-1]:
        out.append(out[0])
    return out if len(out) >= 4 else None


def poly(p):
    rs = [ring(r) for r in p]
    if rs[0] is None:
        return None
    return [rs[0]] + [r for r in rs[1:] if r]


d = json.load(open(src))
feats = []
n0 = n1 = 0
for f in d["features"]:
    g = f["geometry"]
    polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
    n0 += sum(len(r) for p in polys for r in p)
    ps = [q for q in (poly(p) for p in polys) if q]
    n1 += sum(len(r) for p in ps for r in p)
    geom = (
        {"type": "Polygon", "coordinates": ps[0]}
        if len(ps) == 1
        else {"type": "MultiPolygon", "coordinates": ps}
    )
    feats.append(
        {
            "type": "Feature",
            "properties": {
                "STNAME_SH": f["properties"].get("STNAME_SH"),
                "STCODE11": f["properties"].get("STCODE11"),
            },
            "geometry": geom,
        }
    )
json.dump(
    {"type": "FeatureCollection", "features": feats},
    open(dst, "w"),
    separators=(",", ":"),
)
print(n0, n1, os.path.getsize(dst))
