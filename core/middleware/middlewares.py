"""
Scrapy 下载中间件集合
- UserAgentMiddleware: UA 轮换
- ProxyMiddleware: 代理注入
- RetryMiddleware: 智能重试
- CookieMiddleware: Cookie 池管理
"""
import random
import time
from typing import Optional
from scrapy import signals
from scrapy.downloadermiddlewares.retry import RetryMiddleware as ScrapyRetryMiddleware
from scrapy.utils.response import response_status_message

from utils.fingerprint import random_ua, build_headers
from utils.logger import get_logger

logger = get_logger("middleware")


class UserAgentMiddleware:
    """随机 User-Agent 中间件"""

    def __init__(self, mobile_ratio: float = 0.0):
        self.mobile_ratio = mobile_ratio

    @classmethod
    def from_crawler(cls, crawler):
        return cls(
            mobile_ratio=crawler.settings.getfloat("UA_MOBILE_RATIO", 0.0),
        )

    def process_request(self, request, spider):
        if "User-Agent" not in request.headers:
            is_mobile = random.random() < self.mobile_ratio
            request.headers["User-Agent"] = random_ua(is_mobile)


class ProxyMiddleware:
    """
    代理中间件
    从代理池获取代理，注入到请求中
    失败时自动切换代理
    """

    def __init__(self):
        self._pool = None

    @classmethod
    def from_crawler(cls, crawler):
        return cls()

    def _get_pool(self):
        if self._pool is None:
            from proxy.pool.manager import ProxyPool
            self._pool = ProxyPool()
        return self._pool

    def process_request(self, request, spider):
        # 站点配置中禁用代理则跳过
        if not getattr(spider, "use_proxy", True):
            return
        if request.meta.get("dont_proxy"):
            return

        pool = self._get_pool()
        proxy = pool.get_proxy(domain=request.url)
        if proxy:
            request.meta["proxy"] = proxy
            request.meta["_proxy_used"] = proxy

    def process_exception(self, request, exception, spider):
        """请求异常时标记代理失败"""
        proxy = request.meta.get("_proxy_used")
        if proxy:
            self._get_pool().report_failure(proxy)
        return None


class SmartRetryMiddleware(ScrapyRetryMiddleware):
    """
    智能重试中间件
    在 Scrapy 原生重试基础上增加：
    - 重试时自动换 UA
    - 指数退避
    - 特定状态码不重试（404 等）
    """

    NO_RETRY_CODES = {404, 403, 410}

    def _retry(self, request, reason, spider):
        retries = request.meta.get("retry_times", 0) + 1
        if retries > self.max_retry_times:
            logger.warning(f"达到最大重试次数，放弃: {request.url}")
            return None

        # 404 等不重试
        if hasattr(reason, "status") and reason.status in self.NO_RETRY_CODES:
            logger.warning(f"状态码 {reason.status} 不重试: {request.url}")
            return None

        retryreq = request.copy()
        retryreq.meta["retry_times"] = retries
        retryreq.dont_filter = True

        # 换 UA
        retryreq.headers["User-Agent"] = random_ua()

        # 指数退避
        delay = min(2 ** retries, 30)
        retryreq.meta["download_latency"] = delay
        logger.info(f"第 {retries} 次重试 ({reason}): {request.url}，等待 {delay}s")

        return retryreq


class CookieMiddleware:
    """
    Cookie 池中间件
    维护多个 Cookie 会话，按域名轮换
    """

    def __init__(self):
        self._cookies: dict = {}  # domain -> list of cookie dicts

    @classmethod
    def from_crawler(cls, crawler):
        return cls()

    def process_request(self, request, spider):
        domain = request.url.split("/")[2] if "://" in request.url else ""
        cookies = self._cookies.get(domain)
        if cookies:
            cookie = random.choice(cookies)
            request.cookies = cookie

    def process_response(self, request, response, spider):
        # 保存响应中的 Set-Cookie
        set_cookies = response.headers.getlist("Set-Cookie")
        if set_cookies:
            domain = request.url.split("/")[2] if "://" in request.url else ""
            if domain not in self._cookies:
                self._cookies[domain] = []
            # 简单解析 cookie
            for sc in set_cookies:
                cookie_str = sc.decode("utf-8", errors="ignore")
                parts = cookie_str.split(";")[0].split("=", 1)
                if len(parts) == 2:
                    self._cookies[domain].append({parts[0]: parts[1]})
        return response
