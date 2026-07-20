import asyncio
import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from backend.auth.crypto import decrypt_api_key
from backend.auth.deps import get_current_user
from backend.config.loader import load_config, load_raw
from backend.db import crud
from backend.db.models import User
from backend.db.session import get_session, AsyncSessionLocal
from backend.providers.base import StreamEvent
from backend.providers.claude import ClaudeProvider
from backend.providers.openai import OpenAIProvider

router = APIRouter()

# In-memory map: session_id -> stream data
_active_streams: dict[str, dict] = {}


# ── Request/Response models ──────────────────────────────────────────────

class ChatRequest(BaseModel):
    content: str
    conversation_id: Optional[str] = None
    thinking_enabled: bool = False


class ChatResponse(BaseModel):
    session_id: str
    conversation_id: str


class ConversationItem(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class MessageItem(BaseModel):
    id: str
    role: str
    content: str
    thinking_content: Optional[str] = None
    created_at: str


class RenameRequest(BaseModel):
    title: str


# ── Helpers ──────────────────────────────────────────────────────────────

def _build_provider_from_dict(p: dict):
    """Create a Provider instance from a config dict."""
    protocol = p.get("protocol", "")
    if protocol == "anthropic":
        return ClaudeProvider(
            model=p.get("model", ""),
            base_url=p.get("base_url", ""),
            api_key=p.get("api_key", ""),
        )
    elif protocol == "openai":
        return OpenAIProvider(
            model=p.get("model", ""),
            base_url=p.get("base_url", ""),
            api_key=p.get("api_key", ""),
        )
    raise ValueError(f"Unsupported protocol '{protocol}'")


async def _load_user_provider(session: AsyncSession, user: User) -> tuple:
    """Load the user's active provider, or fallback for admin.

    Returns (provider_instance, provider_display_name) or raises HTTPException.
    """
    # Try user's active provider first
    active_prov = await crud.get_active_provider(session, user.id)
    if active_prov:
        api_key = decrypt_api_key(active_prov.api_key_encrypted)
        prov = _build_provider_from_dict({
            "protocol": active_prov.protocol,
            "model": active_prov.model,
            "base_url": active_prov.base_url,
            "api_key": api_key,
        })
        return prov, active_prov.provider_name

    # Fallback for admin: use global config
    if user.role == "admin":
        cfg = load_config()
        raw = load_raw()
        providers = raw.get("providers", {})
        provider_name = cfg.provider
        p = providers.get(provider_name)
        if p:
            prov = _build_provider_from_dict(p)
            return prov, provider_name

    # No provider configured
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="请先在设置中配置模型供应商",
    )


async def _summarize_title(
    provider,
    user_message: str,
    ai_response: str,
    conv_id: uuid.UUID,
):
    """Generate a conversation title from the first exchange."""
    try:
        summary_messages = [
            {
                "role": "user",
                "content": f"用不超过10个字总结以下对话的主题：\n\n用户：{user_message}\n\nAI：{ai_response}",
            }
        ]
        full_title = ""
        async for event in provider.chat_stream(summary_messages, thinking_enabled=False):
            if event.type == "content":
                full_title += event.delta

        title = full_title.strip().strip('"').strip("'")[:50] or "新会话"
        async with AsyncSessionLocal() as save_session:
            await crud.rename_conversation(save_session, conv_id, conv_id, title)
    except Exception:
        pass  # Title summarization is best-effort


# ── Conversation endpoints ───────────────────────────────────────────────

@router.get("/conversations")
async def api_list_conversations(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    convs = await crud.list_conversations(session, user.id)
    return [
        ConversationItem(
            id=str(c.id),
            title=c.title,
            created_at=c.created_at.isoformat() if c.created_at else "",
            updated_at=c.updated_at.isoformat() if c.updated_at else "",
        )
        for c in convs
    ]


@router.get("/conversations/{conv_id}/messages")
async def api_get_messages(
    conv_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        cid = uuid.UUID(conv_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    conv = await crud.get_conversation(session, cid, user.id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    msgs = await crud.get_messages_by_conversation(session, cid)
    return [
        MessageItem(
            id=str(m.id),
            role=m.role,
            content=m.content,
            thinking_content=m.thinking_content,
            created_at=m.created_at.isoformat() if m.created_at else "",
        )
        for m in msgs
    ]


@router.put("/conversations/{conv_id}/rename")
async def api_rename_conversation(
    conv_id: str,
    req: RenameRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        cid = uuid.UUID(conv_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    conv = await crud.rename_conversation(session, cid, user.id, req.title)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@router.delete("/conversations/{conv_id}")
async def api_delete_conversation(
    conv_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        cid = uuid.UUID(conv_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    deleted = await crud.delete_conversation(session, cid, user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


# ── Chat endpoints ───────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def api_chat(
    req: ChatRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    # Load user's provider (or admin fallback)
    provider, provider_name = await _load_user_provider(session, user)

    # Check if this is the first message (for title summarization later)
    is_first_message = False

    # Resolve conversation
    conv_id: uuid.UUID
    if req.conversation_id:
        try:
            conv_id = uuid.UUID(req.conversation_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid conversation_id")
        conv = await crud.get_conversation(session, conv_id, user.id)
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        conv = await crud.create_conversation(session, user.id, title=req.content[:50])
        conv_id = conv.id
        is_first_message = True

    # Save user message
    await crud.add_message(session, conv_id, role="user", content=req.content)

    # Load history and build messages for LLM
    history = await crud.get_messages_by_conversation(session, conv_id)
    llm_messages = []
    for m in history:
        llm_messages.append({"role": m.role, "content": m.content})

    # Create a session ID for SSE streaming
    session_id = f"sess-{uuid.uuid4().hex[:12]}"
    _active_streams[session_id] = {
        "provider": provider,
        "provider_name": provider_name,
        "messages": llm_messages,
        "conversation_id": conv_id,
        "thinking_enabled": req.thinking_enabled,
        "user_id": user.id,
        "is_first_message": is_first_message,
        "first_user_message": req.content,
    }

    return ChatResponse(session_id=session_id, conversation_id=str(conv_id))


@router.get("/chat/stream/{session_id}")
async def api_chat_stream(session_id: str):
    stream_data = _active_streams.pop(session_id, None)
    if not stream_data:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    provider = stream_data["provider"]
    messages = stream_data["messages"]
    conv_id = stream_data["conversation_id"]
    thinking_enabled = stream_data.get("thinking_enabled", False)
    is_first_message = stream_data.get("is_first_message", False)
    first_user_message = stream_data.get("first_user_message", "")

    async def event_generator():
        full_content = ""
        full_thinking = ""
        try:
            async for event in provider.chat_stream(messages, thinking_enabled=thinking_enabled):
                if event.type == "content":
                    full_content += event.delta
                    yield f"event: content\ndata: {json.dumps({'delta': event.delta})}\n\n"
                elif event.type == "thinking":
                    full_thinking += event.delta
                    yield f"event: thinking\ndata: {json.dumps({'delta': event.delta})}\n\n"
                elif event.type == "done":
                    # Save assistant message
                    async with AsyncSessionLocal() as save_session:
                        await crud.add_message(
                            save_session,
                            conversation_id=conv_id,
                            role="assistant",
                            content=full_content,
                            thinking_content=full_thinking or None,
                        )

                    # Auto-summarize title on first message
                    if is_first_message and full_content:
                        asyncio.create_task(_summarize_title(
                            provider, first_user_message, full_content, conv_id,
                        ))

                    yield f"event: done\ndata: {json.dumps({'type': 'done'})}\n\n"
                    return
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'delta': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
