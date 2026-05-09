import os
import re
import subprocess
import time
import uuid
from threading import Thread

import psycopg2
import psycopg2.extras
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://scraper:scraper@localhost:5433/kleinanzeigen",
)

app = FastAPI(title="Kleinanzeigen Scraper")

# col_expr, direction — None direction means rank (needs lr.rank join)
_SORT_MAP: dict[str, tuple[str, str] | None] = {
    "scraped_at":  ("l.scraped_at",    "DESC"),
    "inserted_at": ("l.inserted_at",   "DESC NULLS LAST"),
    "title":       ("l.title",         "ASC"),
    "price_asc":   ("l.price_numeric", "ASC NULLS LAST"),
    "price_desc":  ("l.price_numeric", "DESC NULLS LAST"),
    "location":    ("l.location",      "ASC NULLS LAST"),
    "rank":        None,   # ORDER BY lr.rank ASC — only useful when run_id is set
}

_jobs: dict[str, dict] = {}
_active_job_id: str | None = None

# City name (lowercase) → (url_slug, location_code)
# Location codes verified against kleinanzeigen.de URL patterns.
# Districts of major cities resolve to the parent city (same code, district slug).
_CITY_MAP: dict[str, tuple[str, str]] = {
    # ── Whole Germany ──────────────────────────────────────────────────────────
    "deutschland":              ("deutschland",              "k0l1"),
    "germany":                  ("deutschland",              "k0l1"),
    # ── Top-10 cities (verified codes) ────────────────────────────────────────
    "berlin":                   ("berlin",                   "k0l3331"),
    "hamburg":                  ("hamburg",                  "k0l9"),
    "münchen":                  ("muenchen",                 "k0l6524"),
    "muenchen":                 ("muenchen",                 "k0l6524"),
    "munich":                   ("muenchen",                 "k0l6524"),
    "köln":                     ("koeln",                    "k0l17"),
    "koeln":                    ("koeln",                    "k0l17"),
    "cologne":                  ("koeln",                    "k0l17"),
    "frankfurt":                ("frankfurt-am-main",        "k0l63"),
    "frankfurt am main":        ("frankfurt-am-main",        "k0l63"),
    "stuttgart":                ("stuttgart",                "k0l13"),
    "düsseldorf":               ("duesseldorf",              "k0l114"),
    "duesseldorf":              ("duesseldorf",              "k0l114"),
    "dortmund":                 ("dortmund",                 "k0l84"),
    "essen":                    ("essen",                    "k0l20"),
    "bremen":                   ("bremen",                   "k0l14"),
    "leipzig":                  ("leipzig",                  "k0l110"),
    "dresden":                  ("dresden",                  "k0l109"),
    "hannover":                 ("hannover",                 "k0l25"),
    "nürnberg":                 ("nuernberg",                "k0l30"),
    "nuernberg":                ("nuernberg",                "k0l30"),
    # ── Further major cities ───────────────────────────────────────────────────
    "duisburg":                 ("duisburg",                 "k0l15"),
    "bochum":                   ("bochum",                   "k0l22"),
    "wuppertal":                ("wuppertal",                "k0l81"),
    "bielefeld":                ("bielefeld",                "k0l55"),
    "bonn":                     ("bonn",                     "k0l58"),
    "münster":                  ("muenster",                 "k0l64"),
    "muenster":                 ("muenster",                 "k0l64"),
    "mannheim":                 ("mannheim",                 "k0l131"),
    "karlsruhe":                ("karlsruhe",                "k0l116"),
    "augsburg":                 ("augsburg",                 "k0l42"),
    "wiesbaden":                ("wiesbaden",                "k0l62"),
    "gelsenkirchen":            ("gelsenkirchen",            "k0l24"),
    "mönchengladbach":          ("moenchengladbach",         "k0l93"),
    "moenchengladbach":         ("moenchengladbach",         "k0l93"),
    "braunschweig":             ("braunschweig",             "k0l26"),
    "chemnitz":                 ("chemnitz",                 "k0l107"),
    "kiel":                     ("kiel",                     "k0l6"),
    "aachen":                   ("aachen",                   "k0l57"),
    "halle":                    ("halle-saale",              "k0l106"),
    "halle (saale)":            ("halle-saale",              "k0l106"),
    "magdeburg":                ("magdeburg",                "k0l104"),
    "freiburg":                 ("freiburg-im-breisgau",     "k0l119"),
    "freiburg im breisgau":     ("freiburg-im-breisgau",     "k0l119"),
    "krefeld":                  ("krefeld",                   "k0l95"),
    "lübeck":                   ("luebeck",                  "k0l8"),
    "luebeck":                  ("luebeck",                  "k0l8"),
    "oberhausen":               ("oberhausen",               "k0l21"),
    "erfurt":                   ("erfurt",                   "k0l99"),
    "rostock":                  ("rostock",                  "k0l44"),
    "mainz":                    ("mainz",                    "k0l60"),
    "kassel":                   ("kassel",                   "k0l69"),
    "hagen":                    ("hagen",                    "k0l79"),
    "hamm":                     ("hamm",                     "k0l85"),
    "saarbrücken":              ("saarbruecken",             "k0l134"),
    "saarbruecken":             ("saarbruecken",             "k0l134"),
    "mülheim an der ruhr":      ("muelheim-an-der-ruhr",     "k0l19"),
    "muelheim an der ruhr":     ("muelheim-an-der-ruhr",     "k0l19"),
    "potsdam":                  ("potsdam",                  "k0l97"),
    "leverkusen":               ("leverkusen",               "k0l91"),
    "oldenburg":                ("oldenburg",                "k0l33"),
    "osnabrück":                ("osnabrueck",               "k0l65"),
    "osnabrueck":               ("osnabrueck",               "k0l65"),
    "heidelberg":               ("heidelberg",               "k0l130"),
    "paderborn":                ("paderborn",                "k0l71"),
    "darmstadt":                ("darmstadt",                "k0l67"),
    "regensburg":               ("regensburg",               "k0l35"),
    "ingolstadt":               ("ingolstadt",               "k0l40"),
    "würzburg":                 ("wuerzburg",                "k0l38"),
    "wuerzburg":                ("wuerzburg",                "k0l38"),
    "ulm":                      ("ulm",                      "k0l126"),
    "wolfsburg":                ("wolfsburg",                "k0l28"),
    "heilbronn":                ("heilbronn",                "k0l121"),
    "göttingen":                ("goettingen",               "k0l31"),
    "goettingen":               ("goettingen",               "k0l31"),
    "recklinghausen":           ("recklinghausen",           "k0l83"),
    "koblenz":                  ("koblenz",                  "k0l56"),
    "bremerhaven":              ("bremerhaven",              "k0l16"),
    "erlangen":                 ("erlangen",                 "k0l43"),
    "pforzheim":                ("pforzheim",                "k0l117"),
    "remscheid":                ("remscheid",                "k0l82"),
    "reutlingen":               ("reutlingen",               "k0l124"),
    "bottrop":                  ("bottrop",                  "k0l23"),
    "trier":                    ("trier",                    "k0l53"),
    "jena":                     ("jena",                     "k0l102"),
    "salzgitter":               ("salzgitter",               "k0l27"),
    "gütersloh":                ("guetersloh",               "k0l72"),
    "guetersloh":               ("guetersloh",               "k0l72"),
    "hildesheim":               ("hildesheim",               "k0l29"),
    "cottbus":                  ("cottbus",                  "k0l98"),
    "siegen":                   ("siegen",                   "k0l77"),
    "gera":                     ("gera",                     "k0l100"),
    "moers":                    ("moers",                    "k0l94"),
    "zwickau":                  ("zwickau",                  "k0l108"),
    "schwerin":                 ("schwerin",                 "k0l45"),
    "solingen":                 ("solingen",                 "k0l80"),
    "ludwigshafen":             ("ludwigshafen-am-rhein",    "k0l133"),
    "ludwigshafen am rhein":    ("ludwigshafen-am-rhein",    "k0l133"),
    "fürth":                    ("fuerth",                   "k0l41"),
    "fuerth":                   ("fuerth",                   "k0l41"),
    "bergisch gladbach":        ("bergisch-gladbach",        "k0l92"),
    "offenbach":                ("offenbach-am-main",        "k0l66"),
    "offenbach am main":        ("offenbach-am-main",        "k0l66"),
    "neuss":                    ("neuss",                    "k0l96"),
    "iserlohn":                 ("iserlohn",                 "k0l76"),
    # ── Berlin districts (→ Berlin city-level) ────────────────────────────────
    "berlin-mitte":             ("berlin",                   "k0l3331"),
    "berlin mitte":             ("berlin",                   "k0l3331"),
    "berlin-prenzlauer berg":   ("berlin",                   "k0l3331"),
    "berlin prenzlauer berg":   ("berlin",                   "k0l3331"),
    "berlin-friedrichshain":    ("berlin",                   "k0l3331"),
    "berlin friedrichshain":    ("berlin",                   "k0l3331"),
    "berlin-kreuzberg":         ("berlin",                   "k0l3331"),
    "berlin kreuzberg":         ("berlin",                   "k0l3331"),
    "berlin-charlottenburg":    ("berlin",                   "k0l3331"),
    "berlin charlottenburg":    ("berlin",                   "k0l3331"),
    "berlin-schöneberg":        ("berlin",                   "k0l3331"),
    "berlin schöneberg":        ("berlin",                   "k0l3331"),
    "berlin-neukölln":          ("berlin",                   "k0l3331"),
    "berlin neukölln":          ("berlin",                   "k0l3331"),
    "berlin-pankow":            ("berlin",                   "k0l3331"),
    "berlin pankow":            ("berlin",                   "k0l3331"),
    "berlin-tempelhof":         ("berlin",                   "k0l3331"),
    "berlin tempelhof":         ("berlin",                   "k0l3331"),
    "berlin-spandau":           ("berlin",                   "k0l3331"),
    "berlin spandau":           ("berlin",                   "k0l3331"),
    "berlin-reinickendorf":     ("berlin",                   "k0l3331"),
    "berlin reinickendorf":     ("berlin",                   "k0l3331"),
    "berlin-steglitz":          ("berlin",                   "k0l3331"),
    "berlin steglitz":          ("berlin",                   "k0l3331"),
    "berlin-zehlendorf":        ("berlin",                   "k0l3331"),
    "berlin zehlendorf":        ("berlin",                   "k0l3331"),
    "berlin-treptow":           ("berlin",                   "k0l3331"),
    "berlin treptow":           ("berlin",                   "k0l3331"),
    "berlin-köpenick":          ("berlin",                   "k0l3331"),
    "berlin köpenick":          ("berlin",                   "k0l3331"),
    "berlin-lichtenberg":       ("berlin",                   "k0l3331"),
    "berlin lichtenberg":       ("berlin",                   "k0l3331"),
    "berlin-marzahn":           ("berlin",                   "k0l3331"),
    "berlin marzahn":           ("berlin",                   "k0l3331"),
    # ── Hamburg districts (→ Hamburg city-level) ──────────────────────────────
    "hamburg-altona":           ("hamburg",                  "k0l9"),
    "hamburg altona":           ("hamburg",                  "k0l9"),
    "hamburg-eimsbüttel":       ("hamburg",                  "k0l9"),
    "hamburg eimsbüttel":       ("hamburg",                  "k0l9"),
    "hamburg-nord":             ("hamburg",                  "k0l9"),
    "hamburg nord":             ("hamburg",                  "k0l9"),
    "hamburg-wandsbek":         ("hamburg",                  "k0l9"),
    "hamburg wandsbek":         ("hamburg",                  "k0l9"),
    "hamburg-harburg":          ("hamburg",                  "k0l9"),
    "hamburg harburg":          ("hamburg",                  "k0l9"),
    "hamburg-bergedorf":        ("hamburg",                  "k0l9"),
    "hamburg bergedorf":        ("hamburg",                  "k0l9"),
    # ── München districts (→ München city-level) ──────────────────────────────
    "münchen-schwabing":        ("muenchen",                 "k0l6524"),
    "münchen schwabing":        ("muenchen",                 "k0l6524"),
    "münchen-maxvorstadt":      ("muenchen",                 "k0l6524"),
    "münchen maxvorstadt":      ("muenchen",                 "k0l6524"),
    "münchen-bogenhausen":      ("muenchen",                 "k0l6524"),
    "münchen bogenhausen":      ("muenchen",                 "k0l6524"),
    "münchen-haidhausen":       ("muenchen",                 "k0l6524"),
    "münchen haidhausen":       ("muenchen",                 "k0l6524"),
    "münchen-sendling":         ("muenchen",                 "k0l6524"),
    "münchen sendling":         ("muenchen",                 "k0l6524"),
    "münchen-neuhausen":        ("muenchen",                 "k0l6524"),
    "münchen neuhausen":        ("muenchen",                 "k0l6524"),
}


