"""
存储基类接口
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional


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
