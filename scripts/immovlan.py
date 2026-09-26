"""Case in vendita su Immovlan per una lista di comuni.

Uso:
    python3 immovlan.py 1560-hoeilaart 3070-kortenberg ... [--max-price 800000] [--out immovlan.json]

Per ogni comune legge l'elenco dei risultati (dati strutturati JSON-LD della pagina),
tiene gli annunci con prezzo <= --max-price e apre la pagina di ciascuno per leggere
solo ciò che vi è scritto: camere, facciate, giardino, terreno, superficie, anno, EPC,
indirizzo se pubblicato, coordinate della pagina. Nessun dato è ricostruito: ciò che
manca resta null. Richiede requests.
"""
import argparse
import html
import json
import re
import sys
import time

import requests

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
SEARCH = "https://immovlan.be/nl/vastgoed?transactiontypes=te-koop&propertytypes=huis,villa&towns={town}&page={page}"


def num(s):
    if s is None:
        return None
    m = re.search(r"\d[\d\s .]*", s)
    return int(re.sub(r"\D", "", m.group(0))) if m else None


def text_lines(h):
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", h, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", "\n", t))
    return [l.strip() for l in t.split("\n") if l.strip()]


def after(lines, label, want=None):
    """Valore dopo l'etichetta; con want, la prima occorrenza il cui valore corrisponde."""
    for i, l in enumerate(lines[:-1]):
        if l == label and (want is None or re.fullmatch(want, lines[i + 1])):
            return lines[i + 1]
    return None


def items(d):
    """Tutti gli itemListElement dentro un documento JSON-LD."""
    if isinstance(d, dict):
        yield from d.get("itemListElement", [])
        for k, v in d.items():
            if k != "itemListElement":
                yield from items(v)
    elif isinstance(d, list):
        for v in d:
            yield from items(v)


RENOVATED = re.compile(r"onlangs\b.{0,40}renov|recent\w*\b.{0,40}(?:gerenoveerd|renovatie)|volledig gerenoveerd|grondig gerenoveerd|recent gerenoveerd|totaal ?gerenoveerd|volledig vernieuwd|instapklaar|gerenoveerd in 20\d\d|nieuwbouw|als nieuw", re.I)
TO_RENOVATE = re.compile(r"te renoveren|renovatieproject|op te frissen|te verfrissen|renovatiewoning|nood aan renovatie|op te knappen", re.I)


STATE_IT = {"gerenoveerd": "ristrutturata", "volledig gerenoveerd": "completamente ristrutturata",
            "uitstekend": "ottimo stato", "uitstekende staat": "ottimo stato", "als nieuw": "come nuova",
            "nieuw": "nuova", "nieuw pand": "nuova costruzione", "nieuwbouw": "nuova costruzione",
            "in opbouw": "in costruzione", "normaal": "normale", "goed": "buono",
            "te renoveren": "da ristrutturare", "te slopen": "da demolire",
            "te verfrissen": "da rinfrescare", "op te frissen": "da rinfrescare"}
DONE = {"gerenoveerd", "volledig gerenoveerd", "uitstekend", "uitstekende staat", "als nieuw", "nieuw",
        "nieuw pand", "nieuwbouw", "in opbouw"}


def renovation(state, text):
    """Stato dei lavori secondo l'annuncio: renovated, to_renovate, refresh, unknown, con la frase che lo dice."""
    st = (state or "").strip().lower()
    label = f"Annuncio: «{state}» ({STATE_IT.get(st, '?')})" if state else None
    if st in DONE:
        return "renovated", label
    if st in ("te renoveren", "te slopen"):
        return "to_renovate", label
    if st in ("te verfrissen", "op te frissen"):
        return "refresh", label
    m = TO_RENOVATE.search(text)
    if m:
        return "to_renovate", f"Descrizione: «…{text[max(0, m.start() - 40):m.end() + 40].strip()}…»"
    m = RENOVATED.search(text)
    if m:
        return "renovated", f"Descrizione: «…{text[max(0, m.start() - 40):m.end() + 40].strip()}…»"
    return "unknown", (label + ": non dice se è ristrutturata") if label else "L'annuncio non lo dice"


