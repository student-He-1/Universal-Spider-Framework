"""
Redis 连接封装
单例模式，全局共享连接池
"""
import redis
from typing import Optional
from config.settings import settings


class RedisClient:
    """Redis 客户端单例"""

    _instance: Optional["RedisClient"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._client = None
        return cls._instance

    @property
    def client(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                max_connections=50,
                socket_timeout=5,
                socket_connect_timeout=5,
            )
        return self._client

    def __getattr__(self, name):
        """代理 Redis 方法"""
        return getattr(self.client, name)


def get_redis() -> redis.Redis:
    """便捷获取 Redis 连接"""
    return RedisClient().client
