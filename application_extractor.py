"""Conservative, evidence-preserving extraction of application document candidates."""

from __future__ import annotations

import re
from typing import Any


DOCUMENT_PATTERN = re.compile(
    r"신청서|주민등록(?:등본|초본)?|통장(?:사본)?|재직(?:증명서)?|졸업(?:증명서)?|"
    r"소득(?:증명|금액)|건강보험|가족관계(?:증명서)?|동의서|사업계획서|사업자등록|"
    r"증명서|서약서|신분증|이력서|계약서|확인서"
)
SECTION_PATTERN = re.compile(r"제출\s*서류|구비\s*서류|첨부\s*서류|필수\s*서류|증빙\s*서류|준비\s*서류")
OPTIONAL_PATTERN = re.compile(r"선택|해당자|해당\s*시|선택사항")
FORMAT_PATTERN = re.compile(r"PDF|HWPX?|DOCX?|엑셀|XLSX?|원본|사본", re.IGNORECASE)
PREFIX_PATTERN = re.compile(r"^\s*(?:[-•●◦▪□※]|\d+[.)]|[가-힣][.)]|[①-⑳])\s*")


def _segments(text: str) -> list[str]:
    normalised = text.replace("\r", "\n").replace("\t", " ")
    normalised = re.sub(r"[|]", "\n", normalised)
    raw = re.split(r"\n+|(?<=[.;])\s+(?=(?:[-•●◦▪□※]|\d+[.)]|[가-힣][.)]|[①-⑳]))", normalised)
    return [re.sub(r"\s+", " ", piece).strip() for piece in raw if piece.strip()]


def _issuing_organization(value: str) -> str | None:
    if "주민등록" in value or "가족관계" in value:
        return "주민센터 또는 정부24"
    if "건강보험" in value:
        return "국민건강보험공단"
    if "재직" in value:
        return "재직 기관"
    if "졸업" in value:
        return "학교"
    if "사업자등록" in value:
        return "국세청 또는 세무서"
    return None


def _title(value: str) -> str:
    value = PREFIX_PATTERN.sub("", value)
    value = re.sub(r"^(?:제출|구비|첨부|필수|증빙)\s*서류\s*[:：-]?\s*", "", value)
    return value[:300].strip(" -:：")


def extract_requirement_candidates(policy: dict[str, Any], *, limit: int = 12) -> list[dict[str, Any]]:
    """Return only document candidates supported by an exact policy/attachment excerpt.

    This intentionally does not infer missing documents. Each candidate stays editable and
    unconfirmed until the user checks the official notice.
    """
    source_parts = [policy.get("attachment_text") or "", policy.get("content") or "", policy.get("application_method") or ""]
    source = "\n".join(part for part in source_parts if part)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    context_active = False
    for segment in _segments(source):
        is_section = bool(SECTION_PATTERN.search(segment))
        has_document = bool(DOCUMENT_PATTERN.search(segment))
        if not has_document:
            context_active = is_section
            continue
        # A document word alone is not enough: retain lines in a document section or
        # lines that explicitly express a submission/attachment action.
        has_action = bool(re.search(r"제출|첨부|지참|발급|준비|등록", segment))
        if not (context_active or is_section or has_action):
            continue
        title = _title(segment)
        key = re.sub(r"[^0-9a-z가-힣]", "", title.lower())
        if len(title) < 2 or key in seen:
            continue
        seen.add(key)
        match = FORMAT_PATTERN.search(segment)
        candidates.append(
            {
                "title": title,
                "is_required": not bool(OPTIONAL_PATTERN.search(segment)),
                "issuing_organization": _issuing_organization(segment),
                "validity_text": None,
                "submission_format": match.group(0).upper() if match else None,
                "evidence_text": segment[:2000],
                "source_type": "extracted",
                "extraction_confidence": 0.90 if is_section or context_active else 0.70,
            }
        )
        if len(candidates) >= limit:
            break
    return candidates
