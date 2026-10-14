# -*- coding: utf-8 -*-
"""
================================================================
★ 万能多页爬虫引擎（真正通用，只写这一次）★
================================================================
核心思想：
  引擎 = 渲染方式 + 翻页策略 + 字段提取 + 存表，全部可配置
  你要爬任何网站，只需要写一份 CONFIG 描述它，不用改引擎。

【渲染方式】render
  "direct"      → 直接下载 HTML（快，适合普通静态网站）
  "playwright"  → 用真实浏览器渲染（慢，适合 JS 动态加载的网站）

【翻页策略】pagination.type
  "url_pattern"     → 网址有规律，把页码换成 {page}（两种渲染都支持）
  "next_button"     → 点击"下一页"按钮翻页（必须 playwright，如软科）
  "infinite_scroll" → 滚动到底部自动加载（必须 playwright，如微博/知乎）
  "none"            → 只有一页，不翻页

【可选参数】
  proxy          → str，direct 渲染的 HTTP 代理，如 "http://127.0.0.1:7890"
                    playwright 渲染时也透传给浏览器
  自动反爬     → direct 请求遇到挑战页（Cloudflare/阿里云盾等）
                    自动切 playwright 渲染重试拿真实 HTML
  log_callback  → 函数(msg:str)，引擎日志调用它而非 print（Web 可视化用）

指路牌(css)取值规则：
  xxx::text        取文字
  xxx::attr(href)  取链接
  xxx::attr(src)   取图片
================================================================
"""

import re
import time
import random
import math
import hashlib
import json
import threading
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable
from urllib.parse import urljoin, urlsplit

import parsel


# ================================================================
# 反爬识别常量
# ================================================================
# 常见 JS 挑战页特征（用于 direct 请求后识别）
CHALLENGE_MARKERS = [
    "challenge-platform", "cf-browser-verification", "__cf_chl_",
    "__jschl", "renderData", "just-a-moment", "验证码", "captcha",
    "waf-pass", "sec_verify", "JSESSIONID", "window._signature",
]

# 请求节流在同一进程内按主机共享，避免并发翻页或多个任务把 delay 变成空配置。
_PACE_LOCK = threading.Lock()
_NEXT_REQUEST_AT: Dict[str, float] = {}

# robots.txt 解析结果短时缓存，避免每一页都重新请求。
_ROBOTS_CACHE: Dict[tuple, tuple] = {}
_ROBOTS_LOCK = threading.Lock()
_ROBOTS_HOST_LOCKS: Dict[str, threading.Lock] = {}

# Checkpoint updates can be written by multiple page workers in one process.
_CHECKPOINT_LOCKS: Dict[str, threading.Lock] = {}
_CHECKPOINT_LOCKS_GUARD = threading.Lock()


def _host_key(url: str) -> str:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except (TypeError, ValueError):
        return ""
    if not host:
        return ""
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    return f"{host}:{port}" if port and port != default_port else host


def _wait_for_request_slot(url: str, interval: float, cancel_event=None) -> bool:
    """Reserve a per-host request slot, including requests made by worker threads."""
    try:
        interval = max(0.0, float(interval or 0))
    except (TypeError, ValueError):
        interval = 0.0
    host = _host_key(url)
    if not host or interval <= 0:
        return not (cancel_event is not None and cancel_event.is_set())

    now = time.monotonic()
    with _PACE_LOCK:
        slot = max(now, _NEXT_REQUEST_AT.get(host, now))
        _NEXT_REQUEST_AT[host] = slot + interval
    wait = slot - now
    if wait > 0:
        if cancel_event is not None:
            if cancel_event.wait(wait):
                return False
        else:
            time.sleep(wait)
    return not (cancel_event is not None and cancel_event.is_set())


def _sleep_or_cancel(seconds: float, cancel_event=None) -> bool:
    if cancel_event is not None:
        return not cancel_event.wait(max(0.0, seconds))
    time.sleep(max(0.0, seconds))
    return True

# ================================================================
# 工具函数
# ================================================================
def _extract(box_or_page, css_expr: str) -> str:
    """按指路牌取内容，自动处理 ::text / ::attr(...) / 多段文字拼接"""
    if not css_expr:
        return ""
    m = re.search(r"::attr\((\w+)\)\s*$", css_expr)
    if m:
        attr = m.group(1)
        css_base = css_expr[:m.start()].strip()
        return (box_or_page.css(css_base).attrib.get(attr, "") or "").strip()
    if css_expr.endswith("::text"):
        css_base = css_expr[: -len("::text")].strip()
        texts = box_or_page.css(css_base).xpath(".//text()").getall()
        return re.sub(r"\s+", " ", " ".join(texts)).strip()
    texts = box_or_page.css(css_expr).xpath(".//text()").getall()
    return re.sub(r"\s+", " ", " ".join(texts)).strip()


def _looks_like_challenge(html: str, content_type: str = "") -> bool:
    """判断响应是否疑似 JS 挑战/WAF 页"""
    if not html:
        return False
    if content_type and "json" in content_type.lower():
        return False
    head = html[:6000]
    return any(marker.lower() in head.lower() for marker in CHALLENGE_MARKERS)


def _download_direct(url: str, requester, retries: int = 2,
                     log_callback: Optional[Callable] = None,
                     base_wait: float = 2.0, proxy: Optional[str] = None,
                     proxies: Optional[List[str]] = None,
                     request_delay: float = 0.0,
                     headers: Optional[Dict[str, str]] = None,
                     cancel_event=None) -> str:
    """direct 渲染：用 curl_cffi 下载网页（指数退避 + 随机抖动重试，支持代理轮换）"""
    pool = proxies or ([proxy] if proxy else None)
    return _fetch_with_proxy_rotation(
        url, requester, pool, retries, log_callback, base_wait,
        request_delay=request_delay, headers=headers, cancel_event=cancel_event,
    )


def _parse_proxies(cfg: Dict) -> List[str]:
    """
    解析代理配置，返回候选代理列表（可能为空）。
    支持：
      - proxy:   "http://p1:8080" 或 "http://p1:8080,http://p2:8080"
      - proxies: 同上（列表或逗号分隔串）
      - proxy_file: 每行一个代理的文件路径
    """
    proxies: List[str] = []
    raw = cfg.get("proxy") or cfg.get("proxies")
    if isinstance(raw, str):
        proxies += [p.strip() for p in raw.split(",") if p.strip()]
    elif isinstance(raw, list):
        proxies += [str(p).strip() for p in raw if str(p).strip()]
    # 从文件加载额外代理
    pf = cfg.get("proxy_file")
    if pf and Path(pf).exists():
        try:
            with open(pf, "r", encoding="utf-8") as f:
                proxies += [ln.strip() for ln in f
                            if ln.strip() and not ln.strip().startswith("#")]
        except Exception:
            pass
    return list(dict.fromkeys(proxies))  # 去重保序


def _pick_proxy(proxies: List[str], failed: Optional[set] = None) -> Optional[str]:
    """在候选代理里挑一个，优先没失败过的；用本轮失败集合避免重复踩坑"""
    if not proxies:
        return None
    failed = failed or set()
    candidates = [p for p in proxies if p not in failed]
    pool = candidates or proxies
    return random.choice(pool)


def _fetch_with_proxy_rotation(url, requester, proxies, retries, log_callback, base_wait=2.0,
                               fallback_direct=True, request_delay=0.0, headers=None,
                               cancel_event=None):
    """
    direct 请求软代理轮换：按重试次数轮换代理 + 指数退避 + 随机抖动。
    比单代理更抗封：一个代理失败就换下一个；全部失败后可回退直连（fallback_direct）。
    """
    def _log(msg):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)
    failed = set()
    last_err = ""
    # 有代理 → 先全用代理轮换
    if proxies:
        for attempt in range(retries + 1):
            if cancel_event is not None and cancel_event.is_set():
                return ""
            proxy = _pick_proxy(proxies, failed)
            try:
                if not _wait_for_request_slot(url, request_delay, cancel_event):
                    return ""
                result = requester.get(url, timeout=20, proxy=proxy, headers=headers)
                if result.status_code == 200 and result.text:
                    return result.text
                last_err = f"状态码 {result.status_code}"
                failed.add(proxy)
                _log(f"    代理 {proxy} 失败（{last_err}），切换下一个")
            except Exception as e:
                last_err = str(e)
                failed.add(proxy)
                _log(f"    代理 {proxy} 出错：{e}，切换下一个")
            wait = base_wait * (2 ** attempt) + random.uniform(0, 1)
            if not _sleep_or_cancel(wait, cancel_event):
                return ""
        # 全部代理都失败
        if fallback_direct:
            _log(f"    代理均不可用，回退直连重试...")
            for attempt in range(retries + 1):
                if cancel_event is not None and cancel_event.is_set():
                    return ""
                try:
                    if not _wait_for_request_slot(url, request_delay, cancel_event):
                        return ""
                    result = requester.get(url, timeout=20, headers=headers)
                    if result.status_code == 200 and result.text:
                        return result.text
                    last_err = f"状态码 {result.status_code}"
                    _log(f"    {last_err}，直连重试中...")
                except Exception as e:
                    last_err = str(e)
                    _log(f"    [直连重试 {attempt+1}/{retries+1}] {url} 出错：{e}")
                wait = base_wait * (2 ** attempt) + random.uniform(0, 1)
                if not _sleep_or_cancel(wait, cancel_event):
                    return ""
        _log(f"    所有代理及直连均失败（{last_err}），放弃")
        return ""

    # 无代理 → 原逻辑（指数退避 + 随机抖动）
    for attempt in range(retries + 1):
        if cancel_event is not None and cancel_event.is_set():
            return ""
        try:
            if not _wait_for_request_slot(url, request_delay, cancel_event):
                return ""
            result = requester.get(url, timeout=20, headers=headers)
            if result.status_code == 200 and result.text:
                return result.text
            last_err = f"状态码 {result.status_code}"
            _log(f"    {last_err}，重试中...")
        except Exception as e:
            last_err = str(e)
            _log(f"    [重试 {attempt+1}/{retries+1}] {url} 出错：{e}")
        wait = base_wait * (2 ** attempt) + random.uniform(0, 1)
        if not _sleep_or_cancel(wait, cancel_event):
            return ""
    _log(f"    请求失败（{last_err}），放弃")
    return ""


