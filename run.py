"""
爬虫启动入口
用法:
  python run.py <spider_name> [--urls url1,url2] [--no-proxy] [--single]

示例:
  python run.py ecommerce_demo
  python run.py news_demo --urls http://quotes.toscrape.com/
  python run.py ecommerce_demo --single  # 单机模式（不连 Redis 分布式）
"""
import sys
import argparse
from pathlib import Path

# 项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.logger import get_logger

logger = get_logger("runner")


def main():
    parser = argparse.ArgumentParser(description="万能爬虫框架启动器")
    parser.add_argument("spider", help="爬虫名称（如 ecommerce_demo / news_demo）")
    parser.add_argument("--urls", default="", help="起始 URL，多个用逗号分隔")
    parser.add_argument("--single", action="store_true", help="单机模式（不使用分布式）")
    parser.add_argument("--no-proxy", action="store_true", help="禁用代理")
    parser.add_argument("--log-level", default="INFO", help="日志级别")
    args = parser.parse_args()

    # 动态导入爬虫类
    spider_module = f"spiders.examples.{args.spider}"
    try:
        module = __import__(spider_module, fromlist=["*"])
    except ImportError:
        # 尝试从 spiders 包直接导入
        try:
            spider_module = f"spiders.{args.spider}"
            module = __import__(spider_module, fromlist=["*"])
        except ImportError:
            logger.error(f"找不到爬虫: {args.spider}")
            logger.info("可用爬虫: ecommerce_demo, news_demo")
            sys.exit(1)

    # 查找 Spider 类
    spider_cls = None
    base_names = {"BaseSpider", "RedisBaseSpider"}
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if isinstance(attr, type) and attr.__name__.endswith("Spider") and attr.__name__ not in base_names:
            spider_cls = attr
            break

    if not spider_cls:
        logger.error(f"在模块 {spider_module} 中找不到 Spider 类")
        sys.exit(1)

    # 应用参数
    if args.no_proxy:
        spider_cls.use_proxy = False

    # 解析起始 URL
    start_urls = [u.strip() for u in args.urls.split(",") if u.strip()] if args.urls else None

    # 启动监控
    try:
        from monitor.metrics import start_metrics_server
        start_metrics_server()
    except Exception as e:
        logger.warning(f"监控服务启动失败（不影响爬虫运行）: {e}")

    # 启动爬虫
    from core.engine.runner import SpiderRunner
    from scrapy_redis.spiders import RedisSpider
    distributed = not args.single
    is_redis_spider = issubclass(spider_cls, RedisSpider)

    runner = SpiderRunner(distributed=distributed)

    if distributed and is_redis_spider:
        # Redis 分布式爬虫：master 把起始 URL 推送到 Redis 队列，slave 消费
        redis_key = getattr(spider_cls, "redis_key", f"{args.spider}:start_urls")
        urls = start_urls or list(getattr(spider_cls, "start_urls", []))
        if urls:
            runner._push_start_urls(redis_key, urls)
        runner.run(spider_cls, redis_key=redis_key)
    else:
        # 单机模式，或普通 Spider 的共享队列分布式（start_urls 由各实例发起，Redis 去重）
        if start_urls:
            spider_cls.start_urls = start_urls
        runner.run(spider_cls, distributed=distributed and not is_redis_spider)


if __name__ == "__main__":
    main()
