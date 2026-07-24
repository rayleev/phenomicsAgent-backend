"""Auth routes: register and login."""

import re

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from auth.jwt import create_access_token
from db.crud import create_user, get_user_by_username
from db.session import get_session

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Models ───────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str

    @field_validator("username")
    @classmethod
    def username_length(cls, v: str) -> str:
        if len(v) < 3 or len(v) > 50:
            raise ValueError("用户名需要 3-50 个字符")
        return v

    @field_validator("password")
    @classmethod
    def password_length(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("密码至少 6 位")
        return v

    @field_validator("email")
    @classmethod
    def email_format(cls, v: str) -> str:
        if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", v):
            raise ValueError("邮箱格式不正确")
        return v


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    token: str
    user: dict


# ── Routes ───────────────────────────────────────────────────────────────

@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest, session: AsyncSession = Depends(get_session)):
    # Check username uniqueness
    existing = await get_user_by_username(session, req.username)
    if existing:
        raise HTTPException(status_code=409, detail="用户名已存在")

    # Hash password
    password_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()

    # Create user
    user = await create_user(session, req.username, password_hash, req.email)

    # Generate token
    token = create_access_token(user.id, user.username, user.role)

    return AuthResponse(
        token=token,
        user={"id": str(user.id), "username": user.username, "email": user.email, "role": user.role},
    )


@router.post("/login", response_model=AuthResponse)
async def login(req: LoginRequest, session: AsyncSession = Depends(get_session)):
    user = await get_user_by_username(session, req.username)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if not bcrypt.checkpw(req.password.encode(), user.password_hash.encode()):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_access_token(user.id, user.username, user.role)

    return AuthResponse(
        token=token,
        user={"id": str(user.id), "username": user.username, "email": user.email, "role": user.role},
    )
