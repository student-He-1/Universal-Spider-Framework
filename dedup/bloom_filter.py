"""
Bloom Filter 去重
基于 Redis Bitmap 实现，支持海量 URL 去重，内存可控
"""
import hashlib
import math
from typing import List
from dedup.redis_client import get_redis
from utils.helpers import url_fingerprint
from utils.logger import get_logger

logger = get_logger("bloom_filter")


class BloomFilter:
    """
    Redis Bitmap Bloom Filter
    多个哈希函数映射到 Redis Bitmap 的不同位
    """

    # 默认 Redis key 前缀
    KEY_PREFIX = "spider:bloom:"

    def __init__(
        self,
        name: str = "default",
        expected_items: int = 10_000_000,
        false_positive_rate: float = 0.001,
    ):
        """
        name: 过滤器名称（Redis key 后缀）
        expected_items: 预期元素数量，用于计算 bitmap 大小
        false_positive_rate: 可接受的误判率
        """
        self.redis = get_redis()
        self.key = f"{self.KEY_PREFIX}{name}"

        # 计算最优 bitmap 大小和哈希函数数量
        m = - (expected_items * math.log(false_positive_rate)) / (math.log(2) ** 2)
        k = (m / expected_items) * math.log(2)
        self.bit_size = int(m)
        self.hash_count = int(k)
        logger.info(
            f"BloomFilter 初始化: key={self.key}, "
            f"bitmap_size={self.bit_size / 8 / 1024 / 1024:.1f}MB, "
            f"hash_count={self.hash_count}"
        )

    def _hashes(self, item: str) -> List[int]:
        """生成多个哈希值，映射到 bitmap 位置"""
        positions = []
        # 使用双重哈希法生成 k 个哈希
        h1 = int(hashlib.md5(item.encode()).hexdigest(), 16)
        h2 = int(hashlib.sha1(item.encode()).hexdigest(), 16)
        for i in range(self.hash_count):
            pos = (h1 + i * h2) % self.bit_size
            positions.append(pos)
        return positions

    def add(self, item: str) -> bool:
        """
        添加元素
        返回 True 表示是新元素（之前不存在），False 表示已存在
        """
        positions = self._hashes(item)
        # 检查是否所有位都已置 1
        pipe = self.redis.pipeline()
        for pos in positions:
            pipe.getbit(self.key, pos)
        bits = pipe.execute()

        if all(bits):
            return False  # 已存在

        # 置位
        pipe = self.redis.pipeline()
        for pos in positions:
            pipe.setbit(self.key, pos, 1)
        pipe.execute()
        return True

    def contains(self, item: str) -> bool:
        """检查元素是否存在"""
        positions = self._hashes(item)
        pipe = self.redis.pipeline()
        for pos in positions:
            pipe.getbit(self.key, pos)
        bits = pipe.execute()
        return all(bits)

    def add_url(self, url: str) -> bool:
        """添加 URL（自动计算指纹）"""
        return self.add(url_fingerprint(url))

    def contains_url(self, url: str) -> bool:
        """检查 URL 是否存在"""
        return self.contains(url_fingerprint(url))

    def clear(self):
        """清空过滤器"""
        self.redis.delete(self.key)

    def count(self) -> int:
        """估算元素数量（基于 bitmap 中 1 的比例）"""
        total_bits = self.redis.bitcount(self.key)
        if total_bits == 0:
            return 0
        # 反推元素数量
        ratio = total_bits / self.bit_size
        if ratio >= 1:
            return float("inf")
        estimated = - (self.bit_size / self.hash_count) * math.log(1 - ratio)
        return int(estimated)
