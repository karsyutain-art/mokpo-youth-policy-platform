"""FastAPI API for the React + Vite youth-policy service."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime
from urllib.parse import quote, urlencode

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.middleware.sessions import SessionMiddleware

from application_extractor import extract_requirement_candidates
from application_form_extractor import extract_form_field_candidates
from hwpx_exporter import build_hwpx
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
    legal_name: str | None = Field(default=None, max_length=100)
    phone_number: str | None = Field(default=None, max_length=30)
    postal_code: str | None = Field(default=None, max_length=20)
    address_line1: str | None = Field(default=None, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)


class WishlistInput(BaseModel):
    notifications_enabled: bool = True


class NotificationStatusInput(BaseModel):
    status: str


class ChatInput(BaseModel):
    question: str = Field(min_length=2, max_length=500)


class PreparationUpdateInput(BaseModel):
    source_confirmed: bool | None = None
    status: str | None = None


class RequirementCreateInput(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    is_required: bool = True
    issuing_organization: str | None = Field(default=None, max_length=255)
    validity_text: str | None = Field(default=None, max_length=255)
    submission_format: str | None = Field(default=None, max_length=100)
    evidence_text: str | None = Field(default=None, max_length=2000)
    user_note: str | None = Field(default=None, max_length=1000)


class RequirementUpdateInput(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    is_required: bool | None = None
    issuing_organization: str | None = Field(default=None, max_length=255)
    validity_text: str | None = Field(default=None, max_length=255)
    submission_format: str | None = Field(default=None, max_length=100)
    evidence_text: str | None = Field(default=None, max_length=2000)
    preparation_status: str | None = None
    user_note: str | None = Field(default=None, max_length=1000)
    user_confirmed: bool | None = None


class FormFieldCreateInput(BaseModel):
    label: str = Field(min_length=1, max_length=300)
    field_type: str = "text"
    is_required: bool = False
    max_length: int | None = Field(default=None, ge=1, le=5000)
    source_evidence: str | None = Field(default=None, max_length=2000)
    autofill_profile_key: str | None = None
    autofill_consent: bool = False


class FormFieldUpdateInput(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=300)
    field_type: str | None = None
    is_required: bool | None = None
    max_length: int | None = Field(default=None, ge=1, le=5000)
    value_text: str | None = Field(default=None, max_length=10000)
    user_confirmed: bool | None = None


class DraftInput(BaseModel):
    instruction: str | None = Field(default=None, max_length=500)


POLICY_REGIONS = ("목포", "전남(목포 포함)", "전국(목포 포함)")
POLICY_SEARCH_LIMIT = 100
PROFILE_OPTIONS = {
    "employment_status": {"미취업", "구직 중", "재직 중", "프리랜서", "창업/사업자", "학생", "기타"},
    "income_band": {"중위소득 50% 이하", "중위소득 50~100%", "중위소득 100~150%", "중위소득 150% 초과", "확인 어려움"},
    "education_level": {"고졸 이하", "대학 재학", "대학 휴학", "대학 졸업", "대학원", "기타"},
    "household_status": {"1인 가구", "부모 동거", "부부/자녀", "한부모", "기타"},
}
PREPARATION_STATUSES = {"draft", "ready"}
REQUIREMENT_STATUSES = {"not_started", "in_progress", "completed", "not_applicable"}
FORM_FIELD_TYPES = {"text", "textarea", "date"}
AUTOFILL_PROFILE_KEYS = {"legal_name", "birth_date", "phone_number", "postal_code", "address_line1", "address_line2", "education_level", "employment_status"}


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
    row = (await db.execute(text("""SELECT id, display_name, legal_name, phone_number, postal_code, address_line1, address_line2,
        birth_date, residency_city, residency_months, employment_status, income_band, education_level, household_status
        FROM user_profiles WHERE id = :id"""), {"id": user_id})).mappings().first()
    if not row:
        request.session.clear()
        raise HTTPException(status_code=401, detail="사용자 정보를 찾지 못했습니다.")
    user = dict(row)
    interests = (await db.execute(text("SELECT interest_tag FROM user_interests WHERE user_id = :id ORDER BY interest_tag"), {"id": user_id})).scalars().all()
    user["interests"] = interests
    return user


def default_requirements(policy: dict) -> list[dict]:
    """Create a conservative starter checklist without inventing policy-specific documents."""
    items = [
        {
            "title": "공식 공고의 신청 대상과 자격조건 확인",
            "is_required": True,
            "evidence_text": policy.get("qualification_text") or policy.get("target_condition") or "공식 공고에서 신청 자격을 확인해 주세요.",
            "source_type": "checklist",
            "extraction_confidence": None,
        },
        {
            "title": "신청 기간과 접수 방법 확인",
            "is_required": True,
            "evidence_text": policy.get("application_method") or policy.get("period_text") or "공식 공고에서 신청 기간과 접수 방법을 확인해 주세요.",
            "source_type": "checklist",
            "extraction_confidence": None,
        },
    ]
    if policy.get("attachment_links") or policy.get("attachment_status"):
        items.append(
            {
                "title": "공고 첨부파일과 신청 양식 확인",
                "is_required": True,
                "evidence_text": policy.get("attachment_status") or "첨부된 공고문과 신청 양식을 확인해 주세요.",
                "source_type": "checklist",
                "extraction_confidence": None,
            }
        )
    items.append(
        {
            "title": "필수 증빙서류를 공식 원문과 대조",
            "is_required": True,
            "evidence_text": "자동 추출 전 기본 항목입니다. 공식 공고를 확인한 뒤 필요한 서류를 직접 추가해 주세요.",
            "source_type": "checklist",
            "extraction_confidence": None,
        }
    )
    return items


async def owned_preparation(db: AsyncSession, preparation_id: int, user_id: int) -> dict:
    row = (
        await db.execute(
            text("""SELECT preparation.*, policy.title AS current_policy_title,
                policy.content_hash AS current_content_hash, policy.organization,
                policy.application_end_date
                FROM application_preparations AS preparation
                JOIN policy_records AS policy ON policy.id = preparation.policy_id
                WHERE preparation.id = :preparation_id AND preparation.user_id = :user_id"""),
            {"preparation_id": preparation_id, "user_id": user_id},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="신청 준비 건을 찾지 못했습니다.")
    return dict(row)


async def preparation_payload(db: AsyncSession, preparation: dict) -> dict:
    requirements = (
        await db.execute(
            text("""SELECT id, title, is_required, issuing_organization, validity_text,
                submission_format, evidence_text, preparation_status, user_note,
                source_type, extraction_confidence, user_confirmed, sort_order, created_at, updated_at
                FROM application_requirements WHERE preparation_id = :preparation_id
                ORDER BY sort_order, id"""),
            {"preparation_id": preparation["id"]},
        )
    ).mappings().all()
    form_fields = (
        await db.execute(
            text("""SELECT id, label, field_type, is_required, max_length, source_evidence,
                source_type, autofill_profile_key, value_text, auto_filled, user_confirmed, sort_order, created_at, updated_at
                FROM application_form_fields WHERE preparation_id = :preparation_id ORDER BY sort_order, id"""),
            {"preparation_id": preparation["id"]},
        )
    ).mappings().all()
    item = dict(preparation)
    current_hash = item.pop("current_content_hash", None)
    item["policy_changed"] = bool(current_hash and current_hash != item["policy_content_hash_snapshot"])
    item["requirements"] = [dict(row) for row in requirements]
    item["form_fields"] = [dict(row) for row in form_fields]
    item["completed_count"] = sum(row["preparation_status"] in {"completed", "not_applicable"} for row in requirements)
    item["total_count"] = len(requirements)
    return item


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


@app.post("/api/policies/{policy_id}/preparations")
async def create_preparation(policy_id: int, request: Request):
    async with SessionLocal() as db:
        user = await current_user(request, db)
        policy_row = (
            await db.execute(
                text("""SELECT * FROM policy_records WHERE id = :policy_id
                    AND target_region IN ('목포', '전남(목포 포함)', '전국(목포 포함)')"""),
                {"policy_id": policy_id},
            )
        ).mappings().first()
        if policy_row is None:
            raise HTTPException(status_code=404, detail="정책 정보를 찾지 못했습니다.")
        existing_id = (
            await db.execute(
                text("SELECT id FROM application_preparations WHERE user_id = :user_id AND policy_id = :policy_id"),
                {"user_id": user["id"], "policy_id": policy_id},
            )
        ).scalar_one_or_none()
        if existing_id is None:
            policy = dict(policy_row)
            now = datetime.now().replace(microsecond=0)
            result = await db.execute(
                text("""INSERT INTO application_preparations
                    (user_id, policy_id, policy_title_snapshot, policy_content_hash_snapshot,
                     original_link_snapshot, policy_verified_at, status, source_confirmed, created_at, updated_at)
                    VALUES (:user_id, :policy_id, :title, :content_hash, :original_link,
                            :verified_at, 'draft', FALSE, :now, :now)"""),
                {
                    "user_id": user["id"], "policy_id": policy_id, "title": policy["title"],
                    "content_hash": policy["content_hash"], "original_link": policy.get("original_link"),
                    "verified_at": policy.get("last_seen_at"), "now": now,
                },
            )
            existing_id = result.lastrowid
            extracted = extract_requirement_candidates(policy)
            initial_requirements = [*default_requirements(policy), *extracted]
            for index, requirement in enumerate(initial_requirements):
                await db.execute(
                    text("""INSERT INTO application_requirements
                        (preparation_id, title, is_required, evidence_text, preparation_status,
                         source_type, extraction_confidence, user_confirmed, sort_order, created_at, updated_at)
                        VALUES (:preparation_id, :title, :is_required, :evidence_text, 'not_started',
                                :source_type, :extraction_confidence, FALSE, :sort_order, :now, :now)"""),
                    {"preparation_id": existing_id, "sort_order": index, "now": now, **requirement},
                )
            await db.commit()
        preparation = await owned_preparation(db, int(existing_id), user["id"])
        return await preparation_payload(db, preparation)


@app.get("/api/preparations")
async def list_preparations(request: Request):
    async with SessionLocal() as db:
        user = await current_user(request, db)
        rows = (
            await db.execute(
                text("""SELECT preparation.*, policy.title AS current_policy_title,
                    policy.content_hash AS current_content_hash, policy.organization,
                    policy.application_end_date,
                    COUNT(requirement.id) AS total_count,
                    COALESCE(SUM(requirement.preparation_status IN ('completed', 'not_applicable')), 0) AS completed_count
                    FROM application_preparations AS preparation
                    JOIN policy_records AS policy ON policy.id = preparation.policy_id
                    LEFT JOIN application_requirements AS requirement ON requirement.preparation_id = preparation.id
                    WHERE preparation.user_id = :user_id
                    GROUP BY preparation.id, policy.title, policy.content_hash, policy.organization, policy.application_end_date
                    ORDER BY preparation.updated_at DESC, preparation.id DESC"""),
                {"user_id": user["id"]},
            )
        ).mappings().all()
    items = []
    for row in rows:
        item = dict(row)
        current_hash = item.pop("current_content_hash", None)
        item["policy_changed"] = bool(current_hash and current_hash != item["policy_content_hash_snapshot"])
        item["total_count"] = int(item["total_count"] or 0)
        item["completed_count"] = int(item["completed_count"] or 0)
        items.append(item)
    return items


@app.post("/api/preparations/{preparation_id}/extract")
async def extract_preparation_requirements(preparation_id: int, request: Request):
    """Add only new, evidence-backed candidates; existing user edits always remain."""
    async with SessionLocal() as db:
        user = await current_user(request, db)
        preparation = await owned_preparation(db, preparation_id, user["id"])
        policy_row = (
            await db.execute(text("SELECT * FROM policy_records WHERE id = :id"), {"id": preparation["policy_id"]})
        ).mappings().one()
        candidates = extract_requirement_candidates(dict(policy_row))
        existing_rows = (
            await db.execute(
                text("SELECT title, evidence_text FROM application_requirements WHERE preparation_id = :id"),
                {"id": preparation_id},
            )
        ).mappings().all()
        existing_keys = {
            "".join(ch for ch in f"{row['title']}|{row['evidence_text'] or ''}".lower() if ch.isalnum() or '가' <= ch <= '힣')
            for row in existing_rows
        }
        next_order = (
            await db.execute(
                text("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM application_requirements WHERE preparation_id = :id"),
                {"id": preparation_id},
            )
        ).scalar_one()
        now = datetime.now().replace(microsecond=0)
        added = 0
        for candidate in candidates:
            key = "".join(ch for ch in f"{candidate['title']}|{candidate['evidence_text']}".lower() if ch.isalnum() or '가' <= ch <= '힣')
            if key in existing_keys:
                continue
            await db.execute(
                text("""INSERT INTO application_requirements
                    (preparation_id, title, is_required, issuing_organization, validity_text,
                     submission_format, evidence_text, preparation_status, source_type,
                     extraction_confidence, user_confirmed, sort_order, created_at, updated_at)
                    VALUES (:preparation_id, :title, :is_required, :issuing_organization, :validity_text,
                            :submission_format, :evidence_text, 'not_started', 'extracted',
                            :extraction_confidence, FALSE, :sort_order, :now, :now)"""),
                {"preparation_id": preparation_id, "sort_order": next_order, "now": now, **candidate},
            )
            existing_keys.add(key)
            next_order += 1
            added += 1
        if added:
            await db.execute(text("UPDATE application_preparations SET updated_at = :now WHERE id = :id"), {"id": preparation_id, "now": now})
            await db.commit()
        updated_preparation = await owned_preparation(db, preparation_id, user["id"])
        payload = await preparation_payload(db, updated_preparation)
    return {"added": added, "preparation": payload}


@app.get("/api/preparations/{preparation_id}")
async def get_preparation(preparation_id: int, request: Request):
    async with SessionLocal() as db:
        user = await current_user(request, db)
        preparation = await owned_preparation(db, preparation_id, user["id"])
        return await preparation_payload(db, preparation)


@app.put("/api/preparations/{preparation_id}")
async def update_preparation(preparation_id: int, payload: PreparationUpdateInput, request: Request):
    if payload.status is not None and payload.status not in PREPARATION_STATUSES:
        raise HTTPException(status_code=400, detail="지원하지 않는 신청 준비 상태입니다.")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="변경할 값이 없습니다.")
    async with SessionLocal() as db:
        user = await current_user(request, db)
        await owned_preparation(db, preparation_id, user["id"])
        assignments = [f"{column} = :{column}" for column in changes]
        changes.update({"preparation_id": preparation_id, "now": datetime.now().replace(microsecond=0)})
        await db.execute(text(f"UPDATE application_preparations SET {', '.join(assignments)}, updated_at = :now WHERE id = :preparation_id"), changes)
        await db.commit()
        preparation = await owned_preparation(db, preparation_id, user["id"])
        return await preparation_payload(db, preparation)


@app.delete("/api/preparations/{preparation_id}")
async def delete_preparation(preparation_id: int, request: Request):
    async with SessionLocal() as db:
        user = await current_user(request, db)
        await owned_preparation(db, preparation_id, user["id"])
        await db.execute(text("DELETE FROM application_preparations WHERE id = :id"), {"id": preparation_id})
        await db.commit()
    return {"deleted": True}


@app.post("/api/preparations/{preparation_id}/requirements")
async def create_requirement(preparation_id: int, payload: RequirementCreateInput, request: Request):
    async with SessionLocal() as db:
        user = await current_user(request, db)
        await owned_preparation(db, preparation_id, user["id"])
        sort_order = (
            await db.execute(
                text("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM application_requirements WHERE preparation_id = :id"),
                {"id": preparation_id},
            )
        ).scalar_one()
        now = datetime.now().replace(microsecond=0)
        values = payload.model_dump()
        result = await db.execute(
            text("""INSERT INTO application_requirements
                (preparation_id, title, is_required, issuing_organization, validity_text,
                 submission_format, evidence_text, preparation_status, user_note,
                 source_type, user_confirmed, sort_order, created_at, updated_at)
                VALUES (:preparation_id, :title, :is_required, :issuing_organization, :validity_text,
                        :submission_format, :evidence_text, 'not_started', :user_note,
                        'manual', TRUE, :sort_order, :now, :now)"""),
            {"preparation_id": preparation_id, "sort_order": sort_order, "now": now, **values},
        )
        await db.execute(text("UPDATE application_preparations SET updated_at = :now WHERE id = :id"), {"id": preparation_id, "now": now})
        await db.commit()
        requirement_id = result.lastrowid
        row = (
            await db.execute(text("SELECT * FROM application_requirements WHERE id = :id"), {"id": requirement_id})
        ).mappings().one()
    return dict(row)


@app.put("/api/preparations/{preparation_id}/requirements/{requirement_id}")
async def update_requirement(preparation_id: int, requirement_id: int, payload: RequirementUpdateInput, request: Request):
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="변경할 값이 없습니다.")
    if changes.get("preparation_status") is not None and changes["preparation_status"] not in REQUIREMENT_STATUSES:
        raise HTTPException(status_code=400, detail="지원하지 않는 준비 상태입니다.")
    async with SessionLocal() as db:
        user = await current_user(request, db)
        await owned_preparation(db, preparation_id, user["id"])
        requirement_exists = (
            await db.execute(
                text("SELECT 1 FROM application_requirements WHERE id = :requirement_id AND preparation_id = :preparation_id"),
                {"requirement_id": requirement_id, "preparation_id": preparation_id},
            )
        ).scalar_one_or_none()
        if requirement_exists is None:
            raise HTTPException(status_code=404, detail="체크리스트 항목을 찾지 못했습니다.")
        assignments = [f"{column} = :{column}" for column in changes]
        now = datetime.now().replace(microsecond=0)
        changes.update({"requirement_id": requirement_id, "now": now})
        await db.execute(text(f"UPDATE application_requirements SET {', '.join(assignments)}, updated_at = :now WHERE id = :requirement_id"), changes)
        await db.execute(text("UPDATE application_preparations SET updated_at = :now WHERE id = :id"), {"id": preparation_id, "now": now})
        await db.commit()
        row = (
            await db.execute(text("SELECT * FROM application_requirements WHERE id = :id"), {"id": requirement_id})
        ).mappings().one()
    return dict(row)


@app.delete("/api/preparations/{preparation_id}/requirements/{requirement_id}")
async def delete_requirement(preparation_id: int, requirement_id: int, request: Request):
    async with SessionLocal() as db:
        user = await current_user(request, db)
        await owned_preparation(db, preparation_id, user["id"])
        result = await db.execute(
            text("DELETE FROM application_requirements WHERE id = :requirement_id AND preparation_id = :preparation_id"),
            {"requirement_id": requirement_id, "preparation_id": preparation_id},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="체크리스트 항목을 찾지 못했습니다.")
        await db.execute(
            text("UPDATE application_preparations SET updated_at = :now WHERE id = :id"),
            {"id": preparation_id, "now": datetime.now().replace(microsecond=0)},
        )
        await db.commit()
    return {"deleted": True}


@app.post("/api/preparations/{preparation_id}/form-fields")
async def create_form_field(preparation_id: int, payload: FormFieldCreateInput, request: Request):
    if payload.field_type not in FORM_FIELD_TYPES:
        raise HTTPException(status_code=400, detail="지원하지 않는 문항 형식입니다.")
    if payload.autofill_profile_key and payload.autofill_profile_key not in AUTOFILL_PROFILE_KEYS:
        raise HTTPException(status_code=400, detail="지원하지 않는 자동 채움 항목입니다.")
    async with SessionLocal() as db:
        user = await current_user(request, db)
        await owned_preparation(db, preparation_id, user["id"])
        value = None
        profile_key = payload.autofill_profile_key if payload.autofill_consent else None
        if profile_key:
            value = user.get(profile_key)
            if value is None or value == "":
                raise HTTPException(status_code=400, detail="선택한 프로필 값이 비어 있습니다. 프로필에서 먼저 입력해 주세요.")
            value = str(value)
        sort_order = (await db.execute(text("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM application_form_fields WHERE preparation_id = :id"), {"id": preparation_id})).scalar_one()
        now = datetime.now().replace(microsecond=0)
        result = await db.execute(text("""INSERT INTO application_form_fields
            (preparation_id, label, field_type, is_required, max_length, source_evidence, source_type, autofill_profile_key,
             value_text, auto_filled, user_confirmed, sort_order, created_at, updated_at)
            VALUES (:preparation_id, :label, :field_type, :is_required, :max_length, :source_evidence, 'manual', :profile_key,
                    :value_text, :auto_filled, FALSE, :sort_order, :now, :now)"""),
            {"preparation_id": preparation_id, "label": payload.label, "field_type": payload.field_type, "is_required": payload.is_required, "max_length": payload.max_length, "source_evidence": payload.source_evidence, "profile_key": profile_key, "value_text": value, "auto_filled": bool(profile_key), "sort_order": sort_order, "now": now})
        await db.commit()
        row = (await db.execute(text("SELECT * FROM application_form_fields WHERE id = :id"), {"id": result.lastrowid})).mappings().one()
    return dict(row)


@app.post("/api/preparations/{preparation_id}/form-fields/extract")
async def extract_form_fields(preparation_id: int, request: Request):
    """Add visible application-form labels only; never apply profile values automatically."""
    async with SessionLocal() as db:
        user = await current_user(request, db)
        preparation = await owned_preparation(db, preparation_id, user["id"])
        policy = (await db.execute(text("SELECT * FROM policy_records WHERE id = :id"), {"id": preparation["policy_id"]})).mappings().one()
        candidates = extract_form_field_candidates(dict(policy))
        existing = (await db.execute(text("SELECT label, source_evidence FROM application_form_fields WHERE preparation_id = :id"), {"id": preparation_id})).mappings().all()
        existing_keys = {"".join(ch for ch in f"{row['label']}|{row['source_evidence'] or ''}".lower() if ch.isalnum() or '가' <= ch <= '힣') for row in existing}
        order = (await db.execute(text("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM application_form_fields WHERE preparation_id = :id"), {"id": preparation_id})).scalar_one()
        now = datetime.now().replace(microsecond=0)
        added = 0
        for candidate in candidates:
            key = "".join(ch for ch in f"{candidate['label']}|{candidate['source_evidence']}".lower() if ch.isalnum() or '가' <= ch <= '힣')
            if key in existing_keys:
                continue
            await db.execute(text("""INSERT INTO application_form_fields
                (preparation_id, label, field_type, is_required, max_length, source_evidence, source_type,
                 autofill_profile_key, value_text, auto_filled, user_confirmed, sort_order, created_at, updated_at)
                VALUES (:preparation_id, :label, :field_type, :is_required, :max_length, :source_evidence, 'extracted',
                        NULL, NULL, FALSE, FALSE, :sort_order, :now, :now)"""),
                {"preparation_id": preparation_id, "sort_order": order, "now": now, **candidate})
            existing_keys.add(key); order += 1; added += 1
        if added:
            await db.execute(text("UPDATE application_preparations SET updated_at = :now WHERE id = :id"), {"id": preparation_id, "now": now})
            await db.commit()
        updated = await owned_preparation(db, preparation_id, user["id"])
        return {"added": added, "preparation": await preparation_payload(db, updated)}


@app.post("/api/preparations/{preparation_id}/form-fields/{field_id}/draft")
async def draft_form_field(preparation_id: int, field_id: int, payload: DraftInput, request: Request):
    """Draft only a narrative field, grounding Gemini in the official policy text."""
    async with SessionLocal() as db:
        user = await current_user(request, db)
        preparation = await owned_preparation(db, preparation_id, user["id"])
        field = (await db.execute(text("SELECT * FROM application_form_fields WHERE id = :field_id AND preparation_id = :preparation_id"), {"field_id": field_id, "preparation_id": preparation_id})).mappings().first()
        if field is None:
            raise HTTPException(status_code=404, detail="신청 문항을 찾지 못했습니다.")
        if field["field_type"] != "textarea":
            raise HTTPException(status_code=400, detail="서술형 문항에만 초안을 만들 수 있습니다.")
        policy = (await db.execute(text("SELECT title, content, qualification_text, application_method FROM policy_records WHERE id = :id"), {"id": preparation["policy_id"]})).mappings().one()
    context = "\n".join(str(policy.get(key) or "") for key in ("title", "content", "qualification_text", "application_method"))[:6000]
    prompt = f"""목포 청년정책 신청서의 '{field['label']}' 항목 초안을 한국어로 작성하세요.
