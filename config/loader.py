import yaml
from pathlib import Path

from backend.config.schema import AppConfig, ProviderItem

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def load_raw() -> dict:
    """Load raw YAML as dict — for dynamic provider keys."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"provider": "claude", "providers": {}}


def dump_raw(data: dict) -> None:
    """Write raw dict back to YAML."""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def load_config() -> AppConfig:
    """Parse YAML into AppConfig (validates protocols)."""
    raw = load_raw()
    return AppConfig(**raw)


def write_config(cfg: AppConfig) -> None:
    """Dump AppConfig back to YAML."""
    dump_raw(cfg.model_dump())


def get_database_url() -> str:
    """Get database connection URL from config."""
    cfg = load_config()
    return cfg.database.url


def mask_api_key(key: str) -> str:
    """Return a masked version of an API key for frontend display."""
    if not key or len(key) < 8:
        return "***"
    return key[:3] + "***" + key[-4:]
