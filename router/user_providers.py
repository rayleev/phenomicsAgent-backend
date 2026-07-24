"""User-level LLM provider configuration routes."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from auth.crypto import encrypt_api_key, decrypt_api_key
from auth.deps import get_current_user
from db.crud import (
    list_user_providers,
    get_user_provider,
    upsert_user_provider,
    delete_user_provider,
    set_active_provider,
    get_active_provider,
)
from db.models import User
from db.session import get_session
from config.loader import mask_api_key

router = APIRouter(prefix="/user/providers", tags=["user-providers"])


class ProviderOut(BaseModel):
    id: str
    provider_name: str
    protocol: str
    model: str
    base_url: str
    api_key_masked: str
    is_active: bool
    created_at: str


class ProviderIn(BaseModel):
    provider_name: str
    protocol: str
    model: str
    base_url: str
    api_key: str
    id: Optional[str] = None  # omit or null for create, set for update


@router.get("")
async def api_list_providers(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    providers = await list_user_providers(session, user.id)
    return [
        ProviderOut(
            id=str(p.id),
            provider_name=p.provider_name,
            protocol=p.protocol,
            model=p.model,
            base_url=p.base_url,
            api_key_masked=mask_api_key(decrypt_api_key(p.api_key_encrypted)),
            is_active=p.is_active,
            created_at=p.created_at.isoformat() if p.created_at else "",
        )
        for p in providers
    ]


@router.put("")
async def api_upsert_provider(
    req: ProviderIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    provider_id = UUID(req.id) if req.id else None
    encrypted = encrypt_api_key(req.api_key)

    prov = await upsert_user_provider(
        session,
        user_id=user.id,
        provider_name=req.provider_name,
        protocol=req.protocol,
        model=req.model,
        base_url=req.base_url,
        api_key_encrypted=encrypted,
        provider_id=provider_id,
    )

    # NOTE: intentionally do NOT auto-activate on create (M15). The user may
    # want to save a backup provider without switching their active model.
    # Activation is a separate, explicit action via PUT /{id}/activate.

    return {"ok": True, "id": str(prov.id)}


@router.delete("/{provider_id}")
async def api_delete_provider(
    provider_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        pid = UUID(provider_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid provider ID")

    deleted = await delete_user_provider(session, pid, user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Provider not found")
    return {"ok": True}


@router.put("/{provider_id}/activate")
async def api_activate_provider(
    provider_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        pid = UUID(provider_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid provider ID")

    prov = await set_active_provider(session, pid, user.id)
    if not prov:
        raise HTTPException(status_code=404, detail="Provider not found")
    return {"ok": True}
