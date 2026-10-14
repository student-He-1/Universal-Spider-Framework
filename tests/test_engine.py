"""
引擎与凭据脱敏的单元测试（离线，不依赖网络与外部服务）

运行:
    python -m unittest tests.test_engine -v
    python tests/test_engine.py

覆盖:
    1. 续爬隔离：断点文件名随 scope / 目标 / 提取配置变化
    2. 断点读写：resume 开关、损坏行跳过、同页覆盖、无残留临时文件
    3. 断点清理：_clear_checkpoint 删除文件与临时文件
    4. 并发批量落盘：_save_checkpoints 一次写入多页
    5. 配置校验：拒绝非法 render / pagination / headers
    6. 安全文件名：_safe_name
    7. 凭据脱敏：请求头 / URL / 代理 / 日志，且无误报
    8. SQL 标识符：中文等被转写的字段名不会撞名（storage 列名消歧）
    9. 清洗条件：>= / <= 等比较运算真正生效，不再静默放行
    10. 重试退避：返回延迟调度的 Deferred，而非无效的 download_latency
"""
import os
import shutil
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for path in (str(ROOT), str(ROOT / "examples")):
    if path not in sys.path:
        sys.path.insert(0, path)

import universal_crawler as uc  # noqa: E402
from utils.helpers import sanitize_identifier  # noqa: E402
from utils.secrets import redact_task_config, sanitize_log_text  # noqa: E402


def _ensure_loguru_stub():
    """缺少 loguru 时注入最小替身，让清洗/中间件用例离线可跑且不写日志文件"""
    try:
        import loguru  # noqa: F401
        return
    except ImportError:
        pass
    stub = types.ModuleType("loguru")

    class _StubLogger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: self

    stub.logger = _StubLogger()
    sys.modules["loguru"] = stub


_ensure_loguru_stub()

try:  # 清洗规则引擎（依赖 loguru）
    from cleaner.rules.engine import CleanRule, RuleEngine, RuleType  # noqa: E402
    _HAS_CLEANER = True
except Exception:
    _HAS_CLEANER = False

try:  # Scrapy 下载中间件（依赖 scrapy + loguru）
    from core.middleware.middlewares import SmartRetryMiddleware  # noqa: E402
    _HAS_MIDDLEWARE = True
except Exception:
    _HAS_MIDDLEWARE = False


def _base_config(**overrides):
    cfg = {
        "name": "unit_test_task",
        "render": "direct",
        "pagination": {
            "type": "url_pattern",
            "url_pattern": "http://example.test/page/{page}",
            "start_page": 1,
            "max_pages": 2,
        },
        "item_selector": "div.item",
        "fields": {"标题": "h3::text"},
    }
    cfg.update(overrides)
    return cfg


