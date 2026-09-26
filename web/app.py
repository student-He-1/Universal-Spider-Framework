# -*- coding: utf-8 -*-
"""
================================================================
万能爬虫 - Web 可视化界面（后端）
================================================================
架构：本文件只是一层薄包装，核心爬取逻辑全部调用
      examples/universal_crawler.py 里的 crawl() 函数。
      改引擎代码，这里自动跟着变；两者是同一个引擎的不同表现层。

日志获取：通过 crawl() 的 log_callback 参数（引擎原生支持），
         不替换 sys.stdout，不影响 curl_cffi 等库的正常运行。
================================================================
"""
import os
import sys
import time
import uuid
import queue as _queue
import threading
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file, abort
from flask_socketio import SocketIO

# 把项目根目录和 examples 加入 path，直接复用原有引擎
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "examples"))

from universal_crawler import crawl  # noqa: E402  原有引擎，直接导入

DATA_DIR = PROJECT_ROOT / "data" / "demo"
DATA_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = "universal-crawler-web"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

tasks: dict = {}
tasks_lock = threading.Lock()


# ================================================================
# 预设配置模板
# ================================================================
TEMPLATES = {
    "bookshop": {
        "name": "网上书店（静态·网址翻页）",
        "config": {
            "name": "网上书店图书",
            "render": "direct",
            "pagination": {"type": "url_pattern", "url_pattern": "http://books.toscrape.com/catalogue/page-{page}.html", "start_page": 1, "max_pages": 2},
            "item_selector": "article.product_pod",
            "fields": {"书名": "h3 a::attr(title)", "价格": "p.price_color::text", "评分": "p.star-rating::attr(class)"},
            "delay": 0.3,
        },
    },
    "quotes": {
        "name": "名人名言（静态·网址翻页）",
        "config": {
            "name": "名人名言",
            "render": "direct",
            "pagination": {"type": "url_pattern", "url_pattern": "http://quotes.toscrape.com/page/{page}/", "start_page": 1, "max_pages": 2},
            "item_selector": "div.quote",
            "fields": {"名言内容": "span.text::text", "作者": "small.author::text", "标签": "div.tags a.tag::text"},
            "delay": 0.3,
        },
    },
    "shanghai": {
        "name": "软科中国大学排名（动态·点按钮翻页）",
        "config": {
            "name": "软科中国大学排名",
            "render": "playwright",
            "pagination": {"type": "next_button", "next_selector": "li.ant-pagination-next", "disabled_marker": "disabled", "max_pages": 3},
            "first_url": "https://www.shanghairanking.cn/rankings/bcur/2025",
            "item_selector": "table.rk-table tr",
            "fields": {"排名": "td:nth-child(1)::text", "学校中文名": "td:nth-child(2) .name-cn::text", "学校英文名": "td:nth-child(2) .name-en::text", "标签": "td:nth-child(2) .tags::text", "地区": "td:nth-child(3)::text", "类型": "td:nth-child(4)::text", "总分": "td:nth-child(5)::text"},
            "delay": 0.3,
        },
    },
    "taobao": {
        "name": "淘宝电脑销售（动态·网址翻页）",
        "config": {
            "name": "淘宝电脑销售",
            "render": "playwright",
            "pagination": {"type": "url_pattern", "url_pattern": "https://uland.taobao.com/sem/tbsearch?keyword=电脑&page={page}", "start_page": 1, "max_pages": 2},
            "item_selector": "div[class*=CardV2--doubleCard]",
            "fields": {"商品标题": "div[class*=Title--title] span::text", "价格_元": "span[class*=Price--priceInt]::text", "价格_角分": "span[class*=Price--priceFloat]::text", "发货地": "div[class*=Price--procity] span::text", "商品标签": "div[class*=Abstract--text]::text"},
            "delay": 2.0,
        },
    },
}


# ================================================================
# 日志推送线程：从队列取日志，通过 WebSocket 推送到前端
# ================================================================
def _log_pusher(task_id: str, log_queue: _queue.Queue, stop_event: threading.Event):
    while not stop_event.is_set() or not log_queue.empty():
        try:
            line = log_queue.get(timeout=0.2)
            socketio.emit("crawl_log", {"task_id": task_id, "text": line + "\n"})
        except _queue.Empty:
            continue


