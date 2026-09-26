"""
Prometheus 指标埋点
采集爬虫运行指标：请求量、成功率、延迟、代理可用率、数据量等
"""
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    start_http_server,
    CollectorRegistry,
    generate_latest,
)
from config.settings import settings
from utils.logger import get_logger

logger = get_logger("metrics")

# 自定义 registry
registry = CollectorRegistry()

# ==================== 指标定义 ====================

# 请求总数（按站点、状态码）
REQUEST_TOTAL = Counter(
    "spider_request_total",
    "Total number of requests",
    ["site", "status_code"],
    registry=registry,
)

# 请求延迟（直方图）
REQUEST_LATENCY = Histogram(
    "spider_request_latency_seconds",
    "Request latency in seconds",
    ["site"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
    registry=registry,
)

# 抓取数据量
ITEM_SCRAPED = Counter(
    "spider_item_scraped_total",
    "Total number of items scraped",
    ["site"],
    registry=registry,
)

# 清洗后数据量
ITEM_CLEANED = Counter(
    "spider_item_cleaned_total",
    "Total number of items cleaned",
    ["site"],
    registry=registry,
)

# 代理可用数
PROXY_AVAILABLE = Gauge(
    "spider_proxy_available",
    "Number of available proxies",
    registry=registry,
)

# 代理失败数
PROXY_FAILURE = Counter(
    "spider_proxy_failure_total",
    "Total proxy failures",
    ["proxy"],
    registry=registry,
)

# 队列长度
QUEUE_SIZE = Gauge(
    "spider_queue_size",
    "Current queue size",
    ["site"],
    registry=registry,
)

# 去重命中率
DEDUP_HIT = Counter(
    "spider_dedup_hit_total",
    "Total dedup hits (duplicate URLs)",
    ["site"],
    registry=registry,
)

# 错误数
ERROR_TOTAL = Counter(
    "spider_error_total",
    "Total errors",
    ["site", "error_type"],
    registry=registry,
)

# LLM 调用数
LLM_CALL_TOTAL = Counter(
    "spider_llm_call_total",
    "Total LLM calls",
    ["provider", "task_type"],
    registry=registry,
)

# LLM 调用延迟
LLM_LATENCY = Histogram(
    "spider_llm_latency_seconds",
    "LLM call latency",
    ["provider"],
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
    registry=registry,
)


# ==================== 便捷函数 ====================

def record_request(site: str, status_code: int, latency: float):
    """记录一次请求"""
    REQUEST_TOTAL.labels(site=site, status_code=str(status_code)).inc()
    REQUEST_LATENCY.labels(site=site).observe(latency)


def record_item(site: str, cleaned: bool = False):
    """记录一条数据"""
    if cleaned:
        ITEM_CLEANED.labels(site=site).inc()
    else:
        ITEM_SCRAPED.labels(site=site).inc()


def record_error(site: str, error_type: str):
    """记录错误"""
    ERROR_TOTAL.labels(site=site, error_type=error_type).inc()


def record_dedup_hit(site: str):
    """记录去重命中"""
    DEDUP_HIT.labels(site=site).inc()


def update_proxy_available(count: int):
    """更新代理可用数"""
    PROXY_AVAILABLE.set(count)


def update_queue_size(site: str, size: int):
    """更新队列长度"""
    QUEUE_SIZE.labels(site=site).set(size)


def record_llm_call(provider: str, task_type: str, latency: float):
    """记录 LLM 调用"""
    LLM_CALL_TOTAL.labels(provider=provider, task_type=task_type).inc()
    LLM_LATENCY.labels(provider=provider).observe(latency)


def start_metrics_server(port: int = None):
    """启动 Prometheus metrics HTTP 服务"""
    port = port or settings.PROMETHEUS_PORT
    start_http_server(port, registry=registry)
    logger.info(f"Prometheus metrics 服务已启动: http://localhost:{port}/metrics")


def get_metrics_text() -> str:
    """获取当前指标文本（用于自建 Dashboard）"""
    return generate_latest(registry).decode("utf-8")
