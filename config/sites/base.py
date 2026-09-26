"""
站点配置基类
每个爬虫站点继承此配置，定义请求头、延迟、代理策略等
"""
from pydantic import BaseModel, Field
from typing import Optional, Dict, List


class SiteConfig(BaseModel):
    # 站点标识
    name: str
    # 起始 URL
    start_urls: List[str] = Field(default_factory=list)
    # 允许的域名
    allowed_domains: List[str] = Field(default_factory=list)

    # ---------- 请求策略 ----------
    # 自定义请求头
    headers: Dict[str, str] = Field(default_factory=dict)
    # 请求间隔（秒），覆盖全局
    download_delay: Optional[float] = None
    # 并发数，覆盖全局
    concurrent_requests: Optional[int] = None

    # ---------- 代理策略 ----------
    # 是否使用代理
    use_proxy: bool = True
    # 代理类型：http / https / socks5
    proxy_type: str = "http"

    # ---------- 渲染策略 ----------
    # 是否需要 JS 渲染
    need_render: bool = False
    # 渲染等待时间（毫秒）
    render_wait: int = 2000
    # 渲染时滚动到底部
    render_scroll: bool = False

    # ---------- 去重 ----------
    # 是否启用去重
    dedup_enabled: bool = True
    # 去重键模板（如 {url} 或 {url}_{date}）
    dedup_key_template: str = "{url}"

    # ---------- 存储 ----------
    # 原始数据存储表/集合名
    raw_collection: str = ""
    # 清洗后存储表名
    cleaned_table: str = ""

    # ---------- 清洗规则 ----------
    # 字段映射：原始字段 -> 目标字段
    field_mapping: Dict[str, str] = Field(default_factory=dict)
    # 需要删除的字段
    drop_fields: List[str] = Field(default_factory=list)
