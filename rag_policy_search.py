"""Build and query a lightweight FAISS HNSW index over policy source text."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import faiss
import numpy as np

from mysql_policy_repository import MySQLPolicyRepository
from youth_data_collector import load_local_env


VECTOR_DIMENSION = 384
EMBEDDING_MODEL = "korean-hashed-char-ngram-v1"
INDEX_DIR = Path("data/rag")
INDEX_PATH = INDEX_DIR / "policy_hnsw.faiss"
METADATA_PATH = INDEX_DIR / "policy_hnsw_metadata.json"


def policy_text(policy: dict[str, Any]) -> str:
    sections = [
        f"정책명: {policy.get('title') or ''}",
        f"분야: {policy.get('category') or ''}",
        f"대상 지역: {policy.get('target_region') or ''}",
        f"담당 기관: {policy.get('organization') or ''}",
        f"신청 대상: {policy.get('qualification_text') or policy.get('target_condition') or ''}",
        f"신청 기간: {policy.get('period_text') or ''}",
        f"지원 내용: {policy.get('content') or ''}",
        f"신청 방법: {policy.get('application_method') or ''}",
        f"첨부 내용: {policy.get('attachment_text') or ''}",
    ]
    return "\n".join(section.strip() for section in sections if section.split(":", 1)[-1].strip())


def split_chunks(text: str, size: int = 850, overlap: int = 120) -> list[str]:
    normalized = re.sub(r"[ \t]+", " ", text).strip()
    if not normalized:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n+", normalized) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > size:
            if current:
                chunks.append(current)
                current = ""
            step = max(1, size - overlap)
            chunks.extend(paragraph[start : start + size] for start in range(0, len(paragraph), step) if paragraph[start : start + size].strip())
            continue
        candidate = f"{current}\n{paragraph}".strip()
        if current and len(candidate) > size:
            chunks.append(current)
            current = f"{current[-overlap:]}\n{paragraph}".strip()
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def embed_text(text: str) -> np.ndarray:
    vector = np.zeros(VECTOR_DIMENSION, dtype="float32")
    tokens = re.findall(r"[가-힣A-Za-z0-9]+", text.lower())
    features: list[str] = []
    for token in tokens:
        features.append(token)
        compact = token.replace(" ", "")
        for width in (2, 3):
            features.extend(compact[index : index + width] for index in range(max(0, len(compact) - width + 1)))
    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "little")
        index = value % VECTOR_DIMENSION
        vector[index] += 1.0 if value & (1 << 63) else -1.0
    norm = np.linalg.norm(vector)
    if norm:
        vector /= norm
    return vector


class PolicyRAG:
    def __init__(self) -> None:
        load_local_env()
        self.repository = MySQLPolicyRepository()

    def rebuild(self) -> dict[str, int]:
        connection = self.repository.connect()
        self.repository.initialize(connection)
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute("""SELECT * FROM policy_records
                WHERE target_region IN ('목포', '전남(목포 포함)', '전국(목포 포함)')
                ORDER BY id""")
            policies = cursor.fetchall()
            cursor.execute("DELETE FROM policy_chunks")
            vectors: list[np.ndarray] = []
            metadata: list[dict[str, int]] = []
            now = datetime.now().replace(microsecond=0)
            for policy in policies:
                for chunk_index, content in enumerate(split_chunks(policy_text(policy))):
                    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    cursor.execute(
                        """INSERT INTO policy_chunks
                        (policy_id, chunk_index, content, content_hash, embedding_model, vector_dimension, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (policy["id"], chunk_index, content, content_hash, EMBEDDING_MODEL, VECTOR_DIMENSION, now),
                    )
                    metadata.append({"chunk_id": cursor.lastrowid, "policy_id": policy["id"]})
                    vectors.append(embed_text(content))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

        index = faiss.IndexHNSWFlat(VECTOR_DIMENSION, 32, faiss.METRIC_INNER_PRODUCT)
        index.hnsw.efConstruction = 80
        index.hnsw.efSearch = 64
        if vectors:
            index.add(np.vstack(vectors).astype("float32"))
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        temporary_index = INDEX_PATH.with_suffix(".tmp")
        temporary_metadata = METADATA_PATH.with_suffix(".tmp")
        faiss.write_index(index, str(temporary_index))
        temporary_metadata.write_text(json.dumps({"model": EMBEDDING_MODEL, "dimension": VECTOR_DIMENSION, "items": metadata}, ensure_ascii=False), encoding="utf-8")
        temporary_index.replace(INDEX_PATH)
        temporary_metadata.replace(METADATA_PATH)
        return {"policies": len(policies), "chunks": len(metadata)}

    def search(self, question: str, top_k: int = 5, min_score: float = 0.12) -> list[dict[str, Any]]:
        if not INDEX_PATH.exists() or not METADATA_PATH.exists():
            self.rebuild()
        index = faiss.read_index(str(INDEX_PATH))
        metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))["items"]
        if index.ntotal == 0:
            return []
        query = embed_text(question).reshape(1, -1)
        candidate_count = min(max(top_k * 5, 20), index.ntotal)
        scores, positions = index.search(query, candidate_count)
        ranked = [(metadata[position]["chunk_id"], float(score)) for score, position in zip(scores[0], positions[0]) if position >= 0]
        if not ranked:
            return []
        connection = self.repository.connect()
        cursor = connection.cursor(dictionary=True)
        try:
            placeholders = ",".join(["%s"] * len(ranked))
            cursor.execute(
                f"""SELECT chunk.id AS chunk_id, chunk.content, policy.id AS policy_id,
                    policy.title, policy.organization, policy.application_end_date, policy.original_link
                    FROM policy_chunks AS chunk
                    JOIN policy_records AS policy ON policy.id = chunk.policy_id
                    WHERE chunk.id IN ({placeholders})""",
                tuple(chunk_id for chunk_id, _ in ranked),
            )
            rows = {row["chunk_id"]: row for row in cursor.fetchall()}
        finally:
            cursor.close()
            connection.close()
        generic_terms = {"목포", "청년", "정책", "지원", "사업", "알려줘", "가능한", "있는", "받을", "신청"}
        important_terms = {term for term in re.findall(r"[가-힣A-Za-z0-9]+", question.lower()) if len(term) >= 2 and term not in generic_terms}
        results = []
        for chunk_id, score in ranked:
            if chunk_id in rows:
                searchable = f"{rows[chunk_id]['title']}\n{rows[chunk_id]['content']}".lower()
                keyword_matches = sum(1 for term in important_terms if term in searchable)
                combined_score = score + min(0.75, keyword_matches * 0.3)
                results.append({**rows[chunk_id], "score": round(combined_score, 4)})
        results.sort(key=lambda item: item["score"], reverse=True)
        return [result for result in results if result["score"] >= min_score][:top_k]

    def answer(self, question: str) -> dict[str, Any]:
        results = self.search(question)
        if not results:
            return {
                "answer": "확인 가능한 정책정보가 부족합니다. 정책명이나 취업·주거·창업처럼 관심 분야를 포함해 다시 질문해 주세요.",
                "sources": [],
                "grounded": False,
                "generated": False,
            }
        sources = []
        seen: set[int] = set()
        for result in results:
            if result["policy_id"] in seen:
                continue
            seen.add(result["policy_id"])
            sources.append(
                {
                    "policy_id": result["policy_id"],
                    "title": result["title"],
                    "organization": result["organization"],
                    "application_end_date": result["application_end_date"],
                    "original_link": result["original_link"],
                    "excerpt": result["content"][:500],
                    "score": result["score"],
                }
            )
            if len(sources) == 3:
                break
        lead = sources[0]
        fallback = f"질문과 가장 관련 있는 정책은 ‘{lead['title']}’입니다.\n\n{lead['excerpt']}\n\n아래 공식 원문과 담당기관 정보를 함께 확인해 주세요. 최종 신청자격은 원문 기준으로 판단해야 합니다."
        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            return {"answer": fallback, "sources": sources, "grounded": True, "generated": False}
        context = "\n\n".join(
            f"[{index}] 정책명: {source['title']}\n담당기관: {source['organization'] or '확인 필요'}\n신청마감: {source['application_end_date'] or '원문 확인'}\n원문 발췌: {source['excerpt']}"
            for index, source in enumerate(sources, start=1)
        )
        prompt = f"""당신은 목포 거주 청년을 위한 정책 안내 도우미입니다.
아래 '정책 근거'에 있는 정보만 사용해 한국어로 답변하세요.
근거에 없는 기간·금액·자격은 추정하지 말고 '공식 공고 확인 필요'라고 말하세요.
사용자의 최종 신청 가능 여부를 단정하지 마세요.
핵심 내용을 짧은 문단과 목록으로 정리하고, 각 사실 뒤에 [1]처럼 근거 번호를 표시하세요.

사용자 질문: {question}

정책 근거:
{context}
"""
        try:
            from google import genai

            client = genai.Client(api_key=api_key)
            configured_model = os.getenv("GEMINI_MODEL", "").strip()
            for model in dict.fromkeys((configured_model, "gemini-3.5-flash-lite")):
                if not model:
                    continue
                try:
                    response = client.models.generate_content(model=model, contents=prompt)
                    generated = (response.text or "").strip()
                    if generated:
                        return {"answer": generated, "sources": sources, "grounded": True, "generated": True, "model": model}
                except Exception:
                    continue
        except Exception:
            # Quota and transient failures must not hide the retrieved official evidence.
            pass
        return {"answer": fallback, "sources": sources, "grounded": True, "generated": False}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="정책 원문 FAISS HNSW 검색 인덱스")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("rebuild", help="MySQL 정책 원문을 청크화하고 인덱스를 다시 생성")
    search = subcommands.add_parser("search", help="정책 근거 검색")
    search.add_argument("question")
    args = parser.parse_args()
    rag = PolicyRAG()
    if args.command == "rebuild":
        result = rag.rebuild()
        print(f"RAG 인덱스 생성: 정책 {result['policies']}건 / 청크 {result['chunks']}건")
    else:
        print(json.dumps(rag.answer(args.question), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
