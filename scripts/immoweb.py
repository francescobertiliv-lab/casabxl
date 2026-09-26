"""Case in vendita da Immoweb, già nel formato dei `listings` della mappa.

Uso:
    python3 scripts/immoweb.py --postcodes 3090,1930,1800 [--out immoweb.json]
        [--max-price 800000] [--min-bedrooms 4] [--max-bedrooms 5]
        [--max-pages 5] [--browser]

Come funziona (approccio preso da github.com/feldeh/immoweb-scraper, 2023; codice riscritto):
1. l'endpoint JSON di ricerca di Immoweb dà gli id degli annunci che rispettano i filtri;
2. ogni pagina di annuncio contiene i dati completi in `window.classified = {...}`
   (prezzo, camere, facciate, giardino, terreno, indirizzo, coordinate, EPC).
Nessun login: gli annunci sono pubblici.

Regole: una richiesta alla volta, 3-5 secondi tra una e l'altra. Al primo 403/429 o pagina
di verifica anti-bot si ferma e riporta `blocked`, senza ritentare.
`--browser` usa Chromium con playwright-stealth invece di `requests`.

Uscita: `--out` contiene la lista delle case tenute; su stdout una riga JSON di riepilogo
{status: ok|blocked|proxy|error, note, pages, found, kept, dropped: {motivo: n}}.
Stazione, bici, treno, punteggio e first_seen/last_seen li aggiunge chi scrive nel database.
Codice di uscita: 0 ok, 2 blocked/proxy, 1 error.
"""
import argparse
import json
import random
import re
import sys
import time

SEARCH = ("https://www.immoweb.be/en/search-results/house/for-sale?countries=BE"
          "&isALifeAnnuitySale=false&postalCodes={pc}&minPrice=1&maxPrice={maxp}"
          "&minBedroomCount={minb}&maxBedroomCount={maxb}&page={page}&orderBy=newest")
DETAIL = "https://www.immoweb.be/en/classified/{id}"
CHALLENGE = re.compile(r"just a moment|cf-chl|challenge-platform|captcha|access denied", re.I)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")


class Blocked(Exception):
    def __init__(self, status, note):
        super().__init__(note)
        self.status = status


# ---------- trasporto ----------

class RequestsFetcher:
    def __init__(self):
        import requests
        self.requests = requests
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "nl-BE,nl;q=0.9,en;q=0.8"})

    def get(self, url, want_json=False):
        try:
            r = self.s.get(url, timeout=30,
                           headers={"Accept": "application/json"} if want_json else None)
        except self.requests.exceptions.ProxyError as e:
            raise Blocked("proxy", f"bloccato dalla rete del container: {str(e)[:120]}")
        text = r.text
        if r.status_code in (401, 403, 429) or CHALLENGE.search(text[:5000]):
            raise Blocked("blocked", f"HTTP {r.status_code}, anti-bot o blocco del sito")
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} su {url}")
        return r.json() if want_json else text

    def close(self):
        self.s.close()


class BrowserFetcher:
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
                raise Blocked("proxy", "bloccato dalla rete del container")
            raise
        status = resp.status if resp else None
        text = resp.text() if (resp and want_json) else self.page.content()
        if status in (401, 403, 429) or CHALLENGE.search(text[:5000]):
            raise Blocked("blocked", f"HTTP {status}, anti-bot o blocco del sito")
        if status != 200:
            raise RuntimeError(f"HTTP {status} su {url}")
        return json.loads(text) if want_json else text

    def close(self):
        self.browser.close()
        self.cm.__exit__(None, None, None)


# ---------- estrazione ----------

def extract_classified(html):
    """Restituisce il dict di `window.classified = {...}` oppure None."""
    i = html.find("window.classified")
    if i < 0:
        return None
    j = html.find("{", i)
    if j < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(html[j:])
        return obj
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
    if "brussel" in r or "bruxelles" in r or "brussels" in r:
        return "bi"
    if "flander" in r or "vlaander" in r:
        return "nl"
    if "wallon" in r:
        return "fr"
    return None


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
        "land_m2": g(c, "property", "land", "surface"),
        "living_m2": g(c, "property", "netHabitableSurface"),
        "address": " ".join(x for x in [street, number] if x) or None,
        "approx": approx,
        "lat": lat,
        "lon": lon,
        "postcode": loc.get("postalCode"),
        "commune": commune,
        "lang": lang_of(loc.get("region")),
        "year": g(c, "property", "building", "constructionYear"),
        "condition": g(c, "property", "building", "condition"),
        "epc_kwh": g(c, "transaction", "certificates", "primaryEnergyConsumptionPerSqm"),
        "epc_score": g(c, "transaction", "certificates", "epcScore"),
        "new_build": bool(flags.get("isNewlyBuilt")),
        "to_verify": to_verify,
    }, None


# ---------- ciclo principale ----------

def pause():
    time.sleep(random.uniform(3, 5))


def run(a, fetcher):
    pcs = ",".join(f"BE-{p.strip()}" for p in a.postcodes.split(",") if p.strip())
    summary = {"status": "ok", "note": None, "pages": 0, "found": 0, "kept": 0, "dropped": {}}
    kept, seen = [], set()
    try:
        ids = []
        for page in range(1, a.max_pages + 1):
            if page > 1:
                pause()
            data = fetcher.get(SEARCH.format(pc=pcs, maxp=a.max_price, minb=a.min_bedrooms,
                                             maxb=a.max_bedrooms, page=page), want_json=True)
            summary["pages"] = page
            results = (data or {}).get("results") or []
            ids += [r["id"] for r in results if r.get("id") not in seen and not seen.add(r.get("id"))]
            if len(results) == 0 or page * 30 >= (data.get("totalItems") or 0):
                break
        summary["found"] = len(ids)
        for cid in ids:
            pause()
            url = DETAIL.format(id=cid)
            c = extract_classified(fetcher.get(url))
            if not c:
                summary["dropped"]["pagina senza dati"] = summary["dropped"].get("pagina senza dati", 0) + 1
                continue
            item, why = to_listing(c, url, a)
            if why:
                summary["dropped"][why] = summary["dropped"].get(why, 0) + 1
            else:
                kept.append(item)
    except Blocked as e:
        summary.update(status=e.status, note=str(e))
    except Exception as e:  # noqa: BLE001 - l'esito va riportato
        summary.update(status="error", note=str(e)[:300])
    summary["kept"] = len(kept)
    with open(a.out, "w") as f:
        json.dump(kept, f, ensure_ascii=False, indent=1)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--postcodes", required=True, help="codici postali separati da virgola")
    ap.add_argument("--out", default="immoweb.json")
    ap.add_argument("--max-price", type=int, default=800000)
    ap.add_argument("--min-bedrooms", type=int, default=4)
    ap.add_argument("--max-bedrooms", type=int, default=5)
    ap.add_argument("--max-pages", type=int, default=5)
    ap.add_argument("--browser", action="store_true", help="usa Chromium con playwright-stealth")
    a = ap.parse_args()
    fetcher = BrowserFetcher() if a.browser else RequestsFetcher()
    try:
        s = run(a, fetcher)
    finally:
        fetcher.close()
    print(json.dumps(s, ensure_ascii=False))
    sys.exit({"ok": 0, "blocked": 2, "proxy": 2}.get(s["status"], 1))


if __name__ == "__main__":
    main()
