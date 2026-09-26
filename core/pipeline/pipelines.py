"""
数据管道基类
所有 Item Pipeline 继承此基类，统一处理原始数据存储和清洗触发
"""
from typing import Any, Dict
from itemadapter import ItemAdapter
from utils.logger import get_logger

logger = get_logger("pipeline")


class BasePipeline:
    """管道基类"""

    def process_item(self, item, spider):
        raise NotImplementedError

    def open_spider(self, spider):
        pass

    def close_spider(self, spider):
        pass


class RawStoragePipeline(BasePipeline):
    """
    原始数据存储管道
    将抓取到的原始数据存入 PostgreSQL JSONB 字段 + JSONL 文件双写
    """

    def __init__(self):
        self._storage = None
        self._file_handles = {}

    @classmethod
    def from_crawler(cls, crawler):
        return cls()

    def _get_storage(self):
        if self._storage is None:
            from storage.raw.postgres_raw import PostgresRawStorage
            self._storage = PostgresRawStorage()
        return self._storage

    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        data = dict(adapter)
        site_name = getattr(spider, "name", "unknown")

        # 1. 先写 JSONL 文件（本地兜底，保证数据不丢；单机无数据库时也能落盘）
        try:
            self._write_jsonl(site_name, data)
        except Exception as e:
            logger.error(f"JSONL 写入失败 [{site_name}]: {e}")

        # 2. 再存 PostgreSQL（数据库不可用时不影响 JSONL，仅记录日志）
        try:
            storage = self._get_storage()
            storage.save(site_name=site_name, url=data.get("url", ""), data=data)
        except Exception as e:
            logger.warning(f"PostgreSQL 存储跳过 [{site_name}]（数据库未启动?）: {e}")

        return item

    def _write_jsonl(self, site_name: str, data: Dict):
        import json
        from pathlib import Path
        from config.settings import settings

        raw_dir = Path(settings.RAW_DATA_DIR) / site_name
        raw_dir.mkdir(parents=True, exist_ok=True)

        if site_name not in self._file_handles:
            from datetime import datetime
            date_str = datetime.now().strftime("%Y%m%d")
            f = open(raw_dir / f"{site_name}_{date_str}.jsonl", "a", encoding="utf-8")
            self._file_handles[site_name] = f

        self._file_handles[site_name].write(json.dumps(data, ensure_ascii=False) + "\n")
        self._file_handles[site_name].flush()

    def close_spider(self, spider):
        for f in self._file_handles.values():
            f.close()
        self._file_handles.clear()


class CleanPipeline(BasePipeline):
    """
    清洗管道
    触发清洗流水线，将清洗后的数据存入结构化表
    """

    def __init__(self):
        self._cleaner = None

    @classmethod
    def from_crawler(cls, crawler):
        return cls()

    def _get_cleaner(self):
        if self._cleaner is None:
            from cleaner.pipeline import CleanPipeline as Cleaner
            self._cleaner = Cleaner()
        return self._cleaner

    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        data = dict(adapter)
        site_name = getattr(spider, "name", "unknown")

        try:
            cleaner = self._get_cleaner()
            cleaned = cleaner.clean(data, site_name=site_name)
            if cleaned:
                from storage.cleaned.postgres_cleaned import PostgresCleanedStorage
                storage = PostgresCleanedStorage()
                storage.save(site_name=site_name, data=cleaned)
        except Exception as e:
            logger.error(f"清洗管道失败 [{site_name}]: {e}")

        return item