class CheckpointTestCase(unittest.TestCase):
    """断点路径、读写、清理、批量落盘"""

    def setUp(self):
        self._orig_checkpoint_path = uc._checkpoint_path
        self.tmpdir = Path(tempfile.mkdtemp(prefix="ck_test_"))
        # 保留真实的摘要计算逻辑，仅把落盘目录重定向到临时目录，避免污染 data/resume
        def redirected(cfg, name):
            return self.tmpdir / self._orig_checkpoint_path(cfg, name).name
        uc._checkpoint_path = redirected

    def tearDown(self):
        uc._checkpoint_path = self._orig_checkpoint_path
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---------- 1. 隔离 ----------
    def test_scope_isolates_checkpoints(self):
        a = uc._checkpoint_path(_base_config(_checkpoint_scope="taskA"), "unit_test_task")
        b = uc._checkpoint_path(_base_config(_checkpoint_scope="taskB"), "unit_test_task")
        self.assertNotEqual(a, b, "不同 scope 必须落在不同断点文件")

    def test_same_scope_and_config_is_reusable(self):
        a = uc._checkpoint_path(_base_config(_checkpoint_scope="taskA"), "unit_test_task")
        b = uc._checkpoint_path(_base_config(_checkpoint_scope="taskA"), "unit_test_task")
        self.assertEqual(a, b, "同 scope 同配置应可复用同一断点")

    def test_changed_target_invalidates_checkpoint(self):
        a = uc._checkpoint_path(_base_config(_checkpoint_scope="taskA"), "unit_test_task")
        b = uc._checkpoint_path(
            _base_config(_checkpoint_scope="taskA", item_selector="div.other"), "unit_test_task"
        )
        self.assertNotEqual(a, b, "换提取目标应让旧断点失效")

    # ---------- 2. 读写 ----------
    def test_write_then_read_requires_resume(self):
        cfg = _base_config(_checkpoint_scope="taskC", _checkpoint_write=True)
        uc._save_checkpoint(cfg, "unit_test_task", 1, [{"标题": "A"}], [])
        uc._save_checkpoint(cfg, "unit_test_task", 2, [{"标题": "B"}], [])
        self.assertEqual(uc._load_checkpoint(cfg, "unit_test_task"), {},
                         "未开启 resume 不应读到断点")
        got = uc._load_checkpoint({**cfg, "resume": True}, "unit_test_task")
        self.assertEqual(got, {1: [{"标题": "A"}], 2: [{"标题": "B"}]})

    def test_corrupt_line_skipped_and_same_page_overwritten(self):
        cfg = _base_config(_checkpoint_scope="taskD", _checkpoint_write=True)
        uc._save_checkpoint(cfg, "unit_test_task", 1, [{"标题": "old"}], [])
        uc._save_checkpoint(cfg, "unit_test_task", 2, [{"标题": "keep"}], [])
        with open(uc._checkpoint_path(cfg, "unit_test_task"), "a", encoding="utf-8") as f:
            f.write("{这是一行损坏的 JSON\n\n")
        uc._save_checkpoint({**cfg, "resume": True}, "unit_test_task", 1, [{"标题": "new"}], [])
        got = uc._load_checkpoint({**cfg, "resume": True}, "unit_test_task")
        self.assertEqual(got[1], [{"标题": "new"}], "同页写入应以最后一次为准")
        self.assertEqual(got[2], [{"标题": "keep"}], "损坏行不应影响其它页")

    def test_no_temp_file_left_behind(self):
        cfg = _base_config(_checkpoint_scope="taskE", _checkpoint_write=True)
        for page in range(1, 6):
            uc._save_checkpoint(cfg, "unit_test_task", page, [{"标题": str(page)}], [])
        leftovers = list(self.tmpdir.glob("*.tmp"))
        self.assertEqual(leftovers, [], f"不应残留临时文件: {leftovers}")

    # ---------- 3. 清理 ----------
    def test_clear_checkpoint_removes_file_and_temp(self):
        cfg = _base_config(_checkpoint_scope="taskF", _checkpoint_write=True)
        uc._save_checkpoint(cfg, "unit_test_task", 1, [{"标题": "A"}], [])
        path = uc._checkpoint_path(cfg, "unit_test_task")
        self.assertTrue(path.exists())
        stale = self.tmpdir / (path.name + ".stale123.tmp")
        stale.write_text("x", encoding="utf-8")
        self.assertTrue(uc._clear_checkpoint(cfg, "unit_test_task"))
        self.assertFalse(path.exists(), "断点文件应被删除")
        self.assertFalse(stale.exists(), "残留临时文件应被一并清理")

    def test_clear_checkpoint_is_idempotent(self):
        cfg = _base_config(_checkpoint_scope="taskG")
        self.assertFalse(uc._clear_checkpoint(cfg, "unit_test_task"), "文件本来不存在应返回 False")

    # ---------- 4. 批量落盘 ----------
    def test_save_checkpoints_writes_batch_once(self):
        cfg = _base_config(_checkpoint_scope="taskH", _checkpoint_write=True)
        uc._save_checkpoints(cfg, "unit_test_task", {1: [{"a": 1}], 2: [{"a": 2}], 3: []})
        got = uc._load_checkpoint({**cfg, "resume": True}, "unit_test_task")
        self.assertEqual(got, {1: [{"a": 1}], 2: [{"a": 2}], 3: []})
        self.assertEqual(list(self.tmpdir.glob("*.tmp")), [], "批量写入后不应残留临时文件")

    def test_save_checkpoints_merges_with_existing(self):
        cfg = _base_config(_checkpoint_scope="taskI", _checkpoint_write=True)
        uc._save_checkpoint(cfg, "unit_test_task", 1, [{"a": 1}], [])
        uc._save_checkpoints(cfg, "unit_test_task", {2: [{"a": 2}], 3: [{"a": 3}]})
        got = uc._load_checkpoint({**cfg, "resume": True}, "unit_test_task")
        self.assertEqual(sorted(got), [1, 2, 3])

    def test_disabled_writes_nothing(self):
        cfg = _base_config(_checkpoint_scope="taskJ")  # 无 resume / _checkpoint_write
        uc._save_checkpoint(cfg, "unit_test_task", 1, [{"a": 1}], [])
        uc._save_checkpoints(cfg, "unit_test_task", {2: [{"a": 2}]})
        self.assertEqual(list(self.tmpdir.iterdir()), [], "未开启断点写入时不应产生文件")