def listing_urls(s, town):
    out = {}
    for page in range(1, 6):
        h = s.get(SEARCH.format(town=town, page=page), timeout=30).text
        found = 0
        for m in re.finditer(r'<script type="application/ld(?:\+|&#x2B;)json">(.*?)</script>', h, re.S):
            try:
                d = json.loads(m.group(1))
            except ValueError:
                continue
            for it in items(d):
                url = it.get("url")
                price = ((it.get("item") or {}).get("offers") or {}).get("price")
                if url and "/detail/" in url and f"/{town.split('-')[0]}/" in url:
                    out[url] = price
                    found += 1
        if not found:
            break
        time.sleep(1.5)
    return out


def detail(s, url):
    h = s.get(url, timeout=30).text
    L = text_lines(h)
    m = re.search(r"/te-koop/(\d{4})/([^/]+)/", url)
    postcode, slug = (m.group(1), m.group(2)) if m else (None, None)
    pc = next((l for l in L if postcode and l.startswith(postcode + " ") and l[5:].lower().replace(" ", "-") == slug), None)
    title = next((l for l in L if l.startswith(("Huis te koop", "Villa te koop"))), None)
    lat = re.search(r'(?:lat|latitude)["\']?\s*[:=]\s*["\']?(5\d\.\d+)', h)
    lon = re.search(r'(?:lng|lon|longitude)["\']?\s*[:=]\s*["\']?([2-6]\.\d+)', h)
    i = L.index(pc) if pc in L else -1
    street = L[i - 1] if i > 0 and not re.fullmatch(r"[A-Z]{2,4}\d+", L[i - 1]) and any(c.isdigit() for c in L[i - 1]) else None
    gevels = num(after(L, "Aantal gevels"))
    desc = after(L, "Beschrijving") or ""
    state = after(L, "Staat van het zoekertje")
    reno, reno_why = renovation(state, desc)
    return {
        "source": "Immovlan",
        "url": url,
        "id": "immovlan-" + url.rstrip("/").rsplit("/", 1)[-1],
        "title": title,
        "price": num(after(L, "Prijs")),
        "bedrooms": num(after(L, "Aantal slaapkamers")),
        "facades": gevels,
        "terrace": after(L, "Terras", "Ja|Nee") == "Ja",
        "type": "open" if gevels == 4 else "halfopen" if gevels == 3 else "closed" if gevels == 2 else "unknown",
        "garden": True if after(L, "Tuin", "Ja|Nee") == "Ja" else None,
        "garden_m2": num(after(L, "Oppervlakte tuin")),
        "land_m2": num(after(L, "Totale grondoppervlakte")),
        "living_m2": num(after(L, "Bewoonbare oppervlakte")),
        "year": num(after(L, "Bouwjaar")),
        "epc_kwh": num(after(L, "Specifiek primair energieverbruik")),
        "address": street,
        "postcode": postcode,
        "commune": pc[5:] if pc else (slug or "").replace("-", " ").title() or None,
        "lat": float(lat.group(1)) if lat else None,
        "lon": float(lon.group(1)) if lon else None,
        "approx": street is None,
        "state": state,
        "renovation": reno,
        "renovation_why": reno_why,
        "description": desc[:600],
        "updated": (after(L, "Laatst gewijzigd:") or next((l.split(": ", 1)[1] for l in L if l.startswith("Laatst gewijzigd:")), None)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("towns", nargs="+", help="codice postale e nome, es. 1560-hoeilaart")
    ap.add_argument("--max-price", type=int, default=800000)
    ap.add_argument("--out", default="immovlan.json")
    ap.add_argument("--all", action="store_true", help="apre anche gli annunci sopra prezzo, come termine di paragone per il mercato")
    a = ap.parse_args()
    s = requests.Session()
    s.headers["User-Agent"] = UA
    res = []
    for town in a.towns:
        urls = listing_urls(s, town)
        ok = {u: p for u, p in urls.items() if p is not None and (a.all or p <= a.max_price)}
        print(f"{town}: {len(urls)} annunci, {len(ok)} da aprire", file=sys.stderr)
        for u in ok:
            try:
                d = detail(s, u)
            except requests.RequestException as e:
                print("  errore", u, e, file=sys.stderr)
                continue
            d["in_budget"] = d["price"] is not None and d["price"] <= a.max_price
            res.append(d)
            print(f"  {d['price']} € {d['bedrooms']} camere {d['facades']} facciate tuin={d['garden']} {d['commune']} {u}", file=sys.stderr)
            time.sleep(1.5)
    json.dump(res, open(a.out, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
