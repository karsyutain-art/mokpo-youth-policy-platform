"""FastAPI API for the React + Vite youth-policy service."""

from __future__ import annotations

import asyncio
import hmac
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.middleware.sessions import SessionMiddleware

from mysql_policy_repository import MySQLPolicyRepository
from policy_matcher import TAG_KEYWORDS, PolicyMatcher, eligible_for_policy
from youth_data_collector import load_local_env


load_local_env()
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:5000")
KAKAO_REDIRECT_URI = os.getenv("KAKAO_REDIRECT_URI", f"{BACKEND_URL}/auth/kakao/callback")

database_url = URL.create(
    "mysql+aiomysql",
    username=os.getenv("MYSQL_USER", "root"),
    password=os.getenv("MYSQL_PASSWORD", ""),
    host=os.getenv("MYSQL_HOST", "127.0.0.1"),
    port=int(os.getenv("MYSQL_PORT", "3306")),
    database=os.getenv("MYSQL_DATABASE", "youth_policy"),
    query={"charset": "utf8mb4"},
)
engine = create_async_engine(database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def initialize_schema() -> None:
    repository = MySQLPolicyRepository()
    connection = repository.connect()
    try:
        repository.initialize(connection)
    finally:
        connection.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(initialize_schema)
    yield
    await engine.dispose()


app = FastAPI(title="목포 청년 정책 API", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=os.getenv("FLASK_SECRET_KEY", "change-me"), same_site="lax", https_only=False)
app.add_middleware(CORSMiddleware, allow_origins=[FRONTEND_URL], allow_credentials=True, allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type"])


class ProfileInput(BaseModel):
    birth_date: date
    interests: list[str] = Field(min_length=1)


async def current_user(request: Request, db: AsyncSession) -> dict:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    row = (await db.execute(text("SELECT id, display_name, birth_date, residency_city FROM user_profiles WHERE id = :id"), {"id": user_id})).mappings().first()
    if not row:
        request.session.clear()
        raise HTTPException(status_code=401, detail="사용자 정보를 찾지 못했습니다.")
    user = dict(row)
    interests = (await db.execute(text("SELECT interest_tag FROM user_interests WHERE user_id = :id ORDER BY interest_tag"), {"id": user_id})).scalars().all()
    user["interests"] = interests
    return user


@app.get("/api/health")
async def health() -> dict[str, str]:
    async with SessionLocal() as db:
        await db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/auth/kakao")
async def kakao_login(request: Request):
    rest_api_key = os.getenv("KAKAO_REST_API_KEY", "")
    if not rest_api_key:
        raise HTTPException(status_code=503, detail="KAKAO_REST_API_KEY가 설정되지 않았습니다.")
    state = secrets.token_urlsafe(32)
    request.session["kakao_oauth_state"] = state
    query = urlencode({"client_id": rest_api_key, "redirect_uri": KAKAO_REDIRECT_URI, "response_type": "code", "state": state})
    return RedirectResponse(f"https://kauth.kakao.com/oauth/authorize?{query}")


@app.get("/auth/kakao/callback")
async def kakao_callback(request: Request):
    expected_state = request.session.pop("kakao_oauth_state", "")
    received_state = request.query_params.get("state", "")
    code = request.query_params.get("code")
    if not expected_state or not received_state or not hmac.compare_digest(expected_state, received_state) or not code:
        return RedirectResponse(f"{FRONTEND_URL}/?login_error=session")
    data = {"grant_type": "authorization_code", "client_id": os.getenv("KAKAO_REST_API_KEY", ""), "redirect_uri": KAKAO_REDIRECT_URI, "code": code}
    if os.getenv("KAKAO_CLIENT_SECRET"):
        data["client_secret"] = os.getenv("KAKAO_CLIENT_SECRET", "")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token_response = await client.post("https://kauth.kakao.com/oauth/token", data=data)
            token_response.raise_for_status()
            token = token_response.json()["access_token"]
            account_response = await client.get("https://kapi.kakao.com/v2/user/me", headers={"Authorization": f"Bearer {token}"})
            account_response.raise_for_status()
            kakao_user = account_response.json()
    except (httpx.HTTPError, KeyError):
        return RedirectResponse(f"{FRONTEND_URL}/?login_error=kakao")
    account = kakao_user.get("kakao_account", {})
    nickname = (account.get("profile") or {}).get("nickname") or "카카오 사용자"
    now = datetime.now().replace(microsecond=0)
    async with SessionLocal() as db:
        await db.execute(
            text("""INSERT INTO user_profiles (kakao_user_id, display_name, email, residency_city, created_at, updated_at)
            VALUES (:kakao_user_id, :display_name, :email, '목포', :now, :now)
            ON DUPLICATE KEY UPDATE display_name = VALUES(display_name), email = VALUES(email), updated_at = VALUES(updated_at)"""),
            {"kakao_user_id": int(kakao_user["id"]), "display_name": nickname, "email": account.get("email"), "now": now},
        )
        user_id = (await db.execute(text("SELECT id FROM user_profiles WHERE kakao_user_id = :kakao_user_id"), {"kakao_user_id": int(kakao_user["id"])})).scalar_one()
        await db.commit()
    request.session["user_id"] = user_id
    return RedirectResponse(FRONTEND_URL)


@app.post("/api/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
async def me(request: Request):
    async with SessionLocal() as db:
        user = await current_user(request, db)
    return user


@app.put("/api/me/profile")
async def update_profile(payload: ProfileInput, request: Request):
    invalid = set(payload.interests) - set(TAG_KEYWORDS)
    if invalid:
        raise HTTPException(status_code=400, detail="지원하지 않는 관심 분야가 있습니다.")
    async with SessionLocal() as db:
        user = await current_user(request, db)
        now = datetime.now().replace(microsecond=0)
        await db.execute(text("UPDATE user_profiles SET birth_date = :birth_date, updated_at = :now WHERE id = :id"), {"birth_date": payload.birth_date, "now": now, "id": user["id"]})
        await db.execute(text("DELETE FROM user_interests WHERE user_id = :id"), {"id": user["id"]})
        await db.execute(text("INSERT INTO user_interests (user_id, interest_tag) VALUES (:user_id, :interest_tag)"), [{"user_id": user["id"], "interest_tag": tag} for tag in sorted(set(payload.interests))])
        await db.commit()
    await asyncio.to_thread(PolicyMatcher().create_candidates)
    return {"ok": True}


@app.get("/api/policies/recommended")
async def recommended_policies(request: Request):
    async with SessionLocal() as db:
        user = await current_user(request, db)
        if user["birth_date"] is None:
            return []
        rows = (await db.execute(text("""SELECT * FROM policy_records
            WHERE target_region IN ('목포', '전남(목포 포함)', '전국(목포 포함)')
            AND (application_end_date IS NULL OR application_end_date >= CURDATE())
            ORDER BY application_end_date IS NULL DESC, application_end_date ASC, updated_at DESC"""))).mappings().all()
    return [dict(row) for row in rows if eligible_for_policy(user, dict(row), set(user["interests"]))[0]]


@app.get("/api/notifications")
async def notifications(request: Request):
    async with SessionLocal() as db:
        user = await current_user(request, db)
        rows = (await db.execute(text("""SELECT event.change_type, policy.title, policy.original_link,
            policy.application_end_date, candidate.match_reason, candidate.created_at
            FROM policy_match_candidates AS candidate
            JOIN policy_records AS policy ON policy.id = candidate.policy_id
            JOIN policy_change_events AS event ON event.id = candidate.event_id
            WHERE candidate.user_id = :user_id AND candidate.status = 'pending'
            ORDER BY candidate.created_at DESC"""), {"user_id": user["id"]})).mappings().all()
    return [dict(row) for row in rows]
