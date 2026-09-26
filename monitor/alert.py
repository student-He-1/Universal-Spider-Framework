"""
异常告警
支持钉钉、飞书、邮件等渠道
监控关键指标，异常时自动推送
"""
import json
import time
from typing import Optional, List
from abc import ABC, abstractmethod
from utils.logger import get_logger

logger = get_logger("alert")


class BaseAlertChannel(ABC):
    """告警渠道基类"""

    @abstractmethod
    def send(self, title: str, content: str, level: str = "warning"):
        pass


class DingTalkAlert(BaseAlertChannel):
    """钉钉机器人告警"""

    def __init__(self, webhook: str, secret: str = ""):
        self.webhook = webhook
        self.secret = secret

    def send(self, title: str, content: str, level: str = "warning"):
        import requests
        import hashlib
        import hmac
        import base64
        from urllib.parse import quote_plus

        timestamp = str(round(time.time() * 1000))
        url = self.webhook

        if self.secret:
            string_to_sign = f"{timestamp}\n{self.secret}"
            hmac_code = hmac.new(
                self.secret.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                digestmod=hashlib.sha256,
            ).digest()
            sign = quote_plus(base64.b64encode(hmac_code))
            url = f"{self.webhook}&timestamp={timestamp}&sign={sign}"

        emoji = "⚠️" if level == "warning" else "🔴" if level == "critical" else "ℹ️"
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": f"### {emoji} {title}\n\n{content}\n\n> 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            },
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.error(f"钉钉告警发送失败: {resp.text}")
        except Exception as e:
            logger.error(f"钉钉告警异常: {e}")


class FeishuAlert(BaseAlertChannel):
    """飞书机器人告警"""

    def __init__(self, webhook: str):
        self.webhook = webhook

    def send(self, title: str, content: str, level: str = "warning"):
        import requests
        emoji = "⚠️" if level == "warning" else "🔴" if level == "critical" else "ℹ️"
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": f"{emoji} {title}"},
                    "template": "red" if level == "critical" else "orange",
                },
                "elements": [
                    {"tag": "markdown", "content": content},
                    {"tag": "note", "elements": [
                        {"tag": "plain_text", "content": f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}"}
                    ]},
                ],
            },
        }
        try:
            resp = requests.post(self.webhook, json=payload, timeout=10)
            if resp.status_code != 200:
                logger.error(f"飞书告警发送失败: {resp.text}")
        except Exception as e:
            logger.error(f"飞书告警异常: {e}")


class EmailAlert(BaseAlertChannel):
    """邮件告警"""

    def __init__(self, smtp_host: str, smtp_port: int, username: str, password: str, to_addrs: List[str]):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.to_addrs = to_addrs

    def send(self, title: str, content: str, level: str = "warning"):
        import smtplib
        from email.mime.text import MIMEText
        from email.header import Header

        msg = MIMEText(content, "plain", "utf-8")
        msg["From"] = self.username
        msg["To"] = ", ".join(self.to_addrs)
        msg["Subject"] = Header(f"[爬虫告警] {title}", "utf-8")

        try:
            server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port)
            server.login(self.username, self.password)
            server.sendmail(self.username, self.to_addrs, msg.as_string())
            server.quit()
        except Exception as e:
            logger.error(f"邮件告警异常: {e}")


class AlertManager:
    """
    告警管理器
    支持多渠道，限流（避免告警风暴）
    """

    _instance = None
    _channels: List[BaseAlertChannel] = []
    _last_alert: dict = {}  # 限流记录

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def add_channel(self, channel: BaseAlertChannel):
        self._channels.append(channel)

    def alert(
        self,
        title: str,
        content: str,
        level: str = "warning",
        dedup_key: str = "",
        interval: int = 300,
    ):
        """
        发送告警
        dedup_key: 去重键，相同键在 interval 秒内不重复发送
        """
        # 限流
        if dedup_key:
            now = time.time()
            last = self._last_alert.get(dedup_key, 0)
            if now - last < interval:
                return
            self._last_alert[dedup_key] = now

        logger.warning(f"[告警/{level}] {title}: {content}")

        for channel in self._channels:
            try:
                channel.send(title, content, level)
            except Exception as e:
                logger.error(f"告警渠道发送失败: {e}")

    def alert_low_success_rate(self, site: str, rate: float, threshold: float = 0.8):
        """成功率过低告警"""
        if rate < threshold:
            self.alert(
                title=f"站点 [{site}] 成功率过低",
                content=f"当前成功率: {rate:.1%}，低于阈值 {threshold:.0%}\n请检查代理可用性、目标站点反爬策略",
                level="critical",
                dedup_key=f"low_success_{site}",
            )

    def alert_proxy_exhausted(self, available: int):
        """代理耗尽告警"""
        if available == 0:
            self.alert(
                title="代理池已耗尽",
                content="当前没有可用代理，所有请求将直连\n请及时补充代理或检查代理健康状态",
                level="critical",
                dedup_key="proxy_exhausted",
            )

    def alert_queue_backlog(self, site: str, size: int, threshold: int = 10000):
        """队列积压告警"""
        if size > threshold:
            self.alert(
                title=f"站点 [{site}] 队列积压",
                content=f"当前队列长度: {size}，超过阈值 {threshold}\n建议增加爬虫实例或降低抓取速率",
                level="warning",
                dedup_key=f"queue_backlog_{site}",
            )
