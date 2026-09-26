"""Linee dei treni che fermano a Bruxelles-Luxembourg, dal GTFS ufficiale NMBS/SNCB.

Uso:
    python3 lines.py GTFS.zip AAAAMMGG [--max 30] [--out lines.json]

Il GTFS SNCB non ha shapes.txt: ogni linea è la sequenza delle fermate (schematica).
Raggruppa i treni del giorno per categoria (S, IC, L, P) e percorso; tiene solo la
parte di percorso entro --max minuti da Luxembourg, così la mappa resta leggibile.
Scrive {lines: [{name, kind, serves_lux, trains, coords, stops}]} per il documento geo/lines.
Solo libreria standard.
"""
import argparse
import json
import zipfile
from collections import defaultdict

from trains import LUX_RE, active_services, rows, secs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gtfs")
    ap.add_argument("date")
    ap.add_argument("--max", type=int, default=30)
    ap.add_argument("--out", default="lines.json")
    a = ap.parse_args()
    z = zipfile.ZipFile(a.gtfs)

    stops = {r["stop_id"]: r for r in rows(z, "stops.txt")}
    parent = {sid: (r.get("parent_station") or sid) for sid, r in stops.items()}
    nl = {}
    for r in rows(z, "translations.txt"):
        if r["table_name"] == "stops" and r["field_name"] == "stop_name" and r["language"] == "nl":
            nl[r["field_value"]] = r["translation"]
    lux = {sid for sid, r in stops.items() if LUX_RE.search(r["stop_name"]) and not r.get("parent_station")}

    on = active_services(z, a.date)
    routes = {r["route_id"]: r for r in rows(z, "routes.txt")}
    trips = {r["trip_id"]: r for r in rows(z, "trips.txt") if r["service_id"] in on}

    seq = defaultdict(list)
    for r in rows(z, "stop_times.txt"):
        if r["trip_id"] in trips:
            t = r["departure_time"] or r["arrival_time"]
            seq[r["trip_id"]].append((int(r["stop_sequence"]), parent[r["stop_id"]], secs(t)))

    groups = defaultdict(lambda: {"trains": 0})
    for tid, st in seq.items():
        st.sort()
        idx = [i for i, (_, s, _) in enumerate(st) if s in lux]
        if not idx:
            continue
        i = idx[0]
        t0 = st[i][2]
        # solo le fermate entro --max minuti da Luxembourg, prima e dopo
        keep = [s for _, s, t in st if abs(t - t0) <= a.max * 60]
        kind = routes[trips[tid]["route_id"]]["route_short_name"] or "?"
        key = (kind, tuple(keep))
        g = groups[key]
        g["trains"] += 1

    # un percorso e il suo inverso sono la stessa linea
    merged = {}
    for (kind, path), g in groups.items():
        k = (kind, min(path, path[::-1]))
        m = merged.setdefault(k, {"trains": 0})
        m["trains"] += g["trains"]

    # un tratto contenuto in un percorso più lungo della stessa linea non è una linea a sé
    def inside(short, long):
        n = len(short)
        return any(tuple(long[i:i + n]) in (short, short[::-1]) for i in range(len(long) - n + 1))

    for (kind, path) in sorted(merged, key=lambda k: len(k[1])):
        longer = [k for k in merged if k[0] == kind and len(k[1]) > len(path) and inside(path, k[1])]
        if longer:
            best = max(longer, key=lambda k: len(k[1]))
            merged[best]["trains"] += merged.pop((kind, path))["trains"]

    def name(sid):
        n = stops[sid]["stop_name"]
        return nl.get(n, n)

    out = []
    for (kind, path), g in sorted(merged.items(), key=lambda x: -x[1]["trains"]):
        if len(path) < 2:
            continue
        out.append({
            "name": f"{kind} {name(path[0])} – {name(path[-1])} (schematica)",
            "kind": kind,
            "serves_lux": True,
            "trains": g["trains"],
            "stops": [name(s) for s in path],
            "coords": [[round(float(stops[s]["stop_lat"]), 5), round(float(stops[s]["stop_lon"]), 5)] for s in path],
        })
    with open(a.out, "w") as f:
        json.dump({"lines": out}, f, ensure_ascii=False)
    print(len(out), "percorsi ->", a.out)
    for l in out[:40]:
        print(f"{l['trains']:>4}  {l['name']}  ({len(l['stops'])} fermate)")


if __name__ == "__main__":
    main()
