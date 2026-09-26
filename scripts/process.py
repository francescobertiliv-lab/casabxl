"""Dagli annunci letti alle case della mappa: criteri, bici, Maaseik, mercato, punteggio.

Uso:
    python3 process.py annunci.json stations.json pois.json DATA [--contact EMAIL] [--out listings.json]

annunci.json: uscita di immovlan.py (anche con --all: gli annunci sopra budget servono
solo come termine di paragone per il mercato). stations.json: documento meta/stations.
pois.json: documento meta/pois. DATA: AAAA-MM-GG della raccolta.

Mercato: regressione sui prezzi richiesti di tutti gli annunci letti nella zona,
log(prezzo) ~ log(m² abitabili) + log(m² terreno) + camere + 4 facciate + stato dei lavori
+ comune. Il "prezzo atteso" è quello che il modello dà a una casa con le stesse
caratteristiche; lo scarto dice se la richiesta è sotto o sopra le altre della zona.
Sono prezzi richiesti, non prezzi di vendita: è un confronto, non una perizia.
Richiede requests.
"""
import argparse
import json
import math
import sys
import time

import requests

BIKE = "https://routing.openstreetmap.de/routed-bike/route/v1/driving/{},{};{},{}?overview=false"
CAR = "https://routing.openstreetmap.de/routed-car/route/v1/driving/{},{};{},{}?overview=false"


def air_km(a, b):
    r = math.pi / 180
    x = math.sin((b[0] - a[0]) * r / 2) ** 2 + math.cos(a[0] * r) * math.cos(b[0] * r) * math.sin((b[1] - a[1]) * r / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))


CACHE = {}


def route(s, url, a, b):
    key = url.format(a[1], a[0], b[1], b[0])
    if key in CACHE:
        return CACHE[key]
    time.sleep(1)
    CACHE[key] = _route(s, url, a, b)
    return CACHE[key]


def _route(s, url, a, b):
    try:
        r = s.get(url.format(a[1], a[0], b[1], b[0]), timeout=30)
        if r.ok and r.json().get("routes"):
            rt = r.json()["routes"][0]
            return rt["distance"] / 1000, rt["duration"] / 60
    except (requests.RequestException, ValueError):
        pass
    return None


def solve(A, y, ridge=1e-3):
    """Minimi quadrati con un piccolo ridge, eliminazione di Gauss (solo libreria standard)."""
    k = len(A[0])
    M = [[sum(r[i] * r[j] for r in A) + (ridge if i == j and i else 0) for j in range(k)] for i in range(k)]
    v = [sum(r[i] * t for r, t in zip(A, y)) for i in range(k)]
    for c in range(k):
        p = max(range(c, k), key=lambda i: abs(M[i][c]))
        M[c], M[p], v[c], v[p] = M[p], M[c], v[p], v[c]
        if abs(M[c][c]) < 1e-12:
            continue
        for i in range(k):
            if i != c:
                f = M[i][c] / M[c][c]
                M[i] = [a - f * b for a, b in zip(M[i], M[c])]
                v[i] -= f * v[c]
    return [v[i] / M[i][i] if abs(M[i][i]) > 1e-12 else 0.0 for i in range(k)]


