"""
日志工具：按站点分文件，统一格式
"""
import sys
from pathlib import Path
from loguru import logger
from config.settings import settings, BASE_DIR

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 移除默认 handler
logger.remove()

# 控制台输出
logger.add(
    sys.stdout,
    level=settings.LOG_LEVEL,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
)

# 全局文件日志
logger.add(
    LOG_DIR / "spider_{time:YYYY-MM-DD}.log",
    level=settings.LOG_LEVEL,
    rotation="00:00",
    retention="30 days",
    encoding="utf-8",
    enqueue=True,
)


def get_logger(name: str = "spider"):
    """获取带站点名的 logger，日志会同时写入站点独立文件"""
    site_log_file = LOG_DIR / f"{name}_{{time:YYYY-MM-DD}}.log"
    site_logger = logger.bind(site=name)
    # 为该站点添加独立文件 handler（只添加一次）
    handler_id = f"site_{name}"
    if not hasattr(get_logger, "_handlers"):
        get_logger._handlers = set()
    if handler_id not in get_logger._handlers:
        logger.add(
            site_log_file,
            level=settings.LOG_LEVEL,
            rotation="00:00",
            retention="14 days",
            encoding="utf-8",
            enqueue=True,
            filter=lambda record: record["extra"].get("site") == name,
        )
        get_logger._handlers.add(handler_id)
    return site_logger
