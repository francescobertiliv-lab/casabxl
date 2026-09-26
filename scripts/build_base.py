"""Mappa di base: stazioni con comune e regione, aree bici, confini dei comuni e delle Regioni.

Uso:
    python3 build_base.py stations_trains.json OUTDIR [--contact EMAIL]

Legge l'uscita di trains.py e scrive in OUTDIR:
  stations.json      documento meta/stations (senza built/gtfs_date, li aggiunge chi scrive)
  communes-N.json    documenti geo/communes-N (sotto 200 KB ciascuno)
  base.json          documento geo/base: Regioni e città per orientarsi
Fonti: Overpass e Valhalla (OpenStreetMap); comuni a facilità da nl.wikipedia
"Faciliteitengemeente". Richiede requests.
"""
import argparse
import json
import math
import pathlib
import sys
import time

import requests

OVERPASS = "https://overpass-api.de/api/interpreter"
VALHALLA = "https://valhalla1.openstreetmap.de/isochrone"
# nl.wikipedia.org/wiki/Faciliteitengemeente, "Overzicht van de faciliteitengemeenten"
FACILITIES = {
    "Komen-Waasten", "Mesen", "Moeskroen", "Spiere-Helkijn", "Ronse", "Vloesberg", "Bever",
    "Edingen", "Drogenbos", "Linkebeek", "Sint-Genesius-Rode", "Wemmel", "Kraainem",
    "Wezembeek-Oppem", "Herstappe", "Voeren", "Malmedy", "Weismes", "Lontzen", "Raeren",
    "Eupen", "Kelmis", "Burg-Reuland", "Sankt Vith", "Amel", "Bütgenbach", "Büllingen",
    # nomi francesi degli stessi comuni
    "Comines-Warneton", "Messines", "Mouscron", "Espierres-Helchin", "Renaix", "Flobecq",
    "Biévène", "Enghien", "Linkebeek", "Rhode-Saint-Genèse", "Wezembeek-Oppem", "Fourons",
    "Waimes", "La Calamine",
}


def overpass(q, s):
    for i in range(5):
        try:
            r = s.post(OVERPASS, data={"data": q}, timeout=300)
            if r.status_code == 200:
                return r.json()["elements"]
        except requests.RequestException:
            pass
        time.sleep(10 * (i + 1))
    raise RuntimeError("Overpass non risponde")


def inside_ring(la, lo, ring):
    c = False
    for (a1, o1), (a2, o2) in zip(ring, ring[1:] + ring[:1]):
        if (a1 > la) != (a2 > la) and lo < (o2 - o1) * (la - a1) / (a2 - a1) + o1:
            c = not c
    return c


def lang_of(region_name):
    n = region_name.lower()
    if "brussel" in n or "bruxelles" in n:
        return "bi"
    if "vlaanderen" in n or "flandre" in n:
        return "nl"
    return "fr"


def simplify(pts, tol):
    """Douglas-Peucker su [lat, lon], tolleranza in gradi."""
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    dmax, idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        p = pts[i]
        dx, dy = b[1] - a[1], b[0] - a[0]
        if dx == dy == 0:
            d = math.hypot(p[1] - a[1], p[0] - a[0])
        else:
            d = abs(dy * p[1] - dx * p[0] + b[1] * a[0] - b[0] * a[1]) / math.hypot(dx, dy)
        if d > dmax:
            dmax, idx = d, i
    if dmax <= tol:
        return [a, b]
    return simplify(pts[: idx + 1], tol)[:-1] + simplify(pts[idx:], tol)


def rings_of(rel):
    """Unisce le vie 'outer' di una relazione in anelli chiusi."""
    ways = [[(p["lat"], p["lon"]) for p in m["geometry"]] for m in rel.get("members", [])
            if m["type"] == "way" and m.get("role") in ("outer", "") and m.get("geometry")]
    rings = []
    while ways:
        ring = ways.pop(0)
        changed = True
        while ring[0] != ring[-1] and changed:
            changed = False
            for i, w in enumerate(ways):
                if w[0] == ring[-1]:
                    ring += w[1:]
                elif w[-1] == ring[-1]:
                    ring += w[::-1][1:]
                elif w[-1] == ring[0]:
                    ring = w[:-1] + ring
                elif w[0] == ring[0]:
                    ring = w[::-1][:-1] + ring
                else:
                    continue
                ways.pop(i)
                changed = True
                break
        rings.append(ring)
    return rings


