import os
import re
import urllib.request
from collections import Counter
from datetime import date, timedelta

import psycopg2

_THUMBNAIL_DIR = "/app/thumbnails"

_STOPWORDS = {
    "aber", "alle", "alles", "als", "am", "an", "auch", "auf", "aus",
    "bei", "beim", "bin", "bis", "bitte", "bzw", "da", "damit", "dann",
    "das", "dem", "den", "der", "des", "die", "dies", "durch", "ein",
    "eine", "einem", "einen", "einer", "eines", "er", "es", "eur", "euro",
    "für", "gegen", "gerne", "gibt", "gut", "hat", "habe", "haben", "hier",
    "hin", "ich", "ihr", "immer", "in", "info", "ist", "ja", "je", "jede",
    "kein", "keine", "kann", "mal", "mehr", "mein", "mich", "mir", "mit",
    "nach", "nein", "neu", "nicht", "noch", "nur", "ohne", "oder", "per",
    "pro", "schon", "sehr", "sein", "selbst", "sich", "sie", "sind", "so",
    "tel", "und", "uns", "unter", "vb", "vom", "von", "vor", "war", "waren",
    "werden", "wie", "wird", "wir", "wo", "wenn", "wer", "zu", "zum", "zur",
    "zwei", "über", "uns", "unser", "ihre", "ihren",
}


def _parse_price(s: str):
    if not s:
        return None
    m = re.search(r"\d[\d.,]*", s)
    if not m:
        return None
    num = m.group()
    if "," in num and "." in num:
        num = num.replace(".", "").replace(",", ".")
    elif "," in num:
        num = num.replace(",", ".")
    elif "." in num and len(num.split(".")[-1]) == 3:
        num = num.replace(".", "")
    try:
        return float(num)
    except ValueError:
        return None


def _parse_date(text: str):
    if not text:
        return None
    text = text.strip()
    lc = text.lower()
    today = date.today()
    if lc == "heute":
        return today.isoformat()
    if lc == "gestern":
        return (today - timedelta(days=1)).isoformat()
    m = re.match(r"(\d{1,2})\.(\d{2})\.(\d{4})", text)
    if m:
        return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
    m = re.match(r"(\d{2})\.(\d{4})", text)
    if m:
        return f"{m.group(2)}-{m.group(1)}-01"
    return None


def _extract_keywords(title: str, description: str, n: int = 6):
    text = f"{title or ''} {description or ''}".lower()
    words = re.findall(r"\b[a-züäöß]{3,}\b", text)
    filtered = [w for w in words if w not in _STOPWORDS]
    return [w for w, _ in Counter(filtered).most_common(n)] or None


def _save_thumbnail(url: str, listing_id: int) -> bool:
    path = os.path.join(_THUMBNAIL_DIR, f"{listing_id}.jpg")
    if os.path.exists(path):
        return True
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read()
        if len(data) < 512:   # skip empty/error responses
            return False
        with open(path, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


class PostgresPipeline:
    def open_spider(self):
        self.conn = psycopg2.connect(
            os.environ.get(
                "DATABASE_URL",
                "postgresql://scraper:scraper@localhost:5432/kleinanzeigen",
            )
        )
        self.cur = self.conn.cursor()
        os.makedirs(_THUMBNAIL_DIR, exist_ok=True)

    def close_spider(self):
        self.conn.commit()
        self.cur.close()
        self.conn.close()

    def process_item(self, item):
        price_numeric = _parse_price(item.get("price"))
        keywords = _extract_keywords(item.get("title"), item.get("description"))
        inserted_at = _parse_date(item.get("inserted_at"))
        image_url = item.get("image_url")

        self.cur.execute(
            """
            INSERT INTO listings
                (title, description, price, price_numeric, location, url,
                 keywords, image_url, inserted_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (url) DO UPDATE SET
                title         = EXCLUDED.title,
                description   = EXCLUDED.description,
                price         = EXCLUDED.price,
                price_numeric = EXCLUDED.price_numeric,
                location      = EXCLUDED.location,
                keywords      = EXCLUDED.keywords,
                image_url     = EXCLUDED.image_url,
                inserted_at   = COALESCE(listings.inserted_at, EXCLUDED.inserted_at),
                scraped_at    = NOW()
            RETURNING id
            """,
            (
                item.get("title"),
                item.get("description"),
                item.get("price"),
                price_numeric,
                item.get("location"),
                item.get("url"),
                keywords,
                image_url,
                inserted_at,
            ),
        )
        listing_id = self.cur.fetchone()[0]

        if image_url:
            _save_thumbnail(image_url, listing_id)

        run_id = item.get("scrape_run_id")
        if run_id:
            self.cur.execute(
                """
                INSERT INTO listing_runs (listing_id, run_id, rank)
                VALUES (%s, %s::uuid, %s)
                ON CONFLICT (listing_id, run_id) DO UPDATE SET rank = EXCLUDED.rank
                """,
                (listing_id, run_id, item.get("rank")),
            )
        return item
