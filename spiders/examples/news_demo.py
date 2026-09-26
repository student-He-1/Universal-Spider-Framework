"""
新闻类示例爬虫
目标站点：Quotes to Scrape（http://quotes.toscrape.com）
专门用于爬虫练习的站点，演示：
- 列表页翻页
- 作者详情页跟随
- 标签提取
- 文本清洗
"""
import scrapy
from spiders.base_spider import BaseSpider


class NewsDemoSpider(BaseSpider):
    """新闻示例爬虫：名人名言（模拟新闻文章结构）"""

    name = "news_demo"
    redis_key = "news_demo:start_urls"

    start_urls = ["http://quotes.toscrape.com/"]

    site_config = {
        "headers": {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        "download_delay": 0.5,
    }

    use_proxy = False
    need_render = False

    def parse(self, response):
        """解析列表页"""
        # 提取每条名言
        quotes = response.css("div.quote")
        for quote in quotes:
            text = quote.css("span.text::text").get()
            author = quote.css("small.author::text").get()
            tags = quote.css("div.tags a.tag::text").getall()
            author_link = quote.css("span a::attr(href)").get()

            item = self.build_item(
                url=response.url,
                content=text,
                author=author,
                tags=tags,
                tag_count=len(tags),
            )

            # 同时请求作者详情页
            if author_link:
                author_url = response.urljoin(author_link)
                yield self.make_request_from_url(
                    author_url,
                    callback=self.parse_author,
                    meta={"quote_item": item},
                )
            else:
                yield item

        # 翻页
        next_page = response.css("li.next a::attr(href)").get()
        if next_page:
            next_url = response.urljoin(next_page)
            yield self.make_request_from_url(next_url, callback=self.parse)

    def parse_author(self, response):
        """解析作者详情页，补充作者信息"""
        item = response.meta.get("quote_item", {})

        author_name = response.css("h3.author-title::text").get()
        birth_date = response.css("span.author-born-date::text").get()
        birth_place = response.css("span.author-born-location::text").get()
        description = response.css("div.author-description::text").get()

        item.update({
            "author_name": author_name.strip() if author_name else "",
            "author_birth_date": birth_date.strip() if birth_date else "",
            "author_birth_place": birth_place.strip() if birth_place else "",
            "author_description": description.strip() if description else "",
        })

        yield item
