"""Costruisce index.html da page.src.html inserendo il CSS di Leaflet.

Uso: python3 scripts/build_page.py /percorso/leaflet/package/dist
(la cartella dist si ottiene con `npm pack leaflet@1.9.4 && tar xzf leaflet-1.9.4.tgz`).
Le immagini citate dal CSS diventano data URI, perché l'artifact blocca le immagini esterne.
"""
import base64
import pathlib
import re
import sys

root = pathlib.Path(__file__).resolve().parent.parent
dist = pathlib.Path(sys.argv[1])
css = (dist / "leaflet.css").read_text()
css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def inline(m):
    img = dist / m.group(1)
    data = base64.b64encode(img.read_bytes()).decode()
    return f"url(data:image/png;base64,{data})"


css = re.sub(r"url\((images/[^)]+\.png)\)", inline, css)
css = re.sub(r"\n\s*\n", "\n", css).strip()
src = (root / "page.src.html").read_text()
(root / "index.html").write_text(src.replace("/*LEAFLET_CSS*/", css))
print("scritto", root / "index.html")
