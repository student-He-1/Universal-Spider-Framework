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
import sys
import time
import uuid
import csv
import json
import sqlite3
import queue as _queue
import threading
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file, abort
from flask_socketio import SocketIO

# 把项目根目录和 examples 加入 path，直接复用原有引擎
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "examples"))

from universal_crawler import (  # noqa: E402
    crawl, _normalize_config, _safe_name, _clear_checkpoint,
)
from utils.secrets import redact_task_config, sanitize_log_text  # noqa: E402

DATA_DIR = PROJECT_ROOT / "data" / "demo"
DATA_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = "universal-crawler-web"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

tasks: dict = {}
tasks_lock = threading.Lock()
TASK_DB = PROJECT_ROOT / "data" / "tasks.sqlite3"

# 内存中保留的任务上限（超出后淘汰最旧的终态任务；SQLite 仍保留完整历史）
MAX_INMEM_TASKS = 50


def _evict_terminal_tasks_locked():
    """淘汰内存中最旧的终态任务。调用方必须已持有 tasks_lock。"""
    if len(tasks) <= MAX_INMEM_TASKS:
        return
    terminal = [
        tid for tid, t in tasks.items()
        if t.get("status") in ("completed", "partial", "failed", "cancelled", "interrupted")
    ]
    terminal.sort(key=lambda tid: tasks[tid].get("created_at", 0))
    for tid in terminal[: max(0, len(tasks) - MAX_INMEM_TASKS)]:
        tasks.pop(tid, None)


