"""Case in vendita da Immoweb, già pronte per la mappa (stazione, bici, treno, punteggio).

Uso tipico (è quello del workflow GitHub `.github/workflows/immoweb.yml`):
    python3 scripts/immoweb.py --postcodes-file data/postcodes.txt \
        --stations data/stations.json --state data/immoweb/state.json \
        --out-dir data/immoweb --bike [--browser]

Uso minimo:  python3 scripts/immoweb.py --postcodes 3070,1930 --out immoweb.json

Come funziona (approccio preso da feldeh/immoweb-scraper e fortiax/ghent-real-estate; codice riscritto):
1. l'endpoint JSON di ricerca di Immoweb dà gli annunci che rispettano i filtri, per gruppi di codici postali;
2. con `--stations` scarta subito le case a più di `--max-km` in linea d'aria da ogni stazione;
3. la pagina di ogni annuncio contiene i dati completi in `window.classified` (facciate, giardino,
   terreno, indirizzo, coordinate, EPC). Con `--state` la apre solo per le case nuove o con prezzo cambiato;
4. con `--bike` calcola il percorso reale in bici fino alle stazioni vicine (routing.openstreetmap.de)
   e sceglie la stazione migliore; senza `--bike` la distanza bici resta "da verificare";
5. applica i criteri (giardino non indicato: tiene la casa solo con almeno `--min-land` m² di terreno)
   e calcola punteggio e motivo come in `.claude/agents/monitor-case.md`.
Nessun login: gli annunci sono pubblici.

Regole: una richiesta alla volta, 3-5 s tra una richiesta a Immoweb e l'altra. Se bloccato prova una
volta con cloudscraper (se installato), poi si ferma e riporta `blocked`. `--browser` usa Chromium con
playwright-stealth. Codice di uscita: 0 ok, 2 blocked/proxy, 1 error.

Uscita: con `--out-dir` scrive `<out-dir>/<data>.json` = {date, summary, listings}; altrimenti `--out`.
`summary` = {status, note, mode, pages, found, details, new, kept, deferred, dropped: {motivo: n}}.
"""
import argparse
import datetime
import json
import math
import pathlib
import random
import re
import sys
import time
from zoneinfo import ZoneInfo

SEARCH = ("https://www.immoweb.be/en/search-results/house/for-sale?countries=BE"
          "&isALifeAnnuitySale=false&postalCodes={pc}&minPrice=1&maxPrice={maxp}"
          "&minBedroomCount={minb}&maxBedroomCount={maxb}&page={page}&orderBy=newest")
DETAIL = "https://www.immoweb.be/en/classified/{id}"
BIKE = "https://routing.openstreetmap.de/routed-bike/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
CHALLENGE = re.compile(r"just a moment|cf-chl|challenge-platform|captcha|access denied", re.I)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
CHUNK = 15  # codici postali per ricerca


class Blocked(Exception):
    def __init__(self, status, note):
        super().__init__(note)
        self.status = status


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def pause():
    time.sleep(random.uniform(3, 5))


# ---------- trasporto ----------

class RequestsFetcher:
    mode = "requests"

    def __init__(self):
        import requests
        self.requests = requests
        self.s = self._session(requests.Session())

    @staticmethod
    def _session(s):
        s.headers.update({"User-Agent": UA, "Accept-Language": "nl-BE,nl;q=0.9,en;q=0.8"})
        return s

    def _escalate(self):
        """Una sola volta: passa a cloudscraper se è installato."""
        if self.mode != "requests":
            return False
        try:
            import cloudscraper
        except ImportError:
            return False
        self.s = cloudscraper.create_scraper()
        self.mode = "cloudscraper"
        log("bloccato: passo a cloudscraper")
        return True

    def get(self, url, want_json=False):
        while True:
            try:
                r = self.s.get(url, timeout=30,
                               headers={"Accept": "application/json"} if want_json else None)
            except self.requests.exceptions.ProxyError as e:
                raise Blocked("proxy", f"bloccato dalla rete: {str(e)[:120]}")
            if r.status_code in (401, 403, 429) or CHALLENGE.search(r.text[:5000]):
                if self._escalate():
                    continue
                raise Blocked("blocked", f"HTTP {r.status_code}, anti-bot o blocco del sito ({self.mode})")
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} su {url}")
            if want_json:
                try:
                    return r.json()
                except ValueError:
                    raise Blocked("blocked", "la ricerca non ha risposto in JSON (probabile blocco)")
            return r.text

    def close(self):
        self.s.close()


