from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.config.loader import load_raw, dump_raw, mask_api_key
from backend.config.schema import SUPPORTED_PROTOCOLS

router = APIRouter()


# ── Request / Response models ────────────────────────────────────────────

class ProviderItemOut(BaseModel):
    protocol: str
    model: str
    base_url: str
    api_key_masked: str


class ProviderItemIn(BaseModel):
    protocol: str = Field(..., pattern="^(anthropic|openai)$")
    model: str
    base_url: str
    api_key: str = ""


class ConfigUpdate(BaseModel):
    """Full config replacement body."""
    provider: Optional[str] = None
    providers: Optional[Dict[str, ProviderItemIn]] = None


@router.get("/config")
async def api_get_config():
    raw = load_raw()
    providers_out = {}
    for name, p in raw.get("providers", {}).items():
        providers_out[name] = ProviderItemOut(
            protocol=p.get("protocol", ""),
            model=p.get("model", ""),
            base_url=p.get("base_url", ""),
            api_key_masked=mask_api_key(p.get("api_key", "")),
        )
    return {
        "provider": raw.get("provider", "claude"),
        "providers": providers_out,
    }


@router.put("/config")
async def api_update_config(update: ConfigUpdate):
    raw = load_raw()

    if update.provider is not None:
        raw["provider"] = update.provider

    if update.providers is not None:
        existing = raw.setdefault("providers", {})
        for name, incoming in update.providers.items():
            current = existing.get(name, {})
            merged = {
                "protocol": incoming.protocol or current.get("protocol", ""),
                "model": incoming.model or current.get("model", ""),
                "base_url": incoming.base_url or current.get("base_url", ""),
                "api_key": current.get("api_key", ""),
            }
            # Only replace api_key if user sent a non-masked, non-empty value
            if incoming.api_key and "***" not in incoming.api_key:
                merged["api_key"] = incoming.api_key
            existing[name] = merged

    # Clean up empty providers dict
    if not raw.get("providers"):
        raw["providers"] = {}

    dump_raw(raw)
    return {"ok": True}


@router.delete("/config/provider/{name}")
async def api_delete_provider(name: str):
    raw = load_raw()
    providers = raw.get("providers", {})
    if name not in providers:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    del providers[name]

    # If active provider was deleted, reset to first available or "claude"
    if raw.get("provider") == name:
        raw["provider"] = next(iter(providers.keys()), "claude")

    dump_raw(raw)
    return {"ok": True}
