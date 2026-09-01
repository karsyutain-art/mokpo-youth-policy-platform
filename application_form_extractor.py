"""Evidence-preserving extraction of application form questions.

Only labels that are visibly present in an official notice or attachment become
suggestions.  The extractor deliberately never copies profile information.
"""

from __future__ import annotations

import re
from typing import Any


FIELD_PATTERN = re.compile(
    r"(?:성명|이름|생년월일|연락처|휴대폰|전화번호|주소|우편번호|학력|취업\s*상태|"
    r"지원\s*동기|자기소개|활동\s*계획|사업\s*계획|신청\s*사유|경력|이메일)"
)
FORM_SECTION = re.compile(r"신청\s*서|신청\s*양식|작성\s*내용|기재\s*사항|제출\s*양식")
PREFIX = re.compile(r"^\s*(?:[-•●◦▪□※]|\d+[.)]|[가-힣][.)]|[①-⑳])\s*")


def _segments(source: str) -> list[str]:
    source = source.replace("\r", "\n").replace("\t", " ")
    return [re.sub(r"\s+", " ", part).strip() for part in re.split(r"\n+|[|]", source) if part.strip()]


def _profile_key(label: str) -> str | None:
    mapping = (("성명", "legal_name"), ("이름", "legal_name"), ("생년", "birth_date"),
               ("연락", "phone_number"), ("휴대", "phone_number"), ("전화", "phone_number"),
               ("우편", "postal_code"), ("주소", "address_line1"), ("학력", "education_level"),
               ("취업", "employment_status"))
    return next((key for word, key in mapping if word in label), None)


def extract_form_field_candidates(policy: dict[str, Any], *, limit: int = 12) -> list[dict[str, Any]]:
    source = "\n".join(str(policy.get(key) or "") for key in ("attachment_text", "content", "application_method"))
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    in_form_section = False
    for segment in _segments(source):
        in_form_section = bool(FORM_SECTION.search(segment)) or (in_form_section and len(segment) < 400)
        match = FIELD_PATTERN.search(segment)
        if not match or not (in_form_section or FORM_SECTION.search(segment)):
            continue
        label = PREFIX.sub("", segment).split(":", 1)[0].strip(" -:：")[:300]
        if len(label) < 2:
            continue
        key = re.sub(r"[^0-9a-z가-힣]", "", label.lower())
        if key in seen:
            continue
        seen.add(key)
        narrative = bool(re.search(r"지원\s*동기|자기소개|계획|사유|경력", label))
        candidates.append({
            "label": label,
            "field_type": "textarea" if narrative else "text",
            "is_required": not bool(re.search(r"선택|해당자|해당\s*시", segment)),
            "max_length": None,
            "source_evidence": segment[:2000],
            "autofill_profile_key": _profile_key(label),
        })
        if len(candidates) >= limit:
            break
    return candidates
