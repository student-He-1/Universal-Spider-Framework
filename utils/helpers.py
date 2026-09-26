"""
通用工具函数
"""
import hashlib
import re
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode


def url_fingerprint(url: str) -> str:
    """生成 URL 的 MD5 指纹，用于去重"""
    # 规范化 URL：去掉 fragment，排序 query 参数
    parsed = urlparse(url)
    query = sorted(parse_qsl(parsed.query))
    normalized = urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path,
        parsed.params,
        urlencode(query),
        "",  # 去掉 fragment
    ))
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def clean_text(text: Optional[str]) -> str:
    """清理文本：去除多余空白、HTML 实体"""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    text = text.replace("\u3000", " ").strip()
    return text


def extract_numbers(text: str) -> list:
    """从文本中提取所有数字"""
    return re.findall(r"\d+\.?\d*", text or "")


def safe_get(data: Dict, *keys, default=None) -> Any:
    """安全地从嵌套字典中取值"""
    for key in keys:
        if isinstance(data, dict):
            data = data.get(key, default)
        else:
            return default
    return data


def retry(times: int = 3, delay: float = 1.0):
    """简单重试装饰器"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exc = None
            for i in range(times):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    if i < times - 1:
                        time.sleep(delay * (i + 1))
            raise last_exc
        return wrapper
    return decorator


def chunk_list(lst: list, size: int) -> list:
    """将列表分块"""
    return [lst[i:i + size] for i in range(0, len(lst), size)]