class ConfigValidationTestCase(unittest.TestCase):
    """配置校验"""

    def _rejected(self, cfg):
        with self.assertRaises(ValueError):
            uc._normalize_config(cfg)
        return True

    def test_valid_config_gets_defaults(self):
        cfg = uc._normalize_config(_base_config())
        self.assertEqual(cfg["delay"], 0.5)
        self.assertEqual(cfg["concurrency"], 1)
        self.assertEqual(cfg["page_wait"], 3000)
        self.assertIn("pagination", cfg)

    def test_rejects_bad_render(self):
        self._rejected(_base_config(render="phantom"))

    def test_rejects_unknown_pagination_type(self):
        self._rejected(_base_config(pagination={"type": "teleport"}))

    def test_rejects_button_paging_with_direct_render(self):
        self._rejected(_base_config(pagination={"type": "next_button"}))
        self._rejected(_base_config(pagination={"type": "infinite_scroll"}))

    def test_accepts_button_paging_with_playwright(self):
        cfg = uc._normalize_config(_base_config(render="playwright",
                                                pagination={"type": "next_button"},
                                                first_url="https://example.test/list"))
        self.assertEqual(cfg["pagination"]["type"], "next_button")

    def test_rejects_button_paging_without_first_url(self):
        self._rejected(_base_config(render="playwright",
                                    pagination={"type": "next_button"}))

    def test_rejects_non_dict_config(self):
        self._rejected("not-a-dict")
        self._rejected(["list"])

    def test_rejects_non_dict_pagination(self):
        self._rejected(_base_config(pagination="url_pattern"))

    def test_rejects_bad_headers_type(self):
        self._rejected(_base_config(headers="Referer: x"))

    def test_accepts_headers_dict(self):
        cfg = uc._normalize_config(_base_config(headers={"Referer": "https://x.test/"}))
        self.assertEqual(cfg["headers"]["Referer"], "https://x.test/")


class SafeNameTestCase(unittest.TestCase):
    """安全文件名"""

    def test_strips_path_and_reserved_chars(self):
        unsafe = 'a/b:c*d?e"f<g>h|i\\j'
        safe = uc._safe_name(unsafe)
        for ch in '/\\:*?"<>|':
            self.assertNotIn(ch, safe)

    def test_empty_falls_back(self):
        self.assertEqual(uc._safe_name(""), "result")
        self.assertEqual(uc._safe_name("   "), "result")

    def test_keeps_chinese(self):
        self.assertEqual(uc._safe_name("天津气温15天"), "天津气温15天")


