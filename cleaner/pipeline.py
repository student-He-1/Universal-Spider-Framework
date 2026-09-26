"""
清洗流水线编排
串联：规则引擎 → 数据转换器 → LLM 辅助 → 输出
支持单条和批量清洗
"""
from typing import Dict, List, Optional
from cleaner.rules.engine import RuleEngine, load_rules_from_dict
from cleaner.transformers.data_transformer import DataTransformer
from cleaner.llm_assist.client import LLMCleaner
from utils.logger import get_logger

logger = get_logger("clean_pipeline")


class CleanPipeline:
    """
    清洗流水线
    每个站点可以有独立的清洗配置
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.transformer = DataTransformer(backend="auto")
        self.llm = LLMCleaner()
        self._rule_engines: Dict[str, RuleEngine] = {}  # site -> engine

    def register_rules(self, site_name: str, rules_config: List[Dict]):
        """为站点注册清洗规则"""
        self._rule_engines[site_name] = load_rules_from_dict(rules_config)
        logger.info(f"站点 [{site_name}] 注册了 {len(rules_config)} 条清洗规则")

    def clean(self, data: Dict, site_name: str = "default") -> Optional[Dict]:
        """
        清洗单条数据
        返回 None 表示被过滤
        """
        result = dict(data)

        # 1. 规则引擎清洗
        engine = self._rule_engines.get(site_name)
        if engine:
            result = engine.apply(result)
            if result is None:
                return None

        # 2. 基础文本清理（所有字符串字段）
        for key, value in list(result.items()):
            if isinstance(value, str):
                from utils.helpers import clean_text
                result[key] = clean_text(value)

        # 3. LLM 辅助（仅当配置了 LLM 提取字段时触发）
        # 这里不自动调用，由站点爬虫主动调用 llm_extract 方法
        # 避免每条数据都消耗 LLM token

        return result

    def clean_batch(self, items: List[Dict], site_name: str = "default") -> List[Dict]:
        """批量清洗"""
        results = []
        for item in items:
            cleaned = self.clean(item, site_name)
            if cleaned is not None:
                results.append(cleaned)

        # 批量转换（去重等）
        if results:
            results = self.transformer.clean_batch(
                results,
                dedup_by="url",
            )

        return results

    def llm_extract(
        self,
        data: Dict,
        fields: List[str],
        source_field: str = "",
        description: str = "",
    ) -> Dict:
        """
        对单条数据执行 LLM 辅助提取
        fields: 要提取的字段
        source_field: 从哪个字段读取文本（默认用 description 或全部文本）
        返回: 合并了 LLM 提取结果的新字典
        """
        if not self.llm.available:
            return data

        text = data.get(source_field, "") if source_field else str(data)
        if not text:
            return data

        extracted = self.llm.extract_structured(text, fields, description)
        if extracted:
            result = dict(data)
            result.update(extracted)
            return result
        return data

    def llm_classify(
        self,
        data: Dict,
        categories: List[str],
        source_field: str = "",
        target_field: str = "category",
        description: str = "",
    ) -> Dict:
        """对单条数据执行 LLM 分类"""
        if not self.llm.available:
            return data

        text = data.get(source_field, "") if source_field else str(data)
        if not text:
            return data

        category = self.llm.classify(text, categories, description)
        if category:
            result = dict(data)
            result[target_field] = category
            return result
        return data
