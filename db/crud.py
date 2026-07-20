from uuid import UUID
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Conversation, Message, User, UserProvider


# ── User ─────────────────────────────────────────────────────────────────

async def create_user(
    session: AsyncSession,
    username: str,
    password_hash: str,
    email: str,
    role: str = "user",
) -> User:
    user = User(username=username, password_hash=password_hash, email=email, role=role)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def get_user_by_username(session: AsyncSession, username: str) -> Optional[User]:
    stmt = select(User).where(User.username == username)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: UUID) -> Optional[User]:
    return await session.get(User, user_id)


# ── UserProvider ─────────────────────────────────────────────────────────

async def list_user_providers(session: AsyncSession, user_id: UUID) -> list[UserProvider]:
    stmt = (
        select(UserProvider)
        .where(UserProvider.user_id == user_id)
        .order_by(UserProvider.created_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_user_provider(session: AsyncSession, provider_id: UUID, user_id: UUID) -> Optional[UserProvider]:
    stmt = select(UserProvider).where(
        UserProvider.id == provider_id,
        UserProvider.user_id == user_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def upsert_user_provider(
    session: AsyncSession,
    user_id: UUID,
    provider_name: str,
    protocol: str,
    model: str,
    base_url: str,
    api_key_encrypted: str,
    provider_id: Optional[UUID] = None,
) -> UserProvider:
    if provider_id:
        prov = await get_user_provider(session, provider_id, user_id)
        if prov:
            prov.provider_name = provider_name
            prov.protocol = protocol
            prov.model = model
            prov.base_url = base_url
            prov.api_key_encrypted = api_key_encrypted
            await session.commit()
            await session.refresh(prov)
            return prov

    prov = UserProvider(
        user_id=user_id,
        provider_name=provider_name,
        protocol=protocol,
        model=model,
        base_url=base_url,
        api_key_encrypted=api_key_encrypted,
    )
    session.add(prov)
    await session.commit()
    await session.refresh(prov)
    return prov


async def delete_user_provider(session: AsyncSession, provider_id: UUID, user_id: UUID) -> bool:
    prov = await get_user_provider(session, provider_id, user_id)
    if not prov:
        return False
    await session.delete(prov)
    await session.commit()
    return True


async def set_active_provider(session: AsyncSession, provider_id: UUID, user_id: UUID) -> Optional[UserProvider]:
    # Deactivate all providers for the user first
    stmt = select(UserProvider).where(UserProvider.user_id == user_id)
    result = await session.execute(stmt)
    for p in result.scalars().all():
        p.is_active = False

    # Activate the target provider
    prov = await get_user_provider(session, provider_id, user_id)
    if not prov:
        return None
    prov.is_active = True
    await session.commit()
    await session.refresh(prov)
    return prov


async def get_active_provider(session: AsyncSession, user_id: UUID) -> Optional[UserProvider]:
    stmt = select(UserProvider).where(
        UserProvider.user_id == user_id,
        UserProvider.is_active == True,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


# ── Conversation ─────────────────────────────────────────────────────────

async def create_conversation(session: AsyncSession, user_id: UUID, title: str = "新会话") -> Conversation:
    conv = Conversation(user_id=user_id, title=title)
    session.add(conv)
    await session.commit()
    await session.refresh(conv)
    return conv


async def list_conversations(session: AsyncSession, user_id: UUID, limit: int = 20) -> list[Conversation]:
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_conversation(session: AsyncSession, conv_id: UUID, user_id: UUID) -> Optional[Conversation]:
    stmt = select(Conversation).where(
        Conversation.id == conv_id,
        Conversation.user_id == user_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def rename_conversation(session: AsyncSession, conv_id: UUID, user_id: UUID, title: str) -> Optional[Conversation]:
    conv = await get_conversation(session, conv_id, user_id)
    if not conv:
        return None
    conv.title = title
    await session.commit()
    await session.refresh(conv)
    return conv


async def delete_conversation(session: AsyncSession, conv_id: UUID, user_id: UUID) -> bool:
    conv = await get_conversation(session, conv_id, user_id)
    if not conv:
        return False
    await session.delete(conv)
    await session.commit()
    return True


# ── Message ──────────────────────────────────────────────────────────────

async def add_message(
    session: AsyncSession,
    conversation_id: UUID,
    role: str,
    content: str,
    thinking_content: Optional[str] = None,
    tool_calls: Optional[str] = None,
) -> Message:
    msg = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        thinking_content=thinking_content,
        tool_calls=tool_calls,
    )
    session.add(msg)
    # Update conversation updated_at
    conv = await session.get(Conversation, conversation_id)
    if conv:
        conv.updated_at = func.now()
    await session.commit()
    await session.refresh(msg)
    return msg


async def get_messages_by_conversation(
    session: AsyncSession,
    conv_id: UUID,
    order_by_asc: bool = True,
) -> list[Message]:
    stmt = (
        select(Message)
        .where(Message.conversation_id == conv_id)
        .order_by(Message.created_at.asc() if order_by_asc else Message.created_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
