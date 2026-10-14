"""
存储基类接口
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


def ensure_unique_key(cur: Any, table: str, columns: List[str]) -> None:
    """
    把表上的唯一约束收敛为指定复合键（幂等，便于老库自动迁移）。

    - 删除仅作用于单个旧列的旧唯一约束（如历史遗留的 UNIQUE(url_hash)）；
    - 缺失时补充目标复合唯一约束（如 UNIQUE(site_name, url_hash)）。

    这样不同站点可以各存同一 URL，而改过唯一键的库无需手工建表即可直接工作。
    """
    target = "UNIQUE (" + ", ".join(columns) + ")"
    normalized_target = target.replace(" ", "")
    legacy_single_keys = {("UNIQUE (" + c + ")").replace(" ", "") for c in columns}
    cur.execute(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = %s::regclass AND contype = 'u'",
        (table,),
    )
    has_target = False
    for conname, definition in cur.fetchall():
        normalized = (definition or "").replace(" ", "")
        if normalized == normalized_target:
            has_target = True
        elif normalized in legacy_single_keys:
            cur.execute(f'ALTER TABLE "{table}" DROP CONSTRAINT "{conname}"')
    if not has_target:
        cur.execute(f'ALTER TABLE "{table}" ADD {target}')


class BaseStorage(ABC):
    """存储基类"""

    @abstractmethod
    def save(self, site_name: str, url: str, data: Dict):
        """保存单条数据"""
        pass

    def save_batch(self, site_name: str, items: List[Dict]):
        """批量保存（默认逐条，子类可覆盖优化）"""
        for item in items:
            self.save(site_name=site_name, url=item.get("url", ""), data=item)

    @abstractmethod
    def query(self, site_name: str, limit: int = 100, offset: int = 0) -> List[Dict]:
        """查询数据"""
        pass

    @abstractmethod
    def count(self, site_name: str) -> int:
        """统计数量"""
        pass
