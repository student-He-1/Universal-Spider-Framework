"""
Scrapy 引擎封装
统一 Scrapy 运行入口，支持单机和分布式模式
"""
import os
import sys
from pathlib import Path
from typing import Optional, Dict, List

# 将项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapy.crawler import CrawlerProcess
from scrapy.settings import Settings
from scrapy_redis.spiders import RedisSpider

from utils.logger import get_logger

logger = get_logger("engine")


def build_scrapy_settings(
    site_config: Optional[Dict] = None,
    distributed: bool = True,
) -> Settings:
    """
    构建 Scrapy 配置
    distributed: 是否启用 Scrapy-Redis 分布式
    """
    from config.settings import settings as app_settings

    s = Settings()

    # ---------- 核心 ----------
    s.set("BOT_NAME", "spider_framework")
    s.set("SPIDER_MODULES", ["spiders"])
    s.set("NEWSPIDER_MODULE", "spiders")
    s.set("ROBOTSTXT_OBEY", False)

    # ---------- 并发与延迟 ----------
    s.set("CONCURRENT_REQUESTS", app_settings.CONCURRENT_REQUESTS)
    s.set("DOWNLOAD_DELAY", app_settings.DOWNLOAD_DELAY)
    s.set("RANDOMIZE_DOWNLOAD_DELAY", app_settings.RANDOMIZE_DOWNLOAD_DELAY)
    s.set("CONCURRENT_REQUESTS_PER_DOMAIN", 8)
    s.set("CONCURRENT_REQUESTS_PER_IP", 0)

    # ---------- 重试 ----------
    s.set("RETRY_ENABLED", True)
    s.set("RETRY_TIMES", app_settings.RETRY_TIMES)
    s.set("RETRY_HTTP_CODES", [500, 502, 503, 504, 408, 429, 407])

    # ---------- 下载中间件 ----------
    s.set("DOWNLOADER_MIDDLEWARES", {
        "core.middleware.middlewares.UserAgentMiddleware": 400,
        "core.middleware.middlewares.ProxyMiddleware": 410,
        "core.middleware.middlewares.CookieMiddleware": 420,
        "scrapy.downloadermiddlewares.retry.RetryMiddleware": None,  # 禁用原生
        "core.middleware.middlewares.SmartRetryMiddleware": 550,
    })

    # ---------- Item Pipeline ----------
    s.set("ITEM_PIPELINES", {
        "core.pipeline.pipelines.RawStoragePipeline": 300,
        "core.pipeline.pipelines.CleanPipeline": 400,
    })

    # ---------- 日志 ----------
    s.set("LOG_LEVEL", app_settings.LOG_LEVEL)
    s.set("LOG_ENABLED", True)
    s.set("LOG_FORMAT", "%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    # ---------- 分布式（Scrapy-Redis） ----------
    if distributed:
        s.set("SCHEDULER", "scrapy_redis.scheduler.Scheduler")
        s.set("DUPEFILTER_CLASS", "scrapy_redis.dupefilter.RFPDupeFilter")
        s.set("REDIS_URL", app_settings.REDIS_URL)
        s.set("REDIS_START_URLS_AS_SET", True)
        s.set("SCHEDULER_PERSIST", True)  # 关闭后保留队列，支持断点续爬
        s.set("SCHEDULER_FLUSH_ON_START", False)

    # ---------- 站点级覆盖 ----------
    if site_config:
        if "download_delay" in site_config:
            s.set("DOWNLOAD_DELAY", site_config["download_delay"])
        if "concurrent_requests" in site_config:
            s.set("CONCURRENT_REQUESTS", site_config["concurrent_requests"])

    return s


class SpiderRunner:
    """爬虫运行器"""

    def __init__(self, distributed: bool = True):
        self.distributed = distributed
        self.process: Optional[CrawlerProcess] = None

    def run(
        self,
        spider_cls,
        site_config: Optional[Dict] = None,
        start_urls: Optional[List[str]] = None,
        redis_key: Optional[str] = None,
    ):
        """
        运行爬虫
        spider_cls: Spider 类
        site_config: 站点配置 dict
        start_urls: 起始 URL（单机模式用）
        redis_key: Redis 队列 key（分布式模式用，默认 spider_name:start_urls）
        """
        settings = build_scrapy_settings(site_config, distributed=self.distributed)
        self.process = CrawlerProcess(settings)

        # 分布式模式：通过 Redis 推送起始 URL
        if self.distributed and redis_key:
            self._push_start_urls(redis_key, start_urls or [])

        self.process.crawl(spider_cls)
        logger.info(f"启动爬虫: {spider_cls.__name__} (分布式={self.distributed})")
        self.process.start()

    def _push_start_urls(self, redis_key: str, urls: List[str]):
        """向 Redis 推送起始 URL"""
        import redis
        from config.settings import settings as app_settings

        r = redis.from_url(app_settings.REDIS_URL)
        if urls:
            r.sadd(redis_key, *urls)
            logger.info(f"已向 {redis_key} 推送 {len(urls)} 个起始 URL")
        r.close()

    def stop(self):
        """停止爬虫"""
        if self.process:
            self.process.stop()
