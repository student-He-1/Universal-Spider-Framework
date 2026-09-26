"""
冒烟测试：单机模式抓取少量数据，验证全链路
不依赖 Redis / PostgreSQL，数据落到 D 盘 JSONL
用法: python tests/smoke_test.py [ecommerce_demo|news_demo] [条数]
"""
import sys
import os
from pathlib import Path

# 环境引导（D 盘）
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", r"D:\conda_cache\playwright")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from scrapy.crawler import CrawlerProcess
from core.engine.runner import build_scrapy_settings
from utils.logger import get_logger

logger = get_logger("smoke_test")


def run(spider_name: str = "ecommerce_demo", item_limit: int = 5):
    # 动态导入爬虫
    if spider_name == "ecommerce_demo":
        from spiders.examples.ecommerce_demo import EcommerceDemoSpider as SpiderCls
    elif spider_name == "news_demo":
        from spiders.examples.news_demo import NewsDemoSpider as SpiderCls
    else:
        raise ValueError(f"未知爬虫: {spider_name}")

    settings = build_scrapy_settings(distributed=False)
    # 限制抓取量和时间，便于冒烟测试
    settings.set("CLOSESPIDER_ITEMCOUNT", item_limit)
    settings.set("CLOSESPIDER_TIMEOUT", 60)
    settings.set("LOG_LEVEL", "INFO")
    # 单机测试：JSONL 即可，数据库未启动时自动跳过
    settings.set("HTTPCACHE_ENABLED", False)

    process = CrawlerProcess(settings)
    process.crawl(SpiderCls)
    process.start()

    # 检查 JSONL 输出
    raw_dir = PROJECT_ROOT / "data" / "raw" / spider_name
    print("\n" + "=" * 60)
    print("冒烟测试结果")
    print("=" * 60)
    if raw_dir.exists():
        files = list(raw_dir.glob("*.jsonl"))
        total = 0
        for f in files:
            lines = f.read_text(encoding="utf-8").strip().splitlines()
            n = len([l for l in lines if l.strip()])
            total += n
            print(f"  数据文件: {f} ({n} 条)")
        print(f"\n  共抓取 {total} 条，数据目录: {raw_dir}")
        if total > 0:
            print("  ✅ 冒烟测试通过，数据已落盘到 D 盘")
        else:
            print("  ❌ 未抓到数据")
    else:
        print(f"  ❌ 数据目录不存在: {raw_dir}")


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "ecommerce_demo"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    run(name, limit)
