# Kleinanzeigen Scraper

A full-stack web scraper for [kleinanzeigen.de](https://www.kleinanzeigen.de) with a browser-based UI for triggering scrapes, browsing results, and analysing price and location data.

## Features

- **Scrape on demand** — enter a search term and pick a city or district from a searchable dropdown (80+ German locations including Berlin/Hamburg/München districts); scraping progress is shown live
- **Browse results** — grid or list view with thumbnail previews, keyword tags, price badges, and ad-posted dates; filter by run, text, price type, and sort by price, location, ad date, relevancy, or title
- **Price & location analysis** — per-run breakdown of price categories, numeric stats (min / max / avg / median), and top districts; all scoped to a single scrape run
- **Delete runs** — remove a scrape run and its exclusive listings (+ thumbnails) directly from the UI
- **Persistent thumbnails** — downloaded at scrape time and served locally; survives container rebuilds via a named Docker volume
- **Auto-extracted keywords** — German stop-word filtered, stored as a PostgreSQL array, shown as tags on cards and in the detail modal

## Stack

| Layer | Technology |
|---|---|
| Spider | [Scrapy](https://scrapy.org/) 2.15 |
| Backend | [FastAPI](https://fastapi.tiangolo.com/) + Uvicorn |
| Database | PostgreSQL 16 |
| Frontend | Plain HTML/JS + [Tailwind CSS](https://tailwindcss.com/) (CDN) + [Chart.js](https://www.chartjs.org/) |
| Containers | Docker + Docker Compose |

## Quick start

**Requirements:** Docker and Docker Compose installed.

### 1. Clone the repository

```bash
git clone git@github.com:slgao/kleinanzeigen-scraper.git
cd kleinanzeigen-scraper
```

### 2. Build and start all services

```bash
docker-compose up -d --build
```

This starts three containers:
- `db` — PostgreSQL 16 (host port **5433**)
- `backend` — FastAPI + Uvicorn (host port **8001**)

The database schema and any missing columns are created automatically on first start.

### 3. Open the UI

Visit **http://localhost:8001** in your browser.

### Stopping the app

```bash
docker-compose down
```

Add `-v` to also delete the database and thumbnail volumes (all scraped data will be lost):

```bash
docker-compose down -v
```

### Restarting after code changes

```bash
docker-compose up -d --build backend
```

Only the backend image needs rebuilding — the database container and its data are unaffected.

---

## How to use

1. Type a search term (e.g. `Wohnungsaufloesung`, `Sofa`, `iPhone`)
2. Select a city or district from the location picker — type to filter through 80+ German locations
3. Click **▶ Run** — the progress bar shows items found in real time
4. Switch to the **Browse** tab to view results; pick a run from the dropdown to scope the view to that scrape only
5. Switch to the **Analysis** tab and select a run to see price statistics and top locations
6. Use the **Delete run** button (trash icon) next to the run selector to remove a run and its listings

## Project layout

```
.
├── backend/
│   ├── main.py            # FastAPI app — API endpoints, spider subprocess, migrations
│   ├── requirements.txt
│   ├── Dockerfile
│   └── static/
│       └── index.html     # Single-page frontend
├── ebay/
│   └── ebay/
│       ├── spiders/
│       │   └── ebay_spider.py   # Scrapy spider
│       ├── pipelines.py         # PostgreSQL pipeline, keyword extraction, thumbnail download
│       ├── items.py
│       └── settings.py
├── db/
│   └── init.sql           # Initial schema (migrations run automatically on startup)
└── docker-compose.yml
```

## Database schema

```
listings        — id, title, description, price, price_numeric, location, url,
                  scraped_at, inserted_at, keywords[], image_url
scrape_runs     — id (UUID), search_term, city_name, city_slug,
                  started_at, completed_at, status, item_count
listing_runs    — listing_id, run_id, rank   (junction table)
```

New columns are added automatically via `ALTER TABLE … ADD COLUMN IF NOT EXISTS` on every startup — no manual migrations needed.

## Configuration

All configuration is through environment variables:

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://scraper:scraper@db:5432/kleinanzeigen` | PostgreSQL connection string |

The database port is mapped to `5433` on the host to avoid conflicts with a locally running PostgreSQL.

## Notes

- The spider respects `ROBOTSTXT_OBEY = True` and uses a 5-second download delay between requests.
- Thumbnails are stored in a named Docker volume (`thumbnails`) at `/app/thumbnails/{id}.jpg` and served by FastAPI's `StaticFiles`. If a thumbnail is not available locally the frontend falls back to the original kleinanzeigen.de URL.
- District-level location selections (e.g. *Berlin – Mitte*) resolve to their parent city's location code on kleinanzeigen.de; full district-level filtering is not supported by the public URL API.
