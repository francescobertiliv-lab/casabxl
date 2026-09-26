# casabxl

Case in vendita in Belgio a 5-10 minuti di bici da una stazione con treno per Bruxelles-Luxembourg (Parlamento europeo) in 30 minuti al massimo.

- Mappa: https://claude.ai/artifact/VkknvC4FZtsZNjwj9sH2Wq. I dati stanno nel suo database: `meta` (stato e stazioni), `geo` (linee e comuni), `listings` (annunci), `days` (una raccolta al giorno), `stars` (le stelle di Francesco e Karin).
- `page.src.html`: sorgente della pagina. `python3 scripts/build_page.py <leaflet/dist>` produce `index.html` inserendo il CSS di Leaflet.
- `scripts/browser.py`: apre le pagine degli annunci con Chromium e playwright-stealth; dipendenze in `requirements.txt` (`pip install -r requirements.txt`, senza `playwright install`: Chromium è già in `/opt/pw-browsers`).
- `scripts/immoweb.py` + `.github/workflows/immoweb.yml`: ogni mattina (04:07 UTC) GitHub Actions cerca su Immoweb le case nei codici postali di `data/postcodes.txt` (entro 5 km dalle stazioni di `data/stations.json`), apre solo gli annunci nuovi o con prezzo cambiato (`data/immoweb/state.json`), calcola stazione, bici e punteggio e salva `data/immoweb/<data>.json`. Test senza rete: `python3 tests/test_immoweb.py`. Minuti GitHub: tetto di 25 per giro.
- `scripts/trains.py`: tempi e frequenze verso Bruxelles-Luxembourg dal GTFS ufficiale NMBS/SNCB.
- `.claude/agents/monitor-case.md`: la procedura giornaliera (raccolta, database, mail delle 6:50).

La procedura esiste in due copie: `.claude/agents/monitor-case.md` e il prompt della routine "Case Luxembourg – mail a colazione" (claude.ai → Code → Routines). Se cambi l'una, aggiorna l'altra.
