"""
清洗规则引擎
支持正则提取、字段映射、格式化、去重、过滤等规则
规则可配置化，通过 YAML/JSON 定义
"""
import re
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum

from utils.helpers import clean_text
from utils.logger import get_logger

logger = get_logger("clean_rules")


class RuleType(Enum):
    """规则类型"""
    FIELD_MAP = "field_map"        # 字段重命名
    DROP = "drop"                  # 删除字段
    REGEX_EXTRACT = "regex_extract"  # 正则提取
    REPLACE = "replace"            # 文本替换
    TRIM = "trim"                  # 去除空白
    TYPE_CAST = "type_cast"        # 类型转换
    FILTER = "filter"              # 过滤（不满足条件丢弃整条）
    DEFAULT = "default"            # 默认值填充
    SPLIT = "split"                # 分割
    JOIN = "join"                  # 合并字段


@dataclass
class CleanRule:
    """单条清洗规则"""
    type: RuleType
    source: str = ""           # 源字段
    target: str = ""           # 目标字段
    pattern: str = ""          # 正则/匹配模式
    replacement: str = ""      # 替换内容
    cast_type: str = ""        # 目标类型: int/float/str/bool
    default: Any = None        # 默认值
    separator: str = ""        # 分割/合并分隔符
    condition: str = ""        # 过滤条件（简单表达式）
    group: int = 1             # 正则提取分组


class RuleEngine:
    """规则引擎：按顺序执行规则列表"""

    def __init__(self, rules: List[CleanRule] = None):
        self.rules = rules or []

    def add_rule(self, rule: CleanRule):
        self.rules.append(rule)

    def apply(self, data: Dict) -> Optional[Dict]:
        """
        对单条数据应用所有规则
        返回 None 表示该条数据被过滤掉
        """
        result = dict(data)
        for rule in self.rules:
            result = self._apply_rule(result, rule)
            if result is None:
                return None
        return result

    def _apply_rule(self, data: Dict, rule: CleanRule) -> Optional[Dict]:
        try:
            if rule.type == RuleType.FIELD_MAP:
                if rule.source in data:
                    data[rule.target or rule.source] = data.pop(rule.source)
            elif rule.type == RuleType.DROP:
                data.pop(rule.source, None)
            elif rule.type == RuleType.REGEX_EXTRACT:
                value = data.get(rule.source, "")
                if value:
                    m = re.search(rule.pattern, str(value))
                    if m:
                        data[rule.target or rule.source] = m.group(rule.group)
            elif rule.type == RuleType.REPLACE:
                value = data.get(rule.source, "")
                if value:
                    data[rule.target or rule.source] = re.sub(
                        rule.pattern, rule.replacement, str(value)
                    )
            elif rule.type == RuleType.TRIM:
                target = rule.target or rule.source
                if target in data:
                    data[target] = clean_text(str(data[target]))
            elif rule.type == RuleType.TYPE_CAST:
                target = rule.target or rule.source
                if target in data:
                    data[target] = self._cast(data[target], rule.cast_type)
            elif rule.type == RuleType.DEFAULT:
                target = rule.target or rule.source
                if not data.get(target):
                    data[target] = rule.default
            elif rule.type == RuleType.SPLIT:
                value = data.get(rule.source, "")
                if value and rule.separator:
                    data[rule.target or rule.source] = str(value).split(rule.separator)
            elif rule.type == RuleType.JOIN:
                sources = rule.source.split(",")
                parts = [str(data.get(s.strip(), "")) for s in sources]
                data[rule.target] = rule.separator.join(parts)
            elif rule.type == RuleType.FILTER:
                if not self._eval_condition(data, rule.condition):
                    return None
        except Exception as e:
            logger.warning(f"规则执行失败 [{rule.type}]: {e}")
        return data

    def _cast(self, value: Any, cast_type: str) -> Any:
        """类型转换"""
        try:
            if cast_type == "int":
                return int(float(str(value).replace(",", "")))
            elif cast_type == "float":
                return float(str(value).replace(",", ""))
            elif cast_type == "str":
                return str(value)
            elif cast_type == "bool":
                return str(value).lower() in ("true", "1", "yes", "是")
        except (ValueError, TypeError):
            return value
        return value

    def _eval_condition(self, data: Dict, condition: str) -> bool:
        """
        简单条件评估
        支持: field == value, field != value, field > value, field contains value
        """
        if not condition:
            return True
        try:
            # 简单解析：field op value
            for op in ["==", "!=", ">=", "<=", ">", "<", "contains"]:
                if op in condition:
                    parts = condition.split(op, 1)
                    field = parts[0].strip()
                    value = parts[1].strip().strip("'\"")
                    field_val = str(data.get(field, ""))
                    if op == "==":
                        return field_val == value
                    elif op == "!=":
                        return field_val != value
                    elif op == ">":
                        return float(field_val) > float(value)
                    elif op == "<":
                        return float(field_val) < float(value)
                    elif op == "contains":
                        return value in field_val
        except Exception:
            return True
        return True


def load_rules_from_dict(rules_config: List[Dict]) -> RuleEngine:
    """从字典列表加载规则"""
    rules = []
    for rc in rules_config:
        rule = CleanRule(
            type=RuleType(rc["type"]),
            source=rc.get("source", ""),
            target=rc.get("target", ""),
            pattern=rc.get("pattern", ""),
            replacement=rc.get("replacement", ""),
            cast_type=rc.get("cast_type", ""),
            default=rc.get("default"),
            separator=rc.get("separator", ""),
            condition=rc.get("condition", ""),
            group=rc.get("group", 1),
        )
        rules.append(rule)
    return RuleEngine(rules)