# ================================================================
# 后台爬取线程
# ================================================================
def run_crawl(task_id: str, config: dict):
    task = tasks[task_id]
    task["status"] = "running"
    task["start_time"] = time.time()
    task["log"] = ""

    log_queue: _queue.Queue = _queue.Queue()
    stop_event = threading.Event()

    def log_callback(msg: str):
        if msg.strip():
            log_queue.put(msg)
            task["log"] += msg + "\n"

    pusher = threading.Thread(target=_log_pusher, args=(task_id, log_queue, stop_event), daemon=True)
    pusher.start()

    # 预创建requester（必须在调用crawl之前创建，避免crawl内部在后台线程中创建时出问题）
    requester = None
    if config.get("render") == "direct":
        try:
            from core.request.requester import get_requester
            requester = get_requester("curl_cffi")
            # 应用自定义请求头（反爬站需要 Referer 等）
            custom_headers = config.get("headers")
            if custom_headers and isinstance(custom_headers, dict):
                requester.headers.update(custom_headers)
                task["log"] += f"[配置] 已应用自定义请求头: {list(custom_headers.keys())}\n"
        except Exception as e:
            task["log"] += f"[警告] 创建requester失败: {e}\n"

    try:
        result = crawl(config, requester=requester, log_callback=log_callback)
        task["result"] = result
        task["status"] = "completed"
        name = config.get("name", "result")
        xlsx_path = DATA_DIR / f"{name}.xlsx"
        task["file_path"] = str(xlsx_path) if xlsx_path.exists() else None
        task["sqlite_path"] = config.get("_sqlite_path")
        socketio.emit("crawl_done", {"task_id": task_id, "count": len(result), "status": "completed"})
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        task["status"] = "failed"
        task["error"] = str(e)
        task["traceback"] = tb
        print(f"[爬取失败] {e}\n{tb}", file=sys.stderr)
        log_queue.put(f"\n[错误] {e}\n{tb}")
        socketio.emit("crawl_done", {"task_id": task_id, "status": "failed", "error": str(e)})
    finally:
        stop_event.set()
        pusher.join(timeout=2)
        task["end_time"] = time.time()


# ================================================================
# 路由
# ================================================================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/templates", methods=["GET"])
def get_templates():
    return jsonify([{"key": k, "name": v["name"]} for k, v in TEMPLATES.items()])


@app.route("/api/templates/<key>", methods=["GET"])
def get_template(key):
    if key not in TEMPLATES:
        abort(404)
    return jsonify(TEMPLATES[key]["config"])


@app.route("/api/probe", methods=["POST"])
def probe_url():
    """
    站点指纹识别：给定 URL，返回推荐爬取配置（渲染/翻页/容器/字段候选）。
    """
    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "缺少 url"}), 400
    from universal_crawler import probe_site
    result = probe_site(url, log_callback=None)
    return jsonify(result)


@app.route("/api/stats", methods=["GET"])
def get_stats():
    """
    数据统计：扫描 data/storage/*.db，返回每个库的表/行数/字段数。
    """
    storage_dir = Path(__file__).resolve().parent.parent / "data" / "storage"
    dbs = []
    if storage_dir.exists():
        import sqlite3
        for db_file in sorted(storage_dir.glob("*.db")):
            try:
                conn = sqlite3.connect(str(db_file))
                tables = []
                for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                    n = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                    cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')]
                    tables.append({"table": t, "rows": n, "columns": cols})
                conn.close()
                dbs.append({"name": db_file.stem, "path": str(db_file),
                            "tables": tables, "total_rows": sum(t["rows"] for t in tables)})
            except Exception:
                continue
    return jsonify({"dbs": dbs})


@app.route("/api/crawl", methods=["POST"])
def start_crawl():
    config = request.get_json()
    if not config or not config.get("name"):
        return jsonify({"error": "配置不能为空，且必须包含 name 字段"}), 400

    task_id = str(uuid.uuid4())[:8]
    with tasks_lock:
        tasks[task_id] = {
            "task_id": task_id, "config": config, "status": "pending",
            "result": None, "file_path": None, "error": None,
        }

    t = threading.Thread(target=run_crawl, args=(task_id, config), daemon=True)
    t.start()
    return jsonify({"task_id": task_id, "status": "pending"})


@app.route("/api/status/<task_id>", methods=["GET"])
def get_status(task_id):
    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify({
        "task_id": task_id, "status": task["status"],
        "count": len(task["result"]) if task["result"] else 0,
        "error": task["error"], "has_file": bool(task["file_path"]),
    })


@app.route("/api/log/<task_id>", methods=["GET"])
def get_log(task_id):
    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify({"task_id": task_id, "log": task.get("log", "")})


@app.route("/api/result/<task_id>", methods=["GET"])
def get_result(task_id):
    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    if task["status"] != "completed":
        return jsonify({"error": "任务尚未完成", "status": task["status"]}), 400
    return jsonify({
        "count": len(task["result"]),
        "columns": list(task["result"][0].keys()) if task["result"] else [],
        "rows": task["result"],
    })


@app.route("/api/download/<task_id>", methods=["GET"])
def download_file(task_id):
    task = tasks.get(task_id)
    if not task or not task.get("file_path"):
        abort(404)
    file_path = Path(task["file_path"])
    if not file_path.exists():
        abort(404)
    return send_file(str(file_path), as_attachment=True, download_name=file_path.name)


# ================================================================
# WebSocket 事件
# ================================================================
@socketio.on("connect")
def on_connect():
    print(f"[WebSocket] 客户端连接: {request.sid}")


@socketio.on("disconnect")
def on_disconnect():
    print(f"[WebSocket] 客户端断开: {request.sid}")


# ================================================================
# 启动
# ================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  万能爬虫 Web 可视化界面")
    print("  访问地址: http://127.0.0.1:5000")
    print("  核心引擎: examples/universal_crawler.py（直接复用，未修改核心逻辑）")
    print("=" * 60)
    socketio.run(app, host="127.0.0.1", port=5000, debug=False, allow_unsafe_werkzeug=True)
