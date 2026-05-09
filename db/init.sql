CREATE TABLE IF NOT EXISTS listings (
    id          SERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT,
    price       TEXT,
    location    TEXT,
    url         TEXT UNIQUE NOT NULL,
    scraped_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id           UUID PRIMARY KEY,
    search_term  TEXT NOT NULL,
    city_name    TEXT NOT NULL DEFAULT 'Berlin',
    city_slug    TEXT NOT NULL DEFAULT 'berlin',
    started_at   TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status       TEXT DEFAULT 'running',
    item_count   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS listing_runs (
    listing_id  INTEGER NOT NULL,
    run_id      UUID NOT NULL,
    PRIMARY KEY (listing_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_listings_scraped_at ON listings (scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_listing_runs_run   ON listing_runs (run_id);
