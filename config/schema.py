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


class AppConfig(BaseModel):
    """顶层配置：active_provider + 供应商字典。"""
    provider: str = "claude"
    providers: Dict[str, ProviderItem] = Field(default_factory=dict)
