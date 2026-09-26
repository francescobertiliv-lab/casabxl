# casabxl

Case in vendita in Belgio a 5-10 minuti di bici da una stazione con treno per Bruxelles-Luxembourg (Parlamento europeo) in 30 minuti al massimo.

- Mappa: https://claude.ai/artifact/VkknvC4FZtsZNjwj9sH2Wq. I dati stanno nel suo database: `meta` (stato e stazioni), `geo` (linee e comuni), `listings` (annunci), `days` (una raccolta al giorno), `stars` (le stelle di Francesco e Karin).
- `page.src.html`: sorgente della pagina. `python3 scripts/build_page.py <leaflet/dist>` produce `index.html` inserendo il CSS di Leaflet.
- `scripts/browser.py`: apre le pagine degli annunci con Chromium e playwright-stealth; dipendenze in `requirements.txt` (`pip install -r requirements.txt`, senza `playwright install`: Chromium è già in `/opt/pw-browsers`).
- `scripts/immoweb.py`: case da Immoweb (endpoint di ricerca + dati `window.classified` dell'annuncio, senza login), una richiesta ogni 3-5 s, già nel formato della mappa. Test senza rete: `python3 tests/test_immoweb.py`.
- `scripts/trains.py`: tempi e frequenze verso Bruxelles-Luxembourg dal GTFS ufficiale NMBS/SNCB.
- `.claude/agents/monitor-case.md`: la procedura giornaliera (raccolta, database, mail delle 6:50).

La procedura esiste in due copie: `.claude/agents/monitor-case.md` e il prompt della routine "Case Luxembourg – mail a colazione" (claude.ai → Code → Routines). Se cambi l'una, aggiorna l'altra.