def market(all_ads):
    ok = [d for d in all_ads if d.get("price") and d.get("living_m2") and 40 <= d["living_m2"] <= 1500]
    communes = sorted({d["postcode"] for d in ok})

    def row(d):
        land = d.get("land_m2") or d["living_m2"]
        return ([1.0, math.log(d["living_m2"]), math.log(max(land, 50)), float(d.get("bedrooms") or 3),
                 1.0 if d.get("facades") == 4 else 0.0,
                 1.0 if d.get("renovation") == "renovated" else 0.0,
                 1.0 if d.get("renovation") in ("to_renovate", "refresh") else 0.0]
                + [1.0 if d["postcode"] == c else 0.0 for c in communes[1:]])

    if len(ok) < 15:
        return None, len(ok)
    beta = solve([row(d) for d in ok], [math.log(d["price"]) for d in ok])
    n_by = {c: sum(1 for d in ok if d["postcode"] == c) for c in communes}

    def expect(d):
        if not d.get("living_m2") or d["postcode"] not in communes:
            return None
        return math.exp(sum(b * x for b, x in zip(beta, row(d)))), n_by[d["postcode"]]
    return expect, len(ok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ads", help="uno o più file di annunci separati da virgola (immoscoop.json,immovlan.json)")
    ap.add_argument("stations")
    ap.add_argument("pois")
    ap.add_argument("date")
    ap.add_argument("--contact", default="casabxl")
    ap.add_argument("--max-price", type=int, default=800000)
    ap.add_argument("--out", default="listings.json")
    a = ap.parse_args()
    s = requests.Session()
    s.headers["User-Agent"] = f"casabxl/1.0 ({a.contact})"
    ads = [d for f in a.ads.split(",") for d in json.load(open(f))]
    stations = [x for x in json.load(open(a.stations))["stations"] if x.get("minutes") is not None and x["minutes"] <= 30]
    maaseik = next((p for p in json.load(open(a.pois))["pois"] if p["id"] == "maaseik"), None)

    # stesso immobile su più siti o pubblicato due volte: stesso CAP, prezzo e camere,
    # superficie abitabile simile se entrambi la dicono. Resta il record con l'indirizzo esatto;
    # i campi che gli mancano si prendono dagli altri annunci.
    def same(a, b):
        if (a.get("postcode"), a.get("price"), a.get("bedrooms")) != (b.get("postcode"), b.get("price"), b.get("bedrooms")):
            return False
        if a.get("price") is None or a.get("bedrooms") is None:
            return False
        la, lb = a.get("living_m2"), b.get("living_m2")
        return not (la and lb and abs(la - lb) > 0.1 * max(la, lb))

    groups = []
    for d in ads:
        g = next((g for g in groups if same(g[0], d)), None)
        (g.append(d) if g else groups.append([d]))
    merged = []
    for g in groups:
        g.sort(key=lambda d: (bool(d.get("approx")), d.get("source") != "Immoscoop"))
        p = dict(g[0])
        for o in g[1:]:
            for k, v in o.items():
                if p.get(k) in (None, "", "unknown") and v not in (None, "", "unknown"):
                    p[k] = v
            if p.get("renovation") == "unknown" and o.get("renovation") not in (None, "unknown"):
                p["renovation"], p["renovation_why"] = o["renovation"], o.get("renovation_why")
        p["dups"] = [o["url"] for o in g[1:] if o["url"] != p["url"]]
        merged.append(p)
    ads = merged
    expect, n_model = market(ads)
    print(f"modello di mercato su {n_model} annunci", file=sys.stderr)
    import os
    cache = a.out + ".routes.json"
    if os.path.exists(cache):
        CACHE.update(json.load(open(cache)))
    out = []
    for d in ads:
        if not d.get("in_budget", d.get("price", 0) <= a.max_price) or d.get("price") is None:
            continue
        if d.get("bedrooms") not in (4, 5):
            continue
        if d.get("garden") is not True:
            continue  # giardino obbligatorio e scritto nell'annuncio
        if d.get("lat") is None:
            continue
        here = (d["lat"], d["lon"])
        near = sorted((x for x in stations if air_km(here, (x["lat"], x["lon"])) <= 3.5),
                      key=lambda x: air_km(here, (x["lat"], x["lon"])))[:4]
        best = None
        for st in near:
            r = route(s, BIKE, here, (st["lat"], st["lon"]))
            if not r:
                continue
            km = round(r[0], 1)
            bmin = math.ceil(km / 0.25)
            tot = bmin + st["minutes"] + (0 if st["direct"] else 5)
            if best is None or tot < best[0]:
                best = (tot, st, km, bmin)
        if not best:
            continue
        _, st, km, bmin = best
        limit = 3.5 if d.get("approx") else 3.0
        if km > limit:
            continue
        typ = d.get("type", "unknown")
        if typ == "closed" or d.get("facades") == 2:
            continue  # a schiera: esclusa
        if typ == "halfopen" and not (st["direct"] and st["minutes"] <= 20 and km <= 2.5 and (st.get("peak_am") or 0) >= 2):
            continue  # 3 lati solo se il resto è molto buono
        to_verify = []
        if typ == "unknown":
            to_verify.append("lati liberi")
        if not d.get("land_m2"):
            to_verify.append("terreno")
        if d.get("approx"):
            to_verify.append("indirizzo esatto")
        if km > 3.0:
            to_verify.append("distanza bici")
        if d.get("renovation") == "unknown":
            to_verify.append("stato dei lavori")
        mk = None
        e = expect(d) if expect else None
        if e:
            exp, n = e
            mk = {"expected": int(round(exp, -3)), "gap_pct": round((d["price"] / exp - 1) * 100),
                  "n": n, "n_model": n_model, "method": "regressione sui prezzi richiesti della zona (Immovlan)"}
        car = route(s, CAR, here, (maaseik["lat"], maaseik["lon"])) if maaseik else None
        reno_pts = {"renovated": 8, "refresh": -3, "to_renovate": -8}.get(d.get("renovation"), 0)
        mk_pts = max(-10, min(10, -(mk["gap_pct"]) * 0.5)) if mk else 0
        score = (100 - 1.5 * (st["minutes"] - 10) - 3 * bmin + 4 * min(st.get("peak_am") or 0, 4)
                 + {"open": 10, "halfopen": -10}.get(typ, 0) + {"nl": 5, "bi": 0, "fr": -5}[st["lang"]]
                 - (5 if d["price"] > 700000 else 0) - (0 if st["direct"] else 10) - 4 * len(to_verify)
                 + reno_pts + mk_pts)
        why = [{"open": "4 lati", "halfopen": "3 lati"}.get(typ)]
        if d.get("renovation") == "renovated":
            why.append("ristrutturata")
        why.append(f"{bmin}′ di bici da {st['name']}")
        why.append(f"{'diretto' if st['direct'] else 'con cambio'} in {st['minutes']}′ con {int(st.get('peak_am') or 0)} treni/ora")
        if mk and mk["gap_pct"] <= -5:
            why.append(f"{-mk['gap_pct']}% sotto il mercato")
        rec = {k: d.get(k) for k in ("source", "url", "title", "price", "bedrooms", "type", "facades", "garden",
                                     "garden_m2", "land_m2", "living_m2", "year", "epc_kwh", "state", "renovation",
                                     "renovation_why", "address", "approx", "lat", "lon", "postcode", "commune")}
        rec.update({
            "id": d["id"], "other_urls": d.get("dups", []), "price_history": [{"date": a.date, "price": d["price"]}],
            "lang": st["lang"], "facilities": st["facilities"], "station": st["name"],
            "bike_km": km, "bike_min": bmin, "train_min": st["minutes"], "train_direct": st["direct"],
            "change_at": st.get("change_at"), "peak_am": st.get("peak_am"), "peak_pm": st.get("peak_pm"),
            "to_maaseik": {"km": round(car[0], 1), "min": round(car[1])} if car else None,
            "market": mk, "score": max(0, min(100, round(score))), "reason": ", ".join(w for w in why if w),
            "to_verify": to_verify, "first_seen": a.date, "last_seen": a.date,
        })
        out.append(rec)
        print(f"{rec['score']:>4} {rec['commune']} {rec['price']} {rec['bedrooms']}c {typ} {d.get('renovation')} "
              f"mercato {mk and mk['gap_pct']}% bici {km}km {st['name']} {st['minutes']}′ maaseik {rec['to_maaseik']}", file=sys.stderr)
    json.dump(CACHE, open(cache, "w"))
    # stesso comune scritto in modi diversi dagli agenti: la grafia più frequente per CAP
    from collections import Counter
    spell = {}
    for pc in {d.get("postcode") for d in ads}:
        c = Counter(d["commune"] for d in ads if d.get("postcode") == pc and d.get("commune"))
        if c:
            spell[pc] = max(c, key=lambda n: (c[n], "-" in n))
    for r in out:
        r["commune"] = spell.get(r["postcode"], r["commune"])
    out.sort(key=lambda r: -r["score"])
    json.dump(out, open(a.out, "w"), ensure_ascii=False, indent=1)
    print(len(out), "case ->", a.out, file=sys.stderr)


if __name__ == "__main__":
    main()