def _task_db():
    conn = sqlite3.connect(str(TASK_DB), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _init_task_store():
    TASK_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = _task_db()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                parent_task_id TEXT,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                started_at REAL,
                ended_at REAL,
                item_count INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                file_path TEXT,
                sqlite_path TEXT,
                config_json TEXT NOT NULL,
                has_redacted_secrets INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);
            CREATE TABLE IF NOT EXISTS task_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                text TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_task_logs_task ON task_logs(task_id, id);
            CREATE TABLE IF NOT EXISTS task_pages (
                task_id TEXT NOT NULL,
                page_no INTEGER NOT NULL,
                status TEXT NOT NULL,
                item_count INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                updated_at REAL NOT NULL,
                PRIMARY KEY(task_id, page_no)
            );
        """)
        now = time.time()
        conn.execute(
            "UPDATE tasks SET status='interrupted', ended_at=?, error=COALESCE(error, ?) "
            "WHERE status IN ('pending', 'running', 'cancelling')",
            (now, "服务重启时任务仍未结束"),
        )
        conn.execute(
            "UPDATE task_pages SET status='interrupted', updated_at=? "
            "WHERE status='running' AND task_id IN (SELECT task_id FROM tasks WHERE status='interrupted')",
            (now,),
        )
        conn.commit()
    finally:
        conn.close()


# 凭据脱敏逻辑已抽到 utils/secrets.py（便于单测），此处保留别名以兼容既有调用点。
_redact_task_config = redact_task_config
_sanitize_log_text = sanitize_log_text


def _create_task_record(task_id: str, config: dict, parent_task_id=None):
    safe_config, redacted = _redact_task_config(config)
    now = time.time()
    conn = _task_db()
    try:
        conn.execute(
            "INSERT INTO tasks(task_id,parent_task_id,name,status,created_at,item_count,config_json,has_redacted_secrets) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (task_id, parent_task_id, config.get("name", "result"), "pending", now,
             0, json.dumps(safe_config, ensure_ascii=False), int(redacted)),
        )
        conn.commit()
    finally:
        conn.close()
    task = {
        "task_id": task_id, "config": config, "status": "pending", "result": None,
        "file_path": None, "sqlite_path": None, "error": None,
        "created_at": now, "cancel_event": threading.Event(),
    }
    with tasks_lock:
        tasks[task_id] = task
        _evict_terminal_tasks_locked()
    return task


def _update_task(task_id: str, **values):
    allowed = {"status", "started_at", "ended_at", "item_count", "error", "file_path", "sqlite_path"}
    values = {key: value for key, value in values.items() if key in allowed}
    if not values:
        return
    clause = ", ".join(f"{key}=?" for key in values)
    conn = _task_db()
    try:
        conn.execute(f"UPDATE tasks SET {clause} WHERE task_id=?", (*values.values(), task_id))
        conn.commit()
    finally:
        conn.close()


def _get_task(task_id: str):
    conn = _task_db()
    try:
        return conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    finally:
        conn.close()


def _append_task_logs(task_id: str, lines):
    if not lines:
        return
    now = time.time()
    conn = _task_db()
    try:
        conn.executemany(
            "INSERT INTO task_logs(task_id,created_at,text) VALUES(?,?,?)",
            [(task_id, now, line) for line in lines],
        )
        conn.commit()
    finally:
        conn.close()


def _save_page_status(task_id: str, page_no: int, status: str, count: int, error: str):
    conn = _task_db()
    try:
        conn.execute(
            "INSERT INTO task_pages(task_id,page_no,status,item_count,error,updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(task_id,page_no) DO UPDATE SET status=excluded.status, "
            "item_count=excluded.item_count,error=excluded.error,updated_at=excluded.updated_at",
            (task_id, page_no, status, count, error or None, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def _init_tasks():
    _init_task_store()


_init_tasks()


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
    pending = []

    def flush():
        if pending:
            try:
                _append_task_logs(task_id, pending[:])
            except Exception as exc:
                print(f"[日志持久化失败] {exc}", file=sys.stderr)
            pending.clear()

    while not stop_event.is_set() or not log_queue.empty():
        try:
            line = log_queue.get(timeout=0.2)
            socketio.emit("crawl_log", {"task_id": task_id, "text": line + "\n"})
            pending.append(line)
            if len(pending) >= 20:
                flush()
        except _queue.Empty:
            flush()
            continue
    flush()


def _finalize_task_resources(task_id: str, config: dict, task: dict):
    """
    任务结束后的资源回收：
    - 成功完成时删除断点文件（否则 data/resume 会随任务数无限累积）
    - 结果已落盘时释放内存中的结果列表（避免 tasks 字典长期占用内存）
    """
    status = task.get("status")
    name = config.get("name", "result")
    if status == "completed":
        try:
            if _clear_checkpoint(config, name):
                print(f"[断点] 任务 {task_id} 已完成，清理断点文件")
        except Exception as exc:
            print(f"[断点] 清理失败 {task_id}: {exc}", file=sys.stderr)
    if task.get("file_path") and task.get("result") is not None:
        task["result"] = None


# ================================================================
# 后台爬取线程
# ================================================================
def run_crawl(task_id: str, config: dict):
    with tasks_lock:
        task = tasks.get(task_id)
    if task is None:
        return
    cancel_event = task["cancel_event"]
    start_time = time.time()
    task["started_at"] = start_time
    task["status"] = "cancelling" if cancel_event.is_set() else "running"
    _update_task(task_id, status=task["status"], started_at=start_time)

    log_queue: _queue.Queue = _queue.Queue()
    stop_event = threading.Event()

    def log_callback(msg: str):
        if msg.strip():
            log_queue.put(_sanitize_log_text(msg))

    def page_callback(page_no: int, status: str, count: int, error: str):
        error = _sanitize_log_text(error)
        _save_page_status(task_id, page_no, status, count, error)
        socketio.emit("crawl_page", {
            "task_id": task_id, "page": page_no, "status": status,
            "count": count, "error": error,
        })

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
                log_callback(f"[配置] 已应用自定义请求头: {list(custom_headers.keys())}")
        except Exception as e:
            log_callback(f"[警告] 创建requester失败: {e}")

    try:
        result = crawl(
            config, requester=requester, log_callback=log_callback,
            cancel_event=cancel_event, page_callback=page_callback,
            out_dir=DATA_DIR / "tasks" / task_id,
        )
        task["result"] = result
        name = config.get("name", "result")
        xlsx_path = DATA_DIR / "tasks" / task_id / f"{_safe_name(str(name))}.xlsx"
        task["file_path"] = str(xlsx_path) if result and xlsx_path.exists() else None
        task["sqlite_path"] = config.get("_sqlite_path")
        conn = _task_db()
        try:
            failed_pages = conn.execute(
                "SELECT COUNT(*) FROM task_pages WHERE task_id=? AND status='failed'", (task_id,)
            ).fetchone()[0]
        finally:
            conn.close()
        status = "cancelled" if cancel_event.is_set() else (
            "failed" if failed_pages and not result else
            "partial" if failed_pages else "completed"
        )
        task["status"] = status
        _update_task(
            task_id, status=status, item_count=len(result), file_path=task["file_path"],
            sqlite_path=task["sqlite_path"],
        )
        socketio.emit("crawl_done", {
            "task_id": task_id, "count": len(result), "status": status,
            "failed_pages": failed_pages,
        })
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        status = "cancelled" if cancel_event.is_set() else "failed"
        task["status"] = status
        safe_error = _sanitize_log_text(e)
        task["error"] = safe_error
        print(f"[爬取失败] {e}\n{tb}", file=sys.stderr)
        log_queue.put(_sanitize_log_text(f"\n[错误] {e}\n{tb}"))
        _update_task(task_id, status=status, error=safe_error)
        socketio.emit("crawl_done", {"task_id": task_id, "status": status, "error": safe_error})
    finally:
        stop_event.set()
        pusher.join(timeout=2)
        end_time = time.time()
        task["ended_at"] = end_time
        _update_task(task_id, ended_at=end_time)
        _finalize_task_resources(task_id, config, task)


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


@app.route("/api/tasks", methods=["GET"])
def list_tasks():
    conn = _task_db()
    try:
        rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 50").fetchall()
    finally:
        conn.close()
    return jsonify({"tasks": [_task_summary(row) for row in rows]})


@app.route("/api/tasks/<task_id>", methods=["GET"])
def task_detail(task_id):
    row = _get_task(task_id)
    if row is None:
        return jsonify({"error": "任务不存在"}), 404
    conn = _task_db()
    try:
        pages = conn.execute(
            "SELECT page_no,status,item_count,error,updated_at FROM task_pages "
            "WHERE task_id=? ORDER BY page_no", (task_id,)
        ).fetchall()
    finally:
        conn.close()
    summary = _task_summary(row)
    summary.update({
        "config": json.loads(row["config_json"]),
        "log": _read_task_logs(task_id),
        "pages": [dict(page) for page in pages],
    })
    return jsonify(summary)


@app.route("/api/tasks/<task_id>/cancel", methods=["POST"])
def cancel_task(task_id):
    row = _get_task(task_id)
    if row is None:
        return jsonify({"error": "任务不存在"}), 404
    if row["status"] not in ("pending", "running", "cancelling"):
        return jsonify({"error": "任务已经结束", "status": row["status"]}), 409
    with tasks_lock:
        task = tasks.get(task_id)
        if task is None:
            return jsonify({"error": "任务不在当前进程中，无法取消"}), 409
        task["cancel_event"].set()
        task["status"] = "cancelling"
    _update_task(task_id, status="cancelling")
    return jsonify({"task_id": task_id, "status": "cancelling"})


@app.route("/api/tasks/<task_id>/retry", methods=["POST"])
def retry_task(task_id):
    row = _get_task(task_id)
    if row is None:
        return jsonify({"error": "任务不存在"}), 404
    if row["status"] not in ("partial", "failed", "cancelled", "interrupted"):
        return jsonify({"error": "当前任务状态不支持重试"}), 409
    if row["has_redacted_secrets"]:
        return jsonify({
            "error": "任务配置含有已脱敏的敏感凭据。请在配置表单中补回凭据后重新启动任务。",
            "requires_credentials": True,
        }), 409
    config = json.loads(row["config_json"])
    config["resume"] = True
    config["_checkpoint_write"] = True
    if not config.get("_checkpoint_scope"):
        config["_checkpoint_scope"] = task_id
    retry_id = uuid.uuid4().hex[:12]
    _create_task_record(retry_id, config, parent_task_id=task_id)
    thread = threading.Thread(target=run_crawl, args=(retry_id, config), daemon=True)
    thread.start()
    return jsonify({"task_id": retry_id, "status": "pending", "parent_task_id": task_id})


def _task_summary(row):
    if row is None:
        return None
    path = Path(row["file_path"]) if row["file_path"] else None
    conn = _task_db()
    try:
        page_stats = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed, "
            "SUM(CASE WHEN status IN ('completed','empty','reused') THEN 1 ELSE 0 END) AS done "
            "FROM task_pages WHERE task_id=?", (row["task_id"],)
        ).fetchone()
    finally:
        conn.close()
    terminal = row["status"] in ("completed", "partial", "failed", "cancelled", "interrupted")
    retryable = row["status"] in ("partial", "failed", "cancelled", "interrupted")
    try:
        saved_config = json.loads(row["config_json"])
    except (TypeError, json.JSONDecodeError):
        saved_config = {}
    pagination = saved_config.get("pagination") or {}
    retry_action = "resume_pages" if pagination.get("type") == "url_pattern" else "rerun"
    return {
        "task_id": row["task_id"], "parent_task_id": row["parent_task_id"],
        "name": row["name"], "status": row["status"],
        "created_at": row["created_at"], "started_at": row["started_at"],
        "ended_at": row["ended_at"], "count": row["item_count"],
        "error": row["error"], "has_file": bool(path and path.exists()),
        "failed_pages": page_stats["failed"] or 0,
        "done_pages": page_stats["done"] or 0, "total_pages": page_stats["total"] or 0,
        "has_redacted_secrets": bool(row["has_redacted_secrets"]),
        "can_cancel": row["status"] in ("pending", "running", "cancelling"),
        "can_retry": retryable and not bool(row["has_redacted_secrets"]),
        "retry_action": retry_action,
        "terminal": terminal,
    }


def _read_task_logs(task_id: str, limit=1000):
    conn = _task_db()
    try:
        rows = conn.execute(
            "SELECT text FROM task_logs WHERE task_id=? ORDER BY id DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
        return "\n".join(row["text"] for row in reversed(rows))
    finally:
        conn.close()


def _result_from_csv(file_path: str):
    csv_path = Path(file_path).with_suffix(".csv")
    if not csv_path.exists():
        return [], []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames or []
        rows = []
        for row in reader:
            rows.append(row)
            if len(rows) >= 200:
                break
    return columns, rows


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
    if not isinstance(config, dict) or not config.get("name"):
        return jsonify({"error": "配置不能为空，且必须包含 name 字段"}), 400
    try:
        config = _normalize_config(config)
    except (TypeError, ValueError) as e:
        return jsonify({"error": str(e)}), 400

    task_id = uuid.uuid4().hex[:12]
    config["_checkpoint_scope"] = task_id
    config["_checkpoint_write"] = True
    try:
        _create_task_record(task_id, config)
    except Exception as exc:
        app.logger.exception("创建爬取任务记录失败")
        return jsonify({"error": f"无法保存任务：{exc}"}), 500

    t = threading.Thread(target=run_crawl, args=(task_id, config), daemon=True)
    t.start()
    return jsonify({"task_id": task_id, "status": "pending"})


@app.route("/api/status/<task_id>", methods=["GET"])
def get_status(task_id):
    row = _get_task(task_id)
    if row is None:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify(_task_summary(row))


@app.route("/api/log/<task_id>", methods=["GET"])
def get_log(task_id):
    if _get_task(task_id) is None:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify({"task_id": task_id, "log": _read_task_logs(task_id)})


@app.route("/api/result/<task_id>", methods=["GET"])
def get_result(task_id):
    row = _get_task(task_id)
    if row is None:
        return jsonify({"error": "任务不存在"}), 404
    if row["status"] not in ("completed", "partial", "cancelled"):
        return jsonify({"error": "任务尚未完成", "status": row["status"]}), 400
    with tasks_lock:
        task = tasks.get(task_id)
        result = task.get("result") if task else None
    if result is not None:
        columns = list(result[0].keys()) if result else []
        rows = result[:200]
    elif row["file_path"] and Path(row["file_path"]).exists():
        columns, rows = _result_from_csv(row["file_path"])
    else:
        columns, rows = [], []
    return jsonify({
        "count": row["item_count"], "columns": columns, "rows": rows,
        "status": row["status"],
    })


@app.route("/api/download/<task_id>", methods=["GET"])
def download_file(task_id):
    row = _get_task(task_id)
    if row is None or not row["file_path"]:
        abort(404)
    file_path = Path(row["file_path"])
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