def bbox(pts):
    la = [p[0] for p in pts]
    lo = [p[1] for p in pts]
    return min(la), min(lo), max(la), max(lo)


def overlaps(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trains")
    ap.add_argument("outdir")
    ap.add_argument("--contact", default="casabxl")
    ap.add_argument("--gtfs", help="GTFS per i nomi olandesi delle stazioni (translations.txt)")
    a = ap.parse_args()
    out = pathlib.Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers["User-Agent"] = f"casabxl/1.0 ({a.contact})"
    trains = json.load(open(a.trains))
    nl = {}
    if a.gtfs:
        import csv, io, zipfile
        with zipfile.ZipFile(a.gtfs).open("translations.txt") as f:
            for r in csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")):
                if r["table_name"] == "stops" and r["field_name"] == "stop_name" and r["language"] == "nl":
                    nl[r["field_value"]] = r["translation"]

    # 1. Regioni e comuni (due richieste in blocco), poi comune e regione di ogni stazione in locale
    q = '[out:json][timeout:300];relation[boundary=administrative][admin_level=4]["ISO3166-2"~"^BE-(VLG|WAL|BRU)$"];out geom;'
    regions = []
    for e in overpass(q, s):
        code = e["tags"]["ISO3166-2"]
        full = [r for r in rings_of(e) if len(r) > 3]
        regions.append({"name": e["tags"].get("name"), "code": code,
                        "lang": {"BE-VLG": "nl", "BE-WAL": "fr", "BE-BRU": "bi"}[code], "full": full})
    print(len(regions), "regioni", file=sys.stderr)

    def region_lang(la, lo):
        # Bruxelles è un'enclave dentro le Fiandre: va controllata per prima
        for r in sorted(regions, key=lambda r: r["code"] != "BE-BRU"):
            if any(inside_ring(la, lo, ring) for ring in r["full"]):
                return r["lang"]
        return None

    la0 = min(t["lat"] for t in trains) - 0.06
    lo0 = min(t["lon"] for t in trains) - 0.08
    la1 = max(t["lat"] for t in trains) + 0.06
    lo1 = max(t["lon"] for t in trains) + 0.08
    q = f'[out:json][timeout:300];relation[boundary=administrative][admin_level=8]({la0},{lo0},{la1},{lo1});out geom;'
    crel = []
    for e in overpass(q, s):
        full = [r for r in rings_of(e) if len(r) > 3]
        if full:
            crel.append((e["tags"], full))
    print(len(crel), "comuni nel riquadro", file=sys.stderr)

    stations = []
    for t in trains:
        tg = next((tg for tg, full in crel if any(inside_ring(t["lat"], t["lon"], r) for r in full)), {})
        lang = region_lang(t["lat"], t["lon"]) or "nl"
        commune = tg.get("name:nl" if lang == "nl" else "name:fr" if lang == "fr" else "name", tg.get("name", ""))
        fac = bool({tg.get("name:nl"), tg.get("name:fr"), tg.get("name")} & FACILITIES)
        name = t["name"]
        if lang == "nl":
            name = nl.get(name, name)
        elif lang == "bi" and nl.get(name, name) != name:
            name = f"{name} / {nl[name]}"
        stations.append({
            "id": t["stop_id"], "name": name, "lat": t["lat"], "lon": t["lon"],
            "minutes": t["minutes"], "direct": t["direct"], "change_at": t.get("change_at"),
            "peak_am": t.get("peak_am"), "peak_pm": t.get("peak_pm"),
            "commune": commune, "lang": lang, "facilities": fac,
        })
        print(f'{name}: {commune} ({lang}{", facilità" if fac else ""})', file=sys.stderr)

    # 2. area bici 10 minuti (Valhalla)
    for st in stations:
        body = {"locations": [{"lat": st["lat"], "lon": st["lon"]}], "costing": "bicycle",
                "contours": [{"time": 10}], "polygons": True}
        area = None
        try:
            r = s.post(VALHALLA, json=body, timeout=60)
            if r.ok:
                g = r.json()["features"][0]["geometry"]
                ring = g["coordinates"][0] if g["type"] == "Polygon" else g["coordinates"][0][0]
                pts = [[round(y, 5), round(x, 5)] for x, y in ring]
                tol = 0.0003
                while len(pts) > 150:
                    pts = simplify(pts, tol)
                    tol *= 1.5
                area = pts
        except requests.RequestException:
            pass
        st["bike_area"] = area
        print(f'{st["name"]}: area bici {"ok" if area else "cerchio"}', file=sys.stderr)
        time.sleep(1)
    json.dump({"stations": stations}, open(out / "stations.json", "w"), ensure_ascii=False)

    # 3. comuni che toccano un'area bici
    zones = [bbox(st["bike_area"]) if st["bike_area"] else
             (st["lat"] - 0.025, st["lon"] - 0.035, st["lat"] + 0.025, st["lon"] + 0.035) for st in stations]
    communes = []
    for tg, full in crel:
        rings = [simplify(r, 0.0005) for r in full]
        rings = [[[round(p[0], 5), round(p[1], 5)] for p in r] for r in rings if len(r) > 3]
        if not rings or not any(overlaps(bbox(r), z) for r in rings for z in zones):
            continue
        inside = [st for st in stations if st["commune"] in (tg.get("name"), tg.get("name:nl"), tg.get("name:fr"))]
        lang = inside[0]["lang"] if inside else None
        communes.append({"name": tg.get("name"), "name_nl": tg.get("name:nl"), "name_fr": tg.get("name:fr"),
                         "lang": lang, "facilities": bool({tg.get("name:nl"), tg.get("name:fr"), tg.get("name")} & FACILITIES),
                         "rings": [max(rings, key=len)]})
    # lingua dei comuni senza stazione: regione del centro
    for c in communes:
        if c["lang"]:
            continue
        r0 = c["rings"][0]
        la = sum(p[0] for p in r0) / len(r0)
        lo = sum(p[1] for p in r0) / len(r0)
        c["lang"] = region_lang(la, lo) or "nl"
    parts, cur, size = [], [], 0
    for c in communes:
        n = len(json.dumps(c))
        if cur and size + n > 180_000:
            parts.append(cur)
            cur, size = [], 0
        cur.append(c)
        size += n
    if cur:
        parts.append(cur)
    for i, p in enumerate(parts, 1):
        json.dump({"communes": p}, open(out / f"communes-{i}.json", "w"), ensure_ascii=False)
    print(len(communes), "comuni in", len(parts), "parti", file=sys.stderr)

    # 4. Regioni e città per orientarsi
    for r in regions:
        tol = 0.001 if r["code"] == "BE-BRU" else 0.004
        rr = [simplify(x, tol) for x in r.pop("full")]
        r["rings"] = [[[round(p[0], 4), round(p[1], 4)] for p in x] for x in rr if len(x) > 3]
    q = '[out:json][timeout:120];area["ISO3166-1"="BE"][admin_level=2]->.be;node[place=city](area.be);out;'
    cities = [{"name": e["tags"].get("name:nl") or e["tags"].get("name"), "name_fr": e["tags"].get("name:fr"),
               "lat": round(e["lat"], 4), "lon": round(e["lon"], 4)} for e in overpass(q, s)]
    json.dump({"regions": regions, "cities": cities}, open(out / "base.json", "w"), ensure_ascii=False)
    print(len(regions), "regioni,", len(cities), "città", file=sys.stderr)


if __name__ == "__main__":
    main()
