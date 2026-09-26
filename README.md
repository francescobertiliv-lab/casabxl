# casabxl

Case in vendita in Belgio a 5-10 minuti di bici da una stazione con treno per Bruxelles-Luxembourg (Parlamento europeo) in 30 minuti al massimo.

- Mappa: https://claude.ai/artifact/VkknvC4FZtsZNjwj9sH2Wq. I dati stanno nel suo database: `meta` (stato e stazioni), `geo` (linee e comuni), `listings` (annunci), `days` (una raccolta al giorno), `stars` (le stelle di Francesco e Karin).
- `page.src.html`: sorgente della pagina. `python3 scripts/build_page.py <leaflet/dist>` produce `index.html` inserendo il CSS di Leaflet.
- `scripts/trains.py`: tempi e frequenze verso Bruxelles-Luxembourg dal GTFS ufficiale NMBS/SNCB.
- `scripts/lines.py`: linee (S4, S5, S8, S9, S19, IC, P) che fermano a Bruxelles-Luxembourg, dal GTFS.
- `scripts/build_base.py`: comune e lingua delle stazioni, aree bici di 10 minuti (Valhalla), confini dei comuni e delle Regioni (OpenStreetMap).
- `scripts/immovlan.py`: annunci Immovlan, con lo stato dei lavori come lo scrive l'annuncio.
- `scripts/process.py`: criteri, bici fino alla stazione, auto fino a Maaseik, confronto col mercato (regressione sui prezzi richiesti della zona) e punteggio.
- `.claude/agents/monitor-case.md`: la procedura giornaliera (raccolta, database, mail delle 6:50).

La procedura esiste in due copie: `.claude/agents/monitor-case.md` e il prompt della routine "Case Luxembourg – mail a colazione" (claude.ai → Code → Routines). Se cambi l'una, aggiorna l'altra.
