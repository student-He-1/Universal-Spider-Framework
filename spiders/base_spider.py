"""
爬虫基类
- BaseSpider: 继承 scrapy.Spider，单机可用；配置 RedisScheduler 后也可多机共享队列
- RedisBaseSpider: 继承 scrapy-redis 的 RedisSpider，用于 master 推送 URL、slave 消费的分布式模式

注意：Scrapy 的 Spider.logger 是只读 property，不可在 __init__ 中赋值；
业务日志可直接用 Scrapy 自带的 self.logger，或用 self.site_logger（loguru）。
"""
import scrapy
from typing import List, Dict, Optional
from scrapy_redis.spiders import RedisSpider

from utils.logger import get_logger


class _SpiderCommon:
    """两个基类共用的方法"""

    site_config: Dict = {}
    use_proxy = True
    need_render = False

    @property
    def site_logger(self):
        """loguru 站点日志器（懒加载，避免覆盖 Scrapy 的 self.logger）"""
        if not hasattr(self, "_site_logger"):
            self._site_logger = get_logger(self.name)
        return self._site_logger

    def make_request_from_url(self, url: str, callback=None, meta: Dict = None):
        """构造请求，自动注入站点配置"""
        headers = self.site_config.get("headers", {})
        request = scrapy.Request(
            url,
            callback=callback or self.parse,
            headers=headers,
            meta=meta or {},
            dont_filter=False,
        )
        if self.need_render:
            request.meta["need_render"] = True
        return request

    def build_item(self, **kwargs) -> Dict:
        """构建标准 Item（字典形式），自动添加抓取时间和站点名"""
        from datetime import datetime
        item = dict(kwargs)
        item["spider_name"] = self.name
        item["crawled_at"] = datetime.now().isoformat()
        return item

    def parse(self, response):
        raise NotImplementedError("子类必须实现 parse 方法")


class BaseSpider(_SpiderCommon, scrapy.Spider):
    """
    标准爬虫基类（单机 / 共享队列分布式均可）
    - 单机：settings 不配置 RedisScheduler
    - 分布式：settings 配置 scrapy-redis 的 Scheduler + DupeFilter，
      多个实例用相同 start_urls 启动，由 Redis 去重保证 URL 不被重复抓取
    """


class RedisBaseSpider(_SpiderCommon, RedisSpider):
    """
    Redis 分布式爬虫基类
    起始 URL 由 master 通过 LPUSH/SADD 推送到 redis_key，slave 持续消费
    适用于 URL 动态产生、长期运行的分布式场景
    """

    redis_key = "spider:start_urls"
