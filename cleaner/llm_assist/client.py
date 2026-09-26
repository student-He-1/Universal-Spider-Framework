"""
LLM 辅助清洗
用于非结构化文本的结构化提取、字段补全、内容分类等
支持 DeepSeek / 千问(DashScope) / 本地 OpenAI 兼容模型
规则能处理的不走 LLM，LLM 只处理规则搞不定的部分
"""
import json
import re
from typing import Optional, Dict, Any, List
from abc import ABC, abstractmethod

from config.settings import settings
from utils.logger import get_logger

logger = get_logger("llm_clean")


class BaseLLMClient(ABC):
    """LLM 客户端基类"""

    @abstractmethod
    def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """发送聊天请求，返回文本"""
        pass


class DeepSeekClient(BaseLLMClient):
    """DeepSeek API 客户端（OpenAI 兼容）"""

    def __init__(self):
        from openai import OpenAI
        self.client = OpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
        )
        self.model = settings.DEEPSEEK_MODEL

    def chat(self, messages, **kwargs) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=kwargs.get("temperature", 0.1),
            max_tokens=kwargs.get("max_tokens", 2048),
        )
        return resp.choices[0].message.content


class QwenClient(BaseLLMClient):
    """千问 DashScope 客户端"""

    def __init__(self):
        import dashscope
        dashscope.api_key = settings.DASHSCOPE_API_KEY
        self.model = settings.QWEN_MODEL

    def chat(self, messages, **kwargs) -> str:
        import dashscope
        resp = dashscope.Generation.call(
            model=self.model,
            messages=messages,
            temperature=kwargs.get("temperature", 0.1),
            max_tokens=kwargs.get("max_tokens", 2048),
            result_format="message",
        )
        return resp.output.choices[0].message.content


class LocalLLMClient(BaseLLMClient):
    """本地模型客户端（OpenAI 兼容接口，如 Ollama / vLLM）"""

    def __init__(self):
        from openai import OpenAI
        self.client = OpenAI(
            api_key=settings.LOCAL_LLM_API_KEY,
            base_url=settings.LOCAL_LLM_BASE_URL,
        )
        self.model = settings.LOCAL_LLM_MODEL

    def chat(self, messages, **kwargs) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=kwargs.get("temperature", 0.1),
            max_tokens=kwargs.get("max_tokens", 2048),
        )
        return resp.choices[0].message.content


def get_llm_client(provider: str = None) -> Optional[BaseLLMClient]:
    """
    工厂方法获取 LLM 客户端
    provider: deepseek / qwen / local / None(自动选择)
    """
    provider = provider or settings.LLM_PROVIDER

    try:
        if provider == "deepseek" and settings.DEEPSEEK_API_KEY:
            return DeepSeekClient()
        elif provider == "qwen" and settings.DASHSCOPE_API_KEY:
            return QwenClient()
        elif provider == "local":
            return LocalLLMClient()
    except Exception as e:
        logger.warning(f"{provider} 客户端初始化失败: {e}")

    # 自动降级：按优先级尝试
    for p, cls, key_check in [
        ("deepseek", DeepSeekClient, lambda: bool(settings.DEEPSEEK_API_KEY)),
        ("qwen", QwenClient, lambda: bool(settings.DASHSCOPE_API_KEY)),
        ("local", LocalLLMClient, lambda: True),
    ]:
        try:
            if key_check():
                logger.info(f"自动选择 LLM 提供商: {p}")
                return cls()
        except Exception:
            continue

    logger.warning("没有可用的 LLM 客户端，LLM 辅助清洗将被跳过")
    return None


# ==================== 清洗任务 ====================

class LLMCleaner:
    """LLM 辅助清洗器"""

    def __init__(self, provider: str = None):
        self.client = get_llm_client(provider)

    @property
    def available(self) -> bool:
        return self.client is not None

    def extract_structured(
        self,
        text: str,
        fields: List[str],
        description: str = "",
    ) -> Dict[str, Any]:
        """
        从非结构化文本中提取结构化字段
        fields: 要提取的字段名列表
        description: 额外说明
        返回: {field: value} 字典
        """
        if not self.available or not text:
            return {}

        fields_str = ", ".join(fields)
        system_prompt = (
            "你是一个数据提取专家。请从给定文本中提取指定字段，"
            "以 JSON 格式返回，只返回 JSON，不要有其他文字。"
            f"需要提取的字段: {fields_str}"
        )
        if description:
            system_prompt += f"\n额外说明: {description}"

        user_prompt = f"文本内容:\n{text[:8000]}"  # 限制长度

        try:
            result = self.client.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ])
            return self._parse_json(result)
        except Exception as e:
            logger.error(f"LLM 结构化提取失败: {e}")
            return {}

    def classify(
        self,
        text: str,
        categories: List[str],
        description: str = "",
    ) -> str:
        """
        文本分类
        返回: 分类结果（categories 中的一个）
        """
        if not self.available or not text:
            return ""

        cat_str = ", ".join(categories)
        system_prompt = (
            "你是一个文本分类专家。请将给定文本分类到以下类别之一，"
            f"只返回类别名称，不要有其他文字。类别: {cat_str}"
        )
        if description:
            system_prompt += f"\n说明: {description}"

        try:
            result = self.client.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text[:5000]},
            ])
            result = result.strip().strip('"').strip("'")
            # 匹配最接近的类别
            for cat in categories:
                if cat in result:
                    return cat
            return result
        except Exception as e:
            logger.error(f"LLM 分类失败: {e}")
            return ""

    def normalize_text(self, text: str, target_format: str = "") -> str:
        """
        文本规范化：统一格式、纠错、补全
        """
        if not self.available or not text:
            return text

        system_prompt = "你是一个文本规范化专家。请将输入文本规范化，只返回规范化后的文本。"
        if target_format:
            system_prompt += f"目标格式: {target_format}"

        try:
            result = self.client.chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text[:3000]},
            ])
            return result.strip()
        except Exception as e:
            logger.error(f"LLM 文本规范化失败: {e}")
            return text

    def _parse_json(self, text: str) -> Dict:
        """从 LLM 输出中解析 JSON"""
        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试提取 ```json ... ``` 块
        m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

        # 尝试提取第一个 { 到最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass

        logger.warning(f"无法解析 LLM JSON 输出: {text[:200]}")
        return {}
