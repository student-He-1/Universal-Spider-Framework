"""环境验证脚本：检查 Playwright 浏览器路径、项目模块导入、数据目录位置"""
import os
import sys
from pathlib import Path

# 必须在 import playwright 前设置
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = r"D:\conda_cache\playwright"

print("=" * 60)
print("1. 浏览器路径检查")
print("=" * 60)
print("PLAYWRIGHT_BROWSERS_PATH =", os.environ["PLAYWRIGHT_BROWSERS_PATH"])
browser_root = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
for d in sorted(browser_root.iterdir()):
    if d.is_dir():
        # 找 chrome.exe
        exe = list(d.rglob("chrome.exe")) + list(d.rglob("headless_shell.exe"))
        exe_name = exe[0].name if exe else "(无 exe)"
        print(f"  {d.name:<28} {exe_name}")

print()
print("=" * 60)
print("2. Playwright 浏览器启动测试")
print("=" * 60)
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<h1>test</h1>")
        title = page.evaluate("() => document.querySelector('h1').textContent")
        browser.close()
    print(f"  Chromium 启动成功，页面渲染测试: {title}")
except Exception as e:
    print(f"  启动失败: {e}")

print()
print("=" * 60)
print("3. 项目模块导入测试")
print("=" * 60)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 项目根目录
sys.path.insert(0, str(Path(__file__).resolve().parent))
modules = [
    "config.settings",
    "core.request.requester",
    "core.renderer.playwright_renderer",
    "core.middleware.middlewares",
    "core.pipeline.pipelines",
    "core.engine.runner",
    "proxy.pool.manager",
    "proxy.validator.checker",
    "dedup.bloom_filter",
    "dedup.scheduler",
    "storage.raw.postgres_raw",
    "storage.cleaned.postgres_cleaned",
    "cleaner.rules.engine",
    "cleaner.transformers.data_transformer",
    "cleaner.llm_assist.client",
    "cleaner.pipeline",
    "monitor.metrics",
    "monitor.alert",
    "utils.logger",
    "utils.fingerprint",
    "utils.helpers",
]
ok = 0
for mod in modules:
    try:
        __import__(mod)
        print(f"  OK   {mod}")
        ok += 1
    except Exception as e:
        print(f"  FAIL {mod}: {e}")
print(f"\n  模块导入: {ok}/{len(modules)}")

print()
print("=" * 60)
print("4. 数据目录位置检查（必须全部在 D 盘）")
print("=" * 60)
from config.settings import settings
paths = {
    "项目根目录": Path(__file__).resolve().parent.parent,
    "原始数据": Path(settings.RAW_DATA_DIR),
    "清洗数据": Path(settings.CLEANED_DATA_DIR),
    "Playwright": Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]),
    "pip缓存": Path(os.environ.get("PIP_CACHE_DIR", "")),
    "TEMP": Path(os.environ.get("TEMP", "")),
}
all_d = True
for name, p in paths.items():
    drive = p.drive if hasattr(p, "drive") else ""
    is_d = str(p).startswith("D:")
    mark = "OK" if is_d else "!! 非D盘"
    if not is_d:
        all_d = False
    print(f"  [{mark}] {name:<12}: {p}")

print()
print("=" * 60)
print("验证完成")
print("=" * 60)
