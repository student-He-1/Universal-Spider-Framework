"""
第三方代理 API 适配器基类
预留接口，后续接入快代理、芝麻代理、青苹代理等
纯自建模式下不使用
"""
from abc import ABC, abstractmethod
from typing import List


class BaseProxyProvider(ABC):
    """第三方代理提供商基类"""

    @abstractmethod
    def fetch(self, count: int = 1) -> List[str]:
        """获取代理列表，返回代理 URL 列表"""
        pass

    @abstractmethod
    def get_remaining(self) -> int:
        """获取剩余可用代理数量/额度"""
        pass


class KuaiDailiProvider(BaseProxyProvider):
    """快代理适配器（示例，需填入 API 地址）"""

    def __init__(self, api_url: str = "", secret_id: str = "", secret_key: str = ""):
        self.api_url = api_url
        self.secret_id = secret_id
        self.secret_key = secret_key

    def fetch(self, count: int = 1) -> List[str]:
        # TODO: 实现快代理 API 调用
        return []

    def get_remaining(self) -> int:
        return 0


class ZhimaDailiProvider(BaseProxyProvider):
    """芝麻代理适配器（示例）"""

    def __init__(self, api_url: str = ""):
        self.api_url = api_url

    def fetch(self, count: int = 1) -> List[str]:
        # TODO: 实现芝麻代理 API 调用
        return []

    def get_remaining(self) -> int:
        return 0


def get_provider(name: str, **kwargs) -> BaseProxyProvider:
    """工厂方法获取代理提供商"""
    providers = {
        "kuaidaili": KuaiDailiProvider,
        "zhima": ZhimaDailiProvider,
    }
    if name not in providers:
        raise ValueError(f"不支持的代理提供商: {name}")
    return providers[name](**kwargs)