def _robots_allowed(url: str, cfg: Dict, log_callback=None) -> bool:
    """
    robots.txt 合规检查（默认关闭；cfg['respect_robots'] 为 True 时启用）。
    结果按站点和 User-Agent 缓存；超时或无法获取时按现有策略放行。
    """
    if not cfg.get("respect_robots"):
        return True
    try:
        import urllib.request
        from urllib.robotparser import RobotFileParser
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            return True
        base = f"{parsed.scheme}://{parsed.netloc}"
        custom_headers = cfg.get("headers") or {}
        ua = str(cfg.get("user_agent") or custom_headers.get("User-Agent") or "*")
        cache_key = (base, ua)
        now = time.monotonic()
        cached = _ROBOTS_CACHE.get(cache_key)
        if cached and cached[0] > now:
            rp = cached[1]
            return rp.can_fetch(ua, url) if rp is not None else True

        with _ROBOTS_LOCK:
            host_lock = _ROBOTS_HOST_LOCKS.setdefault(base, threading.Lock())
        with host_lock:
            now = time.monotonic()
            cached = _ROBOTS_CACHE.get(cache_key)
            if cached and cached[0] > now:
                rp = cached[1]
                return rp.can_fetch(ua, url) if rp is not None else True

            robots_url = base + "/robots.txt"
            timeout = min(max(float(cfg.get("robots_timeout", 5)), 0.5), 20.0)
            req = urllib.request.Request(robots_url, headers={"User-Agent": ua})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    body = response.read(512 * 1024).decode("utf-8", errors="replace")
                rp = RobotFileParser()
                rp.set_url(robots_url)
                rp.parse(body.splitlines())
                _ROBOTS_CACHE[cache_key] = (time.monotonic() + 300, rp)
                return rp.can_fetch(ua, url)
            except Exception:
                # 保持兼容：robots 服务异常时不阻断采集，但缓存短暂失败结果，避免每页重试。
                _ROBOTS_CACHE[cache_key] = (time.monotonic() + 60, None)
                return True
    except Exception:
        return True


def _install_playwright_request_policy(page, cfg: Dict, log_callback=None) -> None:
    """Apply robots checks to browser document/API requests."""
    def _route(route):
        request = route.request
        resource_type = request.resource_type
        if cfg.get("respect_robots") and resource_type in ("document", "xhr", "fetch"):
            if not _robots_allowed(request.url, cfg, log_callback):
                if log_callback:
                    log_callback(f"[robots.txt] 禁止请求：{request.url}")
                route.abort()
                return
        route.continue_()

    page.route("**/*", _route)


def _goto_with_pacing(page, url: str, cfg: Dict, **kwargs):
    """Pace browser navigations without blocking Playwright's route event loop."""
    if not _wait_for_request_slot(url, cfg.get("delay", 0.5), cfg.get("_cancel_event")):
        return None
    return page.goto(url, **kwargs)


def _wait_playwright_page(page, milliseconds: int, cancel_event=None) -> bool:
    """Wait in short slices so a task cancellation is noticed promptly."""
    deadline = time.monotonic() + max(0, milliseconds) / 1000
    while True:
        if cancel_event is not None and cancel_event.is_set():
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        page.wait_for_timeout(min(250, max(1, int(remaining * 1000))))


def _apply_postprocess(row: Dict, rules: Dict) -> Dict:
    """
    字段后处理（清洗规则），返回新字典，不改原行。
    rules 形如：
      { "价格": {"remove": "元", "digits_only": True},
        "标题": {"strip": True, "collapse_ws": True},
        "描述": {"lower": True} }
    支持规则（可按 order 指定清洗顺序）：
      strip / collapse_ws / lower / upper / digits_only /
      remove（去子串）/ regex_replace（[pattern, repl]）
    """
    if not rules:
        return row
    out = dict(row)
    for field, r in rules.items():
        if field not in out:
            continue
        v = out[field]
        if not isinstance(v, str):
            out[field] = v
            continue
        s = v
        # 按固定顺序清洗：strip → collapse → remove → regex → case → digits
        if r.get("strip", True):
            s = s.strip()
        if r.get("collapse_ws"):
            s = re.sub(r"\s+", " ", s).strip()
        if r.get("remove"):
            s = s.replace(str(r["remove"]), "")
        if r.get("regex_replace"):
            pat, repl = r["regex_replace"]
            flags = re.IGNORECASE if r.get("ignorecase") else 0
            s = re.sub(pat, repl, s, flags=flags)
        if r.get("lower"):
            s = s.lower()
        elif r.get("upper"):
            s = s.upper()
        if r.get("digits_only"):
            s = "".join(ch for ch in s if ch.isdigit())
        out[field] = s
    return out


def _safe_name(name: str) -> str:
    """文件名安全化：去掉非法字符"""
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "result"


def _save_to_sqlite(items: List[Dict], cfg: Dict, name: str, log_callback) -> Optional[str]:
    """
    追加写 SQLite（幂等：已有行按全字段指纹去重，避免重复入库）。
    表按结果字段动态创建；已有表缺列时 ALTER ADD COLUMN。
    返回 db 路径（失败返回 None）。配置：
      storage: {"type": "sqlite", "path": "data/storage/xxx.db", "table": "items"}
    path 缺省 data/storage/{name}.db；table 缺省 "items"。
    """
    try:
        import sqlite3
        storage = cfg.get("storage") or {}
        project_root = Path(__file__).resolve().parent.parent
        default_dir = project_root / "data" / "storage"
        default_dir.mkdir(parents=True, exist_ok=True)
        db_path = Path(storage.get("path") or (default_dir / f"{_safe_name(name)}.db"))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        table = storage.get("table") or "items"
        _safe_table = re.sub(r'[^A-Za-z0-9_]', "_", table)

        conn = sqlite3.connect(str(db_path))
        try:
            # 收集所有列
            cols = []
            for row in items:
                for k in row.keys():
                    if k not in cols:
                        cols.append(k)
            # 建表（若不存在）
            col_defs = ", ".join(f'"{c}" TEXT' for c in cols) or '"__empty" TEXT'
            conn.execute(f'CREATE TABLE IF NOT EXISTS "{_safe_table}" ({col_defs})')
            # 补列
            existing = {r[1] for r in conn.execute(f'PRAGMA table_info("{_safe_table}")')}
            for c in cols:
                if c not in existing:
                    conn.execute(f'ALTER TABLE "{_safe_table}" ADD COLUMN "{c}" TEXT')
            # 已有行指纹集 → 幂等去重
            fingerprint_cache = set()
            for row in conn.execute(f'SELECT * FROM "{_safe_table}"'):
                fingerprint_cache.add(_row_fingerprint(row, cols))
            to_insert = []
            for row in items:
                vals = [row.get(c, "") for c in cols]
                fp = _row_fingerprint(vals, cols)
                if fp not in fingerprint_cache:
                    fingerprint_cache.add(fp)
                    to_insert.append(vals)
            # 批量插入（仅有的列）
            if to_insert:
                placeholders = ", ".join("?" * len(cols))
                col_sql = ", ".join(f'"{c}"' for c in cols)
                conn.executemany(
                    f'INSERT INTO "{_safe_table}" ({col_sql}) VALUES ({placeholders})',
                    to_insert,
                )
                conn.commit()
        finally:
            conn.close()
        if log_callback:
            log_callback(f"[存储] SQLite 已追加 {len(items)} 条（新增 {len(to_insert)}）→ {db_path}（表 {_safe_table}）")
        return str(db_path)
    except Exception as e:
        if log_callback:
            log_callback(f"[存储] SQLite 写入失败：{e}")
        return None


