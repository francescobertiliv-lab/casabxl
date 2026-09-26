"""Test di scripts/immoweb.py con dati sintetici (nessuna rete). Uso: python3 tests/test_immoweb.py"""
import json
import pathlib
import sys
import tempfile
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import immoweb  # noqa: E402

immoweb.pause = lambda: None


def classified(cid, price=650000, beds=4, facades=4, garden=True, region="Flanders", street="Teststraat"):
    c = {"id": cid, "flags": {"isNewlyBuilt": False, "isPublicSale": False},
         "transaction": {"sale": {"price": price, "isSubjectToVat": False},
                         "certificates": {"primaryEnergyConsumptionPerSqm": 150, "epcScore": "B"}},
         "property": {"subtype": "VILLA", "bedroomCount": beds, "netHabitableSurface": 200,
                      "hasGarden": garden, "gardenSurface": 500, "land": {"surface": 900},
                      "building": {"facadeCount": facades, "constructionYear": 1990, "condition": "GOOD"},
                      "location": {"region": region, "locality": "Testdorp", "postalCode": "9999",
                                   "street": street, "number": "1", "latitude": 50.9, "longitude": 4.5}}}
    return f"<script>window.classified = {json.dumps(c)};\n</script>"


PAGES = {
    1: classified(1),                                   # tenuta, 4 facciate
    2: classified(2, facades=3, region="Wallonie"),     # tenuta, 3 facciate, fr
    3: classified(3, facades=2),                        # scartata: a schiera
    4: classified(4, garden=False),                     # scartata: senza giardino
    5: classified(5, price=950000),                     # scartata: prezzo
    6: classified(6, price=0, facades=None, garden=None, street=None),  # tenuta, tutto da verificare
    7: "<html>niente dati</html>",                      # pagina senza dati
}


class Fake:
    def get(self, url, want_json=False):
        if want_json:
            return {"results": [{"id": i} for i in PAGES], "totalItems": len(PAGES)}
        return PAGES[int(url.rsplit("/", 1)[1])]

    def close(self):
        pass


class Blocking(Fake):
    def get(self, url, want_json=False):
        raise immoweb.Blocked("blocked", "HTTP 403")


def args(out):
    return types.SimpleNamespace(postcodes="3090", out=out, max_price=800000, min_bedrooms=4,
                                 max_bedrooms=5, max_pages=5)


out = tempfile.mktemp(suffix=".json")
s = immoweb.run(args(out), Fake())
kept = {d["id"]: d for d in json.load(open(out))}
assert s["status"] == "ok" and s["found"] == 7 and s["kept"] == 3, s
assert s["dropped"] == {"casa a schiera (2 facciate)": 1, "senza giardino": 1,
                        "prezzo oltre il massimo": 1, "pagina senza dati": 1}, s["dropped"]
assert kept["immoweb-1"]["type"] == "open" and kept["immoweb-1"]["lang"] == "nl" and not kept["immoweb-1"]["approx"]
assert kept["immoweb-2"]["type"] == "halfopen" and kept["immoweb-2"]["lang"] == "fr"
k6 = kept["immoweb-6"]
assert k6["price"] is None and k6["type"] == "unknown" and k6["garden"] is None and k6["approx"]
assert set(k6["to_verify"]) == {"prezzo", "lati liberi", "giardino"}, k6["to_verify"]
s = immoweb.run(args(out), Blocking())
assert s["status"] == "blocked" and s["kept"] == 0, s
print("ok")
