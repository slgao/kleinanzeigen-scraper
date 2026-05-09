import scrapy
from ebay.items import KleinanzeigenItem


class KleinanzeigenSpider(scrapy.Spider):
    name = "ebay"
    base_url = "https://www.kleinanzeigen.de"

    def __init__(
        self,
        search_string="Wohnungsaufloesung",
        city_slug="berlin",
        city_code="k0l3331",
        scrape_run_id=None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.scrape_run_id = scrape_run_id
        self._rank = 0
        self.start_urls = [
            f"https://www.kleinanzeigen.de/s-{city_slug}/{search_string}/{city_code}"
        ]

    def _strip(self, value):
        return value.strip() if isinstance(value, str) and value.strip() else None

    def parse(self, response):
        for ad in response.css("li.ad-listitem"):
            title = self._strip(ad.css("a.ellipsis::text").get())
            if not title:
                continue

            self._rank += 1

            item = KleinanzeigenItem()
            item["title"] = title
            item["description"] = self._strip(
                ad.css("p.aditem-main--middle--description::text").get()
            )
            item["price"] = self._strip(
                ad.css("p.aditem-main--middle--price-shipping--price::text").get()
            )
            geo_parts = ad.css("div.aditem-main--top--left::text").getall()
            item["location"] = self._strip(geo_parts[-1]) if geo_parts else None

            href = ad.css("a::attr(href)").get()
            item["url"] = self.base_url + href if href else None
            item["scrape_run_id"] = self.scrape_run_id
            item["rank"] = self._rank

            # Thumbnail — try src first, fall back to lazy-load data-src
            img_url = (
                ad.css("div.imagebox img::attr(src)").get()
                or ad.css("div.imagebox img::attr(data-src)").get()
                or ad.css("img.lazyload::attr(data-src)").get()
                or ad.css("article img::attr(src)").get()
            )
            if img_url and (img_url.startswith("data:") or "placeholder" in img_url.lower()):
                img_url = None
            item["image_url"] = img_url

            # Inserted date shown on the card (Heute / Gestern / DD.MM.YYYY)
            date_parts = ad.css("div.aditem-main--top--right::text").getall()
            item["inserted_at"] = next(
                (self._strip(t) for t in date_parts if self._strip(t)), None
            )

            yield item

        current_page = response.css("span.pagination-current::text").get()
        if not current_page:
            return
        next_marker = f"seite:{int(current_page) + 1}"
        for href in response.css("a.pagination-page::attr(href)").getall():
            if next_marker in href:
                yield response.follow(href, callback=self.parse)
                break
