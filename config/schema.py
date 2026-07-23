"""YAML 配置 schema — 支持自定义供应商名称。"""

from typing import Dict

from pydantic import BaseModel, Field

# 限制 protocol 只能是这两种
SUPPORTED_PROTOCOLS = {"anthropic", "openai"}


class ProviderItem(BaseModel):
    """单个供应商配置。"""
    protocol: str = ""
    model: str = ""
    base_url: str = ""
    api_key: str = ""


class DatabaseConfig(BaseModel):
    """数据库连接配置。"""
    # 默认值仅作开发占位；生产环境请在 config.yaml 中配置真实地址。
    url: str = "postgresql+asyncpg://user:password@localhost:5432/phenomics"


class AppConfig(BaseModel):
    """顶层配置：数据库 + active_provider + 供应商字典。"""
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    provider: str = "claude"
    providers: Dict[str, ProviderItem] = Field(default_factory=dict)
