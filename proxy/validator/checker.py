"""
代理校验器
检测代理是否可用、匿名度、响应速度
"""
import time
from typing import Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from utils.logger import get_logger

logger = get_logger("proxy_checker")

# 用于检测的目标 URL（返回访问者 IP）
CHECK_URLS = [
    "http://httpbin.org/ip",
    "https://api.ipify.org?format=json",
]

# 超时时间
CHECK_TIMEOUT = 10


class ProxyChecker:
    """代理校验器"""

    def __init__(self, check_url: str = None, timeout: int = CHECK_TIMEOUT):
        self.check_url = check_url or CHECK_URLS[0]
        self.timeout = timeout

    def check(self, proxy_url: str) -> Tuple[bool, float]:
        """
        校验单个代理
        返回 (是否可用, 响应时间秒)
        """
        import requests
        start = time.time()
        try:
            proxies = {"http": proxy_url, "https": proxy_url}
            resp = requests.get(
                self.check_url,
                proxies=proxies,
                timeout=self.timeout,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            elapsed = time.time() - start
            if resp.status_code == 200:
                return True, elapsed
            return False, elapsed
        except Exception as e:
            elapsed = time.time() - start
            return False, elapsed

    def check_batch(
        self,
        proxy_urls: List[str],
        max_workers: int = 20,
    ) -> Dict[str, bool]:
        """
        批量校验代理
        返回 {proxy_url: 是否可用}
        """
        results = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_proxy = {
                executor.submit(self.check, p): p for p in proxy_urls
            }
            for future in as_completed(future_to_proxy):
                proxy = future_to_proxy[future]
                try:
                    is_alive, elapsed = future.result()
                    results[proxy] = is_alive
                except Exception:
                    results[proxy] = False
        return results

    def check_anonymity(self, proxy_url: str) -> str:
        """
        检测代理匿名度
        返回: transparent / anonymous / elite
        - transparent: 透传真实 IP
        - anonymous: 隐藏真实 IP 但声明自己是代理
        - elite: 高匿，完全隐藏
        """
        import requests
        try:
            proxies = {"http": proxy_url, "https": proxy_url}
            resp = requests.get(
                "http://httpbin.org/headers",
                proxies=proxies,
                timeout=self.timeout,
            )
            headers = resp.json().get("headers", {})

            # 检查是否有代理相关头
            proxy_headers = ["Via", "X-Forwarded-For", "X-Proxy-ID", "Forwarded"]
            has_proxy_header = any(h in headers for h in proxy_headers)

            if has_proxy_header:
                return "anonymous"
            return "elite"
        except Exception:
            return "unknown"