class BrowserFetcher:
    mode = "browser"

    def __init__(self):
        import os
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth
        self.cm = Stealth(navigator_languages_override=("nl-BE", "nl")).use_sync(sync_playwright())
        p = self.cm.__enter__()
        launch = {"headless": True}
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if proxy:
            launch["proxy"] = {"server": proxy}
        self.browser = p.chromium.launch(**launch)
        self.page = self.browser.new_context(locale="nl-BE", timezone_id="Europe/Brussels").new_page()

    def get(self, url, want_json=False):
        try:
            resp = self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:  # noqa: BLE001
            msg = str(e).splitlines()[0]
            if "ERR_TUNNEL_CONNECTION_FAILED" in msg or "ERR_PROXY" in msg:
                raise Blocked("proxy", "bloccato dalla rete")
            raise
        status = resp.status if resp else None
        if want_json and self.page.locator("pre").count():
            text = self.page.locator("pre").first.inner_text()
        else:
            text = resp.text() if (resp and want_json) else self.page.content()
        if status in (401, 403, 429) or CHALLENGE.search(text[:5000]):
            raise Blocked("blocked", f"HTTP {status}, anti-bot o blocco del sito (browser)")
        if status != 200:
            raise RuntimeError(f"HTTP {status} su {url}")
        if want_json:
            try:
                return json.loads(text)
            except ValueError:
                raise Blocked("blocked", "la ricerca non ha risposto in JSON (probabile blocco)")
        return text

    def close(self):
        self.browser.close()
        self.cm.__exit__(None, None, None)


# ---------- dati ----------

def extract_classified(html):
    """Restituisce il dict di `window.classified = {...}` oppure None."""
    i = html.find("window.classified")
    j = html.find("{", i) if i >= 0 else -1
    if j < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(html[j:])[0]
    except ValueError:
        return None


def g(d, *path):
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def lang_of(region):
    r = (region or "").lower()
    if "brussel" in r or "bruxelles" in r:
        return "bi"
    if "flander" in r or "vlaander" in r:
        return "nl"
    if "wallon" in r:
        return "fr"
    return None


def km(lat1, lon1, lat2, lon2):
    p = math.radians
    a = (math.sin(p(lat2 - lat1) / 2) ** 2
         + math.cos(p(lat1)) * math.cos(p(lat2)) * math.sin(p(lon2 - lon1) / 2) ** 2)
    return 2 * 6371 * math.asin(math.sqrt(a))


