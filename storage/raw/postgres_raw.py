"""
原始数据存储：PostgreSQL JSONB
保留完整原始响应，便于回溯和重新清洗
表结构：
  id (SERIAL PK)
  site_name (VARCHAR, indexed)
  url (VARCHAR, indexed)
  url_hash (VARCHAR, unique)
  raw_data (JSONB)
  status (VARCHAR)  -- crawled / cleaning / cleaned / failed
  created_at (TIMESTAMP)
  updated_at (TIMESTAMP)
"""
import json
import time
from typing import Dict, List, Optional
import psycopg2
from psycopg2.extras import Json, execute_values

from config.settings import settings
from storage.base import BaseStorage
from utils.helpers import url_fingerprint
from utils.logger import get_logger

logger = get_logger("raw_storage")

# 建表 SQL
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS raw_data (
    id SERIAL PRIMARY KEY,
    site_name VARCHAR(128) NOT NULL,
    url TEXT NOT NULL,
    url_hash VARCHAR(64) NOT NULL,
    raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(32) NOT NULL DEFAULT 'crawled',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(url_hash)
);
CREATE INDEX IF NOT EXISTS idx_raw_site ON raw_data(site_name);
CREATE INDEX IF NOT EXISTS idx_raw_status ON raw_data(status);
CREATE INDEX IF NOT EXISTS idx_raw_created ON raw_data(created_at);
"""


class PostgresRawStorage(BaseStorage):
    """PostgreSQL 原始数据存储"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._conn = None
        return cls._instance

    def __init__(self):
        self._ensure_table()

    def _get_conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(settings.POSTGRES_DSN)
            self._conn.autocommit = True
        return self._conn

    def _ensure_table(self):
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(CREATE_TABLE_SQL)
        except Exception as e:
            logger.error(f"原始数据表初始化失败: {e}")

    def save(self, site_name: str, url: str, data: Dict):
        """保存单条原始数据（冲突时更新）"""
        url_hash = url_fingerprint(url)
        sql = """
        INSERT INTO raw_data (site_name, url, url_hash, raw_data, status)
        VALUES (%s, %s, %s, %s, 'crawled')
        ON CONFLICT (url_hash) DO UPDATE
        SET raw_data = EXCLUDED.raw_data,
            status = 'crawled',
            updated_at = CURRENT_TIMESTAMP
        """
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(sql, (site_name, url, url_hash, Json(data)))
        except Exception as e:
            logger.error(f"原始数据保存失败 [{site_name}] {url}: {e}")

    def save_batch(self, site_name: str, items: List[Dict]):
        """批量保存"""
        if not items:
            return
        rows = []
        for item in items:
            url = item.get("url", "")
            rows.append((
                site_name,
                url,
                url_fingerprint(url),
                Json(item),
                "crawled",
            ))
        sql = """
        INSERT INTO raw_data (site_name, url, url_hash, raw_data, status)
        VALUES %s
        ON CONFLICT (url_hash) DO UPDATE
        SET raw_data = EXCLUDED.raw_data,
            status = 'crawled',
            updated_at = CURRENT_TIMESTAMP
        """
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                execute_values(cur, sql, rows)
        except Exception as e:
            logger.error(f"原始数据批量保存失败 [{site_name}]: {e}")

    def query(
        self,
        site_name: str,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
    ) -> List[Dict]:
        """查询原始数据"""
        sql = "SELECT id, site_name, url, raw_data, status, created_at FROM raw_data WHERE site_name = %s"
        params = [site_name]
        if status:
            sql += " AND status = %s"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(sql, params)
                columns = [desc[0] for desc in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"原始数据查询失败: {e}")
            return []

    def count(self, site_name: str, status: Optional[str] = None) -> int:
        sql = "SELECT COUNT(*) FROM raw_data WHERE site_name = %s"
        params = [site_name]
        if status:
            sql += " AND status = %s"
            params.append(status)
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()[0]
        except Exception as e:
            logger.error(f"原始数据统计失败: {e}")
            return 0

    def update_status(self, url_hash: str, status: str):
        """更新数据状态"""
        sql = "UPDATE raw_data SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE url_hash = %s"
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(sql, (status, url_hash))
        except Exception as e:
            logger.error(f"状态更新失败: {e}")

    def get_pending_clean(self, site_name: str, limit: int = 1000) -> List[Dict]:
        """获取待清洗的数据"""
        return self.query(site_name, limit=limit, status="crawled")
