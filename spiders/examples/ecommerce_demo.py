"""
电商类示例爬虫
目标站点：Books to Scrape（http://books.toscrape.com）
这是一个专门用于爬虫练习的电商站点，结构清晰，无反爬
演示：列表页翻页 + 详情页数据提取 + 价格清洗
"""
import scrapy
from spiders.base_spider import BaseSpider


class EcommerceDemoSpider(BaseSpider):
    """电商示例爬虫：图书商品信息"""

    name = "ecommerce_demo"
    redis_key = "ecommerce_demo:start_urls"

    # 起始 URL（单机模式用，分布式模式通过 Redis 推送）
    start_urls = ["http://books.toscrape.com/"]

    site_config = {
        "headers": {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        "download_delay": 1.0,
    }

    use_proxy = False  # 示例站点不需要代理
    need_render = False  # 纯静态页面，不需要 JS 渲染

    def parse(self, response):
        """解析商品列表页"""
        # 提取商品详情页链接
        book_links = response.css("article.product_pod h3 a::attr(href)").getall()
        for link in book_links:
            detail_url = response.urljoin(link)
            yield self.make_request_from_url(
                detail_url,
                callback=self.parse_detail,
            )

        # 翻页
        next_page = response.css("li.next a::attr(href)").get()
        if next_page:
            next_url = response.urljoin(next_page)
            yield self.make_request_from_url(next_url, callback=self.parse)

    def parse_detail(self, response):
        """解析商品详情页"""
        # 基本信息
        title = response.css("div.product_main h1::text").get()
        price = response.css("p.price_color::text").get()
        availability = response.css("p.instock.availability::text").getall()
        rating = response.css("p.star-rating::attr(class)").get()

        # 产品信息表
        info_rows = response.css("table.table-striped tr")
        info = {}
        for row in info_rows:
            key = row.css("th::text").get()
            value = row.css("td::text").get()
            if key and value:
                info[key.strip()] = value.strip()

        # 描述
        description = response.css("div#product_description + p::text").get()

        # 分类（面包屑）
        breadcrumb = response.css("ul.breadcrumb li a::text").getall()
        category = breadcrumb[-1] if len(breadcrumb) > 1 else ""

        item = self.build_item(
            url=response.url,
            title=title,
            price=price,
            price_num=info.get("Price (incl. tax)", ""),
            currency="GBP",
            availability=" ".join(availability).strip() if availability else "",
            rating=rating.replace("star-rating ", "") if rating else "",
            upc=info.get("UPC", ""),
            category=category,
            description=description,
            tax=info.get("Tax", ""),
            reviews=info.get("Number of reviews", "0"),
        )

        yield item
