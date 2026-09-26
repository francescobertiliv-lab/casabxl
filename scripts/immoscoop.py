"""Case in vendita su Immoscoop (agenzie fiamminghe) per una lista di comuni.

Uso:
    python3 immoscoop.py 1560-hoeilaart 1640-sint-genesius-rode ... [--max-price 800000] [--all] [--out immoscoop.json]

Usa le pagine per comune /zoeken/te-koop/<cap>-<comune> (robots.txt esclude solo /zoeken/query/).
Dall'elenco strutturato (JSON-LD) prende gli annunci di case; dalla pagina di ciascuno legge
solo ciò che vi è scritto: prezzo, indirizzo e coordinate, camere, superfici, giardino, EPC,
facciate e stato dei lavori se l'annuncio li dice. Stesso formato di immovlan.py.
Richiede requests.
"""
import argparse
import html
import json
import re
import sys
import time

import requests

from immovlan import UA, num, renovation

BASE = "https://www.immoscoop.be"


def ldjson(h):
    out = []
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', h, re.S):
        try:
            out.append(json.loads(m.group(1)))
        except ValueError:
            pass
    return out


def lines(h):
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", h, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", "\n", t))
    return [l.strip() for l in t.split("\n") if l.strip()]


def after(L, label):
    for i, l in enumerate(L[:-1]):
        if l == label:
            return L[i + 1]
    return None


def facades(text, subtype):
    t = text.lower()
    if re.search(r"open bebouwing|vrijstaand|4 gevels|vier gevels|alleenstaand", t) or (subtype or "").lower() == "villa":
        return 4, "open"
    if re.search(r"halfopen|half-open|3 gevels|drie gevels", t):
        return 3, "halfopen"
    if re.search(r"gesloten bebouwing|rijwoning|2 gevels|twee gevels|rijhuis", t):
        return 2, "closed"
    return None, "unknown"


def listing_urls(s, town):
    urls = []
    for page in range(1, 6):
        u = f"{BASE}/zoeken/te-koop/{town}" + (f"?page={page}" if page > 1 else "")
        h = s.get(u, timeout=30).text
        new = 0
        for d in ldjson(h):
            if d.get("@type") != "ItemList":
                continue
            for it in d.get("itemListElement", []):
                item = it.get("item") or {}
                if (item.get("mainEntity") or {}).get("@type") == "House" and item.get("url") not in urls:
                    urls.append(item["url"])
                    new += 1
        if not new:
            break
        time.sleep(1.5)
    return urls


def detail(s, url):
    h = s.get(url, timeout=30).text
    L = lines(h)
    price, desc, addr, geo = None, "", {}, {}
    for d in ldjson(h):
        if d.get("@type") == "Product":
            price = num(str((d.get("offers") or {}).get("price") or ""))
            desc = html.unescape(re.sub(r"<br\s*/?>", " ", d.get("description") or ""))
        if d.get("@type") == "House":
            addr = d.get("address") or {}
            geo = d.get("geo") or {}
    subtype = after(L, "Subtype")
    text = " ".join([desc] + L[:60])
    gevels = num(after(L, "Aantal gevels")) or num(after(L, "Gevels"))
    typ = {4: "open", 3: "halfopen", 2: "closed"}.get(gevels)
    if not typ:
        gevels, typ = facades(text, subtype)
    garden_m2 = num(after(L, "Oppervlakte tuin"))
    garden = True if garden_m2 or re.search(r"\btuin\b", desc, re.I) else None
    state = after(L, "Staat van het pand") or after(L, "Staat")
    reno, reno_why = renovation(state, desc)
    street = addr.get("streetAddress")
    return {
        "source": "Immoscoop",
        "url": url,
        "id": "immoscoop-" + url.rstrip("/").rsplit("/", 1)[-1],
        "title": next((l for l in L if " te koop in " in l), None),
        "price": price,
        "bedrooms": num(after(L, "Aantal slaapkamers")),
        "facades": gevels,
        "type": typ,
        "subtype": subtype,
        "garden": garden,
        "garden_m2": garden_m2,
        "land_m2": num(after(L, "Perceeloppervlakte")),
        "living_m2": num(after(L, "Bewoonbare oppervlakte")),
        "year": num(after(L, "Bouwjaar")),
        "epc_kwh": num(after(L, "EPC-score (kWh/(m² jaar))")),
        "epc_label": after(L, "EPC-label"),
        "address": street,
        "postcode": addr.get("postalCode"),
        "commune": addr.get("addressLocality"),
        "lat": geo.get("latitude"),
        "lon": geo.get("longitude"),
        "approx": not street,
        "state": state,
        "renovation": reno,
        "renovation_why": reno_why,
        "description": desc[:600],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("towns", nargs="+", help="codice postale e nome, es. 1560-hoeilaart")
    ap.add_argument("--max-price", type=int, default=800000)
    ap.add_argument("--all", action="store_true", help="apre anche gli annunci sopra prezzo (confronto di mercato)")
    ap.add_argument("--out", default="immoscoop.json")
    a = ap.parse_args()
    s = requests.Session()
    s.headers["User-Agent"] = UA
    res = []
    for town in a.towns:
        try:
            urls = listing_urls(s, town)
        except requests.RequestException as e:
            print(f"{town}: errore {e}", file=sys.stderr)
            continue
        print(f"{town}: {len(urls)} case", file=sys.stderr)
        for u in urls:
            try:
                d = detail(s, u)
            except requests.RequestException as e:
                print("  errore", u, e, file=sys.stderr)
                continue
            d["in_budget"] = d["price"] is not None and d["price"] <= a.max_price
            if d["in_budget"] or a.all:
                res.append(d)
                print(f"  {d['price']} € {d['bedrooms']}c {d['type']} tuin={d['garden']} {d['renovation']} {d['address']}, {d['commune']}", file=sys.stderr)
            time.sleep(1.5)
    json.dump(res, open(a.out, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
