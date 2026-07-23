import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from backend.auth.crypto import decrypt_api_key
from backend.auth.deps import get_current_user
from backend.auth.jwt import verify_token
from backend.config.loader import load_config, load_raw
from backend.db import crud
from backend.db.models import User
from backend.db.session import get_session, AsyncSessionLocal
from backend.providers.base import BaseProvider, StreamEvent
from backend.providers.claude import ClaudeProvider
from backend.providers.openai import OpenAIProvider
from backend.services.base import ServiceResult
from backend.services.registry import ServiceRegistry

router = APIRouter()

# In-memory map: session_id -> stream data
_active_streams: dict[str, dict] = {}

# Streams older than this are considered abandoned and purged (M3 fix).
STREAM_TTL_SECONDS = 1800  # 30 minutes


def _cleanup_expired_streams() -> None:
    """Remove streams that have been sitting in _active_streams too long.

    A stream is abandoned when the client disconnects (closes tab / loses
    network) before consuming it. Without cleanup this dict grows without
    bound and leaks provider instances + full message histories (M3).
    """
    if not _active_streams:
        return
    now = datetime.now(timezone.utc).timestamp()
    expired = [
        sid
        for sid, data in _active_streams.items()
        if now - data.get("_created_at", 0) > STREAM_TTL_SECONDS
    ]
    for sid in expired:
        _active_streams.pop(sid, None)


async def _get_user_from_query_token(token: str | None) -> User | None:
    """Verify a bearer token supplied as a query param and return the user.

    EventSource cannot send Authorization headers, so the stream endpoint
    accepts the token via the `token` query param instead. Returns None if
    the token is missing/invalid or the user no longer exists.
    """
    if not token:
        return None
    try:
        payload = verify_token(token)
        user_id = uuid.UUID(payload.get("sub"))
    except Exception:
        return None
    async with AsyncSessionLocal() as session:
        return await crud.get_user_by_id(session, user_id)


# ── SSRF guard (M5) ────────────────────────────────────────────────────
import ipaddress
from urllib.parse import urlparse

# Hosts / schemes that must never be targeted by a user-registered service.
_FORBIDDEN_SCHEMES = {"file", "ftp", "gopher", "dict", "ldap", "ldaps", "tftp"}
_FORBIDDEN_HOSTS = {
    "localhost",
    "ip6-localhost",
    "ip6-loopback",
    "metadata.google.internal",
    "[::1]",
    "[::ffff:127.0.0.1]",
}