def to_listing(c, url, crit):
    """Converte un annuncio nel formato della mappa. Restituisce (listing, None) o (None, motivo)."""
    to_verify = []
    price = g(c, "transaction", "sale", "price")
    beds = g(c, "property", "bedroomCount")
    facades = g(c, "property", "building", "facadeCount")
    has_garden = g(c, "property", "hasGarden")
    loc = g(c, "property", "location") or {}

    if price is not None and price > crit.max_price:
        return None, "prezzo oltre il massimo"
    if beds is not None and not (crit.min_bedrooms <= beds <= crit.max_bedrooms):
        return None, "camere fuori intervallo"
    if facades is not None and facades < 3:
        return None, "casa a schiera (2 facciate)"
    if has_garden is False:
        return None, "senza giardino"
    land = g(c, "property", "land", "surface")
    if g(c, "property", "gardenSurface"):
        has_garden = True
    if has_garden is None and (land is None or land < crit.min_land):
        return None, "giardino non indicato e terreno sotto la soglia"

    if not price:
        price = None
        to_verify.append("prezzo")
    if beds is None:
        to_verify.append("camere")
    if facades is None:
        to_verify.append("lati liberi")
        typ = "unknown"
    else:
        typ = "open" if facades >= 4 else "halfopen"
    if has_garden is None:
        to_verify.append("giardino")

    street, number = loc.get("street"), loc.get("number")
    lat, lon = loc.get("latitude"), loc.get("longitude")
    approx = not (street and number and lat is not None and lon is not None)
    if lat is None or lon is None:
        to_verify.append("posizione")
    if g(c, "transaction", "sale", "isSubjectToVat"):
        to_verify.append("IVA 21% (nuova costruzione)")
    flags = c.get("flags") or {}
    if flags.get("isPublicSale"):
        to_verify.append("vendita pubblica: il prezzo è quello di partenza")

    cid = str(c.get("id") or url.rstrip("/").split("/")[-1])
    commune = loc.get("locality")
    return {
        "id": f"immoweb-{cid}",
        "source": "Immoweb",
        "url": url,
        "other_urls": [],
        "title": " ".join(str(x) for x in [g(c, "property", "subtype") or "House", "te koop",
                                            street, number, loc.get("postalCode"), commune] if x),
        "price": price,
        "bedrooms": beds,
        "type": typ,
        "facades": facades,
        "garden": True if has_garden else None,
        "garden_m2": g(c, "property", "gardenSurface"),
        "land_m2": land,
        "living_m2": g(c, "property", "netHabitableSurface"),
        "address": " ".join(x for x in [street, number] if x) or None,
        "approx": approx,
        "lat": lat,
        "lon": lon,
        "postcode": loc.get("postalCode"),
        "commune": commune,
        "lang": lang_of(loc.get("region")),
        "facilities": False,
        "year": g(c, "property", "building", "constructionYear"),
        "condition": g(c, "property", "building", "condition"),
        "epc_kwh": g(c, "transaction", "certificates", "primaryEnergyConsumptionPerSqm"),
        "epc_score": g(c, "transaction", "certificates", "epcScore"),
        "new_build": bool(flags.get("isNewlyBuilt")),
        "to_verify": to_verify,
    }, None


# ---------- stazione, bici, punteggio ----------

def bike_route_km(lat1, lon1, lat2, lon2):
    """Distanza del percorso in bici (km) con routing.openstreetmap.de, o None."""
    import requests
    try:
        r = requests.get(BIKE.format(lat1=lat1, lon1=lon1, lat2=lat2, lon2=lon2), timeout=20,
                         headers={"User-Agent": "casabxl (ricerca casa personale)"})
        routes = r.json().get("routes") if r.status_code == 200 else None
        return round(routes[0]["distance"] / 1000, 1) if routes else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        time.sleep(1)


def near_stations(lat, lon, stations, max_km):
    out = [(km(lat, lon, s["lat"], s["lon"]), s) for s in stations]
    return sorted([x for x in out if x[0] <= max_km], key=lambda x: x[0])


def assign_station(item, stations, crit):
    """Sceglie la stazione migliore e compila i campi di spostamento. Restituisce il motivo di scarto o None."""
    if item["lat"] is None or item["lon"] is None:
        item.update(station=None, bike_km=None, bike_min=None, train_min=None, train_direct=None,
                    change_at=None, peak_am=None, peak_pm=None)
        return None
    cands = near_stations(item["lat"], item["lon"], stations, crit.max_km)[:3]
    if not cands:
        return "lontana dalle stazioni"
    limit = 3.5 if item["approx"] else 3.0
    best = None
    for line_km, s in cands:
        bkm = bike_route_km(item["lat"], item["lon"], s["lat"], s["lon"]) if crit.bike else None
        if bkm is not None and bkm > limit:
            continue
        bmin = math.ceil(bkm / 0.25) if bkm is not None else None
        cost = (bmin if bmin is not None else math.ceil(line_km * 1.3 / 0.25)) + s["minutes"] \
            + (0 if s.get("direct", True) else 10)
        if best is None or cost < best[0]:
            best = (cost, s, bkm, bmin)
    if best is None:
        return "bici oltre 3 km"
    _, s, bkm, bmin = best
    item.update(station=s["name"], bike_km=bkm, bike_min=bmin, train_min=s["minutes"],
                train_direct=s.get("direct", True), change_at=s.get("change_at"),
                peak_am=s.get("peak_am"), peak_pm=s.get("peak_pm"))
    if bkm is None:
        item["to_verify"].append("distanza bici")
    if not item.get("lang"):
        item["lang"] = s.get("lang")
    item["facilities"] = bool(s.get("facilities"))
    if item["type"] == "halfopen" and not (
            item["train_direct"] and item["train_min"] <= 20 and bkm is not None and bkm <= 2.5
            and (item["peak_am"] or 0) >= 2):
        return "3 facciate senza condizioni ottime"
    return None


