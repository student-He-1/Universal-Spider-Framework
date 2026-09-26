"""
清洗后数据存储：PostgreSQL 结构化表
根据站点动态建表，字段由清洗后的数据自动推断
也支持预定义表结构
"""
import re
from typing import Dict, List, Optional, Set
import psycopg2
from psycopg2.extras import Json, execute_values

from config.settings import settings
from storage.base import BaseStorage
from utils.helpers import url_fingerprint
from utils.logger import get_logger

logger = get_logger("cleaned_storage")


def _sanitize_table_name(name: str) -> str:
    """清理表名，只保留字母数字下划线"""
    return re.sub(r"[^a-zA-Z0-9_]", "_", name).lower()


def _sanitize_column(name: str) -> str:
    """清理列名"""
    return re.sub(r"[^a-zA-Z0-9_]", "_", name).lower()


def _infer_sql_type(value) -> str:
    """根据 Python 值推断 SQL 类型"""
    if isinstance(value, bool):
        return "BOOLEAN"
    elif isinstance(value, int):
        return "BIGINT"
    elif isinstance(value, float):
        return "DOUBLE PRECISION"
    elif isinstance(value, (dict, list)):
        return "JSONB"
    else:
        return "TEXT"


class PostgresCleanedStorage(BaseStorage):
    """PostgreSQL 清洗后数据存储（动态表）"""

    _instance = None
    _existing_tables: Set[str] = set()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._conn = None
        return cls._instance

    def _get_conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(settings.POSTGRES_DSN)
            self._conn.autocommit = True
        return self._conn

    def _table_name(self, site_name: str) -> str:
        return f"cleaned_{_sanitize_table_name(site_name)}"

    def _ensure_table(self, table: str, sample: Dict):
        """根据样本数据动态建表"""
        if table in self._existing_tables:
            return

        # 基础字段
        columns = [
            "id SERIAL PRIMARY KEY",
            "url TEXT",
            "url_hash VARCHAR(64) UNIQUE",
            "site_name VARCHAR(128)",
            "cleaned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        ]

        # 动态字段
        for key, value in sample.items():
            col = _sanitize_column(key)
            if col in ("id", "url", "url_hash", "site_name", "cleaned_at"):
                continue
            sql_type = _infer_sql_type(value)
            columns.append(f'"{col}" {sql_type}')

        sql = f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(columns)})"
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(sql)
            self._existing_tables.add(table)
            logger.info(f"清洗数据表已创建/确认: {table}")
        except Exception as e:
            logger.error(f"清洗数据表创建失败 [{table}]: {e}")

    def _ensure_columns(self, table: str, data: Dict):
        """确保表中有所有需要的列（自动 ALTER TABLE）"""
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                    (table,),
                )
                existing = {row[0] for row in cur.fetchall()}

            for key, value in data.items():
                col = _sanitize_column(key)
                if col not in existing and col not in ("id", "url", "url_hash", "site_name", "cleaned_at"):
                    sql_type = _infer_sql_type(value)
                    with conn.cursor() as cur:
                        cur.execute(f'ALTER TABLE {table} ADD COLUMN "{col}" {sql_type}')
                    existing.add(col)
                    logger.info(f"表 {table} 新增列: {col} ({sql_type})")
        except Exception as e:
            logger.error(f"列检查失败 [{table}]: {e}")

    def save(self, site_name: str, url: str = "", data: Dict = None):
        """保存清洗后的数据"""
        if data is None:
            return
        table = self._table_name(site_name)

        # 首次保存时建表
        if table not in self._existing_tables:
            self._ensure_table(table, data)

        # 确保列存在
        self._ensure_columns(table, data)

        # 组装数据
        url = data.get("url", url)
        url_hash = url_fingerprint(url) if url else ""

        # 过滤掉非字典值的列
        clean_data = {}
        for k, v in data.items():
            col = _sanitize_column(k)
            if col in ("id", "cleaned_at"):
                continue
            if isinstance(v, (dict, list)):
                clean_data[col] = Json(v)
            else:
                clean_data[col] = v

        columns = ["url", "url_hash", "site_name"] + list(clean_data.keys())
        values = [url, url_hash, site_name] + list(clean_data.values())
        placeholders = ", ".join(["%s"] * len(columns))
        col_str = ", ".join(f'"{c}"' for c in columns)

        # UPSERT
        update_cols = [c for c in columns if c not in ("url_hash",)]
        update_str = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in update_cols)

        sql = f"""
        INSERT INTO {table} ({col_str}) VALUES ({placeholders})
        ON CONFLICT (url_hash) DO UPDATE SET {update_str}
        """
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(sql, values)
        except Exception as e:
            logger.error(f"清洗数据保存失败 [{site_name}]: {e}")

    def save_batch(self, site_name: str, items: List[Dict]):
        """批量保存清洗后数据"""
        if not items:
            return
        table = self._table_name(site_name)

        # 用第一条建表
        if table not in self._existing_tables:
            self._ensure_table(table, items[0])

        # 收集所有列
        all_keys = set()
        for item in items:
            all_keys.update(item.keys())

        # 确保列存在
        self._ensure_columns(table, {k: "" for k in all_keys})

        rows = []
        for item in items:
            url = item.get("url", "")
            url_hash = url_fingerprint(url) if url else ""
            row = {"url": url, "url_hash": url_hash, "site_name": site_name}
            for k, v in item.items():
                col = _sanitize_column(k)
                if col in ("id", "cleaned_at", "url", "url_hash", "site_name"):
                    continue
                if isinstance(v, (dict, list)):
                    row[col] = Json(v)
                else:
                    row[col] = v
            rows.append(row)

        if not rows:
            return

        columns = list(rows[0].keys())
        col_str = ", ".join(f'"{c}"' for c in columns)
        update_cols = [c for c in columns if c != "url_hash"]
        update_str = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in update_cols)

        sql = f"""
        INSERT INTO {table} ({col_str}) VALUES %s
        ON CONFLICT (url_hash) DO UPDATE SET {update_str}
        """
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                execute_values(cur, sql, [tuple(r.get(c) for c in columns) for r in rows])
        except Exception as e:
            logger.error(f"清洗数据批量保存失败 [{site_name}]: {e}")

    def query(self, site_name: str, limit: int = 100, offset: int = 0) -> List[Dict]:
        table = self._table_name(site_name)
        sql = f"SELECT * FROM {table} ORDER BY id DESC LIMIT %s OFFSET %s"
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(sql, (limit, offset))
                columns = [desc[0] for desc in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"清洗数据查询失败: {e}")
            return []

    def count(self, site_name: str) -> int:
        table = self._table_name(site_name)
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                return cur.fetchone()[0]
        except Exception:
            return 0