def _row_fingerprint(vals, cols):
    """行指纹：全字段值序列化，用于幂等去重"""
    return tuple(str(v) for v in vals)


def _checkpoint_path(cfg: Dict, name: str) -> Path:
    project_root = Path(__file__).resolve().parent.parent
    # A checkpoint is reusable only when its source and extraction contract match.
    relevant = {
        "version": 2,
        "render": cfg.get("render", "direct"),
        "first_url": cfg.get("first_url"),
        "pagination": cfg.get("pagination", {}),
        "item_selector": cfg.get("item_selector"),
        "fields": cfg.get("fields", {}),
        "api": cfg.get("api", {}),
        "headers": cfg.get("headers", {}),
        "user_agent": cfg.get("user_agent"),
        "storage_state": cfg.get("storage_state"),
        "page_wait": cfg.get("page_wait"),
        "checkpoint_scope": cfg.get("_checkpoint_scope"),
    }
    signature = json.dumps(relevant, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), default=str)
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
    return project_root / "data" / "resume" / f"{_safe_name(name)}-{digest}.jl"


def _load_checkpoint(cfg: Dict, name: str) -> Dict[int, List[Dict]]:
    """读断点：返回 {页号: items}。resume 未开或文件缺失返回空 dict"""
    if not cfg.get("resume"):
        return {}
    path = _checkpoint_path(cfg, name)
    if not path.exists():
        return {}
    out: Dict[int, List[Dict]] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    page = int(rec["page"])
                    items = rec["items"]
                    if page >= 1 and isinstance(items, list) and all(isinstance(x, dict) for x in items):
                        out[page] = items
                except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                    continue
    except Exception:
        pass
    return out


def _checkpoint_lock_for(path: Path) -> threading.Lock:
    """取（或创建）某个断点文件对应的进程内锁"""
    with _CHECKPOINT_LOCKS_GUARD:
        return _CHECKPOINT_LOCKS.setdefault(str(path.resolve()), threading.Lock())


