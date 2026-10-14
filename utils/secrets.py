"""
凭据脱敏工具（纯函数，无框架依赖）
- redact_task_config: 脱敏任务配置中的敏感请求头、带凭据的代理及 URL 认证信息
- sanitize_log_text: 脱敏日志文本中的 URL 认证信息与敏感查询参数

从 web/app.py 抽出，便于单独测试与在两条采集路径复用。
"""
import json
import re

# 请求头中视为敏感的键名特征（小写子串匹配）
SENSITIVE_HEADER_MARKERS = (
    "authorization", "auth", "cookie", "session", "csrf", "token",
    "secret", "password", "api-key", "apikey", "x-key",
)

# URL 查询串中视为敏感的键名特征
_SENSITIVE_QUERY_PATTERN = re.compile(
    r"([?&][^=&#]*(?:token|api[_-]?key|secret|password|auth|cookie)[^=&#]*=)[^&#\s]*",
    re.IGNORECASE,
)

# URL 中的 userinfo（user:pass@host）
_URL_USERINFO_PATTERN = re.compile(r"(https?://)[^/@\s]+@", re.IGNORECASE)


def redact_task_config(config: dict):
    """
    脱敏任务配置，返回 (safe_config, redacted)。
    redacted 为 True 表示配置中含被脱敏的凭据（此类任务不可直接重试）。
    不修改传入对象。
    """
    safe = json.loads(json.dumps(config, ensure_ascii=False, default=str))
    redacted = False

    headers = safe.get("headers") or {}
    for key in list(headers):
        if any(marker in str(key).lower() for marker in SENSITIVE_HEADER_MARKERS):
            headers[key] = "[REDACTED]"
            redacted = True
    safe["headers"] = headers

    def redact_url_values(value, key=""):
        nonlocal redacted
        if isinstance(value, dict):
            return {k: redact_url_values(v, str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [redact_url_values(item, key) for item in value]
        if not isinstance(value, str) or not ("url" in key.lower() or "pattern" in key.lower()):
            return value
        userinfo_scrubbed, userinfo_count = _URL_USERINFO_PATTERN.subn(r"\1[REDACTED]@", value)
        redacted = redacted or bool(userinfo_count)
        scrubbed, n = _SENSITIVE_QUERY_PATTERN.subn(r"\1[REDACTED]", userinfo_scrubbed)
        redacted = redacted or bool(n)
        return scrubbed

    safe = redact_url_values(safe)

    def redact_proxy(value):
        nonlocal redacted
        if not isinstance(value, str):
            return value
        parts = value.split("://", 1)
        scheme = parts[0] + "://" if len(parts) == 2 else ""
        remainder = parts[1] if len(parts) == 2 else value
        if "@" in remainder:
            redacted = True
            return scheme + "[REDACTED]@" + remainder.rsplit("@", 1)[1]
        return value

    for key in ("proxy", "proxies"):
        value = safe.get(key)
        if isinstance(value, str):
            safe[key] = redact_proxy(value) if "," not in value else ",".join(
                redact_proxy(part.strip()) for part in value.split(",")
            )
        elif isinstance(value, list):
            safe[key] = [redact_proxy(item) for item in value]
    return safe, redacted


def sanitize_log_text(value) -> str:
    """脱敏日志文本：URL 认证信息与敏感查询参数"""
    text = str(value)
    text = _SENSITIVE_QUERY_PATTERN.sub(r"\1[REDACTED]", text)
    return _URL_USERINFO_PATTERN.sub(r"\1[REDACTED]@", text)
