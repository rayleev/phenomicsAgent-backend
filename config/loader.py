import yaml
from pathlib import Path

from backend.config.schema import AppConfig, ProviderItem

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"

# ── Raw-config cache (M9) ─────────────────────────────────────────────
# Parsing YAML on every request is wasteful (GET/PUT config, get_database_url).
# Cache the parsed raw dict and invalidate on write.
_raw_cache: dict | None = None


def load_raw() -> dict:
    """Load raw YAML as dict — for dynamic provider keys (cached)."""
    global _raw_cache
    if _raw_cache is None:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            _raw_cache = yaml.safe_load(f) or {"provider": "claude", "providers": {}}
    return _raw_cache


def _invalidate_cache() -> None:
    global _raw_cache
    _raw_cache = None


def dump_raw(data: dict) -> None:
    """Write raw dict back to YAML (invalidates cache)."""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    _invalidate_cache()


def load_config() -> AppConfig:
    """Parse YAML into AppConfig (validates protocols)."""
    raw = load_raw()
    return AppConfig(**raw)


def write_config(cfg: AppConfig) -> None:
    """Dump AppConfig back to YAML (invalidates cache)."""
    dump_raw(cfg.model_dump())


def get_database_url() -> str:
    """Get database connection URL from config."""
    cfg = load_config()
    return cfg.database.url


def mask_api_key(key: str) -> str:
    """Return a masked version of an API key for frontend display.

    Only the first 3 chars are shown; the rest is fully hidden (M7).
    A partial prefix is enough to let users distinguish keys without
    leaking enough characters to meaningfully aid brute-force.
    """
    if not key or len(key) < 8:
        return "***"
    return key[:3] + "***"
