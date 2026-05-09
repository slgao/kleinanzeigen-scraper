import scrapy


class KleinanzeigenItem(scrapy.Item):
    title = scrapy.Field()
    description = scrapy.Field()
    price = scrapy.Field()
    location = scrapy.Field()
    url = scrapy.Field()
    scrape_run_id = scrapy.Field()
    image_url = scrapy.Field()
    inserted_at = scrapy.Field()   # raw date text from listing card
    rank = scrapy.Field()          # 1-based position in search results