def _atomic_write_checkpoint(path: Path, recs: Dict[int, List[Dict]]) -> None:
    """原子落盘断点：临时文件 → fsync → os.replace，异常清理临时文件。"""
    temp_path = None
    try:
        import tempfile
        import os
        fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        temp_path = Path(temp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for p in sorted(recs):
                f.write(json.dumps({"page": p, "items": recs[p]}, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    except Exception:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass


def _checkpoint_enabled(cfg: Dict) -> bool:
    return bool(cfg.get("resume") or cfg.get("_checkpoint_write"))


def _save_checkpoint(cfg: Dict, name: str, page: int, items: List[Dict], all_results: List[Dict]):
    """
    断点续爬：把已完成页结果持久化（JSON Lines 一行一页）。
    同一页重复写入时以最后一次为准，并通过临时文件原子替换。
    """
    if not _checkpoint_enabled(cfg):
        return
    try:
        path = _checkpoint_path(cfg, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        ck_lock = _checkpoint_lock_for(path)
    except Exception:
        return
    with ck_lock:
        recs = _load_checkpoint({**cfg, "resume": True}, name)
        recs[page] = items
        _atomic_write_checkpoint(path, recs)


def _save_checkpoints(cfg: Dict, name: str, pages: Dict[int, List[Dict]]):
    """
    批量落盘多页断点：一次读取 + 一次原子写入。
    并发翻页场景用它替代逐页 _save_checkpoint，避免 N 次全量重写（O(n²) I/O）。
    """
    if not pages or not _checkpoint_enabled(cfg):
        return
    try:
        path = _checkpoint_path(cfg, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        ck_lock = _checkpoint_lock_for(path)
    except Exception:
        return
    with ck_lock:
        recs = _load_checkpoint({**cfg, "resume": True}, name)
        recs.update({int(p): v for p, v in pages.items()})
        _atomic_write_checkpoint(path, recs)


def _clear_checkpoint(cfg: Dict, name: str) -> bool:
    """
    删除断点文件（任务成功后调用），避免 data/resume 随任务数无限累积。
    同时清理该断点可能残留的临时文件。
    """
    try:
        path = _checkpoint_path(cfg, name)
    except Exception:
        return False
    removed = False
    try:
        if path.exists():
            path.unlink()
            removed = True
    except Exception:
        removed = False
    try:
        for tmp in path.parent.glob(path.name + ".*.tmp"):
            try:
                tmp.unlink()
            except Exception:
                pass
    except Exception:
        pass
    return removed


def _normalize_config(config: Dict) -> Dict:
    if not isinstance(config, dict):
        raise ValueError("爬取配置必须是 JSON 对象")
    cfg = dict(config)
    cfg.setdefault("render", "direct")
    if cfg["render"] not in ("direct", "playwright"):
        raise ValueError("render 只能是 direct 或 playwright")
    if "pagination" not in cfg:
        pg = {}
        if cfg.get("page_url_pattern"):
            pg["type"] = "url_pattern"
            pg["url_pattern"] = cfg["page_url_pattern"]
            pg["start_page"] = cfg.get("start_page", 1)
            pg["max_pages"] = cfg.get("max_pages", 1)
        else:
            pg["type"] = "none"
        cfg["pagination"] = pg
    if not isinstance(cfg["pagination"], dict):
        raise ValueError("pagination 必须是 JSON 对象")
    pg = dict(cfg["pagination"])
    cfg["pagination"] = pg
    pg.setdefault("type", "none")
    if pg["type"] not in ("none", "url_pattern", "next_button", "infinite_scroll"):
        raise ValueError("不支持的 pagination.type")
    if cfg["render"] == "direct" and pg["type"] in ("next_button", "infinite_scroll"):
        raise ValueError(f"{pg['type']} 翻页必须使用 playwright 渲染")
    try:
        pg["start_page"] = int(pg.get("start_page", 1))
        pg["max_pages"] = int(pg.get("max_pages", 1))
        cfg["concurrency"] = int(cfg.get("concurrency", 1) or 1)
        cfg["delay"] = float(cfg.get("delay", 0.5))
        cfg["page_wait"] = int(cfg.get("page_wait", 3000))
        cfg["detail_concurrency"] = int(cfg.get("detail_concurrency", 1) or 1)
        cfg["robots_timeout"] = float(cfg.get("robots_timeout", 5))
    except (TypeError, ValueError, OverflowError):
        raise ValueError("页码、页数、并发数、延时和等待时间必须是有效数字")
    if pg["start_page"] < 1 or pg["max_pages"] < 1 or pg["max_pages"] > 10000:
        raise ValueError("start_page 必须大于等于 1，max_pages 必须在 1 到 10000 之间")
    if cfg["concurrency"] < 1 or cfg["concurrency"] > 20:
        raise ValueError("concurrency 必须在 1 到 20 之间")
    if not math.isfinite(cfg["delay"]) or cfg["delay"] < 0 or cfg["delay"] > 3600:
        raise ValueError("delay 必须在 0 到 3600 秒之间")
    if cfg["page_wait"] < 0 or cfg["page_wait"] > 120000:
        raise ValueError("page_wait 必须在 0 到 120000 毫秒之间")
    if cfg["detail_concurrency"] < 1 or cfg["detail_concurrency"] > 20:
        raise ValueError("detail_concurrency 必须在 1 到 20 之间")
    if not math.isfinite(cfg["robots_timeout"]) or not 0.5 <= cfg["robots_timeout"] <= 20:
        raise ValueError("robots_timeout 必须在 0.5 到 20 秒之间")
    cfg["name"] = str(cfg.get("name") or "result").strip() or "result"
    first_url = cfg.get("first_url")

    def _validate_http_url(value, label):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} 必须是有效的 http 或 https URL")
        try:
            parsed = urlsplit(value.strip())
            hostname = parsed.hostname
            parsed.port  # Force validation of malformed ports.
        except ValueError as exc:
            raise ValueError(f"{label} 必须是有效的 http 或 https URL") from exc
        if parsed.scheme not in ("http", "https") or not hostname:
            raise ValueError(f"{label} 必须是有效的 http 或 https URL")
        return value.strip()

    if first_url:
        cfg["first_url"] = _validate_http_url(first_url, "first_url")
    if pg["type"] == "url_pattern":
        pattern = pg.get("url_pattern")
        if not isinstance(pattern, str) or "{page}" not in pattern:
            raise ValueError("url_pattern 翻页必须包含 {page}")
        try:
            sample_url = pattern.format(page=pg["start_page"])
        except (KeyError, IndexError, ValueError, AttributeError, TypeError) as exc:
            raise ValueError("url_pattern 只应使用 {page} 作为格式占位符") from exc
        _validate_http_url(sample_url, "url_pattern")
    elif pg.get("url_pattern"):
        # `none` historically also accepts a single formatted URL pattern.
        try:
            sample_url = str(pg["url_pattern"]).format(page=pg["start_page"])
        except (KeyError, IndexError, ValueError, AttributeError, TypeError) as exc:
            raise ValueError("url_pattern 只应使用 {page} 作为格式占位符") from exc
        pg["url_pattern"] = _validate_http_url(sample_url, "url_pattern")
    if pg["type"] == "none" and not first_url and not pg.get("url_pattern"):
        raise ValueError("单页任务必须填写 first_url")
    if pg["type"] in ("next_button", "infinite_scroll") and not first_url:
        raise ValueError(f"{pg['type']} 翻页必须填写 first_url")
    if cfg.get("fields") is not None and not isinstance(cfg.get("fields"), dict):
        raise ValueError("fields 必须是对象")
    cfg["fields"] = cfg.get("fields") or {}
    if any(not isinstance(key, str) or not key.strip()
           or not isinstance(value, str) or not value.strip()
           for key, value in cfg["fields"].items()):
        raise ValueError("fields 的列名和选择器必须是非空字符串")
    if cfg.get("api") is not None and not isinstance(cfg.get("api"), dict):
        raise ValueError("api 必须是对象")
    api_fields = (cfg.get("api") or {}).get("fields")
    if api_fields is not None and (
        not isinstance(api_fields, dict)
        or any(not isinstance(key, str) or not isinstance(value, str)
               for key, value in api_fields.items())
    ):
        raise ValueError("api.fields 必须是字符串到字符串的对象")
    if cfg.get("headers") is not None and not isinstance(cfg.get("headers"), dict):
        raise ValueError("headers 必须是对象")
    cfg["headers"] = dict(cfg.get("headers") or {})
    if any(not isinstance(key, str) or not isinstance(value, str)
           or not key.strip() or "\r" in key or "\n" in key
           or "\r" in value or "\n" in value
           for key, value in cfg["headers"].items()):
        raise ValueError("headers 必须使用无换行的字符串键和值")
    if cfg.get("item_selector") is not None and not isinstance(cfg.get("item_selector"), str):
        raise ValueError("item_selector 必须是字符串")
    selector = (cfg.get("item_selector") or "").strip()
    if not selector and cfg["fields"]:
        raise ValueError("配置了 HTML 字段时必须填写 item_selector")
    cfg["item_selector"] = selector or "body"
    if cfg.get("user_agent") is not None and not isinstance(cfg.get("user_agent"), str):
        raise ValueError("user_agent 必须是字符串")
    for proxy_key in ("proxy", "proxies"):
        proxy_value = cfg.get(proxy_key)
        if proxy_value is not None and not isinstance(proxy_value, (str, list)):
            raise ValueError(f"{proxy_key} 必须是字符串或字符串列表")
        if isinstance(proxy_value, list) and any(not isinstance(item, str) for item in proxy_value):
            raise ValueError(f"{proxy_key} 列表中的代理地址必须是字符串")
    if cfg.get("proxy_file") is not None and not isinstance(cfg.get("proxy_file"), str):
        raise ValueError("proxy_file 必须是字符串路径")
    header_ua = next(
        (value for key, value in cfg["headers"].items() if key.lower() == "user-agent"),
        None,
    )
    user_agent = (cfg.get("user_agent") or header_ua or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )).strip()
    if not user_agent or "\r" in user_agent or "\n" in user_agent:
        raise ValueError("user_agent 不能为空且不能包含换行")
    cfg["user_agent"] = user_agent
    cfg["headers"] = {
        key: value for key, value in cfg["headers"].items()
        if key.lower() != "user-agent"
    }
    cfg["headers"]["User-Agent"] = user_agent
    for key in ("storage", "detail", "dedup", "postprocess"):
        if cfg.get(key) is not None and not isinstance(cfg.get(key), dict):
            raise ValueError(f"{key} 必须是对象")
    detail_fields = (cfg.get("detail") or {}).get("fields")
    if detail_fields is not None and (
        not isinstance(detail_fields, dict)
        or any(not isinstance(key, str) or not isinstance(value, str)
               for key, value in detail_fields.items())
    ):
        raise ValueError("detail.fields 必须是字符串到字符串的对象")
    if pg["type"] == "infinite_scroll":
        try:
            pg["scroll_pause"] = float(pg.get("scroll_pause", 1.5))
            pg["max_scrolls"] = int(pg.get("max_scrolls", 50))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("scroll_pause 和 max_scrolls 必须是有效数字") from exc
        if not math.isfinite(pg["scroll_pause"]) or not 0 <= pg["scroll_pause"] <= 3600:
            raise ValueError("scroll_pause 必须在 0 到 3600 秒之间")
        if not 1 <= pg["max_scrolls"] <= 10000:
            raise ValueError("max_scrolls 必须在 1 到 10000 之间")
    return cfg


def _extract_items_from_html(html: str, config: Dict) -> List[Dict]:
    """从一页 HTML 文本中提取所有条目，自动跳过全空行（表头）"""
    sel = parsel.Selector(text=html)
    boxes = sel.css(config["item_selector"])
    results = []
    for box in boxes:
        row = {}
        for col_name, css_expr in config.get("fields", {}).items():
            row[col_name] = _extract(box, css_expr)
        if any(v.strip() for v in row.values()):
            results.append(row)
    return results


def _extract_items_from_json(text: str, config: Dict):
    """
    若文本是 JSON 响应，按 config["api"] 配置提取条目；不是 JSON 返回 None。
    api 配置：{ "item_path": "daily",               # 列表所在路径（可空=自动找）
                "fields": { "日期": "time[0]",
                            "最高温": "temperature_2m_max[0]" } }
    自动把标量数组（如 daily.temperature_2m_max）转成逐条记录。
    """
    if not text:
        return None
    trimmed = text.lstrip()
    if not (trimmed.startswith("{") or trimmed.startswith("[")):
        return None
    import json as _json
    try:
        data = _json.loads(text)
    except Exception:
        return None  # 不是 JSON

    api_cfg = config.get("api") or {}
    fields = api_cfg.get("fields") or {}
    if not fields:
        return None

    # 定位列表：item_path 优先；否则根数组，再否则在对象里找第一个数组
    arr = None
    if api_cfg.get("item_path"):
        arr = _json_path(data, api_cfg["item_path"])
    if not isinstance(arr, list):
        if isinstance(data, list):
            arr = data
        elif isinstance(data, dict):
            # 平行数组容器：所有值都是等长数组（如 open-meteo daily{time[],max[],min[]})
            list_fields = {k: v for k, v in data.items() if isinstance(v, list)}
            if not list_fields:
                # 深入一层：顶层没有数组字段时，在子 dict 里找平行数组容器
                for v in data.values():
                    if isinstance(v, dict):
                        sub = {k2: v2 for k2, v2 in v.items() if isinstance(v2, list)}
                        if sub:
                            list_fields = sub
                            break
            if list_fields:
                return _extract_parallel_arrays(list_fields, fields)
            arr = next((v for v in data.values() if isinstance(v, list)), None)
    if not isinstance(arr, list):
        return []

    # 特殊：标量数组 → 每索引一条（如 time 与 temperature 数组平行）
    results = []
    len_arr = len(arr)
    for i in range(len_arr):
        row = {}
        for col, path in fields.items():
            base = arr[i] if isinstance(arr[i], dict) else None
            if isinstance(base, dict):
                v = _json_path(base, path)
            else:
                v = arr[i]
            row[col] = "" if v is None else str(v).strip()
        if any(v for v in row.values()):
            results.append(row)
    return results


def _extract_parallel_arrays(list_fields: Dict, fields: Dict) -> List[Dict]:
    """
    平行数组转逐条：{key: [v0,v1,...]} → [{key:v0},{key:v1},...]
    fields 的 value 是字段名（直接取该 key 的数组第 i 项）。
    """
    n = max((len(v) for v in list_fields.values()), default=0)
    results = []
    for i in range(n):
        row = {}
        for col, path in fields.items():
            base = list_fields.get(path)
            if isinstance(base, list) and i < len(base):
                row[col] = str(base[i]).strip()
            else:
                row[col] = ""
        if any(v for v in row.values()):
            results.append(row)
    return results


def _extract_items_from_page(page, config: Dict) -> List[Dict]:
    """从 Playwright 页面对象中提取所有条目（转 HTML 后复用提取逻辑）"""
    html = page.content()
    return _extract_items_from_html(html, config)


def _json_path(obj, path):
    """
    按点路径从 JSON 对象取值，支持数组索引，如：
      "data.items"        → data.items
      "data.items[0]"     → data.items[0]
      "title"             → 顶层 title
    """
    if not path or obj is None:
        return None
    cur = obj
    for token in str(path).split("."):
        if cur is None:
            return None
        name = re.sub(r"\[.*?\]", "", token)
        idxs = re.findall(r"\[(\d+)\]", token)
        if name:
            if isinstance(cur, dict) and name in cur:
                cur = cur[name]
            else:
                return None
        for i in idxs:
            i = int(i)
            if isinstance(cur, list) and i < len(cur):
                cur = cur[i]
            else:
                return None
    return cur


def _extract_items_via_api(api_responses, api_cfg) -> List[Dict]:
    """
    从捕获的 JSON 接口响应中提取条目。
    api_cfg 形如：
      {
        "item_path": "data.items",        # 列表在 JSON 中的点路径（可空=整包）
        "fields": { "书名": "title", "价格": "price" }  # 列名 -> JSON 点路径
      }
    """
    if not api_responses or not api_cfg:
        return []
    item_path = api_cfg.get("item_path", "")
    results: List[Dict] = []
    for data in api_responses:
        lst = _json_path(data, item_path) if item_path else data
        if not isinstance(lst, list):
            continue
        for entry in lst:
            if not isinstance(entry, dict):
                continue
            row = {}
            for col, jp in api_cfg.get("fields", {}).items():
                v = _json_path(entry, jp)
                row[col] = "" if v is None else str(v).strip()
            if any(v for v in row.values()):
                results.append(row)
    return results


def _dedupe_results(items: List[Dict], dedup_cfg, log_callback=None) -> List[Dict]:
    """
    按指定字段保序去重。
    防漏策略：
      - dedup_cfg 为 None / 未配 key → 不去重（后续风险全无）
      - key 字段为空或缺失的条目 → 永不删除（避免因字段缺失误删导致分析偏差）
      - 仅当 key 有值且重复时，保留第一条，丢弃后续重复
    返回去重后的列表（原始输入不被修改）。
    """
    if not items or not dedup_cfg or not dedup_cfg.get("key"):
        return items
    key = dedup_cfg["key"]
    seen = set()
    out: List[Dict] = []
    dropped = 0
    for it in items:
        k = it.get(key)
        if k is None or str(k).strip() == "":
            out.append(it)  # key 缺失/空：永不删，防止误删数据
            continue
        k = str(k).strip()
        if k in seen:
            dropped += 1
            continue
        seen.add(k)
        out.append(it)
    if dropped and log_callback:
        log_callback(f"[去重] 按字段「{key}」去重：保留 {len(out)} 条，丢弃重复 {dropped} 条")
    return out


# ================================================================
# stealth 反检测（多策略，兼容不同 playwright-stealth 版本）
# ================================================================
_STEALTH_JS = r"""
// 基础反检测：消除 webdriver 特征（兼容所有 playwright-stealth 缺失时的兜底）
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
if (!window.chrome) { window.chrome = { runtime: {} }; }
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
const _qp = window.chrome && window.chrome.runtime && window.chrome.runtime.connect;
"""


def _apply_stealth(page, log_callback=None) -> None:
    """为页面注入反检测。优先用 playwright-stealth，失败用手动脚本兜底。"""
    def _log(m):
        if log_callback:
            log_callback(m)
    injected = False
    try:
        # 策略1（新版）：playwright_stealth.stealth.sync_api 已被 monkeypatch 到 playwright.sync_api
        from playwright_stealth import Stealth
        stealth = Stealth()
        stealth.apply_stealth_sync(page)
        injected = True
    except Exception:
        pass
    if not injected:
        try:
            # 策略2（旧版）：stealth_sync
            from playwright_stealth import stealth_sync
            stealth_sync(page)
            injected = True
        except Exception:
            pass
    if not injected:
        try:
            # 策略3：新版 sync_api 挂载
            import playwright_stealth.stealth as _st
            if hasattr(_st, 'sync_api') and hasattr(_st.sync_api, 'stealth_sync'):
                _st.sync_api.stealth_sync(page)
                injected = True
        except Exception:
            pass
    if not injected:
        # 兜底：手动注入基础反检测
        page.add_init_script(_STEALTH_JS)
        _log("  已注入手动反检测（stealth 库版本不兼容）")
    else:
        _log("  已注入 stealth 反检测")


def _render_with_playwright(url: str, config: Dict, log_callback: Optional[Callable]) -> str:
    """用 Playwright 渲染拿真实 HTML（带 stealth，支持代理）"""
    import os
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", r"D:\conda_cache\playwright")
    from playwright.sync_api import sync_playwright

    ua = config.get("user_agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
    proxy = config.get("proxy")
    headless = config.get("headless", True)
    storage_state = config.get("storage_state")

    def _log(msg=""):
        if log_callback:
            log_callback(str(msg))

    with sync_playwright() as p:
        launch_kwargs = dict(headless=headless)
        if proxy and proxy.startswith("http"):
            launch_kwargs["proxy"] = {"server": proxy}
        try:
            browser = p.chromium.launch(**launch_kwargs)
        except Exception as e:
            _log(f"  [错误] 浏览器启动失败（可能是代理不可用）：{e}")
            if proxy:
                _log("  [提示] 尝试去掉 proxy 配置后重试")
            return ""

        ctx_kwargs = {"viewport": {"width": 1400, "height": 900}, "user_agent": ua}
        if config.get("headers"):
            ctx_kwargs["extra_http_headers"] = config["headers"]
        if storage_state and os.path.exists(storage_state):
            ctx_kwargs["storage_state"] = storage_state
            _log(f"  已加载登录态: {storage_state}")
        context = browser.new_context(**ctx_kwargs)

        page = context.new_page()
        _apply_stealth(page, log_callback)
        _install_playwright_request_policy(page, config, log_callback)

        try:
            _goto_with_pacing(page, url, config, wait_until="domcontentloaded", timeout=30000)
            _wait_playwright_page(page, config.get("page_wait", 3000), config.get("_cancel_event"))
            return page.content()
        except Exception as e:
            _log(f"  [错误] Playwright 渲染失败：{e}")
            return ""
        finally:
            try:
                browser.close()
            except Exception:
                pass


# ================================================================
# 站点指纹识别：探测目标站，返回推荐爬取配置
# ================================================================
def probe_site(url: str, config: Optional[Dict] = None,
               log_callback: Optional[Callable] = None) -> Dict:
    """
    探测一个网站，返回推荐配置（渲染方式/翻页/选择器候选）。
    供 Web 界面的「站点探测」功能使用，也可作为写 CONFIG 前的辅助。

    探测策略：
      - 直接请求 HTML → 判断是否挑战页/是否 JS 动态/是否 SSR 内嵌数据
      - 统计常见内容容器与字段选择器出现频次，给出候选
      - 检测翻页链接特征（rel=next / 下一页 / page={n}）
    返回 dict（safe 的普通 JSON 结构）。
    """
    cfg = dict(config or {})
    cfg.setdefault("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
    out: Dict = {
        "url": url, "ok": False, "error": "",
        "title": "", "render": "direct", "js_dynamic": False,
        "challenge": False, "has_ssr_json": False,
        "pagination": "unknown", "pagination_note": "",
        "item_candidates": [], "fields_candidates": {},
    }

    def _log(m=""):
        if log_callback:
            log_callback(m)

    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from core.request.requester import get_requester
        requester = get_requester("curl_cffi")
    except Exception:
        out["ok"] = False
        out["error"] = "requester 初始化失败"
        return out

    _log(f"[探测] {url}")
    html = _download_direct(url, requester, retries=1, log_callback=_log,
                            base_wait=1.0, proxies=None)
    if not html:
        # direct 失败 → 说明可能需要 playwright（或网络问题），给个建议
        out["ok"] = False
        out["render"] = "playwright"
        out["pagination_note"] = "direct 下载失败，建议 playwright 渲染后重试"
        out["error"] = f"direct 下载失败（可能反爬或网络）"
        return out

    # 挑战页？
    if _looks_like_challenge(html):
        out["challenge"] = True
        out["render"] = "playwright"
        out["pagination_note"] = "疑似 WAF/挑战页，建议 playwright + stealth"

    sel = parsel.Selector(text=html)
    # 标题
    t = sel.xpath("//title/text()").get("")
    out["title"] = t.strip()[:120]

    # 可见文本长度（粗略）
    body_text = re.sub(r"\s+", " ", sel.xpath("//body//text()").get(" ") or " ").strip()
    text_len = len(body_text)
    script_count = len(sel.css("script"))
    has_ssr = any(k in html for k in ("__NEXT_DATA__", "__NUXT__", "__INITIAL_STATE__", "window.__INITIAL_DATA__"))
    out["has_ssr_json"] = has_ssr

    # JS 动态判定：文本少 + 脚本多 → 大概率依赖 JS
    if not out["challenge"]:
        if script_count >= 8 and text_len < 800:
            out["js_dynamic"] = True
            out["render"] = "playwright" if not has_ssr else "direct"
        else:
            out["render"] = "direct"
        if has_ssr and out["render"] == "playwright":
            out["pagination_note"] = "检测到 SSR 内嵌 JSON，可能 direct + api 提取更高效"

    # ---------- 翻页特征 ----------
    next_links = sel.xpath('a[@rel="next"]|a[contains(@class,"next")]|a[contains(text(),"下一页")]|a[contains(text(),"Next")]')
    page_links = sel.css('a[href*="page="], a[href*="page/"], a[href*="?p="], a[href*="&p="]')
    if next_links:
        out["pagination"] = "next_button"
        out["pagination_note"] = "检测到「下一页」链接，可用 next_button 或 url_pattern"
    elif page_links:
        hrefs = [a.attrib.get("href", "") for a in page_links][:8]
        out["pagination"] = "url_pattern"
        out["pagination_note"] = "检测到分页链接（page= 模式），适合 url_pattern"
        out["pagination_samples"] = hrefs[:4]

    # ---------- 内容容器候选 ----------
    cands = [
        ("article", "article"),
        ("div.item", "div[class*=item]"),
        ("div.product", "div[class*=product]"),
        ("li.list", "li[class*=list]"),
        ("table tr", "table tr"),
        ("div.card", "div[class*=card]"),
    ]
    scored = []
    for label, selx in cands:
        n = len(sel.css(selx))
        if n > 0:
            scored.append((n, label, selx))
    scored.sort(reverse=True)
    out["item_candidates"] = [{"label": lb, "selector": sx, "count": n}
                               for n, lb, sx in scored[:4]]

    # ---------- 字段候选 ----------
    field_cands = []
    title_el = sel.css("a[rel~=title], h3 a, .title a, .name")
    if title_el:
        field_cands.append(("标题", "h3 a::text"))
    price_el = sel.css(".price, [class*=price], .Price")
    if price_el:
        field_cands.append(("价格", ".price::text"))
    if page_links or next_links:
        field_cands.append(("链接", "a::attr(href)"))
    out["fields_candidates"] = dict(field_cands)

    out["ok"] = True
    out["pagination"] = out.get("pagination", "none")
    _log(f"[探测] 完成：渲染={out['render']} 翻页={out['pagination']} "
         f"JS动态={out['js_dynamic']} 容器候选={len(out['item_candidates'])}")
    return out


# ================================================================
# 主入口：按配置爬取
# ================================================================
def crawl(config: Dict[str, Any], requester=None, out_dir: Optional[Path] = None,
          log_callback: Optional[Callable[[str], None]] = None,
          cancel_event: Optional[threading.Event] = None,
          page_callback: Optional[Callable[..., None]] = None) -> List[Dict]:
    """
    按配置爬取一个网站（自动选择渲染方式和翻页策略），返回结果并存 Excel。

    参数:
        config: 爬取配置字典
        requester: 可选，自定义请求器（direct渲染时用）
        out_dir: 可选，输出目录
        log_callback: 可选，日志回调函数(msg:str)，传入后日志走回调而不是print
    """
    # 日志函数：有回调走回调，没有用print
    def _log(msg=""):
        if log_callback:
            log_callback(str(msg))
        else:
            print(msg)

    def _cancelled():
        return cancel_event is not None and cancel_event.is_set()

    def _report_page(page_no, status, count=0, error=""):
        if page_callback:
            try:
                page_callback(int(page_no), str(status), int(count), str(error or ""))
            except Exception as exc:
                _log(f"[任务状态] 页面状态回调失败：{exc}")

    cfg = _normalize_config(config)
    cfg["_cancel_event"] = cancel_event
    name = cfg.get("name", "result")
    render = cfg["render"]
    pg = cfg["pagination"]
    delay = cfg.get("delay", 0.5)

    _log(f"\n{'='*60}")
    _log(f"开始爬取：{name}")
    _log(f"  渲染方式：{render}    翻页策略：{pg['type']}")
    proxies = _parse_proxies(cfg)
    if proxies:
        _log(f"  代理池：{len(proxies)} 个（自动轮换）")
    elif cfg.get("proxy"):
        _log(f"  代理：{cfg['proxy']}")
    if cfg.get("respect_robots"):
        _log("  robots.txt 合规检查：已启用")
    _log(f"{'='*60}")

    all_results: List[Dict] = []

    # ---------- 分支1：direct 渲染 ----------
    if render == "direct":
        if requester is None:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from core.request.requester import get_requester
            requester = get_requester("curl_cffi")

            # direct 请求时若配置了代理，给requester注入（curl_cffi 实例持有自己的impersonate）
            # requester.get 支持 proxy 参数，见下方调用处

        if pg["type"] == "none":
            url = cfg.get("first_url") or pg.get("url_pattern", "").format(page=pg["start_page"])
            _log(f"[单页] {url}")
            if _cancelled():
                _report_page(1, "cancelled")
                return []
            _report_page(1, "running")
            html = _fetch_with_challenge_fallback(
                url, cfg, requester, log_callback, proxies,
                request_delay=delay,
            )
            if not html:
                _report_page(1, "cancelled" if _cancelled() else "failed",
                             error="" if _cancelled() else "页面下载失败或被 robots.txt 阻止")
                return []
            try:
                items = _extract_items_from_json(html, cfg)
                if items is None:
                    items = _extract_items_from_html(html, cfg)
            except Exception as exc:
                _report_page(1, "failed", error=str(exc))
                raise
            _log(f"    找到 {len(items)} 条")
            _report_page(1, "completed" if items else "empty", len(items))
            all_results.extend(items)

        elif pg["type"] == "url_pattern":
            concurrency = int(cfg.get("concurrency", 1) or 1)
            resume = bool(cfg.get("resume"))
            checkpoint = _load_checkpoint(cfg, name)   # {页号: items}

            def _fetch_page(pno: int):
                """抓单页，返回页号、条目与状态；失败页不会进入续爬检查点。"""
                if _cancelled():
                    _report_page(pno, "cancelled")
                    return pno, [], "cancelled"
                _report_page(pno, "running")
                url = pg["url_pattern"].format(page=pno)
                try:
                    html = _fetch_with_challenge_fallback(
                        url, cfg, requester, log_callback, proxies,
                        request_delay=delay,
                    )
                    if not html:
                        if _cancelled():
                            _report_page(pno, "cancelled")
                            return pno, [], "cancelled"
                        _log(f"[第 {pno} 页] 下载失败，返回空")
                        _report_page(pno, "failed", error="页面下载失败或被 robots.txt 阻止")
                        return pno, [], "failed"
                    items = _extract_items_from_json(html, cfg)
                    if items is None:
                        items = _extract_items_from_html(html, cfg)
                    status = "completed" if items else "empty"
                    _report_page(pno, status, len(items))
                    return pno, items, status
                except Exception as exc:
                    _log(f"[第 {pno} 页] 抓取失败：{exc}")
                    _report_page(pno, "failed", error=str(exc))
                    return pno, [], "failed"

            if concurrency > 1:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                page_items: Dict[int, List[Dict]] = {}
                # 断点：已完成的页直接用 checkpoint 恢复，不重复下载
                pending_pnos = []
                for p0 in range(pg["start_page"], pg["start_page"] + pg["max_pages"]):
                    if resume and p0 in checkpoint:
                        page_items[p0] = checkpoint[p0]
                        _log(f"[第 {p0} 页] 已爬过（断点），直接复用 {len(checkpoint[p0])} 条")
                        _report_page(p0, "reused", len(checkpoint[p0]))
                    else:
                        pending_pnos.append(p0)
                _log(f"[并发] url_pattern 翻页并发 {concurrency} 线程，待抓 {len(pending_pnos)} 页")
                if pending_pnos:
                    to_checkpoint: Dict[int, List[Dict]] = {}
                    with ThreadPoolExecutor(max_workers=concurrency) as ex:
                        futures = {ex.submit(_fetch_page, p): p for p in pending_pnos}
                        for fut in as_completed(futures):
                            pno, items, page_status = fut.result()
                            page_items[pno] = items
                            _log(f"[第 {pno} 页] 找到 {len(items)} 条")
                            if page_status in ("completed", "empty") and _checkpoint_enabled(cfg):
                                to_checkpoint[pno] = items
                    # 批量落盘：一次读取 + 一次原子写入（替代逐页各写一次的全量重写）
                    _save_checkpoints(cfg, name, to_checkpoint)
                # 按页号排序合并，保证输出顺序稳定（不随并发完成顺序乱序）
                for pno in sorted(page_items):
                    all_results.extend(page_items.get(pno, []))
            else:
                page = pg["start_page"]
                pages_done = 0
                while pages_done < pg["max_pages"]:
                    if _cancelled():
                        break
                    if resume and page in checkpoint:
                        items = checkpoint[page]
                        _log(f"[第 {page} 页] 已爬过（断点），直接复用 {len(items)} 条")
                        _report_page(page, "reused", len(items))
                    else:
                        pno, items, page_status = _fetch_page(page)
                        _log(f"[第 {page} 页] 找到 {len(items)} 条")
                        if page_status in ("completed", "empty"):
                            _save_checkpoint(cfg, name, page, items, all_results)
                        if page_status == "failed":
                            break
                        if page_status == "cancelled":
                            break
                    if not items:
                        _log("    本页无内容，停止（可能已到最后一页）")
                        break
                    all_results.extend(items)
                    pages_done += 1
                    page += 1
        else:
            _log(f"  [错误] direct 渲染不支持翻页策略 '{pg['type']}'，请改用 render='playwright'")
            return []

    # ---------- 分支2：playwright 渲染 ----------
    elif render == "playwright":
        import os
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", r"D:\conda_cache\playwright")
        from playwright.sync_api import sync_playwright

        first_url = cfg.get("first_url") or pg.get("url_pattern", "").format(page=pg["start_page"])
        ua = cfg["user_agent"]
        storage_state = cfg.get("storage_state")
        proxy = (proxies or [cfg.get("proxy")])[0] if (proxies or cfg.get("proxy")) else None
        headless = cfg.get("headless", True)

        launch_kwargs = dict(headless=headless)
        if proxy and proxy.startswith("http"):
            launch_kwargs["proxy"] = {"server": proxy}

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(**launch_kwargs)
            except Exception as e:
                _log(f"  [错误] 浏览器启动失败（可能是代理不可用）：{e}")
                return []
            ctx_kwargs = {"viewport": {"width": 1400, "height": 900}, "user_agent": ua}
            if cfg.get("headers"):
                ctx_kwargs["extra_http_headers"] = cfg["headers"]
            if storage_state and os.path.exists(storage_state):
                ctx_kwargs["storage_state"] = storage_state
                _log(f"  已加载登录态: {storage_state}")
            context = browser.new_context(**ctx_kwargs)

            page = context.new_page()
            _apply_stealth(page, _log)
            _install_playwright_request_policy(page, cfg, _log)

            # ---------- XHR/API 响应捕获（可选）----------
            # 若配置了 api，则监听页面所有 JSON 响应并缓存，提取阶段优先用接口数据
            api_responses = []
            api_cfg = cfg.get("api")
            if api_cfg:
                import json as _json
                def _on_response(resp):
                    try:
                        ct = resp.headers.get("content-type", "") or ""
                        if "json" in ct.lower():
                            body = resp.text()
                            try:
                                api_responses.append(_json.loads(body))
                            except Exception:
                                pass  # 非 JSON 正文，忽略
                    except Exception:
                        pass
                page.on("response", _on_response)
                _log(f"  已启用 API 响应捕获（item_path={api_cfg.get('item_path', '整包')}）")

            def _pick_items(pg_no):
                """优先 API 数据，其次 DOM 提取；返回 (items, from_api)"""
                if api_cfg and api_responses:
                    items = _extract_items_via_api(api_responses, api_cfg)
                    if items:
                        _log(f"    [第 {pg_no} 页] 接口取到 {len(items)} 条")
                        return items, True
                items = _extract_items_from_page(page, cfg)
                return items, False

            if pg["type"] == "none":
                _log(f"[单页] {first_url}")
                if _cancelled():
                    _report_page(1, "cancelled")
                else:
                    _report_page(1, "running")
                    try:
                        _goto_with_pacing(page, first_url, cfg, wait_until="domcontentloaded", timeout=30000)
                        if not _wait_playwright_page(page, cfg.get("page_wait", 3000), cancel_event):
                            _report_page(1, "cancelled")
                        else:
                            items, _ = _pick_items(1)
                            _log(f"    找到 {len(items)} 条")
                            _report_page(1, "completed" if items else "empty", len(items))
                            all_results.extend(items)
                    except Exception as exc:
                        _report_page(1, "failed", error=str(exc))
                        raise

            elif pg["type"] == "url_pattern":
                page_num = pg["start_page"]
                pages_done = 0
                resume = bool(cfg.get("resume"))
                checkpoint = _load_checkpoint(cfg, name)
                while pages_done < pg["max_pages"]:
                    if _cancelled():
                        break
                    if resume and page_num in checkpoint:
                        items = checkpoint[page_num]
                        _report_page(page_num, "reused", len(items))
                        _log(f"[第 {page_num} 页] 已爬过（断点），直接复用 {len(items)} 条")
                        if not items:
                            break
                        all_results.extend(items)
                        pages_done += 1
                        page_num += 1
                        continue
                    url = pg["url_pattern"].format(page=page_num)
                    _log(f"[第 {page_num} 页] {url}")
                    _report_page(page_num, "running")
                    try:
                        api_responses.clear()  # 每页单独收集接口响应
                        _goto_with_pacing(page, url, cfg, wait_until="domcontentloaded", timeout=30000)
                        if not _wait_playwright_page(page, cfg.get("page_wait", 3000), cancel_event):
                            _report_page(page_num, "cancelled")
                            break
                    except Exception as e:
                        _log(f"    [错误] 打开页面失败：{e}")
                        _report_page(page_num, "failed", error=str(e))
                        break
                    try:
                        items, _ = _pick_items(page_num)
                    except Exception as exc:
                        _log(f"    [错误] 提取第 {page_num} 页失败：{exc}")
                        _report_page(page_num, "failed", error=str(exc))
                        break
                    _log(f"    找到 {len(items)} 条")
                    page_status = "completed" if items else "empty"
                    _report_page(page_num, page_status, len(items))
                    _save_checkpoint(cfg, name, page_num, items, all_results)
                    if not items:
                        _log("    本页无内容，停止")
                        break
                    all_results.extend(items)
                    pages_done += 1
                    page_num += 1

            elif pg["type"] == "next_button":
                _log(f"[第 1 页] {first_url}")
                if _cancelled():
                    _report_page(1, "cancelled")
                    browser.close()
                    return []
                _report_page(1, "running")
                try:
                    _goto_with_pacing(page, first_url, cfg, wait_until="domcontentloaded", timeout=30000)
                except Exception as exc:
                    _report_page(1, "failed", error=str(exc))
                    raise
                if not _wait_playwright_page(page, cfg.get("page_wait", 3000), cancel_event):
                    _report_page(1, "cancelled")
                    browser.close()
                    return all_results

                next_sel = pg.get("next_selector", "li.ant-pagination-next")
                disabled_marker = pg.get("disabled_marker", "disabled")

                page_num = 0
                while page_num < pg["max_pages"]:
                    if _cancelled():
                        break
                    page_num += 1
                    try:
                        items = _extract_items_from_page(page, cfg)
                    except Exception as exc:
                        _log(f"    [错误] 提取第 {page_num} 页失败：{exc}")
                        _report_page(page_num, "failed", error=str(exc))
                        raise
                    _report_page(page_num, "completed" if items else "empty", len(items))
                    first_rank = items[0].get("排名", items[0].get("标题", "?")) if items else "?"
                    last_rank = items[-1].get("排名", items[-1].get("标题", "?")) if items else "?"
                    _log(f"  第 {page_num} 页：{first_rank} ~ {last_rank}（本页 {len(items)} 条，累计 {len(all_results)+len(items)} 条）")
                    all_results.extend(items)
                    if page_num >= pg["max_pages"]:
                        break

                    next_btn = page.query_selector(next_sel)
                    if not next_btn:
                        _log("    没找到下一页按钮，结束")
                        break
                    classes = (next_btn.get_attribute("class") or "") + " " + (next_btn.get_attribute("aria-disabled") or "")
                    if disabled_marker in classes.lower():
                        _log("    下一页按钮已禁用，已到最后一页，结束")
                        break

                    old_first = first_rank
                    if _cancelled():
                        _report_page(page_num + 1, "cancelled")
                        break
                    _report_page(page_num + 1, "running")
                    try:
                        if not _wait_for_request_slot(page.url, delay, cancel_event):
                            _report_page(page_num + 1, "cancelled")
                            break
                        if _cancelled():
                            _report_page(page_num + 1, "cancelled")
                            break
                        next_btn.click()
                        page_changed = False
                        for _ in range(20):
                            if _cancelled():
                                _report_page(page_num + 1, "cancelled")
                                break
                            time.sleep(0.2)
                            new_items = _extract_items_from_page(page, cfg)
                            if new_items:
                                new_first = new_items[0].get("排名", new_items[0].get("标题", ""))
                                if new_first and new_first != old_first:
                                    page_changed = True
                                    break
                        if not _cancelled() and not page_changed:
                            _log(f"    [错误] 点击后页面未变化，停止翻页")
                            _report_page(page_num + 1, "failed", error="点击下一页后内容未变化")
                            break
                    except Exception as exc:
                        _log(f"    [错误] 翻到第 {page_num + 1} 页失败：{exc}")
                        _report_page(page_num + 1, "failed", error=str(exc))
                        break

            elif pg["type"] == "infinite_scroll":
                _log(f"[无限滚动] {first_url}")
                if _cancelled():
                    _report_page(1, "cancelled")
                    browser.close()
                    return []
                _report_page(1, "running")
                try:
                    _goto_with_pacing(page, first_url, cfg, wait_until="domcontentloaded", timeout=30000)
                except Exception as exc:
                    _report_page(1, "failed", error=str(exc))
                    raise
                _wait_playwright_page(page, cfg.get("page_wait", 3000), cancel_event)
                scroll_pause = pg.get("scroll_pause", 1.5)
                max_scrolls = pg.get("max_scrolls", 50)  # 兜底，防止无限加载

                last_count = 0
                stable_rounds = 0
                scrolls = 0
                seen_keys = set()   # 去重：按条目序列的指纹
                collected: List[Dict] = []

                def _fingerprint(items):
                    """条目 → 指纹（无唯一字段时用全部文本）"""
                    fp = []
                    for it in items:
                        vals = [str(v).strip() for v in it.values() if str(v).strip()]
                        fp.append(frozenset(vals[:3]))
                    return fp

                while stable_rounds < 3 and scrolls < max_scrolls:
                    if _cancelled():
                        break
                    scrolls += 1
                    try:
                        if not _wait_for_request_slot(page.url, delay, cancel_event):
                            break
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    except Exception as exc:
                        _log(f"    [错误] 滚动加载失败：{exc}")
                        _report_page(1, "failed", error=str(exc))
                        raise
                    if not _wait_playwright_page(page, int(scroll_pause * 1000), cancel_event):
                        break

                    try:
                        items = _extract_items_from_page(page, cfg)
                    except Exception as exc:
                        _log(f"    [错误] 提取滚动结果失败：{exc}")
                        _report_page(1, "failed", error=str(exc))
                        raise
                    # 只吸收新条目（按指纹去重）
                    current_fp = set(_fingerprint(items))
                    new_fp = current_fp - seen_keys
                    if new_fp:
                        stable_rounds = 0
                        for fp in new_fp:
                            item = next((it for it in items if _fingerprint([it])[0] == fp), None)
                            if item is not None:
                                collected.append(item)
                        seen_keys |= new_fp
                        _log(f"    滚动 {scrolls}：共计 {len(collected)} 条（本页新增 {len(new_fp)}）")
                    else:
                        stable_rounds += 1
                        _log(f"    滚动 {scrolls}：无新增，稳定计数 {stable_rounds}/3")
                    last_count = len(items)

                if scrolls >= max_scrolls:
                    _log(f"  达到最大滚动次数 {max_scrolls}，停止")
                all_results = collected
                _report_page(1, "cancelled" if _cancelled() else ("completed" if collected else "empty"), len(collected))

            else:
                _log(f"  [错误] 未知翻页策略 '{pg['type']}'")
                browser.close()
                return []

            browser.close()

    else:
        _log(f"  [错误] 未知渲染方式 '{render}'，请用 direct 或 playwright")
        return []

    # ---------- 详情页补充（可选）----------
    detail_cfg = cfg.get("detail")
    if detail_cfg and all_results:
        _log(f"\n补充详情页信息...")
        if requester is None and render == "direct":
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from core.request.requester import get_requester
            requester = get_requester("curl_cffi")
        link_field = detail_cfg.get("link_field")
        limit = detail_cfg.get("limit", 0)
        done = 0

        # 预筛选需要补充详情的行
        rows_to_fill = []
        for row in all_results:
            if _cancelled():
                break
            if limit and done >= limit:
                break
            if not link_field or not row.get(link_field):
                continue
            rows_to_fill.append((row,))
            done += 1
        done = 0

        def _fill_detail(row):
            """抓详情页并回填字段，返回是否成功"""
            nonlocal done
            if _cancelled():
                return False
            detail_url = row[link_field]
            if not detail_url.startswith("http"):
                base = cfg.get("first_url") or pg.get("url_pattern", "")
                detail_url = urljoin(base, detail_url)
            if not _robots_allowed(detail_url, cfg, log_callback):
                _log(f"[robots.txt] 禁止抓取详情页：{detail_url}")
                return False
            html = (
                _fetch_with_challenge_fallback(
                    detail_url, cfg, requester, log_callback, proxies,
                    request_delay=delay,
                )
                if requester else ""
            )
            if html and not _looks_like_challenge(html):
                detail_sel = parsel.Selector(text=html)
                for col_name, css_expr in detail_cfg.get("fields", {}).items():
                    row[col_name] = _extract(detail_sel, css_expr)
                done += 1
                return True
            return False

        detail_workers = int(cfg.get("detail_concurrency", 1) or 1)
        if detail_workers > 1:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            _log(f"[并发] 详情页并发 {detail_workers} 线程，共 {len(rows_to_fill)} 条")
            with ThreadPoolExecutor(max_workers=detail_workers) as ex:
                futs = [ex.submit(_fill_detail, row) for (row,) in rows_to_fill]
                for _ in as_completed(futs):
                    pass  # done 计数由 _fill_detail 累计
        else:
            for (row,) in rows_to_fill:
                _fill_detail(row)
        _log(f"  完成 {done} 条详情页补充")

    # ---------- 字段后处理（清洗）----------
    pp_cfg = cfg.get("postprocess")
    if pp_cfg and all_results:
        all_results = [_apply_postprocess(row, pp_cfg) for row in all_results]
        _log(f"[清洗] 已应用字段后处理规则（{len(pp_cfg)} 个字段）")

    # ---------- 存储 ----------
    # 去重（可选）：在详情回填完成后统一执行，避免跨页/跨并发重复
    if all_results and cfg.get("dedup"):
        all_results = _dedupe_results(all_results, cfg["dedup"], log_callback=_log)

    if all_results:
        # SQLite（可选，storage.type == sqlite 时启用，与 Excel/CSV 并存）
        if (cfg.get("storage") or {}).get("type") == "sqlite":
            db_path = _save_to_sqlite(all_results, cfg, name, _log)
            cfg["_sqlite_path"] = db_path  # 供上层读取
            config["_sqlite_path"] = db_path  # 写回原 config（浅拷贝不影响外部）

        out_dir = out_dir or (Path(__file__).resolve().parent.parent / "data" / "demo")
        out_dir.mkdir(parents=True, exist_ok=True)
        xlsx_path = out_dir / f"{_safe_name(str(name))}.xlsx"
        import pandas as pd
        df = pd.DataFrame(all_results)
        df.to_excel(xlsx_path, index=False)
        df.to_csv(xlsx_path.with_suffix(".csv"), index=False, encoding="utf-8-sig")
        _log(f"\n已保存 Excel：{xlsx_path}")

    _log(f"\n{name} 爬取完成，共 {len(all_results)} 条")
    return all_results


def _fetch_with_challenge_fallback(url: str, cfg: Dict, requester, log_callback,
                                   proxies: Optional[List[str]] = None,
                                   request_delay: float = 0.0) -> str:
    """
    direct 请求，若响应疑似 WAF/挑战页，自动切 playwright 渲染拿真实 HTML。
    这就是"挑战页识别 → 浏览器渲染 → 重放"的闭环（浏览器执行 JS 后拿到真实 DOM）。
    """
    if not _robots_allowed(url, cfg, log_callback):
        if log_callback:
            log_callback(f"[robots.txt] 禁止抓取：{url}")
        return ""

    # 若配置禁用了自动降级（如目标页较大、只想快速走 direct），则不启用
    if not cfg.get("auto_fallback", True):
        return _download_direct(
            url, requester, log_callback=log_callback, proxies=proxies,
            request_delay=request_delay, headers=cfg.get("headers"),
            cancel_event=cfg.get("_cancel_event"),
        )

    html = _download_direct(
        url, requester, log_callback=log_callback, proxies=proxies,
        request_delay=request_delay, headers=cfg.get("headers"),
        cancel_event=cfg.get("_cancel_event"),
    )
    if cfg.get("_cancel_event") is not None and cfg["_cancel_event"].is_set():
        return ""
    if html and _looks_like_challenge(html):
        def _log(m=""):
            if log_callback:
                log_callback(m)
            else:
                print(m)
        _log(f"  [反爬] 检测到挑战/WAF页（Cloudflare/盾类），切换 playwright 渲染重试...")
        page_html = _render_with_playwright(url, cfg, log_callback)
        if page_html and not _looks_like_challenge(page_html):
            return page_html
        _log("  [反爬] playwright 渲染仍被拦截，返回空")
        return ""
    return html