class RedactionTestCase(unittest.TestCase):
    """凭据脱敏（utils.secrets）"""

    def test_redacts_sensitive_headers(self):
        safe, redacted = redact_task_config({
            "name": "t",
            "headers": {"Authorization": "Bearer abc123", "Referer": "https://ok.test/"},
        })
        self.assertEqual(safe["headers"]["Authorization"], "[REDACTED]")
        self.assertEqual(safe["headers"]["Referer"], "https://ok.test/", "非敏感头不应改动")
        self.assertTrue(redacted)

    def test_redacts_url_userinfo_and_query_token(self):
        safe, redacted = redact_task_config({
            "name": "t",
            "first_url": "https://user:pass@a.test/list?token=xyz&p=1",
        })
        self.assertNotIn("user:pass", safe["first_url"])
        self.assertNotIn("xyz", safe["first_url"])
        self.assertIn("p=1", safe["first_url"], "普通参数应保留")
        self.assertTrue(redacted)

    def test_redacts_proxy_credentials(self):
        safe, redacted = redact_task_config({
            "name": "t",
            "proxy": "http://u:p@h1:8080,http://h2:8080",
        })
        self.assertNotIn("u:p", safe["proxy"])
        self.assertIn("h2:8080", safe["proxy"], "无凭据代理应原样保留")
        self.assertTrue(redacted)

    def test_no_false_positive(self):
        safe, redacted = redact_task_config({
            "name": "t",
            "headers": {"Referer": "https://ok.test/"},
            "first_url": "https://a.test/page/1",
        })
        self.assertFalse(redacted, "不含凭据不应标记为需重填")
        self.assertEqual(safe["first_url"], "https://a.test/page/1")

    def test_does_not_mutate_input(self):
        original = {"name": "t", "headers": {"Cookie": "sid=1"}}
        redact_task_config(original)
        self.assertEqual(original["headers"]["Cookie"], "sid=1", "不应修改传入对象")

    def test_sanitizes_log_text(self):
        text = sanitize_log_text("GET https://u:p@a.test/x?token=abc&k=1")
        self.assertNotIn("u:p", text)
        self.assertNotIn("abc", text)
        self.assertIn("k=1", text)


class BatchWriteConcurrencyTestCase(unittest.TestCase):
    """批量落盘在多线程下不丢页（模拟并发翻页的收尾写入）"""

    def setUp(self):
        self._orig_checkpoint_path = uc._checkpoint_path
        self.tmpdir = Path(tempfile.mkdtemp(prefix="ck_conc_"))
        def redirected(cfg, name):
            return self.tmpdir / self._orig_checkpoint_path(cfg, name).name
        uc._checkpoint_path = redirected

    def tearDown(self):
        uc._checkpoint_path = self._orig_checkpoint_path
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_concurrent_single_page_writes_do_not_lose_pages(self):
        cfg = _base_config(_checkpoint_scope="taskK", _checkpoint_write=True)
        errors = []

        def worker(page):
            try:
                uc._save_checkpoint(cfg, "unit_test_task", page, [{"a": page}], [])
            except Exception as exc:  # pragma: no cover - 失败时记录以便断言
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(p,)) for p in range(1, 21)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"并发写入不应抛异常: {errors}")
        got = uc._load_checkpoint({**cfg, "resume": True}, "unit_test_task")
        self.assertEqual(sorted(got), list(range(1, 21)), "并发写入不应丢页")
        self.assertEqual(list(self.tmpdir.glob("*.tmp")), [], "并发写入不应残留临时文件")


class SanitizeIdentifierTestCase(unittest.TestCase):
    """中文/非法字段名 → SQL 标识符（storage 列名消歧）"""

    def test_chinese_names_do_not_collide(self):
        a = sanitize_identifier("书名")
        b = sanitize_identifier("价格")
        self.assertNotEqual(a, b, "不同中文列名不能撞成同一列")
        self.assertRegex(a, r"^[A-Za-z_][A-Za-z0-9_]*$")
        self.assertRegex(b, r"^[A-Za-z_][A-Za-z0-9_]*$")

    def test_deterministic(self):
        self.assertEqual(sanitize_identifier("书名"), sanitize_identifier("书名"))

    def test_ascii_names_kept(self):
        self.assertEqual(sanitize_identifier("price"), "price")
        self.assertEqual(sanitize_identifier("Book Title"), "book_title")
        self.assertEqual(sanitize_identifier("user-name"), "user_name")
        self.assertEqual(sanitize_identifier("a.b"), "a_b")

    def test_empty_returns_empty_for_caller_fallback(self):
        self.assertEqual(sanitize_identifier(""), "")
        self.assertEqual(sanitize_identifier("   "), "")

    def test_mixed_name_keeps_ascii_and_disambiguates(self):
        a = sanitize_identifier("书名 title")
        self.assertTrue(a.startswith("title_"), a)
        self.assertNotEqual(a, sanitize_identifier("价格 title"))

    def test_length_bounded(self):
        self.assertLessEqual(len(sanitize_identifier("x" * 80)), 48)


