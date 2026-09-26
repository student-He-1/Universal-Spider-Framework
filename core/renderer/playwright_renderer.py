"""
动态渲染层：Playwright 封装
处理 JS 渲染页面，支持反检测 stealth
"""
import asyncio
from typing import Optional, Dict, List
from dataclasses import dataclass


@dataclass
class RenderResult:
    """渲染结果"""
    url: str
    html: str
    title: str = ""
    cookies: List[Dict] = None
    screenshot: Optional[bytes] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


class PlaywrightRenderer:
    """
    Playwright 渲染器
    使用 playwright-stealth 绕过反爬检测
    """

    def __init__(
        self,
        headless: bool = True,
        proxy: Optional[str] = None,
        user_agent: Optional[str] = None,
        viewport: Dict = None,
    ):
        self.headless = headless
        self.proxy = proxy
        self.user_agent = user_agent
        self.viewport = viewport or {"width": 1920, "height": 1080}
        self._browser = None
        self._context = None

    async def _ensure_browser(self):
        """懒加载浏览器实例"""
        if self._browser is None:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            browser_kwargs = {"headless": self.headless}
            if self.proxy:
                browser_kwargs["proxy"] = {"server": self.proxy}
            self._browser = await self._pw.chromium.launch(**browser_kwargs)

            context_kwargs = {
                "viewport": self.viewport,
                "locale": "zh-CN",
            }
            if self.user_agent:
                context_kwargs["user_agent"] = self.user_agent
            self._context = await self._browser.new_context(**context_kwargs)

            # 注入 stealth 脚本
            try:
                from playwright_stealth import stealth_async
                page = await self._context.new_page()
                await stealth_async(page)
                await page.close()
            except ImportError:
                pass

    async def render(
        self,
        url: str,
        wait_ms: int = 2000,
        scroll: bool = False,
        wait_selector: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> RenderResult:
        """
        渲染页面并返回 HTML
        wait_ms: 固定等待时间
        scroll: 是否滚动到底部（触发懒加载）
        wait_selector: 等待特定元素出现
        """
        await self._ensure_browser()
        page = await self._context.new_page()

        try:
            if extra_headers:
                await page.set_extra_http_headers(extra_headers)

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # 等待策略
            if wait_selector:
                await page.wait_for_selector(wait_selector, timeout=15000)
            if wait_ms:
                await page.wait_for_timeout(wait_ms)
            if scroll:
                await self._auto_scroll(page)

            html = await page.content()
            title = await page.title()
            cookies = await self._context.cookies()

            return RenderResult(url=url, html=html, title=title, cookies=cookies)
        except Exception as e:
            return RenderResult(url=url, html="", error=str(e))
        finally:
            await page.close()

    async def _auto_scroll(self, page, step: int = 800, pause: float = 0.5):
        """自动滚动页面到底部"""
        previous_height = 0
        for _ in range(20):  # 最多滚动 20 次
            current_height = await page.evaluate("document.body.scrollHeight")
            if current_height == previous_height:
                break
            previous_height = current_height
            await page.evaluate(f"window.scrollBy(0, {step})")
            await page.wait_for_timeout(int(pause * 1000))
        await page.evaluate("window.scrollTo(0, 0)")

    async def close(self):
        """关闭浏览器"""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if hasattr(self, "_pw"):
            await self._pw.stop()
        self._browser = None
        self._context = None


def render_sync(
    url: str,
    wait_ms: int = 2000,
    scroll: bool = False,
    headless: bool = True,
    proxy: Optional[str] = None,
) -> RenderResult:
    """同步渲染便捷函数"""
    renderer = PlaywrightRenderer(headless=headless, proxy=proxy)
    try:
        return asyncio.run(renderer.render(url, wait_ms=wait_ms, scroll=scroll))
    finally:
        asyncio.run(renderer.close())
