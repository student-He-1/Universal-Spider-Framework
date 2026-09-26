"""
URL 调度器
基于 Redis 队列，支持 FIFO / LIFO / 优先级调度
与 Bloom Filter 配合实现去重 + 调度
"""
import json
import time
from typing import Optional, List, Dict, Any
from enum import Enum
from dataclasses import dataclass, asdict

from dedup.redis_client import get_redis
from dedup.bloom_filter import BloomFilter
from utils.helpers import url_fingerprint
from utils.logger import get_logger

logger = get_logger("scheduler")


class QueueType(Enum):
    """队列类型"""
    FIFO = "fifo"        # 先进先出（广度优先）
    LIFO = "lifo"        # 后进先出（深度优先）
    PRIORITY = "priority"  # 优先级队列


@dataclass
class Task:
    """调度任务"""
    url: str
    site: str = ""
    priority: int = 0       # 优先级，数字越大越优先
    depth: int = 0          # 爬取深度
    meta: Dict[str, Any] = None  # 附加元数据
    created_at: float = 0.0

    def __post_init__(self):
        if self.meta is None:
            self.meta = {}
        if self.created_at == 0:
            self.created_at = time.time()

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "Task":
        data = json.loads(s)
        return cls(**data)


class Scheduler:
    """
    URL 调度器
    结合 Bloom Filter 去重 + Redis 队列调度
    """

    QUEUE_PREFIX = "spider:queue:"
    PROCESSING_PREFIX = "spider:processing:"

    def __init__(
        self,
        site: str = "default",
        queue_type: QueueType = QueueType.FIFO,
        use_bloom: bool = True,
        expected_items: int = 10_000_000,
    ):
        self.site = site
        self.queue_type = queue_type
        self.redis = get_redis()
        self.queue_key = f"{self.QUEUE_PREFIX}{site}"
        self.processing_key = f"{self.PROCESSING_PREFIX}{site}"

        if use_bloom:
            self.bloom = BloomFilter(
                name=f"url_{site}",
                expected_items=expected_items,
            )
        else:
            self.bloom = None

    def add(self, task: Task) -> bool:
        """
        添加任务到队列
        返回 True 表示成功加入（新 URL），False 表示重复被过滤
        """
        # 去重检查
        if self.bloom and not self.bloom.add_url(task.url):
            return False

        # 入队
        if self.queue_type == QueueType.PRIORITY:
            # 优先级队列：用 sorted set，score = priority
            self.redis.zadd(self.queue_key, {task.to_json(): task.priority})
        else:
            # FIFO/LIFO：用 list
            self.redis.rpush(self.queue_key, task.to_json())

        return True

    def add_url(self, url: str, **kwargs) -> bool:
        """便捷添加 URL"""
        task = Task(url=url, site=self.site, **kwargs)
        return self.add(task)

    def add_batch(self, tasks: List[Task]) -> int:
        """批量添加，返回成功加入的数量"""
        count = 0
        for task in tasks:
            if self.add(task):
                count += 1
        return count

    def pop(self) -> Optional[Task]:
        """
        从队列取出一个任务
        FIFO: 左端弹出
        LIFO: 右端弹出
        PRIORITY: 最高优先级弹出
        """
        if self.queue_type == QueueType.FIFO:
            raw = self.redis.lpop(self.queue_key)
        elif self.queue_type == QueueType.LIFO:
            raw = self.redis.rpop(self.queue_key)
        else:  # PRIORITY
            # 取出优先级最高的（zrevrange 取第一个）
            items = self.redis.zrevrange(self.queue_key, 0, 0)
            if not items:
                return None
            raw = items[0]
            self.redis.zrem(self.queue_key, raw)

        if not raw:
            return None

        try:
            task = Task.from_json(raw)
            # 加入处理中集合（用于崩溃恢复）
            self.redis.hset(self.processing_key, url_fingerprint(task.url), raw)
            return task
        except Exception as e:
            logger.error(f"任务解析失败: {e}")
            return None

    def complete(self, task: Task):
        """标记任务完成（从处理中移除）"""
        self.redis.hdel(self.processing_key, url_fingerprint(task.url))

    def fail(self, task: Task, requeue: bool = True):
        """标记任务失败，可选重新入队"""
        self.redis.hdel(self.processing_key, url_fingerprint(task.url))
        if requeue:
            task.priority = max(0, task.priority - 1)  # 降低优先级
            self.redis.rpush(self.queue_key, task.to_json())

    def size(self) -> int:
        """队列长度"""
        if self.queue_type == QueueType.PRIORITY:
            return self.redis.zcard(self.queue_key)
        return self.redis.llen(self.queue_key)

    def processing_count(self) -> int:
        """处理中的任务数"""
        return self.redis.hlen(self.processing_key)

    def recover_stuck(self, timeout: int = 300) -> int:
        """
        恢复卡住的任务（处理超时未完成的，重新入队）
        返回恢复的任务数
        """
        now = time.time()
        recovered = 0
        all_processing = self.redis.hgetall(self.processing_key)
        for fp, raw in all_processing.items():
            try:
                task = Task.from_json(raw)
                if now - task.created_at > timeout:
                    self.redis.hdel(self.processing_key, fp)
                    self.redis.rpush(self.queue_key, raw)
                    recovered += 1
            except Exception:
                self.redis.hdel(self.processing_key, fp)
        return recovered

    def clear(self):
        """清空队列和去重"""
        self.redis.delete(self.queue_key, self.processing_key)
        if self.bloom:
            self.bloom.clear()