def _is_safe_service_url(url: str) -> tuple[bool, str]:
    """Reject URLs that could be used for SSRF against internal/network targets.

    Returns (ok, reason). Only http/https schemes pointing at public
    (non-private, non-loopback, non-link-local) IPs are allowed.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "URL 无法解析"

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False, f"不支持的协议: {scheme or '(空)'}（仅允许 http/https）"
    if not parsed.hostname:
        return False, "URL 缺少主机名"

    hostname = parsed.hostname.lower().strip("[]")
    if hostname in _FORBIDDEN_HOSTS:
        return False, "禁止指向本机/保留主机名"

    # Reject raw private/loopback/link-local IPv4 and IPv6 literals.
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            return False, "禁止指向内网/保留 IP 地址"
    except ValueError:
        pass  # not a literal IP; a hostname — allow (DNS rebinding is out of scope)

    return True, ""


# ── Request/Response models ──────────────────────────────────────────────

class ChatRequest(BaseModel):
    content: str
    conversation_id: Optional[str] = None
    thinking_enabled: bool = False
    enabled_services: list[str] = []


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
    tool_calls: Optional[list[dict]] = None
    created_at: str


class RenameRequest(BaseModel):
    title: str


class ServiceInfo(BaseModel):
    name: str
    description: str
    url: str = ""
    method: str = "POST"


class CustomServiceRequest(BaseModel):
    name: str
    description: str = ""
    url: str
    method: str = "POST"
    headers: dict[str, str] = {}
    request_template: dict = {}
    timeout: float = 30.0


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
    user_id: uuid.UUID,
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
            await crud.rename_conversation(save_session, conv_id, user_id, title)
    except Exception as e:
        # Title summarization is best-effort: log but never break the request.
        import logging
        logging.getLogger("backend").warning("title summarization failed: %s", e)


def _convert_tools_to_openai(tools: list[dict]) -> list[dict]:
    """Convert tools from standard format to format accepted by provider.

    The ServiceRegistry returns tools in OpenAI-compatible format:
      {"type": "function", "function": {...}}
    This works directly for OpenAI, but Anthropic expects a slightly different structure.
    We pass the OpenAI format to both since both SDKs accept it via the 'tools' parameter.
    """
    return tools


def _get_service_tool_registry() -> ServiceRegistry:
    """Get the global services.yaml."""
    return ServiceRegistry()


# ── Provider-specific tool message formatting (C4) ─────────────────────
# LLM APIs disagree on how tool_use / tool_result are represented in the
# message list. OpenAI uses a `tool_calls` array on the assistant message
# plus `role: "tool"` replies keyed by `tool_call_id`. Anthropic uses
# content blocks (`tool_use` / `tool_result`) inside the message. These
# helpers build the correct shape for each provider so the second LLM
# call can actually "see" the tool results.

def _append_assistant_tool_message(
    messages: list[dict],
    provider: BaseProvider,
    full_content: str,
    tool_uses: list[dict],
) -> None:
    """Append the assistant message that requested tool calls."""
    if isinstance(provider, ClaudeProvider):
        # Anthropic: content is an array of blocks.
        blocks: list[dict] = []
        if full_content:
            blocks.append({"type": "text", "text": full_content})
        for tu in tool_uses:
            blocks.append({
                "type": "tool_use",
                "id": tu.get("tool_use_id") or "",
                "name": tu["tool_name"],
                "input": tu["input"],
            })
        messages.append({"role": "assistant", "content": blocks})
    else:
        # OpenAI: assistant message carries a `tool_calls` array.
        tool_calls = [
            {
                "id": tu.get("tool_use_id") or "",
                "type": "function",
                "function": {
                    "name": tu["tool_name"],
                    "arguments": json.dumps(tu["input"], ensure_ascii=False),
                },
            }
            for tu in tool_uses
        ]
        messages.append({
            "role": "assistant",
            "content": full_content or None,
            "tool_calls": tool_calls,
        })


def _append_tool_result_message(
    messages: list[dict],
    provider: BaseProvider,
    tool_use_id: str,
    tool_name: str,
    content_str: str,
    success: bool,
) -> None:
    """Append the tool's result so the LLM can incorporate it."""
    if isinstance(provider, ClaudeProvider):
        # Anthropic: tool_result blocks live inside a "user" message.
        messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": content_str,
                "is_error": not success,
            }],
        })
    else:
        # OpenAI: a "tool" message keyed by tool_call_id.
        messages.append({
            "role": "tool",
            "tool_call_id": tool_use_id,
            "name": tool_name,
            "content": content_str,
        })


async def _execute_service(tool_name: str, tool_input: dict) -> tuple[ServiceResult, str]:
    """Execute a service and return (result, tool_result_content_str).

    The tool_result_content_str is the human-readable content to feed back
    to the LLM (and to display). Message formatting for the specific
    provider is handled separately by _append_tool_result_message.
    """
    registry = _get_service_tool_registry()
    service = registry.get(tool_name)

    if service is None:
        error_msg = f"Service '{tool_name}' not found"
        return (
            ServiceResult(success=False, error=error_msg),
            json.dumps({
                "tool_use_id": tool_name,
                "success": False,
                "error": error_msg,
            }, ensure_ascii=False),
        )

    try:
        result = await service.invoke(**tool_input)

        if result.success:
            content_str = json.dumps({
                "success": True,
                "data": result.data,
            }, ensure_ascii=False)
        else:
            content_str = json.dumps({
                "success": False,
                "error": result.error,
            }, ensure_ascii=False)

        return result, content_str
    except Exception as e:
        error_msg = f"Service execution error: {e}"
        return (
            ServiceResult(success=False, error=error_msg),
            json.dumps({
                "success": False,
                "error": error_msg,
            }, ensure_ascii=False),
        )


# ── Service info endpoint ─────────────────────────────────────────────

@router.get("/services")
async def api_list_services():
    """Return all registered services with name and description."""
    registry = _get_service_tool_registry()
    services = registry._services if hasattr(registry, '_services') else {}
    return [
        ServiceInfo(
            name=svc.name,
            description=svc.description,
            url=getattr(svc, 'url', ''),
            method=getattr(svc, 'method', 'POST'),
        )
        for svc in services.values()
    ]


