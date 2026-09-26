"""
数据转换器：pandas / polars 批量处理
用于大批量数据的清洗、转换、聚合
polars 优先（性能更好），pandas 作为兜底
"""
from typing import List, Dict, Optional, Callable
from utils.logger import get_logger

logger = get_logger("transformer")


class DataTransformer:
    """
    数据转换器
    优先使用 polars，不可用时回退 pandas
    """

    def __init__(self, backend: str = "auto"):
        """
        backend: polars / pandas / auto
        """
        self.backend = backend
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            if self.backend == "polars":
                import polars as pl
                self._engine = "polars"
            elif self.backend == "pandas":
                import pandas as pd
                self._engine = "pandas"
            else:  # auto
                try:
                    import polars as pl
                    self._engine = "polars"
                except ImportError:
                    import pandas as pd
                    self._engine = "pandas"
            logger.info(f"数据转换后端: {self._engine}")
        return self._engine

    def to_dataframe(self, data: List[Dict]):
        """将字典列表转为 DataFrame"""
        engine = self._get_engine()
        if engine == "polars":
            import polars as pl
            return pl.DataFrame(data)
        else:
            import pandas as pd
            return pd.DataFrame(data)

    def to_records(self, df) -> List[Dict]:
        """将 DataFrame 转为字典列表"""
        engine = self._get_engine()
        if engine == "polars":
            return df.to_dicts()
        else:
            return df.to_dict(orient="records")

    def clean_batch(
        self,
        data: List[Dict],
        field_mapping: Dict[str, str] = None,
        drop_fields: List[str] = None,
        text_fields: List[str] = None,
        numeric_fields: List[str] = None,
        dedup_by: str = "",
    ) -> List[Dict]:
        """
        批量清洗
        field_mapping: 字段重命名 {old: new}
        drop_fields: 要删除的字段列表
        text_fields: 需要清理空白的文本字段
        numeric_fields: 需要转为数字的字段
        dedup_by: 按此字段去重
        """
        if not data:
            return []

        df = self.to_dataframe(data)
        engine = self._get_engine()

        # 字段重命名
        if field_mapping:
            if engine == "polars":
                df = df.rename(field_mapping)
            else:
                df = df.rename(columns=field_mapping)

        # 删除字段
        if drop_fields:
            existing = [f for f in drop_fields if f in df.columns]
            if existing:
                if engine == "polars":
                    df = df.drop(existing)
                else:
                    df = df.drop(columns=existing)

        # 文本清理
        if text_fields:
            for field in text_fields:
                if field in df.columns:
                    if engine == "polars":
                        import polars as pl
                        df = df.with_columns(
                            pl.col(field).cast(pl.Utf8).str.strip_chars().str.replace_all(r"\s+", " ")
                        )
                    else:
                        df[field] = df[field].astype(str).str.strip().str.replace(r"\s+", " ", regex=True)

        # 数字转换
        if numeric_fields:
            for field in numeric_fields:
                if field in df.columns:
                    if engine == "polars":
                        import polars as pl
                        df = df.with_columns(
                            pl.col(field).cast(pl.Utf8).str.replace_all(",", "").cast(pl.Float64, strict=False)
                        )
                    else:
                        df[field] = df[field].astype(str).str.replace(",", "", regex=False)
                        df[field] = df[field].apply(pd.to_numeric, errors="coerce")

        # 去重
        if dedup_by and dedup_by in df.columns:
            if engine == "polars":
                df = df.unique(subset=[dedup_by], keep="first")
            else:
                df = df.drop_duplicates(subset=[dedup_by], keep="first")

        return self.to_records(df)

    def export_parquet(self, data: List[Dict], filepath: str):
        """导出为 Parquet 文件"""
        df = self.to_dataframe(data)
        engine = self._get_engine()
        if engine == "polars":
            df.write_parquet(filepath)
        else:
            df.to_parquet(filepath, index=False)
        logger.info(f"已导出 Parquet: {filepath} ({len(data)} 条)")