공식 정책 근거에 없는 개인 경험·학력·경력·성과는 절대 지어내지 마세요. 모르는 개인 정보는 [직접 작성]으로 남기세요.
과장된 표현을 피하고 300자 이내로 작성하세요.\n\n공식 정책 근거:\n{context}\n\n사용자 요청: {payload.instruction or '정책 목적에 맞는 짧은 초안'}"""
    generated = ""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if api_key:
        try:
            from google import genai
            response = genai.Client(api_key=api_key).models.generate_content(model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"), contents=prompt)
            generated = (response.text or "").strip()
        except Exception:
            generated = ""
    if not generated:
        generated = f"본인은 ‘{policy['title']}’의 목적과 신청 요건을 확인했습니다. 지원을 통해 [직접 작성]을 이루고자 하며, 안내된 절차와 제출 요건을 성실히 준수하겠습니다."
    if field["max_length"]:
        generated = generated[:field["max_length"]]
    return {"draft": generated, "generated": bool(api_key and generated), "notice": "초안은 사실과 다를 수 있으므로 본인 경험과 공식 공고를 대조한 뒤 저장하세요."}


@app.get("/api/preparations/{preparation_id}/export/hwpx")
async def export_preparation_hwpx(preparation_id: int, request: Request):
    async with SessionLocal() as db:
        user = await current_user(request, db)
        preparation = await owned_preparation(db, preparation_id, user["id"])
        payload = await preparation_payload(db, preparation)
    filename = "신청준비_" + "".join(ch for ch in payload["current_policy_title"] if ch.isalnum() or ch in " _-")[:60] + ".hwpx"
    return Response(content=build_hwpx(payload), media_type="application/hwp+zip", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@app.put("/api/preparations/{preparation_id}/form-fields/{field_id}")
async def update_form_field(preparation_id: int, field_id: int, payload: FormFieldUpdateInput, request: Request):
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("field_type") and changes["field_type"] not in FORM_FIELD_TYPES:
        raise HTTPException(status_code=400, detail="지원하지 않는 문항 형식입니다.")
    if not changes:
        raise HTTPException(status_code=400, detail="변경할 값이 없습니다.")
    async with SessionLocal() as db:
        user = await current_user(request, db)
        await owned_preparation(db, preparation_id, user["id"])
        exists = (await db.execute(text("SELECT 1 FROM application_form_fields WHERE id=:field_id AND preparation_id=:preparation_id"), {"field_id": field_id, "preparation_id": preparation_id})).scalar_one_or_none()
        if exists is None:
            raise HTTPException(status_code=404, detail="신청 문항을 찾지 못했습니다.")
        assignments = [f"{column} = :{column}" for column in changes]
        changes.update({"field_id": field_id, "now": datetime.now().replace(microsecond=0)})
        await db.execute(text(f"UPDATE application_form_fields SET {', '.join(assignments)}, auto_filled = FALSE, updated_at = :now WHERE id=:field_id"), changes)
        await db.commit()
        row = (await db.execute(text("SELECT * FROM application_form_fields WHERE id=:id"), {"id": field_id})).mappings().one()
    return dict(row)


@app.delete("/api/preparations/{preparation_id}/form-fields/{field_id}")
async def delete_form_field(preparation_id: int, field_id: int, request: Request):
    async with SessionLocal() as db:
        user = await current_user(request, db)
        await owned_preparation(db, preparation_id, user["id"])
        result = await db.execute(text("DELETE FROM application_form_fields WHERE id=:field_id AND preparation_id=:preparation_id"), {"field_id": field_id, "preparation_id": preparation_id})
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="신청 문항을 찾지 못했습니다.")
        await db.commit()
    return {"deleted": True}


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
                household_status = :household_status, legal_name = :legal_name,
                phone_number = :phone_number, postal_code = :postal_code,
                address_line1 = :address_line1, address_line2 = :address_line2, updated_at = :now
                WHERE id = :id"""),
            {
                "birth_date": payload.birth_date,
                "residency_months": payload.residency_months,
                "employment_status": payload.employment_status,
                "income_band": payload.income_band,
                "education_level": payload.education_level,
                "household_status": payload.household_status,
                "legal_name": payload.legal_name,
                "phone_number": payload.phone_number,
                "postal_code": payload.postal_code,
                "address_line1": payload.address_line1,
                "address_line2": payload.address_line2,
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
