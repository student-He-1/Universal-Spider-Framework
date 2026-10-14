"""
通用工具函数
"""
import hashlib
import re
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode


# SQL 标识符长度上限（PG 硬上限 63 字节，留出前缀与后缀余量）
_IDENT_MAX_LEN = 48
# 视为分隔符的字符（转写为下划线不算“信息有损”）
_IDENT_SEPARATOR_RE = re.compile(r"[\s\-.]+")
# 非 ASCII / 非法标识符字符
_NON_IDENT_RE = re.compile(r"[^a-zA-Z0-9_]")


def short_hash(text: str, length: int = 6) -> str:
    """取文本的短哈希（默认 6 位十六进制），用于标识符消歧"""
    return hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:length]


def sanitize_identifier(name: str, fallback: str = "col") -> str:
    """
    把任意名字转成合法且确定的 SQL 标识符。

    非 ASCII / 非法字符会被替换为下划线；一旦发生替换（信息有损，会出现
    "书名"、"价格" 都变成 "__" 这类撞名），就追加原始名字的短哈希来消歧。
    这样不同名字必得到不同标识符，且同一名字每次结果一致。
    """
    raw = str(name or "").strip()
    # 分隔符归一化后的参照值：只把空格/连字符/点视为分隔符，不追加哈希
    normalized = _IDENT_SEPARATOR_RE.sub("_", raw.lower())
    base = _NON_IDENT_RE.sub("_", raw).lower()
    base = re.sub(r"_+", "_", base).strip("_")
    if base != normalized:
        suffix = short_hash(raw)
        base = f"{base}_{suffix}" if base else f"{fallback}_{suffix}"
    return base[:_IDENT_MAX_LEN]


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