@unittest.skipUnless(_HAS_CLEANER, "缺少 loguru 等依赖，跳过清洗规则用例")
class CleanRuleConditionTestCase(unittest.TestCase):
    """清洗过滤条件的比较运算（>= / <= 曾静默放行）"""

    def _keep(self, condition, data):
        engine = RuleEngine([CleanRule(type=RuleType.FILTER, condition=condition)])
        return engine.apply(dict(data)) is not None

    def test_greater_equal_filters_low(self):
        self.assertFalse(self._keep("score >= 60", {"score": "59"}))
        self.assertTrue(self._keep("score >= 60", {"score": "60"}))
        self.assertTrue(self._keep("score >= 60", {"score": "61"}))

    def test_less_equal_filters_high(self):
        self.assertFalse(self._keep("score <= 60", {"score": "61"}))
        self.assertTrue(self._keep("score <= 60", {"score": "60"}))
        self.assertTrue(self._keep("score <= 60", {"score": "59"}))

    def test_greater_less_still_work(self):
        self.assertFalse(self._keep("score > 60", {"score": "60"}))
        self.assertTrue(self._keep("score > 60", {"score": "61"}))
        self.assertTrue(self._keep("score < 60", {"score": "59"}))
        self.assertFalse(self._keep("score < 60", {"score": "60"}))

    def test_equality_and_contains(self):
        self.assertTrue(self._keep("city == 天津", {"city": "天津"}))
        self.assertFalse(self._keep("city == 天津", {"city": "北京"}))
        self.assertTrue(self._keep("city contains 津", {"city": "天津"}))
        self.assertFalse(self._keep("city != 天津", {"city": "天津"}))

    def test_non_numeric_is_kept(self):
        self.assertTrue(self._keep("score >= 60", {"score": "未知"}),
                        "无法数值比较时应保留而非丢弃")


@unittest.skipUnless(_HAS_MIDDLEWARE, "缺少 scrapy 等依赖，跳过重试中间件用例")
class RetryDelayTestCase(unittest.TestCase):
    """重试退避应真实调度延迟（Deferred），而非写无效的 download_latency"""

    def setUp(self):
        from scrapy.http import Request
        from scrapy.settings import Settings
        self._Request = Request
        self.mw = SmartRetryMiddleware(Settings())
        self.mw.max_retry_times = 3

    def test_retry_returns_delayed_deferred(self):
        from twisted.internet.defer import Deferred
        req = self._Request("http://example.test/a")
        retry = self.mw._retry(req, "500 Internal Server Error", spider=None)
        self.assertIsInstance(retry, Deferred, "重试应调度延迟后返回 Deferred")
        self.assertNotIn("download_latency", req.meta, "不应再用无效的 download_latency 假延时")

    def test_process_response_retries_500(self):
        from scrapy.http import Response
        from twisted.internet.defer import Deferred
        req = self._Request("http://example.test/a")
        resp = Response("http://example.test/a", status=500)
        out = self.mw.process_response(req, resp, spider=None)
        self.assertIsInstance(out, Deferred)

    def test_gives_up_after_max_retries(self):
        req = self._Request("http://example.test/a", meta={"retry_times": 3})
        self.assertIsNone(self.mw._retry(req, "500 Internal Server Error", spider=None))

    def test_dont_retry_returns_response(self):
        from scrapy.http import Response
        req = self._Request("http://example.test/a", meta={"dont_retry": True})
        resp = Response("http://example.test/a", status=500)
        self.assertIs(self.mw.process_response(req, resp, spider=None), resp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
