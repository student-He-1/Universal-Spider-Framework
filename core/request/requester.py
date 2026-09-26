"""
请求层封装
统一接口，底层支持 curl_cffi（TLS 指纹模拟）、httpx、aiohttp
"""
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from utils.fingerprint import random_impersonate, build_headers


@dataclass
class RequestResult:
    """请求结果统一封装"""
    url: str
    status_code: int
    text: str = ""
    content: bytes = b""
    headers: Dict[str, str] = field(default_factory=dict)
    cookies: Dict[str, str] = field(default_factory=dict)
    elapsed: float = 0.0
    error: Optional[str] = None
    proxy_used: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status_code < 400

    def json(self) -> Any:
        import json
        return json.loads(self.text)


class BaseRequester:
    """请求器基类"""

    def get(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict] = None,
        proxy: Optional[str] = None,
        timeout: int = 30,
        **kwargs,
    ) -> RequestResult:
        raise NotImplementedError

    def post(
        self,
        url: str,
        data: Any = None,
        json: Any = None,
        headers: Optional[Dict[str, str]] = None,
        proxy: Optional[str] = None,
        timeout: int = 30,
        **kwargs,
    ) -> RequestResult:
        raise NotImplementedError


class CurlCffiRequester(BaseRequester):
    """
    curl_cffi 请求器：模拟浏览器 TLS 指纹，绕过 JA3 检测
    这是主力请求器
    """

    def __init__(self, impersonate: Optional[str] = None):
        self.impersonate = impersonate or random_impersonate()
        self.headers = {}  # 自定义请求头，会与默认 headers 合并

    def _merge_headers(self, url, headers=None):
        """合并默认 headers + 自定义 headers + 调用时传入的 headers"""
        merged = build_headers(url)
        if self.headers:
            merged.update(self.headers)
        if headers:
            merged.update(headers)
        return merged

    def get(self, url, headers=None, params=None, proxy=None, timeout=30, **kwargs):
        from curl_cffi import requests as cffi_requests
        import time
        start = time.time()
        merged_headers = self._merge_headers(url, headers)
        try:
            resp = cffi_requests.get(
                url,
                headers=merged_headers,
                params=params,
                proxy=proxy,
                timeout=timeout,
                impersonate=self.impersonate,
                **kwargs,
            )
            return RequestResult(
                url=url,
                status_code=resp.status_code,
                text=resp.text,
                content=resp.content,
                headers=dict(resp.headers),
                cookies=dict(resp.cookies),
                elapsed=time.time() - start,
                proxy_used=proxy,
            )
        except Exception as e:
            return RequestResult(
                url=url, status_code=0, elapsed=time.time() - start,
                error=str(e), proxy_used=proxy,
            )

    def post(self, url, data=None, json=None, headers=None, proxy=None, timeout=30, **kwargs):
        from curl_cffi import requests as cffi_requests
        import time
        start = time.time()
        merged_headers = self._merge_headers(url, headers)
        try:
            resp = cffi_requests.post(
                url,
                data=data,
                json=json,
                headers=merged_headers,
                proxy=proxy,
                timeout=timeout,
                impersonate=self.impersonate,
                **kwargs,
            )
            return RequestResult(
                url=url,
                status_code=resp.status_code,
                text=resp.text,
                content=resp.content,
                headers=dict(resp.headers),
                cookies=dict(resp.cookies),
                elapsed=time.time() - start,
                proxy_used=proxy,
            )
        except Exception as e:
            return RequestResult(
                url=url, status_code=0, elapsed=time.time() - start,
                error=str(e), proxy_used=proxy,
            )


class HttpxRequester(BaseRequester):
    """httpx 请求器：普通请求兜底"""

    def get(self, url, headers=None, params=None, proxy=None, timeout=30, **kwargs):
        import httpx
        import time
        start = time.time()
        try:
            proxies = {"http://": proxy, "https://": proxy} if proxy else None
            with httpx.Client(proxies=proxies, timeout=timeout, follow_redirects=True) as client:
                resp = client.get(url, headers=headers or build_headers(url), params=params, **kwargs)
            return RequestResult(
                url=url, status_code=resp.status_code, text=resp.text,
                content=resp.content, headers=dict(resp.headers),
                cookies=dict(resp.cookies), elapsed=time.time() - start,
                proxy_used=proxy,
            )
        except Exception as e:
            return RequestResult(url=url, status_code=0, elapsed=time.time() - start, error=str(e), proxy_used=proxy)

    def post(self, url, data=None, json=None, headers=None, proxy=None, timeout=30, **kwargs):
        import httpx
        import time
        start = time.time()
        try:
            proxies = {"http://": proxy, "https://": proxy} if proxy else None
            with httpx.Client(proxies=proxies, timeout=timeout, follow_redirects=True) as client:
                resp = client.post(url, data=data, json=json, headers=headers or build_headers(url), **kwargs)
            return RequestResult(
                url=url, status_code=resp.status_code, text=resp.text,
                content=resp.content, headers=dict(resp.headers),
                cookies=dict(resp.cookies), elapsed=time.time() - start,
                proxy_used=proxy,
            )
        except Exception as e:
            return RequestResult(url=url, status_code=0, elapsed=time.time() - start, error=str(e), proxy_used=proxy)


def get_requester(backend: str = "curl_cffi", **kwargs) -> BaseRequester:
    """
    工厂方法：获取请求器
    backend: curl_cffi / httpx
    """
    if backend == "curl_cffi":
        return CurlCffiRequester(**kwargs)
    elif backend == "httpx":
        return HttpxRequester(**kwargs)
    else:
        raise ValueError(f"不支持的请求后端: {backend}")