def score(item):
    tm = item.get("train_min") or 30
    bm = item.get("bike_min") if item.get("bike_min") is not None else 12
    pam = item.get("peak_am") or 0
    s = (100 - 1.5 * (tm - 10) - 3 * bm + 4 * min(pam, 4)
         + {"open": 10, "halfopen": -10}.get(item["type"], 0)
         + {"nl": 5, "fr": -5}.get(item.get("lang"), 0)
         - (5 if (item.get("price") or 0) > 700000 else 0)
         - (10 if item.get("train_direct") is False else 0)
         - 4 * len(item["to_verify"]))
    item["score"] = max(0, min(100, round(s)))
    parts = [{"open": "4 lati", "halfopen": "3 lati"}.get(item["type"])]
    if item.get("station"):
        bike = f"{item['bike_min']}′ di bici" if item.get("bike_min") is not None else "bici da verificare"
        parts.append(f"{bike} da {item['station']}")
        how = "diretto" if item.get("train_direct") else f"con cambio a {item.get('change_at') or '?'}"
        parts.append(f"{how} in {item['train_min']}′" + (f" con {pam} treni/ora" if pam else ""))
    item["reason"] = ", ".join(p for p in parts if p)


# ---------- ciclo principale ----------

def today_be():
    return datetime.datetime.now(ZoneInfo("Europe/Brussels")).date().isoformat()


def load_json(path, default):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return default


