"""Test di scripts/immoweb.py con dati sintetici (nessuna rete). Uso: python3 tests/test_immoweb.py"""
import json
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import immoweb  # noqa: E402

immoweb.pause = lambda: None
# percorso in bici finto: 1,3 volte la linea d'aria
immoweb.bike_route_km = lambda a, b, c, d: round(immoweb.km(a, b, c, d) * 1.3, 1)

ST = [{"name": "Stazione A", "lat": 50.90, "lon": 4.50, "minutes": 15, "direct": True, "peak_am": 4, "lang": "nl"},
      {"name": "Stazione B", "lat": 50.70, "lon": 4.50, "minutes": 28, "direct": False, "change_at": "X",
       "peak_am": 1, "lang": "fr"}]
NEAR_A = (50.905, 4.505)   # ~0,7 km da A
NEAR_B = (50.705, 4.505)   # ~0,7 km da B
FAR = (51.30, 5.50)        # lontanissima
EDGE_A = (50.925, 4.53)    # ~3,4 km in linea d'aria, ~4,4 in bici: oltre 3 km


def classified(cid, at=NEAR_A, price=650000, beds=4, facades=4, garden=True, region="Flanders", street="Teststraat",
               land=900, garden_m2=500):
    return {"id": cid, "flags": {"isNewlyBuilt": False, "isPublicSale": False},
            "transaction": {"sale": {"price": price, "isSubjectToVat": False},
                            "certificates": {"primaryEnergyConsumptionPerSqm": 150, "epcScore": "B"}},
            "property": {"subtype": "VILLA", "bedroomCount": beds, "netHabitableSurface": 200,
                         "hasGarden": garden, "gardenSurface": garden_m2, "land": {"surface": land},
                         "building": {"facadeCount": facades, "constructionYear": 1990, "condition": "GOOD"},
                         "location": {"region": region, "locality": "Testdorp", "postalCode": "9999",
                                      "street": street, "number": "1", "latitude": at[0], "longitude": at[1]}}}


LISTINGS = {
    1: classified(1),                                           # tenuta: 4 facciate vicino ad A
    2: classified(2, at=NEAR_B, facades=3, region="Wallonie"),  # scartata: 3 facciate con cambio
    3: classified(3, facades=3),                                # tenuta: 3 facciate ma condizioni ottime
    4: classified(4, facades=2),                                # scartata: a schiera
    5: classified(5, garden=False),                             # scartata: senza giardino
    6: classified(6, price=0, facades=None, garden=None, garden_m2=None, street=None),  # tenuta, da verificare
    9: classified(9, garden=None, garden_m2=None, land=185),   # scartata: giardino non detto, terreno piccolo
    10: classified(10, garden=None, garden_m2=120),             # tenuta: superficie giardino indicata
    11: classified(11, garden=None, garden_m2=None, land=250),  # scartata dopo il dettaglio: giardino non detto
    12: classified(12, beds=6),                                 # scartata dalla ricerca: camere
    7: classified(7, at=FAR),                                   # scartata prima del dettaglio: lontana
    8: classified(8, at=EDGE_A),                                # scartata: bici oltre 3 km
}


class Fake:
    mode = "requests"

    def __init__(self, listings):
        self.listings, self.details = listings, 0

    def get(self, url, want_json=False):
        if want_json:
            res = [{"id": c["id"], "property": {"location": c["property"]["location"],
                                                "bedroomCount": c["property"]["bedroomCount"],
                                                "landSurface": c["property"]["land"]["surface"]},
                    "transaction": {"sale": {"price": c["transaction"]["sale"]["price"]}}}
                   for c in self.listings.values()]
            return {"results": res, "totalItems": len(res)}
        self.details += 1
        return f"<script>window.classified = {json.dumps(self.listings[int(url.rsplit('/', 1)[1])])};\n</script>"

    def close(self):
        pass


class Blocking(Fake):
    def get(self, url, want_json=False):
        raise immoweb.Blocked("blocked", "HTTP 403")


def args(**kw):
    d = dict(postcodes="3090", date="2026-09-27", max_price=800000, min_bedrooms=4, max_bedrooms=5,
             max_pages=5, max_details=150, max_km=3.5, bike=True, min_land=300, time_budget=0, min_land_search=200)
    d.update(kw)
    return types.SimpleNamespace(**d)


# primo giro
f = Fake(LISTINGS)
res, state = immoweb.run(args(), f, ST, {})
s, kept = res["summary"], {d["id"]: d for d in res["listings"]}
assert s["status"] == "ok" and s["found"] == 12 and s["details"] == 9 and s["new"] == 4, s
assert set(kept) == {"immoweb-1", "immoweb-3", "immoweb-6", "immoweb-10"}, kept.keys()
assert s["dropped"] == {"lontana dalle stazioni": 1, "3 facciate senza condizioni ottime": 1,
                        "casa a schiera (2 facciate)": 1, "senza giardino": 1, "bici oltre 3 km": 1,
                        "giardino non indicato e terreno sotto la soglia": 1, "terreno sotto 200 m²": 1,
                        "camere fuori intervallo": 1}, s["dropped"]
assert kept["immoweb-10"]["garden"] is True and "giardino" not in kept["immoweb-10"]["to_verify"]
k1 = kept["immoweb-1"]
assert k1["station"] == "Stazione A" and k1["train_min"] == 15 and k1["bike_km"] <= 3 and k1["type"] == "open"
assert k1["first_seen"] == "2026-09-27" and k1["score"] > kept["immoweb-6"]["score"] and "diretto" in k1["reason"]
k6 = kept["immoweb-6"]
assert k6["price"] is None and k6["type"] == "unknown" and k6["approx"]
assert set(k6["to_verify"]) == {"prezzo", "lati liberi", "giardino"}, k6["to_verify"]

# secondo giro: niente di nuovo, un prezzo cambiato -> un solo annuncio riaperto
L2 = dict(LISTINGS)
L2[1] = classified(1, price=620000)
f2 = Fake(L2)
res2, state = immoweb.run(args(date="2026-09-28"), f2, ST, state)
s2, kept2 = res2["summary"], {d["id"]: d for d in res2["listings"]}
assert f2.details == 1 and s2["new"] == 0 and set(kept2) == set(kept), (f2.details, s2)
assert kept2["immoweb-1"]["first_seen"] == "2026-09-27" and kept2["immoweb-1"]["last_seen"] == "2026-09-28"
assert [p["price"] for p in kept2["immoweb-1"]["price_history"]] == [650000, 620000]

# regola del giardino applicata anche alle case già note
old = {"immoweb-1": dict(state["immoweb-1"], garden=None, land_m2=150)}
res5, st5 = immoweb.run(args(date="2026-09-29"), Fake(L2), ST, dict(state, **old))
assert "immoweb-1" not in {d["id"] for d in res5["listings"]} and st5["immoweb-1"]["dropped"]

# limite di annunci per giro
res3, _ = immoweb.run(args(max_details=2), Fake(LISTINGS), ST, {})
assert res3["summary"]["details"] == 2 and res3["summary"]["deferred"] == 7, res3["summary"]

# tempo finito: nessun annuncio aperto, tutti rinviati
res6, _ = immoweb.run(args(time_budget=-1), Fake(LISTINGS), ST, {})
assert res6["summary"]["details"] == 0 and res6["summary"]["deferred"] == 9, res6["summary"]

# blocco
res4, _ = immoweb.run(args(), Blocking(LISTINGS), ST, {})
assert res4["summary"]["status"] == "blocked" and res4["listings"] == []
print("ok")
