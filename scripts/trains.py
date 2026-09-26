"""Tempi di treno verso Bruxelles-Luxembourg dal GTFS ufficiale NMBS/SNCB.

Uso:
    python3 trains.py GTFS.zip AAAAMMGG [--max 30] [--out stations_trains.json]

AAAAMMGG deve essere un giorno feriale normale (niente festivi né vacanze).
Per ogni stazione calcola, nella punta del mattino (arrivo a Luxembourg 7:00-9:00):
  - minutes: durata mediana dei treni diretti (partenza -> arrivo a Luxembourg);
  - peak_am: treni diretti all'ora in quella fascia;
e nella punta della sera (partenza da Luxembourg 16:30-18:30):
  - peak_pm: treni diretti all'ora verso la stazione.
Per le stazioni senza diretto entro --max minuti cerca il miglior percorso con un
cambio (almeno 4 minuti), e riporta change_at e il tempo totale mediano.
Solo libreria standard.
"""
import argparse
import csv
import io
import json
import re
import statistics
import zipfile
from collections import defaultdict

LUX_RE = re.compile(r"(brussel|bruxelles|brussels)[- ]?(luxemburg|luxembourg)", re.I)
AM = (7 * 3600, 9 * 3600)            # arrivo a Luxembourg
PM = (16 * 3600 + 1800, 18 * 3600 + 1800)  # partenza da Luxembourg
MIN_CHANGE = 4 * 60


def rows(z, name):
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, "utf-8-sig"))


def secs(t):
    h, m, s = (int(x) for x in t.split(":"))
    return h * 3600 + m * 60 + s


def active_services(z, date):
    names = set(z.namelist())
    wd = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    import datetime
    d = datetime.date(int(date[:4]), int(date[4:6]), int(date[6:]))
    on = set()
    if "calendar.txt" in names:
        for r in rows(z, "calendar.txt"):
            if r["start_date"] <= date <= r["end_date"] and r[wd[d.weekday()]] == "1":
                on.add(r["service_id"])
    if "calendar_dates.txt" in names:
        for r in rows(z, "calendar_dates.txt"):
            if r["date"] == date:
                (on.add if r["exception_type"] == "1" else on.discard)(r["service_id"])
    return on


def load(z, date):
    stops = {r["stop_id"]: r for r in rows(z, "stops.txt")}
    # stazione = parent_station se presente, altrimenti lo stop stesso
    station_of = {sid: (r.get("parent_station") or sid) for sid, r in stops.items()}
    svc = active_services(z, date)
    trips = {r["trip_id"]: r for r in rows(z, "trips.txt") if r["service_id"] in svc}
    seq = defaultdict(list)
    for r in rows(z, "stop_times.txt"):
        if r["trip_id"] in trips and r["arrival_time"] and r["departure_time"]:
            seq[r["trip_id"]].append((int(r["stop_sequence"]), station_of.get(r["stop_id"], r["stop_id"]),
                                      secs(r["arrival_time"]), secs(r["departure_time"])))
    for v in seq.values():
        v.sort()
    return stops, station_of, trips, seq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gtfs")
    ap.add_argument("date")
    ap.add_argument("--max", type=int, default=30)
    ap.add_argument("--lux", help="stop_id della stazione Bruxelles-Luxembourg, se il riconoscimento per nome fallisce")
    ap.add_argument("--out", default="stations_trains.json")
    a = ap.parse_args()
    z = zipfile.ZipFile(a.gtfs)
    stops, station_of, trips, seq = load(z, a.date)

    if a.lux:
        lux = {station_of.get(a.lux, a.lux)}
    else:
        lux = {station_of[s] for s, r in stops.items() if LUX_RE.search(r["stop_name"])}
    if not lux:
        raise SystemExit("Bruxelles-Luxembourg non trovata in stops.txt: passa --lux")
    print("Luxembourg =", sorted(lux), [stops[s]["stop_name"] for s in lux if s in stops])

    am = defaultdict(list)   # station -> [(dep, arr_lux)]
    pm = defaultdict(list)   # station -> [(dep_lux, arr)]
    legs = defaultdict(list)  # (from, to) -> [(dep, arr)] per i cambi, solo mattina
    for tid, st in seq.items():
        idx = [i for i, x in enumerate(st) if x[1] in lux]
        for i in idx:
            _, _, arr_l, dep_l = st[i]
            for _, s, _, dep in st[:i]:
                if AM[0] <= arr_l <= AM[1] and s not in lux:
                    am[s].append((dep, arr_l))
            for _, s, arr, _ in st[i + 1:]:
                if PM[0] <= dep_l <= PM[1] and s not in lux:
                    pm[s].append((dep_l, arr))
        # tratte generiche nella fascia 6:00-9:30 per i cambi
        for j, (_, s1, _, d1) in enumerate(st):
            if not (6 * 3600 <= d1 <= 9 * 3600 + 1800):
                continue
            for _, s2, a2, _ in st[j + 1:]:
                legs[(s1, s2)].append((d1, a2))

    hours_am = (AM[1] - AM[0]) / 3600
    hours_pm = (PM[1] - PM[0]) / 3600
    out = {}
    for s, v in am.items():
        durs = [(arr - dep) / 60 for dep, arr in v]
        med = statistics.median(durs)
        out[s] = dict(minutes=round(med), minutes_min=round(min(durs)), direct=True,
                      peak_am=round(len({d for d, _ in v}) / hours_am, 1),
                      peak_pm=round(len(pm.get(s, [])) / hours_pm, 1))

    # un cambio: X -> H (qualsiasi treno), poi H -> Luxembourg diretto
    hubs = {s for s, o in out.items()}
    origins = {k[0] for k in legs} - set(out) - lux
    for x in origins:
        best = None
        for h in hubs:
            first = legs.get((x, h))
            if not first:
                continue
            totals = []
            for dep, arr_h in first:
                nxt = [al for d, al in am[h] if d >= arr_h + MIN_CHANGE]
                if nxt:
                    totals.append((min(nxt) - dep) / 60)
            if totals:
                med = statistics.median(totals)
                if best is None or med < best[0]:
                    best = (med, h, len(totals))
        if best and best[0] <= a.max:
            out[x] = dict(minutes=round(best[0]), direct=False,
                          change_at=stops.get(best[1], {}).get("stop_name", best[1]),
                          peak_am=round(best[2] / hours_am, 1), peak_pm=None)

    res = []
    for s, d in out.items():
        if d["minutes"] > a.max:
            continue
        r = stops.get(s, {})
        res.append(dict(stop_id=s, name=r.get("stop_name", s), lat=float(r.get("stop_lat") or 0),
                        lon=float(r.get("stop_lon") or 0), **d))
    res.sort(key=lambda d: d["minutes"])
    with open(a.out, "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"{len(res)} stazioni entro {a.max} min -> {a.out}")


if __name__ == "__main__":
    main()
