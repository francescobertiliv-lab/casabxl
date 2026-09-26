"""Apre pagine di annunci con Chromium (Playwright) e playwright-stealth.

Uso:
    python3 scripts/browser.py URL [URL ...] [--out cartella]

Per ogni URL stampa una riga JSON:
    {"url", "status", "http", "final_url", "title", "bytes", "file", "note"}
status:
    ok       pagina letta; l'HTML è in `file`
    proxy    bloccata dalla rete del container (policy dell'ambiente cloud)
    blocked  il sito ha risposto con captcha o blocco anti-bot: registralo e passa oltre
    error    altro errore (timeout, DNS, ...)

Usa il Chromium già installato in /opt/pw-browsers (Playwright 1.56, vedi requirements.txt):
non lanciare `playwright install`. Una pagina ogni 2-3 secondi, senza ritentare sui blocchi.
"""
import argparse
import asyncio
import json
import os
import pathlib
import random
import re

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

BLOCK_MARKERS = re.compile(
    r"captcha|datadome|px-captcha|perimeterx|just a moment|attention required|"
    r"access denied|toegang geweigerd|accès refusé|are you a robot|ben je een robot",
    re.I,
)
PROXY_ERRORS = ("ERR_TUNNEL_CONNECTION_FAILED", "ERR_PROXY_CONNECTION_FAILED", "ERR_PROXY_")


async def fetch(urls, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    stealth = Stealth(navigator_languages_override=("nl-BE", "nl"))
    launch = {"headless": True}
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        launch["proxy"] = {"server": proxy,
                           "bypass": os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""}
    async with stealth.use_async(async_playwright()) as p:
        browser = await p.chromium.launch(**launch)
        ctx = await browser.new_context(locale="nl-BE", timezone_id="Europe/Brussels",
                                        viewport={"width": 1366, "height": 900})
        page = await ctx.new_page()
        for i, url in enumerate(urls):
            if i:
                await asyncio.sleep(random.uniform(2, 3))
            res = {"url": url, "status": "error", "http": None, "final_url": None,
                   "title": None, "bytes": 0, "file": None, "note": None}
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(1500)
                html = await page.content()
                res.update(http=resp.status if resp else None, final_url=page.url,
                           title=await page.title(), bytes=len(html))
                head = (res["title"] or "") + " " + html[:20000]
                if (res["http"] in (401, 403, 429)) or BLOCK_MARKERS.search(head):
                    res.update(status="blocked", note="captcha o blocco anti-bot del sito")
                elif res["http"] and res["http"] >= 400:
                    res.update(note=f"risposta HTTP {res['http']}")
                else:
                    f = out_dir / f"page-{i:03d}.html"
                    f.write_text(html)
                    res.update(status="ok", file=str(f))
            except Exception as e:  # noqa: BLE001 - l'esito va riportato, non sollevato
                msg = str(e).splitlines()[0]
                res["note"] = msg
                if any(k in msg for k in PROXY_ERRORS):
                    res["status"] = "proxy"
            print(json.dumps(res, ensure_ascii=False), flush=True)
        await browser.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--out", default="pages")
    a = ap.parse_args()
    asyncio.run(fetch(a.urls, pathlib.Path(a.out)))


if __name__ == "__main__":
    main()