def _resolve_city(name: str) -> tuple[str, str]:
    key = name.lower().strip()
    if key in _CITY_MAP:
        return _CITY_MAP[key]
    slug = (key.replace("ä","ae").replace("ö","oe").replace("ü","ue")
               .replace("ß","ss").replace(" ","-"))
    return slug, "k0"


def _conn():
    return psycopg2.connect(DATABASE_URL)


def _serialize(row: dict) -> dict:
    for k in ("scraped_at", "started_at", "completed_at"):
        if row.get(k):
            row[k] = row[k].isoformat()
    for k in ("inserted_at",):
        if row.get(k):
            row[k] = str(row[k])   # date → "YYYY-MM-DD"
    for k in ("id", "scrape_run_id", "run_id"):
        if k in row and row[k] is not None and not isinstance(row[k], (str, int)):
            row[k] = str(row[k])
    return row


def _parse_price(s: str) -> float | None:
    """Extract first numeric value from a German price string like '1.500 € VB'."""
    if not s:
        return None
    m = re.search(r"\d[\d.,]*", s)
    if not m:
        return None
    num = m.group()
    if "," in num and "." in num:          # "1.500,50" → 1500.50
        num = num.replace(".", "").replace(",", ".")
    elif "," in num:                        # "50,50" → 50.50
        num = num.replace(",", ".")
    elif "." in num and len(num.split(".")[-1]) == 3:  # "1.500" → 1500
        num = num.replace(".", "")
    try:
        return float(num)
    except ValueError:
        return None