def run(a, fetcher, stations=None, state=None):
    date = a.date or today_be()
    postcodes = [p.strip() for p in a.postcodes.split(",") if p.strip()]
    state = state if state is not None else {}
    summary = {"status": "ok", "note": None, "mode": getattr(fetcher, "mode", None), "pages": 0,
               "found": 0, "details": 0, "new": 0, "kept": 0, "deferred": 0, "dropped": {}}
    drop = lambda why: summary["dropped"].__setitem__(why, summary["dropped"].get(why, 0) + 1)  # noqa: E731
    kept, first = [], True
    try:
        items = {}
        for i in range(0, len(postcodes), CHUNK):
            pcs = ",".join(f"BE-{p}" for p in postcodes[i:i + CHUNK])
            for page in range(1, a.max_pages + 1):
                if not first:
                    pause()
                first = False
                data = fetcher.get(SEARCH.format(pc=pcs, maxp=a.max_price, minb=a.min_bedrooms,
                                                 maxb=a.max_bedrooms, page=page), want_json=True) or {}
                summary["pages"] += 1
                results = data.get("results") or []
                log(f"ricerca {postcodes[i]}…: pagina {page}, {len(results)} annunci")
                for r in results:
                    if r.get("id") is not None:
                        items[str(r["id"])] = r
                if not results or page * 30 >= (data.get("totalItems") or 0):
                    break
        summary["found"] = len(items)
        todo = []
        for cid, r in items.items():
            lat, lon = g(r, "property", "location", "latitude"), g(r, "property", "location", "longitude")
            if stations and lat is not None and lon is not None \
                    and not near_stations(lat, lon, stations, a.max_km):
                drop("lontana dalle stazioni")
                continue
            known = state.get(f"immoweb-{cid}")
            price = g(r, "transaction", "sale", "price")
            if known and known.get("price") == (price or None):
                if not known.get("dropped") and known.get("garden") is None \
                        and (known.get("land_m2") is None or known["land_m2"] < a.min_land):
                    known = {"id": known["id"], "price": known.get("price"), "last_seen": date,
                             "dropped": "giardino non indicato e terreno sotto la soglia"}
                    state[known["id"]] = known
                    drop(known["dropped"])
                    continue
                if not known.get("dropped"):
                    known["last_seen"] = date
                    kept.append(known)
                continue
            todo.append((cid, known))
        for n, (cid, known) in enumerate(todo, 1):
            if summary["details"] >= a.max_details:
                summary["deferred"] = len(todo) - n + 1
                log(f"limite di {a.max_details} annunci raggiunto: {summary['deferred']} rinviati a domani")
                break
            pause()
            url = DETAIL.format(id=cid)
            log(f"annuncio {n}/{len(todo)}: {cid}")
            c = extract_classified(fetcher.get(url))
            summary["details"] += 1
            if not c:
                drop("pagina senza dati")
                continue
            item, why = to_listing(c, url, a)
            if not why and stations:
                why = assign_station(item, stations, a)
            key = f"immoweb-{cid}"
            if why:
                drop(why)
                state[key] = {"id": key, "price": g(c, "transaction", "sale", "price") or None,
                              "dropped": why, "last_seen": date}
                continue
            score(item)
            item["first_seen"] = (known or {}).get("first_seen") or date
            item["last_seen"] = date
            history = list((known or {}).get("price_history") or [])
            if not history or history[-1].get("price") != item["price"]:
                history.append({"date": date, "price": item["price"]})
            item["price_history"] = history
            if not known or known.get("dropped"):
                summary["new"] += 1
            state[key] = item
            kept.append(item)
    except Blocked as e:
        summary.update(status=e.status, note=str(e))
    except Exception as e:  # noqa: BLE001 - l'esito va riportato
        summary.update(status="error", note=str(e)[:300])
    summary["mode"] = getattr(fetcher, "mode", None)
    summary["kept"] = len(kept)
    kept.sort(key=lambda x: -(x.get("score") or 0))
    return {"date": date, "summary": summary, "listings": kept}, state


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--postcodes", help="codici postali separati da virgola")
    ap.add_argument("--postcodes-file", help="file con un codice postale per riga")
    ap.add_argument("--stations", help="JSON delle stazioni (data/stations.json)")
    ap.add_argument("--state", help="JSON di stato tra un giro e l'altro (data/immoweb/state.json)")
    ap.add_argument("--out", default="immoweb.json")
    ap.add_argument("--out-dir", help="scrive <out-dir>/<data>.json invece di --out")
    ap.add_argument("--date", help="data della raccolta (AAAA-MM-GG, default oggi a Bruxelles)")
    ap.add_argument("--max-price", type=int, default=800000)
    ap.add_argument("--min-bedrooms", type=int, default=4)
    ap.add_argument("--max-bedrooms", type=int, default=5)
    ap.add_argument("--max-pages", type=int, default=10, help="pagine per gruppo di codici postali")
    ap.add_argument("--max-details", type=int, default=150, help="annunci aperti per giro")
    ap.add_argument("--max-km", type=float, default=3.5, help="distanza in linea d'aria dalla stazione")
    ap.add_argument("--min-land", type=int, default=300,
                    help="se l'annuncio non dice nulla del giardino, terreno minimo (m²) per tenere la casa")
    ap.add_argument("--bike", action="store_true", help="percorso reale in bici (routing.openstreetmap.de)")
    ap.add_argument("--browser", action="store_true", help="usa Chromium con playwright-stealth")
    a = ap.parse_args()
    if a.postcodes_file:
        a.postcodes = ",".join(pathlib.Path(a.postcodes_file).read_text().split())
    if not a.postcodes:
        ap.error("servono --postcodes o --postcodes-file")
    stations = None
    if a.stations:
        stations = [s for s in load_json(a.stations, {}).get("stations", []) if s.get("minutes") is not None]
    state = load_json(a.state, {}) if a.state else None

    fetcher = BrowserFetcher() if a.browser else RequestsFetcher()
    try:
        result, state = run(a, fetcher, stations, state)
    finally:
        fetcher.close()
    out = pathlib.Path(a.out_dir) / f"{result['date']}.json" if a.out_dir else pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    if a.state:
        pathlib.Path(a.state).write_text(json.dumps(state, ensure_ascii=False, indent=1))
    print(json.dumps(result["summary"], ensure_ascii=False))
    sys.exit({"ok": 0, "blocked": 2, "proxy": 2}.get(result["summary"]["status"], 1))


if __name__ == "__main__":
    main()