@router.post("/services", status_code=status.HTTP_201_CREATED)
async def api_register_custom_service(
    req: CustomServiceRequest,
    user: User = Depends(get_current_user),
):
    """Register a new custom HTTP service at runtime.

    The service is added to the global ServiceRegistry immediately
    and also persisted to services.yaml.
    """
    from backend.services.http_service import HttpService

    if not req.name or not req.url:
        raise HTTPException(status_code=400, detail="name and url are required")

    # SSRF guard: reject URLs that target internal/network resources (M5).
    ok, reason = _is_safe_service_url(req.url)
    if not ok:
        raise HTTPException(status_code=400, detail=f"URL 被拒绝: {reason}")

    registry = _get_service_tool_registry()

    # Check for duplicate
    if registry.get(req.name):
        raise HTTPException(status_code=409, detail=f"Service '{req.name}' already exists")

    # Build the service
    svc = HttpService(
        name=req.name,
        description=req.description,
        url=req.url,
        method=req.method,
        headers=req.headers,
        request_template=req.request_template,
        timeout=req.timeout,
    )
    registry.register(svc)

    # Persist to services.yaml
    import yaml
    from pathlib import Path

    services_path = Path(__file__).resolve().parent.parent / "services.yaml"
    if services_path.exists():
        with open(services_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}

    if "services" not in data:
        data["services"] = {}

    data["services"][req.name] = {
        "name": req.name,
        "description": req.description,
        "url": req.url,
        "method": req.method,
        "headers": req.headers,
        "request_template": req.request_template,
        "timeout": req.timeout,
    }

    with open(services_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    return ServiceInfo(name=svc.name, description=svc.description, url=svc.url, method=svc.method)


@router.delete("/services/{service_name}")
async def api_delete_custom_service(
    service_name: str,
    user: User = Depends(get_current_user),
):
    """Remove a custom service from registry and services.yaml.

    Admin-only: custom services are global (shared by all users), so only
    admins may register or remove them (M14).
    """
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可删除服务")
    registry = _get_service_tool_registry()
    svc = registry.get(service_name)
    if not svc:
        raise HTTPException(status_code=404, detail=f"Service '{service_name}' not found")

    # Remove from registry
    if hasattr(registry, '_services') and service_name in registry._services:
        del registry._services[service_name]

    # Remove from services.yaml
    import yaml
    from pathlib import Path

    services_path = Path(__file__).resolve().parent.parent / "services.yaml"
    if services_path.exists():
        with open(services_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if "services" in data and service_name in data["services"]:
            del data["services"][service_name]
            with open(services_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    return {"ok": True}


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
            tool_calls=json.loads(m.tool_calls) if m.tool_calls else None,
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
        msg = {"role": m.role, "content": m.content}
        llm_messages.append(msg)

    # Get available service tools (filtered by user selection)
    registry = _get_service_tool_registry()
    if req.enabled_services:
        all_tools = registry.list_tools()
        enabled_set = set(req.enabled_services)
        tools = [t for t in all_tools if t["function"]["name"] in enabled_set]
    else:
        tools = []

    # Create a session ID for SSE streaming
    session_id = f"sess-{uuid.uuid4().hex[:12]}"
    _active_streams[session_id] = {
        "provider": provider,
        "provider_name": provider_name,
        "messages": llm_messages,
        "tools": tools,
        "conversation_id": conv_id,
        "thinking_enabled": req.thinking_enabled,
        "user_id": user.id,
        "is_first_message": is_first_message,
        "first_user_message": req.content,
        "_created_at": datetime.now(timezone.utc).timestamp(),
    }

    return ChatResponse(session_id=session_id, conversation_id=str(conv_id))


@router.get("/chat/stream/{session_id}")
async def api_chat_stream(
    session_id: str,
    token: str | None = Query(None, description="Bearer token (EventSource 无法发送 Authorization header)"),
):
    """Stream chat completion events over SSE.

    Authentication: because browser EventSource cannot set Authorization
    headers, the JWT is accepted via the `token` query param and verified
    here. The stream is only released to the user who owns it (C5).
    """
    # Purge abandoned streams on every stream request (M3).
    _cleanup_expired_streams()

    stream_data = _active_streams.pop(session_id, None)
    if not stream_data:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    # Authenticate and enforce ownership (C5).
    owner_id = stream_data.get("user_id")
    requesting_user = await _get_user_from_query_token(token)
    if requesting_user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if owner_id is not None and requesting_user.id != owner_id:
        raise HTTPException(status_code=403, detail="无权访问该会话流")

    provider = stream_data["provider"]
    messages = stream_data["messages"]
    tools = stream_data.get("tools", [])
    conv_id = stream_data["conversation_id"]
    thinking_enabled = stream_data.get("thinking_enabled", False)
    is_first_message = stream_data.get("is_first_message", False)
    first_user_message = stream_data.get("first_user_message", "")
    user_id = requesting_user.id

    async def event_generator():
        full_content = ""
        full_thinking = ""
        tool_calls_executed: list[dict] = []

        try:
            # Phase 1: First LLM call (with tools)
            if tools:
                tool_uses = []
                async for event in provider.chat_stream_with_tools(
                    messages, tools, thinking_enabled=thinking_enabled
                ):
                    if event.type == "content":
                        full_content += event.delta
                        yield f"event: content\ndata: {json.dumps({'delta': event.delta})}\n\n"
                    elif event.type == "thinking":
                        full_thinking += event.delta
                        yield f"event: thinking\ndata: {json.dumps({'delta': event.delta})}\n\n"
                    elif event.type == "tool_use":
                        tool_info = json.loads(event.delta)
                        tool_uses.append(tool_info)

                        # Immediately notify frontend that tool call started
                        yield (
                            f"event: tool_call\n"
                            f"data: {json.dumps({'tool_name': tool_info['tool_name'], 'input': tool_info['input'], 'status': 'started'})}\n\n"
                        )

                # Phase 2: Execute all tool calls serially (no agent loop yet)
                if tool_uses:
                    # Append the assistant's content + tool_use blocks to messages
                    # in the format expected by the active provider (C4).
                    _append_assistant_tool_message(messages, provider, full_content, tool_uses)

                    for tool_use in tool_uses:
                        tool_name = tool_use["tool_name"]
                        tool_input = tool_use["input"]
                        tool_use_id = tool_use.get("tool_use_id", "")

                        # Execute the service
                        result, tool_result_content = await _execute_service(tool_name, tool_input)

                        # Build tool_call record for frontend
                        tc_record = {
                            "tool_name": tool_name,
                            "input": tool_input,
                            "status": "completed" if result.success else "error",
                            "result": result.data if result.success else None,
                            "error": result.error if not result.success else None,
                        }
                        tool_calls_executed.append(tc_record)

                        # Append the tool result in provider-correct format (C4).
                        _append_tool_result_message(
                            messages, provider, tool_use_id, tool_name, tool_result_content, result.success,
                        )

                        # Notify frontend of completion/error (include result so frontend can render it)
                        yield (
                            f"event: tool_call\n"
                            f"data: {json.dumps({'tool_name': tool_name, 'status': 'completed' if result.success else 'error', 'success': result.success, 'result': result.data if result.success else None, 'error': result.error if not result.success else None}, ensure_ascii=False)}\n\n"
                        )

                    # Phase 3: Second LLM call (without tools) to get final answer
                    full_content = ""
                    full_thinking = ""
                    async for event in provider.chat_stream(messages, thinking_enabled=thinking_enabled):
                        if event.type == "content":
                            full_content += event.delta
                            yield f"event: content\ndata: {json.dumps({'delta': event.delta})}\n\n"
                        elif event.type == "thinking":
                            full_thinking += event.delta
                            yield f"event: thinking\ndata: {json.dumps({'delta': event.delta})}\n\n"
            else:
                # No tools available — normal chat flow
                async for event in provider.chat_stream(messages, thinking_enabled=thinking_enabled):
                    if event.type == "content":
                        full_content += event.delta
                        yield f"event: content\ndata: {json.dumps({'delta': event.delta})}\n\n"
                    elif event.type == "thinking":
                        full_thinking += event.delta
                        yield f"event: thinking\ndata: {json.dumps({'delta': event.delta})}\n\n"

            # Save assistant message (with tool_calls if any)
            async with AsyncSessionLocal() as save_session:
                await crud.add_message(
                    save_session,
                    conversation_id=conv_id,
                    role="assistant",
                    content=full_content,
                    thinking_content=full_thinking or None,
                    tool_calls=json.dumps(tool_calls_executed, ensure_ascii=False) if tool_calls_executed else None,
                )

            # Auto-summarize title on first message
            if is_first_message and full_content:
                asyncio.create_task(_summarize_title(
                    provider, first_user_message, full_content, conv_id, user_id,
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