def _count_for_run(run_id: str) -> int:
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM listing_runs WHERE run_id = %s::uuid", (run_id,))
            return cur.fetchone()[0]
    except Exception:
        return 0


# ── Startup migration ──────────────────────────────────────────────────────────
@app.on_event("startup")
def _migrate():
    os.makedirs("/app/thumbnails", exist_ok=True)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scrape_runs (
                id           UUID PRIMARY KEY,
                search_term  TEXT NOT NULL,
                city_name    TEXT NOT NULL DEFAULT 'Berlin',
                city_slug    TEXT NOT NULL DEFAULT 'berlin',
                started_at   TIMESTAMPTZ DEFAULT NOW(),
                completed_at TIMESTAMPTZ,
                status       TEXT DEFAULT 'running',
                item_count   INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS listing_runs (
                listing_id INTEGER NOT NULL,
                run_id     UUID    NOT NULL,
                rank       INTEGER,
                PRIMARY KEY (listing_id, run_id)
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_listing_runs_run ON listing_runs (run_id)"
        )
        # New columns — safe to run on existing tables
        for stmt in [
            "ALTER TABLE listings ADD COLUMN IF NOT EXISTS price_numeric FLOAT",
            "ALTER TABLE listings ADD COLUMN IF NOT EXISTS inserted_at   DATE",
            "ALTER TABLE listings ADD COLUMN IF NOT EXISTS keywords      TEXT[]",
            "ALTER TABLE listings ADD COLUMN IF NOT EXISTS image_url     TEXT",
            "ALTER TABLE listing_runs ADD COLUMN IF NOT EXISTS rank      INTEGER",
            "CREATE INDEX IF NOT EXISTS idx_listings_price ON listings (price_numeric)",
            "CREATE INDEX IF NOT EXISTS idx_listings_date  ON listings (inserted_at)",
        ]:
            cur.execute(stmt)
        conn.commit()


# ── Cities ────────────────────────────────────────────────────────────────────
# Build a de-duplicated, display-friendly list from _CITY_MAP keys.
_ALIASES = {
    "muenchen", "munich", "koeln", "cologne", "duesseldorf", "nuernberg",
    "germany", "luebeck", "muenster", "osnabrueck", "saarbruecken",
    "wuerzburg", "goettingen", "guetersloh", "muelheim an der ruhr",
    "moenchengladbach", "fuerth", "deutschland",
}
_CITIES_DISPLAY: list[str] = ["Deutschland"] + sorted({
    k.title()
    for k in _CITY_MAP
    if k not in _ALIASES
})


@app.get("/api/cities")
def get_cities():
    return _CITIES_DISPLAY


# ── Stats ──────────────────────────────────────────────────────────────────────
@app.get("/api/stats")
def get_stats():
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT COUNT(*) AS total, MAX(scraped_at) AS last_scraped FROM listings")
        row = dict(cur.fetchone())
    return {
        "total": row["total"] or 0,
        "last_scraped": row["last_scraped"].isoformat() if row["last_scraped"] else None,
    }


# ── Listings ───────────────────────────────────────────────────────────────────
@app.get("/api/listings")
def get_listings(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: str = Query(""),
    price_filter: str = Query(""),
    sort: str = Query("scraped_at"),
    run_id: str = Query(""),
):
    offset = (page - 1) * per_page
    sort_entry = _SORT_MAP.get(sort, ("l.scraped_at", "DESC"))
    rank_sort = sort == "rank"

    conditions: list[str] = []
    params: list = []

    join = ""
    if run_id:
        join = "JOIN listing_runs lr ON l.id = lr.listing_id AND lr.run_id = %s::uuid"
        params.append(run_id)
    elif rank_sort:
        # rank without a run makes no sense — fall back to scraped_at
        rank_sort = False

    if search:
        conditions.append("(l.title ILIKE %s OR l.description ILIKE %s)")
        params += [f"%{search}%", f"%{search}%"]
    if price_filter == "free":
        conditions.append("l.price IS NULL")
    elif price_filter == "vb":
        conditions.append("l.price ILIKE %s")
        params.append("%VB%")
    elif price_filter == "priced":
        conditions.append("l.price IS NOT NULL AND l.price NOT ILIKE %s")
        params.append("%VB%")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    if rank_sort:
        order = "lr.rank ASC NULLS LAST"
    elif sort_entry:
        col, direction = sort_entry
        order = f"{col} {direction}"
    else:
        order = "l.scraped_at DESC"

    select_cols = (
        "l.id, l.title, l.description, l.price, l.price_numeric, "
        "l.location, l.url, l.scraped_at, l.inserted_at, l.keywords, l.image_url"
    )

    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM listings l {join} {where}", params)
        total = cur.fetchone()["n"]
        cur.execute(
            f"SELECT {select_cols} FROM listings l {join} {where} "
            f"ORDER BY {order} LIMIT %s OFFSET %s",
            params + [per_page, offset],
        )
        items = [_serialize(dict(r)) for r in cur.fetchall()]

    return {"items": items, "total": total, "page": page, "per_page": per_page}


# ── Scrape runs list ───────────────────────────────────────────────────────────
@app.get("/api/runs")
def get_runs():
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT id, search_term, city_name, city_slug,
                   started_at, completed_at, status, item_count
            FROM scrape_runs
            ORDER BY started_at DESC
            LIMIT 30
        """)
        return [_serialize(dict(r)) for r in cur.fetchall()]


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: str):
    with _conn() as conn, conn.cursor() as cur:
        # Find listings that belong ONLY to this run (not shared with others)
        cur.execute(
            """
            SELECT listing_id FROM listing_runs
            WHERE run_id = %s::uuid
              AND listing_id NOT IN (
                  SELECT listing_id FROM listing_runs WHERE run_id != %s::uuid
              )
            """,
            (run_id, run_id),
        )
        exclusive_ids = [r[0] for r in cur.fetchall()]

        cur.execute("DELETE FROM listing_runs WHERE run_id = %s::uuid", (run_id,))
        if exclusive_ids:
            cur.execute("DELETE FROM listings WHERE id = ANY(%s)", (exclusive_ids,))
        cur.execute("DELETE FROM scrape_runs WHERE id = %s::uuid", (run_id,))
        conn.commit()

    for lid in exclusive_ids:
        try:
            os.remove(f"/app/thumbnails/{lid}.jpg")
        except FileNotFoundError:
            pass

    return {"deleted_listings": len(exclusive_ids), "run_id": run_id}


# ── Analysis (scoped to a run) ─────────────────────────────────────────────────
@app.get("/api/analysis")
def get_analysis(run_id: str = Query(..., description="UUID of the scrape run")):
    # Build base query through the junction table
    base = """
        FROM listings l
        JOIN listing_runs lr ON l.id = lr.listing_id
        WHERE lr.run_id = %s::uuid
    """
    p = [run_id]

    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # Run metadata
        cur.execute(
            "SELECT search_term, city_name, started_at, completed_at, item_count "
            "FROM scrape_runs WHERE id = %s::uuid",
            (run_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Run not found")
        run_info = _serialize(dict(row))

        # Price category breakdown
        cur.execute(f"""
            SELECT
                CASE
                    WHEN l.price IS NULL OR TRIM(l.price) = ''       THEN 'No price listed'
                    WHEN l.price ILIKE 'Zu verschenken'              THEN 'Free'
                    WHEN l.price ~ '[0-9]' AND l.price ILIKE '%%VB%%' THEN 'Priced + negotiable'
                    WHEN l.price ILIKE '%%VB%%'                       THEN 'Negotiable (VB only)'
                    ELSE 'Fixed price'
                END AS category,
                COUNT(*) AS count
            {base}
            GROUP BY category
            ORDER BY count DESC
        """, p)
        price_breakdown = [dict(r) for r in cur.fetchall()]

        # Summary counts
        cur.execute(f"""
            SELECT
                COUNT(*)                                                                       AS total,
                COUNT(CASE WHEN l.price IS NOT NULL AND TRIM(l.price) != '' THEN 1 END)       AS with_price,
                COUNT(CASE WHEN l.price ILIKE '%%VB%%' THEN 1 END)                            AS vb_count,
                COUNT(CASE WHEN l.price IS NOT NULL AND l.price NOT ILIKE '%%VB%%'
                            AND TRIM(l.price) != '' THEN 1 END)                               AS fixed_count
            {base}
        """, p)
        summary = dict(cur.fetchone())

        # Raw price strings (for numeric parsing)
        cur.execute(f"SELECT l.price {base} AND l.price IS NOT NULL", p)
        raw_prices = [r["price"] for r in cur.fetchall()]

        # Raw locations
        cur.execute(
            f"SELECT l.location {base} AND l.location IS NOT NULL AND l.location != ''", p
        )
        raw_locs = [r["location"] for r in cur.fetchall()]

    # Numeric price stats
    numeric = sorted(
        v for v in (_parse_price(s) for s in raw_prices) if v is not None and v > 0
    )
    price_stats = {
        "count":  len(numeric),
        "min":    round(numeric[0], 2)                         if numeric else None,
        "max":    round(numeric[-1], 2)                        if numeric else None,
        "avg":    round(sum(numeric) / len(numeric), 2)        if numeric else None,
        "median": round(numeric[len(numeric) // 2], 2)         if numeric else None,
    }

    # Clean locations
    loc_counts: dict[str, int] = {}
    for loc in raw_locs:
        loc = re.sub(r"\s*\+\s*\d+\s*km.*$", "", loc, flags=re.IGNORECASE).strip()
        district = re.sub(r"^\d{5}\s+", "", loc).strip()
        if district and district.lower() not in ("kommt zu dir", ""):
            loc_counts[district] = loc_counts.get(district, 0) + 1

    top_locations = sorted(
        [{"district": k, "count": v} for k, v in loc_counts.items()],
        key=lambda x: -x["count"],
    )[:12]

    return {
        "run_info":       run_info,
        "summary":        summary,
        "price_breakdown": price_breakdown,
        "price_stats":    price_stats,
        "top_locations":  top_locations,
    }


# ── Scrape trigger ─────────────────────────────────────────────────────────────
class ScrapeRequest(BaseModel):
    search: str = "Wohnungsaufloesung"
    city: str = "Berlin"


@app.post("/api/scrape")
def start_scrape(body: ScrapeRequest):
    global _active_job_id
    if _active_job_id and _jobs.get(_active_job_id, {}).get("status") == "running":
        raise HTTPException(status_code=409, detail="A scrape is already running — please wait.")

    run_id = str(uuid.uuid4())
    city_slug, city_code = _resolve_city(body.city)

    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO scrape_runs (id, search_term, city_name, city_slug) "
            "VALUES (%s::uuid, %s, %s, %s)",
            (run_id, body.search, body.city, city_slug),
        )
        conn.commit()

    _jobs[run_id] = {"status": "running", "items_so_far": 0, "run_id": run_id}
    _active_job_id = run_id

    def _run():
        global _active_job_id
        try:
            proc = subprocess.Popen(
                [
                    "scrapy", "crawl", "ebay",
                    "-a", f"search_string={body.search}",
                    "-a", f"city_slug={city_slug}",
                    "-a", f"city_code={city_code}",
                    "-a", f"scrape_run_id={run_id}",
                ],
                cwd="/app/ebay",
                env={**os.environ},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            while proc.poll() is None:
                _jobs[run_id]["items_so_far"] = _count_for_run(run_id)
                time.sleep(2)

            final = _count_for_run(run_id)
            status = "done" if proc.returncode == 0 else "error"
            _jobs[run_id] = {"status": status, "items_so_far": final, "run_id": run_id}

            with _conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE scrape_runs SET status=%s, completed_at=NOW(), item_count=%s "
                    "WHERE id=%s::uuid",
                    (status, final, run_id),
                )
                conn.commit()
        except Exception as e:
            _jobs[run_id] = {"status": "error", "items_so_far": 0, "detail": str(e), "run_id": run_id}
            with _conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE scrape_runs SET status='error', completed_at=NOW() WHERE id=%s::uuid",
                    (run_id,),
                )
                conn.commit()
        finally:
            _active_job_id = None

    Thread(target=_run, daemon=True).start()
    return {"job_id": run_id}


@app.get("/api/scrape/{job_id}")
def get_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ── Static files ───────────────────────────────────────────────────────────────
os.makedirs("/app/thumbnails", exist_ok=True)
app.mount("/thumbnails", StaticFiles(directory="/app/thumbnails"), name="thumbnails")
app.mount("/", StaticFiles(directory="static", html=True), name="static")
