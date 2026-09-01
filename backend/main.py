"""FastAPI API for the React + Vite youth-policy service."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.middleware.sessions import SessionMiddleware

from mysql_policy_repository import MySQLPolicyRepository
from policy_matcher import TAG_KEYWORDS, PolicyMatcher, diagnose_eligibility, eligible_for_policy
from rag_policy_search import PolicyRAG
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
app.add_middleware(CORSMiddleware, allow_origins=[FRONTEND_URL], allow_credentials=True, allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type"])


class ProfileInput(BaseModel):
    birth_date: date
    interests: list[str] = Field(min_length=1)
    residency_months: int | None = Field(default=None, ge=0, le=1200)
    employment_status: str | None = Field(default=None, max_length=50)
    income_band: str | None = Field(default=None, max_length=50)
    education_level: str | None = Field(default=None, max_length=50)
    household_status: str | None = Field(default=None, max_length=50)


class WishlistInput(BaseModel):
    notifications_enabled: bool = True


class NotificationStatusInput(BaseModel):
    status: str


class ChatInput(BaseModel):
    question: str = Field(min_length=2, max_length=500)


POLICY_REGIONS = ("목포", "전남(목포 포함)", "전국(목포 포함)")
POLICY_SEARCH_LIMIT = 100
PROFILE_OPTIONS = {
    "employment_status": {"미취업", "구직 중", "재직 중", "프리랜서", "창업/사업자", "학생", "기타"},
    "income_band": {"중위소득 50% 이하", "중위소득 50~100%", "중위소득 100~150%", "중위소득 150% 초과", "확인 어려움"},
    "education_level": {"고졸 이하", "대학 재학", "대학 휴학", "대학 졸업", "대학원", "기타"},
    "household_status": {"1인 가구", "부모 동거", "부부/자녀", "한부모", "기타"},
}


def serialize_policy(row: dict, *, detail: bool = False) -> dict:
    """Return a stable public policy payload without exposing internal hashes."""
    policy = {
        "id": row["id"],
        "source_site": row["source_site"],
        "category": row["category"],
        "title": row["title"],
        "target_region": row["target_region"],
        "target_condition": row.get("target_condition"),
        "qualification_text": row.get("qualification_text"),
        "min_age": row.get("min_age"),
        "max_age": row.get("max_age"),
        "residency_condition": row.get("residency_condition"),
        "period": row.get("period_text"),
        "application_start_date": row.get("application_start_date"),
        "application_end_date": row.get("application_end_date"),
        "organization": row.get("organization"),
        "original_link": row.get("original_link"),
        "last_verified_at": row.get("last_seen_at"),
        "updated_at": row.get("updated_at"),
    }
    content = row.get("content") or ""
    policy["summary"] = content[:280]
    if detail:
        policy.update(
            {
                "content": content,
                "application_method": row.get("application_method"),
                "attachment_links": row.get("attachment_links"),
                "attachment_text": row.get("attachment_text"),
                "attachment_status": row.get("attachment_status"),
            }
        )
    return policy


async def current_user(request: Request, db: AsyncSession) -> dict:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    row = (await db.execute(text("""SELECT id, display_name, birth_date, residency_city, residency_months,
        employment_status, income_band, education_level, household_status
        FROM user_profiles WHERE id = :id"""), {"id": user_id})).mappings().first()
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


@app.post("/api/chat")
async def policy_chat(payload: ChatInput, request: Request):
    result = await asyncio.to_thread(PolicyRAG().answer, payload.question.strip())
    user_id = request.session.get("user_id")
    if user_id:
        async with SessionLocal() as db:
            user = await current_user(request, db)
            await db.execute(
                text("""INSERT INTO policy_chat_messages
                    (user_id, question, answer, sources_json, ai_generated, model_name, created_at)
                    VALUES (:user_id, :question, :answer, CAST(:sources_json AS JSON), :generated, :model_name, NOW())"""),
                {"user_id": user["id"], "question": payload.question.strip(), "answer": result["answer"],
                 "sources_json": json.dumps(result.get("sources", []), ensure_ascii=False, default=str),
                 "generated": bool(result.get("generated")), "model_name": result.get("model")},
            )
            await db.commit()
    return result


@app.get("/api/chat/history")
async def chat_history(request: Request, limit: int = Query(default=20, ge=1, le=100)):
    async with SessionLocal() as db:
        user = await current_user(request, db)
        rows = (await db.execute(text("""SELECT id, question, answer, sources_json, ai_generated AS generated, model_name, created_at
            FROM policy_chat_messages WHERE user_id = :user_id ORDER BY created_at DESC, id DESC LIMIT :limit"""),
            {"user_id": user["id"], "limit": limit})).mappings().all()
    history = []
    for row in rows:
        item = dict(row)
        sources = item.pop("sources_json")
        item["sources"] = sources if isinstance(sources, list) else json.loads(sources or "[]")
        history.append(item)
    return history


@app.delete("/api/chat/history")
async def clear_chat_history(request: Request):
    async with SessionLocal() as db:
        user = await current_user(request, db)
        await db.execute(text("DELETE FROM policy_chat_messages WHERE user_id = :user_id"), {"user_id": user["id"]})
        await db.commit()
    return {"cleared": True}


@app.get("/api/policies")
async def search_policies(
    q: str = Query(default="", max_length=100),
    category: str = Query(default="", max_length=50),
    region: str = Query(default="", max_length=50),
    recruitment: str = Query(default="open", pattern="^(open|closed|all)$"),
    age: int | None = Query(default=None, ge=0, le=120),
    limit: int = Query(default=30, ge=1, le=POLICY_SEARCH_LIMIT),
    offset: int = Query(default=0, ge=0),
):
    """Search policies that a Mokpo resident may apply for."""
    where = ["target_region IN ('목포', '전남(목포 포함)', '전국(목포 포함)')"]
    params: dict[str, object] = {"limit": limit, "offset": offset}
    normalized_q = q.strip()
    if normalized_q:
        where.append("(title LIKE :q OR content LIKE :q OR qualification_text LIKE :q OR organization LIKE :q)")
        params["q"] = f"%{normalized_q}%"
    if category:
        keywords = TAG_KEYWORDS.get(category)
        if keywords is None:
            raise HTTPException(status_code=400, detail="지원하지 않는 정책 분야입니다.")
        category_parts = []
        for index, keyword in enumerate(keywords):
            key = f"category_{index}"
            category_parts.append(f"(category LIKE :{key} OR title LIKE :{key} OR content LIKE :{key})")
            params[key] = f"%{keyword}%"
        where.append(f"({' OR '.join(category_parts)})")
    if region:
        if region not in POLICY_REGIONS:
            raise HTTPException(status_code=400, detail="지원하지 않는 지역 조건입니다.")
        where.append("target_region = :region")
        params["region"] = region
    if recruitment == "open":
        where.append("(application_end_date IS NULL OR application_end_date >= CURDATE())")
        where.append("(application_start_date IS NULL OR application_start_date <= CURDATE())")
    elif recruitment == "closed":
        where.append("application_end_date < CURDATE()")
    if age is not None:
        where.append("(min_age IS NULL OR min_age <= :age)")
        where.append("(max_age IS NULL OR max_age >= :age)")
        params["age"] = age

    where_sql = " AND ".join(where)
    count_sql = text(f"SELECT COUNT(*) FROM policy_records WHERE {where_sql}")
    list_sql = text(
        f"""SELECT * FROM policy_records WHERE {where_sql}
        ORDER BY
            CASE WHEN application_end_date IS NULL THEN 1 ELSE 0 END,
            application_end_date ASC,
            updated_at DESC
        LIMIT :limit OFFSET :offset"""
    )
    async with SessionLocal() as db:
        total = (await db.execute(count_sql, params)).scalar_one()
        rows = (await db.execute(list_sql, params)).mappings().all()
    return {
        "items": [serialize_policy(dict(row)) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/policies/{policy_id}")
async def policy_detail(policy_id: int):
    async with SessionLocal() as db:
        row = (
            await db.execute(
                text("""SELECT * FROM policy_records
                    WHERE id = :policy_id
                    AND target_region IN ('목포', '전남(목포 포함)', '전국(목포 포함)')"""),
                {"policy_id": policy_id},
            )
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="정책 정보를 찾지 못했습니다.")
    return serialize_policy(dict(row), detail=True)


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
    for field, allowed in PROFILE_OPTIONS.items():
        value = getattr(payload, field)
        if value and value not in allowed:
            raise HTTPException(status_code=400, detail=f"지원하지 않는 {field} 값입니다.")
    async with SessionLocal() as db:
        user = await current_user(request, db)
        now = datetime.now().replace(microsecond=0)
        await db.execute(
            text("""UPDATE user_profiles SET birth_date = :birth_date,
                residency_months = :residency_months, employment_status = :employment_status,
                income_band = :income_band, education_level = :education_level,
                household_status = :household_status, updated_at = :now
                WHERE id = :id"""),
            {
                "birth_date": payload.birth_date,
                "residency_months": payload.residency_months,
                "employment_status": payload.employment_status,
                "income_band": payload.income_band,
                "education_level": payload.education_level,
                "household_status": payload.household_status,
                "now": now,
                "id": user["id"],
            },
        )
        await db.execute(text("DELETE FROM user_interests WHERE user_id = :id"), {"id": user["id"]})
        await db.execute(text("INSERT INTO user_interests (user_id, interest_tag) VALUES (:user_id, :interest_tag)"), [{"user_id": user["id"], "interest_tag": tag} for tag in sorted(set(payload.interests))])
        await db.commit()
    await asyncio.to_thread(PolicyMatcher().create_candidates)
    return {"ok": True}


@app.get("/api/policies/{policy_id}/eligibility")
async def policy_eligibility(policy_id: int, request: Request):
    async with SessionLocal() as db:
        user = await current_user(request, db)
        row = (
            await db.execute(
                text("""SELECT * FROM policy_records
                    WHERE id = :policy_id
                    AND target_region IN ('목포', '전남(목포 포함)', '전국(목포 포함)')"""),
                {"policy_id": policy_id},
            )
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="정책 정보를 찾지 못했습니다.")
    return diagnose_eligibility(user, dict(row))


@app.get("/api/policies/{policy_id}/wishlist")
async def wishlist_state(policy_id: int, request: Request):
    async with SessionLocal() as db:
        user = await current_user(request, db)
        row = (
            await db.execute(
                text("SELECT notifications_enabled, created_at FROM policy_wishlists WHERE user_id = :user_id AND policy_id = :policy_id"),
                {"user_id": user["id"], "policy_id": policy_id},
            )
        ).mappings().first()
    return {"saved": row is not None, "notifications_enabled": bool(row["notifications_enabled"]) if row else False, "saved_at": row["created_at"] if row else None}


@app.post("/api/policies/{policy_id}/wishlist")
async def save_wishlist(policy_id: int, payload: WishlistInput, request: Request):
    async with SessionLocal() as db:
        user = await current_user(request, db)
        policy_exists = (
            await db.execute(
                text("""SELECT 1 FROM policy_records WHERE id = :policy_id
                    AND target_region IN ('목포', '전남(목포 포함)', '전국(목포 포함)')"""),
                {"policy_id": policy_id},
            )
        ).scalar_one_or_none()
        if policy_exists is None:
            raise HTTPException(status_code=404, detail="정책 정보를 찾지 못했습니다.")
        now = datetime.now().replace(microsecond=0)
        await db.execute(
            text("""INSERT INTO policy_wishlists (user_id, policy_id, notifications_enabled, created_at, updated_at)
                VALUES (:user_id, :policy_id, :notifications_enabled, :now, :now)
                ON DUPLICATE KEY UPDATE notifications_enabled = VALUES(notifications_enabled), updated_at = VALUES(updated_at)"""),
            {"user_id": user["id"], "policy_id": policy_id, "notifications_enabled": payload.notifications_enabled, "now": now},
        )
        await db.commit()
    return {"saved": True, "notifications_enabled": payload.notifications_enabled}


@app.delete("/api/policies/{policy_id}/wishlist")
async def remove_wishlist(policy_id: int, request: Request):
    async with SessionLocal() as db:
        user = await current_user(request, db)
        result = await db.execute(
            text("DELETE FROM policy_wishlists WHERE user_id = :user_id AND policy_id = :policy_id"),
            {"user_id": user["id"], "policy_id": policy_id},
        )
        await db.commit()
    return {"saved": False, "removed": result.rowcount > 0}


@app.get("/api/wishlist")
async def wishlist(request: Request):
    async with SessionLocal() as db:
        user = await current_user(request, db)
        rows = (
            await db.execute(
                text("""SELECT policy.*, wishlist.notifications_enabled, wishlist.created_at AS saved_at
                    FROM policy_wishlists AS wishlist
                    JOIN policy_records AS policy ON policy.id = wishlist.policy_id
                    WHERE wishlist.user_id = :user_id
                    ORDER BY wishlist.updated_at DESC"""),
                {"user_id": user["id"]},
            )
        ).mappings().all()
    items = []
    for row in rows:
        item = serialize_policy(dict(row))
        item["notifications_enabled"] = bool(row["notifications_enabled"])
        item["saved_at"] = row["saved_at"]
        items.append(item)
    return items


@app.put("/api/notifications/{candidate_id}")
async def update_notification(candidate_id: int, payload: NotificationStatusInput, request: Request):
    if payload.status not in {"notified", "dismissed"}:
        raise HTTPException(status_code=400, detail="지원하지 않는 알림 상태입니다.")
    async with SessionLocal() as db:
        user = await current_user(request, db)
        result = await db.execute(
            text("""UPDATE policy_match_candidates SET status = :status
                WHERE id = :candidate_id AND user_id = :user_id"""),
            {"status": payload.status, "candidate_id": candidate_id, "user_id": user["id"]},
        )
        await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="알림을 찾지 못했습니다.")
    return {"ok": True, "status": payload.status}


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
    return [serialize_policy(dict(row)) for row in rows if eligible_for_policy(user, dict(row), set(user["interests"]))[0]]


@app.get("/api/notifications")
async def notifications(request: Request):
    async with SessionLocal() as db:
        user = await current_user(request, db)
        rows = (await db.execute(text("""SELECT candidate.id, candidate.policy_id, candidate.status,
            event.change_type, policy.title, policy.original_link,
            policy.application_end_date, candidate.match_reason, candidate.created_at
            FROM policy_match_candidates AS candidate
            JOIN policy_records AS policy ON policy.id = candidate.policy_id
            JOIN policy_change_events AS event ON event.id = candidate.event_id
            LEFT JOIN policy_wishlists AS wishlist
              ON wishlist.user_id = candidate.user_id AND wishlist.policy_id = candidate.policy_id
            WHERE candidate.user_id = :user_id AND candidate.status = 'pending'
              AND (wishlist.user_id IS NULL OR wishlist.notifications_enabled = TRUE)
            ORDER BY candidate.created_at DESC"""), {"user_id": user["id"]})).mappings().all()
    return [dict(row) for row in rows]
