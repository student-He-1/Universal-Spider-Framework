"""
全局配置中心
通过环境变量或 .env 文件加载，所有模块统一从此读取
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# ============================================================
# 环境引导：所有缓存/临时/浏览器数据强制指向 D 盘，禁止写 C 盘
# 必须在 import playwright 等第三方库之前执行
# ============================================================
_D_CACHE = Path("D:/conda_cache")
(_D_CACHE / "temp").mkdir(parents=True, exist_ok=True)
(_D_CACHE / "playwright").mkdir(parents=True, exist_ok=True)
(_D_CACHE / "pip").mkdir(parents=True, exist_ok=True)

# Playwright 浏览器安装位置（D 盘）
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_D_CACHE / "playwright"))
# 临时目录（D 盘），防止 pip/playwright 解压时写 C 盘 TEMP
os.environ.setdefault("TEMP", str(_D_CACHE / "temp"))
os.environ.setdefault("TMP", str(_D_CACHE / "temp"))
# pip 缓存（D 盘）
os.environ.setdefault("PIP_CACHE_DIR", str(_D_CACHE / "pip"))

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / "config" / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- 数据库 ----------
    REDIS_URL: str = "redis://localhost:6379/0"
    POSTGRES_DSN: str = "postgresql://spider:spider123@localhost:5432/spider_data"

    # ---------- LLM ----------
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    DASHSCOPE_API_KEY: str = ""
    QWEN_MODEL: str = "qwen-plus"

    LOCAL_LLM_BASE_URL: str = "http://localhost:11434/v1"
    LOCAL_LLM_API_KEY: str = "ollama"
    LOCAL_LLM_MODEL: str = "qwen2.5:1.5b"

    # LLM 提供商优先级：deepseek / qwen / local
    LLM_PROVIDER: str = "deepseek"

    # ---------- 代理 ----------
    PROXY_FIXED: str = ""
    PROXY_POOL_REFRESH_INTERVAL: int = 300
    PROXY_MAX_FAIL: int = 3

    # ---------- 爬虫全局 ----------
    CONCURRENT_REQUESTS: int = 16
    DOWNLOAD_DELAY: float = 1.0
    RANDOMIZE_DOWNLOAD_DELAY: bool = True
    RETRY_TIMES: int = 3
    LOG_LEVEL: str = "INFO"

    # ---------- 监控 ----------
    PROMETHEUS_PORT: int = 9100

    # ---------- 存储 ----------
    RAW_DATA_DIR: str = str(BASE_DIR / "data" / "raw")
    CLEANED_DATA_DIR: str = str(BASE_DIR / "data" / "cleaned")


settings = Settings()
