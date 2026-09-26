"""
代理池管理器
- 纯自建模式：从 Redis 读取代理列表
- 按域名分配代理
- 失败计数，自动剔除失效代理
- 定时健康检查
"""
import random
import time
import threading
from typing import Optional, Dict, List
from dataclasses import dataclass, field

from config.settings import settings
from utils.logger import get_logger

logger = get_logger("proxy")

# Redis 中代理存储的 key
PROXY_POOL_KEY = "spider:proxy:pool"           # 全部代理集合
PROXY_FAIL_KEY = "spider:proxy:fail:{proxy}"    # 单个代理失败计数
PROXY_DOMAIN_KEY = "spider:proxy:domain:{domain}"  # 域名绑定代理


@dataclass
class ProxyInfo:
    """代理信息"""
    url: str  # 如 http://127.0.0.1:7890
    fail_count: int = 0
    last_check: float = 0.0
    last_use: float = 0.0
    speed: float = 0.0  # 响应时间（秒）

    @property
    def is_alive(self) -> bool:
        return self.fail_count < settings.PROXY_MAX_FAIL


class ProxyPool:
    """
    代理池
    单例模式，全局共享
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._redis = None
        self._domain_proxy_cache: Dict[str, List[str]] = {}
        self._start_health_check()

    def _get_redis(self):
        if self._redis is None:
            import redis
            self._redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
        return self._redis

    # ---------- 代理管理 ----------

    def add_proxy(self, proxy_url: str):
        """添加代理到池"""
        r = self._get_redis()
        r.sadd(PROXY_POOL_KEY, proxy_url)
        r.delete(PROXY_FAIL_KEY.format(proxy=proxy_url))
        logger.info(f"添加代理: {proxy_url}")

    def add_proxies(self, proxy_urls: List[str]):
        """批量添加代理"""
        if not proxy_urls:
            return
        r = self._get_redis()
        r.sadd(PROXY_POOL_KEY, *proxy_urls)
        for p in proxy_urls:
            r.delete(PROXY_FAIL_KEY.format(proxy=p))
        logger.info(f"批量添加 {len(proxy_urls)} 个代理")

    def remove_proxy(self, proxy_url: str):
        """移除代理"""
        r = self._get_redis()
        r.srem(PROXY_POOL_KEY, proxy_url)
        r.delete(PROXY_FAIL_KEY.format(proxy=proxy_url))
        logger.warning(f"移除失效代理: {proxy_url}")

    def get_all_proxies(self) -> List[str]:
        """获取所有代理"""
        r = self._get_redis()
        return list(r.smembers(PROXY_POOL_KEY))

    def get_proxy(self, domain: str = "") -> Optional[str]:
        """
        获取一个可用代理
        domain: 按域名绑定代理（同一域名尽量用同一代理，保持会话）
        """
        # 固定代理模式
        if settings.PROXY_FIXED:
            return settings.PROXY_FIXED

        r = self._get_redis()
        all_proxies = list(r.smembers(PROXY_POOL_KEY))
        if not all_proxies:
            return None

        # 过滤掉失效代理
        alive_proxies = []
        for p in all_proxies:
            fail_key = PROXY_FAIL_KEY.format(proxy=p)
            fail_count = int(r.get(fail_key) or 0)
            if fail_count < settings.PROXY_MAX_FAIL:
                alive_proxies.append(p)

        if not alive_proxies:
            logger.warning("代理池中没有可用代理！")
            return None

        # 按域名绑定：优先返回该域名上次使用的代理
        if domain:
            domain_key = PROXY_DOMAIN_KEY.format(domain=domain)
            bound = r.get(domain_key)
            if bound and bound in alive_proxies:
                r.set(domain_key, bound)
                return bound

        # 随机选择
        chosen = random.choice(alive_proxies)

        # 绑定域名
        if domain:
            domain_key = PROXY_DOMAIN_KEY.format(domain=domain)
            r.set(domain_key, chosen)

        return chosen

    def report_failure(self, proxy_url: str):
        """报告代理失败，增加失败计数，超过阈值自动剔除"""
        r = self._get_redis()
        fail_key = PROXY_FAIL_KEY.format(proxy=proxy_url)
        count = r.incr(fail_key)
        if count >= settings.PROXY_MAX_FAIL:
            self.remove_proxy(proxy_url)

    def report_success(self, proxy_url: str, elapsed: float = 0.0):
        """报告代理成功，重置失败计数"""
        r = self._get_redis()
        fail_key = PROXY_FAIL_KEY.format(proxy=proxy_url)
        r.delete(fail_key)

    # ---------- 健康检查 ----------

    def _start_health_check(self):
        """启动后台健康检查线程"""
        def check_loop():
            while True:
                try:
                    self._health_check()
                except Exception as e:
                    logger.error(f"代理健康检查异常: {e}")
                time.sleep(settings.PROXY_POOL_REFRESH_INTERVAL)

        t = threading.Thread(target=check_loop, daemon=True)
        t.start()

    def _health_check(self):
        """检查所有代理的可用性"""
        from proxy.validator.checker import ProxyChecker
        proxies = self.get_all_proxies()
        if not proxies:
            return

        logger.info(f"开始健康检查，共 {len(proxies)} 个代理")
        checker = ProxyChecker()
        results = checker.check_batch(proxies)

        alive = 0
        for proxy, is_alive in results.items():
            if not is_alive:
                self.remove_proxy(proxy)
            else:
                alive += 1
                self.report_success(proxy)

        logger.info(f"健康检查完成: 存活 {alive}/{len(proxies)}")

    def load_from_file(self, filepath: str):
        """从文件加载代理（每行一个代理 URL）"""
        proxies = []
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    proxies.append(line)
        self.add_proxies(proxies)
        return len(proxies)
