"""
请求指纹工具：UA 轮换、TLS 指纹模拟、请求头生成
"""
import random
from typing import Dict

# 常用桌面浏览器 UA
DESKTOP_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

# 移动端 UA
MOBILE_UAS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
]

# curl_cffi 支持的浏览器 impersonate 列表
CURL_CFFI_IMPERSONATES = [
    "chrome120", "chrome123", "chrome124", "chrome126",
    "chrome110", "chrome107", "chrome104",
    "safari17_0", "safari17_2_ios",
    "firefox120", "firefox123",
    "edge101", "edge99",
]


def random_ua(mobile: bool = False) -> str:
    """随机获取一个 User-Agent"""
    pool = MOBILE_UAS if mobile else DESKTOP_UAS
    return random.choice(pool)


def random_impersonate() -> str:
    """随机获取一个 curl_cffi impersonate 目标（TLS 指纹）"""
    return random.choice(CURL_CFFI_IMPERSONATES)


def build_headers(
    url: str = "",
    mobile: bool = False,
    extra: Dict[str, str] = None,
) -> Dict[str, str]:
    """构建浏览器风格请求头"""
    headers = {
        "User-Agent": random_ua(mobile),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }
    if extra:
        headers.update(extra)
    return headers
