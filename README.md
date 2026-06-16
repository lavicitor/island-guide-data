# Croatian Island Guides — Data & Site

Static travel guide site for Croatian islands, powered by the
[island-guide-scraper](https://github.com/lavicitor/island-guide-scraper)
data pipeline.

**Live site:** https://lavicitor.github.io/island-guide-data/

## Structure

- `data/` — scraped island data (OpenStreetMap, Open-Meteo, Jadrolinija, Wikipedia)
- `community/` — curated tips and corrections layered on top of scraped data
- `islands/` — generated HTML guide pages
- `generate_pages.py` — builds HTML from JSON sources
- `islands.txt` — list of islands included in the site

## Adding an island

1. Run the scraper: `python island_guide.py --island "Island Name"`
2. Copy output to `data/<slug>.json`
3. Optionally add `community/<slug>.json` with curated tips
4. Run: `python generate_pages.py --island "Island Name"`
5. Add the island name to `islands.txt`
