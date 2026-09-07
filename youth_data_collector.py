"""목포 및 전국 청년 대상 정보를 수집·정규화하는 데모 수집기.

공개 화면에서 제공되는 정보만 저속으로 수집한다. 기본값은 각 대상의 첫
목록 페이지만 읽으며, 로그인·CAPTCHA·접근 제한을 우회하지 않는다.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "YouthPolicyDemoCollector/1.0 (+educational project)"}
GWANGJU_BASE = "https://youth.gwangju.go.kr"
YOUTHCENTER_BASE = "https://www.youthcenter.go.kr"
DB2030_BASE = "https://www.2030db.go.kr"
MOKPO_BASE = "https://www.mokpo.go.kr"


@dataclass(frozen=True)
class Target:
    source_site: str
    category: str
    key: str


TARGETS = {
    "youth_tip": Target("온통청년", "청년 꿀팁", "youth_tip"),
    "youth_channel": Target("온통청년", "청년 정책 채널", "youth_channel"),
    "youth_policy_api": Target("온통청년", "청년정책 API", "youth_policy_api"),
    "youth_intern": Target("청년인재DB", "청년 인턴 채용", "youth_intern"),
    "mokpo_youth_business": Target("목포청년센터 누리", "청년 취업·창업 사업", "mokpo_youth_business"),
    "mokpo_youth_independence": Target("목포청년센터 누리", "청년 자립 지원", "mokpo_youth_independence"),
    "mokpo_city_youth_support": Target("목포시청", "청년 지원사업", "mokpo_city_youth_support"),
    "gwangju_policy": Target("전남광주청년통합플랫폼", "청년정책", "gwangju_policy"),
    "gwangju_job": Target("전남광주청년통합플랫폼", "일자리 드림", "gwangju_job"),
    "gwangju_reside": Target("전남광주청년통합플랫폼", "주거 드림", "gwangju_reside"),
    "gwangju_education": Target("전남광주청년통합플랫폼", "교육 드림", "gwangju_education"),
    "gwangju_welfare": Target("전남광주청년통합플랫폼", "복지 드림", "gwangju_welfare"),
}
DEFAULT_CRAWL_TARGETS = tuple(key for key in TARGETS if key != "youth_policy_api")
YOUTHCENTER_BOARDS = {"youth_tip": "46", "youth_channel": "10001"}
MOKPO_CENTER_URLS = {
    "mokpo_youth_business": f"{MOKPO_BASE}/youthcenter/young_business/business",
    "mokpo_youth_independence": f"{MOKPO_BASE}/youthcenter/young_business/youth",
}
GWANGJU_DREAM_TYPES = {"gwangju_job": "job", "gwangju_reside": "reside", "gwangju_education": "edc", "gwangju_welfare": "wlfare"}
OTHER_LOCAL_GOVERNMENTS = ("서울", "부산", "대구", "인천", "광주광역시", "광주광역", "대전", "울산", "세종", "경기", "강원", "충북", "충남", "전북", "제주", "경북", "경남", "여수", "순천", "광양", "나주", "강진", "고흥", "곡성", "구례", "담양", "무안", "보성", "신안", "영광", "영암", "완도", "장성", "장흥", "진도", "함평", "해남", "화순")
EXPORT_COLUMNS = ("source_site", "category", "title", "target_region", "target_condition", "qualification_text", "min_age", "max_age", "residency_condition", "period", "application_start_date", "application_end_date", "content", "application_method", "organization", "attachment_links", "attachment_files", "attachment_text", "attachment_status", "content_hash", "original_link", "source_record_id", "collected_at")
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
TEXT_ATTACHMENT_EXTENSIONS = {".pdf", ".hwp", ".hwpx"}
PLAYWRIGHT_NAVIGATION_TIMEOUT_MS = 60_000
PLAYWRIGHT_NAVIGATION_ATTEMPTS = 2
STRUCTURE_DIAGNOSTIC_DIR = Path("data/diagnostics")
EMPTY_RESULT_MARKERS = ("목록이 없습니다", "검색 결과가 없습니다", "등록된 게시물이 없습니다", "조회된 데이터가 없습니다")


class StructureChangedError(RuntimeError):
    """Raised when a public page loads but no known site structure is found."""


def select_first(root: Any, selectors: Iterable[str]) -> Any | None:
    """Return the first matching element from an ordered selector fallback list."""
    for selector in selectors:
        node = root.select_one(selector)
        if node is not None:
            return node
    return None


def select_all(root: Any, selectors: Iterable[str]) -> list[Any]:
    """Return the first non-empty set from an ordered selector fallback list."""
    for selector in selectors:
        nodes = root.select(selector)
        if nodes:
            return list(nodes)
    return []


def save_structure_diagnostic(source_key: str, url: str, html: str) -> Path:
    """Keep the unexpected public HTML so a selector regression can be reproduced."""
    STRUCTURE_DIAGNOSTIC_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(html.encode("utf-8", errors="replace")).hexdigest()[:10]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = STRUCTURE_DIAGNOSTIC_DIR / f"{source_key}_{stamp}_{digest}.html"
    html_path.write_text(html, encoding="utf-8", errors="replace")
    meta_path = html_path.with_suffix(".json")
    meta_path.write_text(
        json.dumps({"source": source_key, "url": url, "captured_at": datetime.now(timezone.utc).isoformat(), "sha256": hashlib.sha256(html.encode("utf-8", errors="replace")).hexdigest()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return html_path


def structure_error(source_key: str, url: str, html: str, expected: str) -> StructureChangedError:
    snapshot = save_structure_diagnostic(source_key, url, html)
    return StructureChangedError(f"{source_key} HTML 구조를 인식하지 못했습니다 ({expected}). 진단 파일: {snapshot}")


def has_empty_result_marker(text: str) -> bool:
    return any(marker in text for marker in EMPTY_RESULT_MARKERS)


def text_of(node: Any) -> str:
    if node is None:
        return ""
    return "\n".join(part.strip() for part in node.stripped_strings if part.strip())


def load_local_env(path: Path = Path(".env")) -> None:
    """Load simple KEY=VALUE pairs without adding a dotenv dependency."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def normalize_whitespace(value: str) -> str:
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", value)).strip()


def goto_with_retry(page: Any, url: str, *, attempts: int = PLAYWRIGHT_NAVIGATION_ATTEMPTS) -> bool:
    """Open a dynamic page without waiting for background traffic to become idle."""
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=PLAYWRIGHT_NAVIGATION_TIMEOUT_MS)
            return True
        except Exception as error:
            print(f"페이지 이동 실패 ({attempt}/{attempts}): {url} - {error}", file=sys.stderr)
            if attempt < attempts:
                time.sleep(2)
    return False


def extract_age_conditions(text: str) -> tuple[int | None, int | None]:
    compact = re.sub(r"\s+", "", text)
    # '세'를 필수로 해 전화번호(예: 061-270)를 연령으로 오인하지 않는다.
    range_match = re.search(r"(?:만)?(\d{1,2})세?(?:이상)?[~∼\-](?:만)?(\d{1,2})세(?:이하)?", compact)
    if range_match:
        return int(range_match.group(1)), int(range_match.group(2))
    minimum = re.search(r"(?:만)?(\d{1,2})세이상", compact)
    maximum = re.search(r"(?:만)?(\d{1,2})세이하", compact)
    return (int(minimum.group(1)) if minimum else None, int(maximum.group(1)) if maximum else None)


def extract_residency_condition(text: str) -> str:
    candidates: list[tuple[int, str]] = []
    segments = re.split(r"\n+|(?<=[다함임])\.\s+|[❍○□■]\s*", text)
    keyword = re.compile(r"거주|주민등록(?:상)?\s*주소(?:지)?|주소지|전입")
    for segment in segments:
        normalized = normalize_whitespace(segment)
        if not normalized or not keyword.search(normalized):
            continue
        # 신청서의 인적사항 칸은 거주 조건이 아니다.
        if "주민등록번호" in normalized:
            continue
        match = keyword.search(normalized)
        assert match is not None
        if len(normalized) > 220:
            start = max(0, match.start() - 70)
            end = min(len(normalized), match.end() + 120)
            normalized = normalized[start:end].strip(" ,:/-")
        score = 0
        if re.search(r"지원대상|신청자격|자격요건|거주하는|거주기간|기준일|계속\s*거주", normalized):
            score += 3
        if re.search(r"목포|전남|전라남도|전국", normalized):
            score += 2
        if re.search(r"초본|제출서류|변경.?중단|전출일", normalized):
            score -= 2
        candidates.append((score, normalized))
    ordered = [value for _, value in sorted(candidates, key=lambda item: item[0], reverse=True)]
    unique: list[str] = []
    for value in ordered:
        if value in unique or any(value in existing or existing in value for existing in unique):
            continue
        unique.append(value)
        if len(unique) >= 4:
            break
    return " / ".join(unique)[:800]


def extract_qualification_text(text: str) -> str:
    """Keep only lines likely to describe applicant eligibility, with short context."""
    lines = [normalize_whitespace(line) for line in text.splitlines() if normalize_whitespace(line)]
    label = re.compile(r"지원\s*대상|사업\s*대상|신청\s*자격|자격\s*(?:조건|요건)?|대상자|거주\s*요건")
    selected: list[str] = []
    for index, line in enumerate(lines):
        if label.search(line):
            selected.append(line[:500])
    # 라벨이 없는 공고라도 개인화에 필요한 연령·거주 문장을 보존한다.
    for match in re.finditer(r"[^.\n]{0,100}(?:(?:만)?\s*\d{1,2}\s*세|거주|주민등록|주소지|전입)[^.\n]{0,180}", text):
        selected.append(normalize_whitespace(match.group(0)))
    return normalize_whitespace("\n".join(dict.fromkeys(selected)))[:1200]


def extract_application_dates(period: str) -> tuple[str, str]:
    """Convert common Korean application date notations to ISO dates when possible."""
    dates = []
    last_year: int | None = None
    for year, month, day in re.findall(r"(20\d{2})\s*(?:년|[.\-/])\s*(\d{1,2})\s*(?:월|[.\-/])\s*(\d{1,2})\s*(?:일)?", period):
        try:
            last_year = int(year)
            dates.append(datetime(last_year, int(month), int(day)).date().isoformat())
        except ValueError:
            continue
    if last_year:
        for month, day in re.findall(r"(?<!\d)(\d{1,2})\s*[./]\s*(\d{1,2})(?:\s*\.)?(?!\d)", period):
            try:
                candidate = datetime(last_year, int(month), int(day)).date().isoformat()
                if candidate not in dates:
                    dates.append(candidate)
            except ValueError:
                continue
    unique_dates = list(dict.fromkeys(dates))
    if not unique_dates:
        return "", ""
    return unique_dates[0], unique_dates[-1] if len(unique_dates) > 1 else ""


def refresh_structured_fields(record: dict[str, Any]) -> None:
    """Refresh filterable fields after page and attachment text are both available."""
    full_text = "\n".join((record.get("content", ""), record.get("target_condition", ""), record.get("period", ""), record.get("attachment_text", "")))
    min_age, max_age = extract_age_conditions(full_text)
    period_text = record.get("period", "")
    deadline_match = re.search(r"(?:신청기간|접수기간|제출기한)\s*\)?\s*[:：]?\s*(20\d{2}.{0,80}?)(?=\s*(?:❍|Ⅱ|Ⅲ|【)|$)", full_text)
    start_date, end_date = extract_application_dates(deadline_match.group(1) if deadline_match else period_text)
    record.update({
        "qualification_text": extract_qualification_text(full_text),
        "min_age": min_age,
        "max_age": max_age,
        "residency_condition": extract_residency_condition(full_text),
        "application_start_date": start_date,
        "application_end_date": end_date,
        "content_hash": hashlib.sha256("\n".join((record.get("title", ""), full_text, record.get("application_method", ""), record.get("organization", ""))).encode("utf-8")).hexdigest(),
    })


def classify_region(title: str, content: str, target_condition: str = "") -> str:
    # 지원대상·자격조건이 있으면 본문 속 단순 지역 언급보다 우선한다.
    text = f"{title}\n{target_condition or content}"
    # 서비스 이용자는 목포 거주 청년이다. 정책의 게시 기관이 아니라
    # 목포 거주자가 실제로 신청 가능한지를 기준으로 분류한다.
    if re.search(r"목포(?:시)?\s*(?:거주자?|청년)?\s*제외|전라남도\s*(?:거주자?|청년)?\s*제외", text):
        return "제외(목포 대상 아님)"
    if re.search(r"목포(?:시)?", text):
        return "목포"
    if re.search(r"전국|전\s*지역|지역\s*제한\s*없음|누구나\s*신청|대한민국\s*청년|전체\s*청년", text):
        return "전국(목포 포함)"
    if re.search(r"전라남도|전남\s*(?:거주자?|도민|청년|22개\s*시[·ㆍ, ]?군)", text):
        return "전남(목포 포함)"
    if any(keyword in text for keyword in OTHER_LOCAL_GOVERNMENTS):
        return "제외(타 지자체 특정)"
    return "확인필요"


class HttpClient:
    def __init__(self, delay: float) -> None:
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.delay = delay

    def get(self, url: str, *, params: dict[str, Any] | None = None) -> requests.Response:
        response = self.session.get(url, params=params, timeout=30)
        response.raise_for_status()
        time.sleep(self.delay)
        return response

    def post(self, url: str, *, data: dict[str, Any]) -> requests.Response:
        response = self.session.post(url, data=data, timeout=30)
        response.raise_for_status()
        time.sleep(self.delay)
        return response

    def download(self, url: str, destination: Path, max_bytes: int) -> dict[str, str]:
        """Download a public attachment while enforcing a conservative size limit."""
        response = self.session.get(url, timeout=60, stream=True)
        response.raise_for_status()
        content_length = int(response.headers.get("Content-Length", "0") or 0)
        if content_length > max_bytes:
            response.close()
            raise ValueError(f"첨부파일이 제한 크기({max_bytes // 1024 // 1024}MB)를 초과합니다.")
        total = 0
        try:
            with destination.open("wb") as file:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"첨부파일이 제한 크기({max_bytes // 1024 // 1024}MB)를 초과합니다.")
                    file.write(chunk)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        finally:
            response.close()
        time.sleep(self.delay)
        return {
            "content_type": response.headers.get("Content-Type", ""),
            "content_disposition": response.headers.get("Content-Disposition", ""),
        }


def attachment_extension(url: str) -> str:
    parsed = urlparse(url)
    extension = Path(unquote(parsed.path)).suffix.lower()
    if extension in TEXT_ATTACHMENT_EXTENSIONS:
        return extension
    # 공공기관 다운로드 URL은 실제 파일명을 query string에 넣는 경우가 많다.
    for values in parse_qs(parsed.query).values():
        for value in values:
            candidate = Path(unquote(value)).suffix.lower()
            if candidate in TEXT_ATTACHMENT_EXTENSIONS:
                return candidate
    return extension


def is_attachment_link(url: str, label: str = "") -> bool:
    """Recognize both direct files and opaque public download endpoints."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    path_lower = parsed.path.lower()
    label_lower = label.lower()
    if "viewer" in path_lower or query.get("file_type") == ["all"] or re.search(r"전체\s*(?:파일)?\s*다운", label_lower):
        return False
    extension = attachment_extension(url)
    if extension in TEXT_ATTACHMENT_EXTENSIONS:
        return True
    hint = f"{url} {label}".lower()
    return bool(re.search(r"(?:file[_-]?download|download|atch|첨부|붙임|공고문|신청서)", hint))


def safe_attachment_name(url: str, index: int) -> str:
    original = Path(unquote(urlparse(url).path)).name or f"attachment_{index}"
    original = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", original)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    return f"{index:02d}_{digest}_{original[:120]}"


def detect_attachment_extension(path: Path, url: str = "", content_type: str = "", content_disposition: str = "") -> str:
    """Detect document type using URL/header hints and finally the file signature."""
    hints = " ".join((url, content_disposition)).lower()
    for extension in TEXT_ATTACHMENT_EXTENSIONS:
        if re.search(rf"{re.escape(extension)}(?:[^a-z0-9]|$)", unquote(hints)):
            return extension
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type == "application/pdf":
        return ".pdf"
    if "hwpx" in media_type:
        return ".hwpx"
    if media_type in {"application/x-hwp", "application/haansofthwp"}:
        return ".hwp"
    signature = path.read_bytes()[:32]
    if signature.startswith(b"%PDF-"):
        return ".pdf"
    if signature.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return ".hwp"
    if signature.startswith(b"PK"):
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if "mimetype" in names and b"hwp+zip" in archive.read("mimetype")[:200].lower():
                    return ".hwpx"
                if any(re.fullmatch(r"Contents/section\d+\.xml", name) for name in names):
                    return ".hwpx"
        except (OSError, zipfile.BadZipFile):
            pass
    return attachment_extension(url)


def normalize_document_text(value: str) -> str:
    value = value.replace("\x00", "").replace("\u200b", "").replace("\ufeff", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    cleaned: list[str] = []
    for line in lines:
        if not line:
            if cleaned and cleaned[-1]:
                cleaned.append("")
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def remove_repeated_page_margins(pages: list[str]) -> list[str]:
    """Remove short headers/footers repeated on most PDF pages."""
    split_pages = [[line.strip() for line in page.splitlines() if line.strip()] for page in pages]
    if len(split_pages) < 2:
        return pages
    boundary = []
    for lines in split_pages:
        boundary.extend(lines[:2])
        boundary.extend(lines[-2:])
    counts = Counter(line for line in boundary if len(line) <= 120)
    threshold = max(2, (len(split_pages) + 1) // 2)
    repeated = {line for line, count in counts.items() if count >= threshold}
    result = []
    for original, lines in zip(pages, split_pages):
        if not lines:
            result.append(original)
            continue
        result.append("\n".join(line for index, line in enumerate(lines) if line not in repeated or 2 <= index < len(lines) - 2))
    return result


def extract_pdf_text(path: Path) -> str:
    import pdfplumber

    pages: list[str] = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                # 읽기 순서가 안정적인 기본 추출을 우선하고, 실패한 페이지만 layout 모드로 보완한다.
                text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
                if len(re.sub(r"\s+", "", text)) < 20:
                    text = page.extract_text(layout=True, x_tolerance=2, y_tolerance=3) or text
                pages.append(text)
    except Exception:
        pages = []
    pages = remove_repeated_page_margins(pages)
    extracted = normalize_document_text("\n\n".join(pages))
    if extracted:
        return extracted
    # 일부 PDF는 pdfplumber보다 pypdf의 문자 맵 처리가 더 잘 맞는다.
    try:
        from pypdf import PdfReader

        fallback = "\n\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
        return normalize_document_text(fallback)
    except Exception:
        return ""


def extract_hwpx_text(path: Path) -> str:
    """Extract HWPX paragraphs/tables while retaining their reading boundaries."""
    with zipfile.ZipFile(path) as archive:
        sections = sorted(
            (name for name in archive.namelist() if re.fullmatch(r"Contents/section\d+\.xml", name)),
            key=lambda name: int(re.search(r"(\d+)", name).group(1)),
        )
        if not sections:
            raise ValueError("HWPX 본문 XML을 찾지 못했습니다.")
        if len(sections) > 1000 or sum(archive.getinfo(name).file_size for name in sections) > MAX_ATTACHMENT_BYTES * 5:
            raise ValueError("HWPX 내부 XML 크기가 안전 제한을 초과합니다.")
        chunks: list[str] = []
        for section in sections:
            root = ET.fromstring(archive.read(section))
            paragraphs = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "p"]
            for paragraph in paragraphs:
                parts: list[str] = []
                for node in paragraph.iter():
                    local_name = node.tag.rsplit("}", 1)[-1]
                    if local_name == "t" and node.text:
                        parts.append(node.text)
                    elif local_name in {"lineBreak", "br"}:
                        parts.append("\n")
                    elif local_name == "tab":
                        parts.append("\t")
                paragraph_text = "".join(parts).strip()
                if paragraph_text:
                    chunks.append(paragraph_text)
    return normalize_document_text("\n".join(chunks))


class AttachmentProcessor:
    def __init__(self, client: HttpClient, output_dir: Path, max_per_policy: int) -> None:
        self.client = client
        self.output_dir = output_dir
        self.max_per_policy = max_per_policy

    def process(self, record: dict[str, Any]) -> None:
        urls = list(dict.fromkeys(url.strip() for url in record["attachment_links"].splitlines() if url.strip()))
        if not urls:
            record.update({"attachment_files": "", "attachment_text": "", "attachment_status": "없음"})
            return
        files, texts, statuses = [], [], []
        for index, url in enumerate(urls[: self.max_per_policy], start=1):
            extension = attachment_extension(url)
            destination = self.output_dir / safe_attachment_name(url, index)
            try:
                self.output_dir.mkdir(parents=True, exist_ok=True)
                metadata = self.client.download(url, destination, MAX_ATTACHMENT_BYTES)
                detected_extension = detect_attachment_extension(
                    destination,
                    url,
                    metadata.get("content_type", ""),
                    metadata.get("content_disposition", ""),
                )
                if detected_extension in TEXT_ATTACHMENT_EXTENSIONS and destination.suffix.lower() != detected_extension:
                    renamed = destination.with_name(f"{destination.name}{detected_extension}")
                    destination.replace(renamed)
                    destination = renamed
                files.append(str(destination))
                extension = detected_extension or extension
                if extension == ".pdf":
                    extracted = extract_pdf_text(destination)
                    texts.append(extracted)
                    statuses.append(f"PDF 텍스트 추출 완료({len(extracted)}자)" if extracted else "PDF 텍스트 없음(이미지형·암호화 문서 확인 필요)")
                elif extension == ".hwpx":
                    extracted = extract_hwpx_text(destination)
                    texts.append(extracted)
                    statuses.append(f"HWPX 텍스트 추출 완료({len(extracted)}자)" if extracted else "HWPX 텍스트 없음")
                elif extension == ".hwp":
                    statuses.append("HWP 다운로드 완료(구형 HWP는 현재 텍스트 추출 미지원)")
                else:
                    statuses.append(f"다운로드 완료(지원하지 않는 형식: {extension or '알 수 없음'})")
            # 손상된 공개 문서 한 건이 전체 수집을 멈추지 않도록 상태만 남긴다.
            except Exception as error:
                statuses.append(f"처리 실패: {error}")
        if len(urls) > self.max_per_policy:
            statuses.append(f"첨부파일 {len(urls) - self.max_per_policy}건은 정책별 제한으로 건너뜀")
        record.update({
            "attachment_files": "\n".join(files),
            "attachment_text": "\n\n".join(text for text in texts if text),
            "attachment_status": "\n".join(statuses),
        })


def normalize_field_label(label: str) -> str:
    return re.sub(r"[\s:：ㆍ·]+", "", label).strip()


def extract_labeled_fields(soup: BeautifulSoup) -> dict[str, str]:
    """Read definition lists, tables, and common label/value blocks."""
    fields: dict[str, str] = {}
    for container in soup.select("dl"):
        labels = container.find_all("dt", recursive=False) or container.find_all("dt")
        values = container.find_all("dd", recursive=False) or container.find_all("dd")
        for label, value in zip(labels, values):
            key = normalize_field_label(text_of(label))
            if key and text_of(value):
                fields.setdefault(key, text_of(value))
    for row in soup.select("tr"):
        label = row.select_one("th")
        value = row.select_one("td")
        key = normalize_field_label(text_of(label))
        if key and text_of(value):
            fields.setdefault(key, text_of(value))
    for row in soup.select(".info-item, .detail-item, .board_info li, .view_info li"):
        label = select_first(row, (".label", ".tit", ".subject", "strong", "em"))
        value = select_first(row, (".value", ".txt", ".cont", "span"))
        key = normalize_field_label(text_of(label))
        if key and text_of(value) and label is not value:
            fields.setdefault(key, text_of(value))
    return fields


def field_value(fields: dict[str, str], *aliases: str) -> str:
    normalized_aliases = [normalize_field_label(alias) for alias in aliases]
    for alias in normalized_aliases:
        if alias in fields:
            return fields[alias]
    for key, value in fields.items():
        if any(alias in key or key in alias for alias in normalized_aliases):
            return value
    return ""


def content_text(node: Any) -> str:
    """Extract useful page content after removing navigation and control noise."""
    if node is None:
        return ""
    from copy import copy

    cleaned = copy(node)
    for nuisance in cleaned.select("script, style, noscript, nav, form, button, .breadcrumb, .location, .share, .sns, .pagination"):
        nuisance.decompose()
    return normalize_document_text(text_of(cleaned))


def collect_attachment_links(root: Any, base_url: str, selectors: Iterable[str] = ("a[href]",)) -> list[str]:
    links: list[str] = []
    for link in select_all(root, selectors):
        href = link.get("href", "").strip()
        if not href or href.lower().startswith(("javascript:", "mailto:")):
            continue
        absolute = urljoin(base_url, href)
        if is_attachment_link(absolute, text_of(link)):
            links.append(absolute)
    return list(dict.fromkeys(links))


def extract_policy_ids(html: str) -> list[str]:
    """Discover policy IDs from JS handlers as well as ordinary detail links."""
    patterns = (
        r"policyView\(\s*['\"]?(\d+)['\"]?\s*\)",
        r"(?:policyId|policy_id|plcyNo)=([0-9]+)",
        r"gjYgPolicyView[^'\"<>]*?[?&](?:policyId|policy_id)=([0-9]+)",
    )
    found: list[str] = []
    for pattern in patterns:
        found.extend(re.findall(pattern, html, flags=re.IGNORECASE))
    return list(dict.fromkeys(found))


class GwangjuCollector:
    def __init__(self, client: HttpClient, target: Target) -> None:
        self.client, self.target = client, target

    def _list_html(self, page: int) -> str:
        params: dict[str, Any] = {"siteId": "www", "pageIndex": page}
        if self.target.key == "gwangju_policy":
            path = "/www/policy/gjYgPolicyList"
        else:
            path = "/www/dream/policyList"
            params.update({"policyTy": GWANGJU_DREAM_TYPES[self.target.key], "searchYear": 2026, "order": "insertDesc"})
        return self.client.get(f"{GWANGJU_BASE}{path}", params=params).text

    def _detail(self, policy_id: str) -> dict[str, str]:
        detail_url = f"{GWANGJU_BASE}/www/policy/gjYgPolicyView"
        html = self.client.get(detail_url, params={"siteId": "www", "policyId": policy_id}).text
        soup = BeautifulSoup(html, "html.parser")
        title_node = select_first(soup, (".dt-tit", ".view-title", ".board_view h3", "article h1", "main h2"))
        body_node = select_first(soup, (".dt-desc", "#section_01", ".view-content", ".board_view .content", "article"))
        if title_node is None or body_node is None:
            raise structure_error(self.target.key, f"{detail_url}?policyId={policy_id}", html, "정책 제목/본문")
        fields = extract_labeled_fields(soup)
        eligibility_node = select_first(soup, ("#section_02", "[data-section='eligibility']", ".eligibility", ".target-condition"))
        method_node = select_first(soup, ("#section_03", "[data-section='application']", ".application-method", ".apply-info"))
        attachments = collect_attachment_links(
            soup,
            GWANGJU_BASE,
            (".file-list a[href], .attach a[href], .attachment a[href], a[href*='download'], a[href*='file']",),
        )
        support = field_value(fields, "지원내용", "사업내용", "지원혜택")
        return {
            "title": text_of(title_node),
            "content": normalize_document_text("\n".join(filter(None, (content_text(body_node), support)))),
            "target_condition": content_text(eligibility_node),
            "period": field_value(fields, "신청기간", "접수기간", "모집기간"),
            "application_method": content_text(method_node) or field_value(fields, "신청방법", "접수방법"),
            "organization": field_value(fields, "담당기관", "주관기관", "운영기관"),
            "attachment_links": "\n".join(attachments),
            "original_link": f"{GWANGJU_BASE}/www/50?policyId={policy_id}",
            "source_record_id": policy_id,
        }

    def collect(self, max_pages: int) -> Iterable[dict[str, str]]:
        for page in range(1, max_pages + 1):
            html = self._list_html(page)
            ids = extract_policy_ids(html)
            if not ids:
                if has_empty_result_marker(text_of(BeautifulSoup(html, "html.parser"))):
                    break
                raise structure_error(self.target.key, f"{GWANGJU_BASE} 목록 {page}페이지", html, "정책 상세 ID")
            for policy_id in ids:
                yield self._detail(policy_id)


class YouthCenterCollector:
    """온통청년의 JS 게시판을 브라우저로 렌더링해 수집한다."""
    def __init__(self, target: Target, delay: float) -> None:
        self.target, self.delay = target, delay

    def collect(self, max_pages: int) -> Iterable[dict[str, str]]:
        from playwright.sync_api import sync_playwright
        board_id = YOUTHCENTER_BOARDS[self.target.key]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(user_agent=HEADERS["User-Agent"])
            try:
                for page_number in range(1, max_pages + 1):
                    list_url = f"{YOUTHCENTER_BASE}/bbs03List/{board_id}?curPageNum={page_number}"
                    if not goto_with_retry(page, list_url):
                        continue
                    try:
                        list_selector = "#bbs03List li, .board-list li, .bbs-list li, table.board-list tbody tr"
                        page.wait_for_selector(list_selector, timeout=30_000)
                        posts = page.locator(list_selector).evaluate_all("""els => els.map(el => {
                            const link = el.querySelector("a[href], a[onclick]");
                            const href = link?.getAttribute("href") || "";
                            const onclick = link?.getAttribute("onclick") || "";
                            const jq = window.jQuery ? window.jQuery(el).data("vData") : null;
                            const raw = jq?.pstSn || el.dataset?.pstSn || el.dataset?.postId || href || onclick;
                            const id = String(raw || "").match(/(?:bbs03View\\/\\d+\\/|pstSn[=:'\"\\s]+)(\\d+)|^(\\d+)$/)?.slice(1).find(Boolean) || "";
                            return {title: el.querySelector(".subject, .title, .tit")?.innerText || link?.innerText || "", postId: id};
                        }).filter(item => item.title || item.postId)""")
                    except Exception as error:
                        html = page.content()
                        if has_empty_result_marker(text_of(BeautifulSoup(html, "html.parser"))):
                            break
                        raise structure_error(self.target.key, list_url, html, f"게시물 목록 ({error})") from error
                    if not posts:
                        html = page.content()
                        if has_empty_result_marker(text_of(BeautifulSoup(html, "html.parser"))):
                            break
                        raise structure_error(self.target.key, list_url, html, "게시물 ID")
                    for post in posts:
                        post_id = str(post.get("postId", ""))
                        if not post_id:
                            continue
                        detail_url = f"{YOUTHCENTER_BASE}/bbs03View/{board_id}/{post_id}"
                        if not goto_with_retry(page, detail_url):
                            continue
                        try:
                            locator = page.locator(".view-content, .board-view, #contents, #content, main, article")
                            content = locator.first.inner_text(timeout=30_000) if locator.count() else page.locator("body").inner_text(timeout=30_000)
                            detail_html = page.content()
                            detail_soup = BeautifulSoup(detail_html, "html.parser")
                            attachment_links = collect_attachment_links(
                                detail_soup,
                                YOUTHCENTER_BASE,
                                (".file-list a[href], .attach a[href], .attachment a[href], a[href*='download'], a[href*='atch']",),
                            )
                            fields = extract_labeled_fields(detail_soup)
                            yield {
                                "title": post.get("title", "").strip(),
                                "content": normalize_document_text(content),
                                "target_condition": field_value(fields, "지원대상", "신청자격", "대상"),
                                "period": field_value(fields, "신청기간", "접수기간", "모집기간"),
                                "application_method": field_value(fields, "신청방법", "접수방법"),
                                "organization": field_value(fields, "담당기관", "주관기관", "운영기관"),
                                "attachment_links": "\n".join(attachment_links),
                                "original_link": detail_url,
                                "source_record_id": post_id,
                            }
                        except Exception as error:
                            print(f"온통청년 상세 해석 실패: {detail_url} - {error}", file=sys.stderr)
                        finally:
                            time.sleep(self.delay)
            finally:
                browser.close()


class YouthPolicyApiCollector:
    """온통청년 공식 청년정책 OPEN API(XML) 수집기."""
    endpoint = f"{YOUTHCENTER_BASE}/opi/youthPlcyList.do"

    def __init__(self, client: HttpClient, target: Target, query: str, display: int) -> None:
        self.client, self.target, self.query, self.display = client, target, query, display
        self.api_key = os.getenv("YOUTHCENTER_POLICY_API_KEY", "")

    @staticmethod
    def _value(element: ET.Element, *names: str) -> str:
        for name in names:
            child = element.find(name)
            if child is not None and child.text:
                return child.text.strip()
        return ""

    def _request_page(self, page: int) -> ET.Element:
        if not self.api_key:
            raise RuntimeError("YOUTHCENTER_POLICY_API_KEY가 없습니다. .env.example을 참고해 .env에 설정하세요.")
        response = self.client.session.get(
            self.endpoint,
            params={"openApiVlak": self.api_key, "pageIndex": page, "display": self.display, "query": self.query},
            timeout=30,
            allow_redirects=False,
        )
        if response.is_redirect:
            raise RuntimeError("온통청년 API가 HTTP 리디렉션을 반환했습니다. API 승인 상태와 공식 API 안내의 현재 요청 URL을 확인하세요.")
        response.raise_for_status()
        time.sleep(self.client.delay)
        try:
            return ET.fromstring(response.content)
        except ET.ParseError as error:
            raise RuntimeError("온통청년 API XML 응답을 해석하지 못했습니다.") from error

    def collect(self, max_pages: int) -> Iterable[dict[str, str]]:
        for page in range(1, max_pages + 1):
            root = self._request_page(page)
            # API 문서의 목록 단위와 호환되는 이름을 우선 사용하고, 변동에 대비한다.
            items = root.findall(".//youthPolicy") or root.findall(".//item") or root.findall(".//youthPlcy")
            if not items:
                break
            for item in items:
                policy_id = self._value(item, "plcyNo", "policyId", "plcyId")
                title = self._value(item, "plcyNm", "policyName", "plcyName")
                support = self._value(item, "plcyExplnCn", "sprtCn", "plcyPvsnCn", "plcySptCn")
                eligibility = "\n".join(filter(None, [
                    self._value(item, "ageInfo", "ageCndCn"),
                    self._value(item, "rgtrInfo", "rgtrCndCn", "zipCd"),
                    self._value(item, "employmentInfo", "jobCndCn"),
                    self._value(item, "addAplyQlfcCndCn", "addAplyQlfcCn"),
                ]))
                period = " ~ ".join(filter(None, [
                    self._value(item, "aplyYmd", "aplyPrd", "aplyBgngYmd"),
                    self._value(item, "aplyEndYmd"),
                ]))
                yield {
                    "title": title,
                    "content": support,
                    "target_condition": eligibility,
                    "period": period,
                    "application_method": self._value(item, "aplyMthdCn", "aplyMthd"),
                    "organization": self._value(item, "operInstCdNm", "operInstNm", "cnsgNm"),
                    "original_link": self._value(item, "refUrlAddr", "aplyUrlAddr", "rfcSiteUrl1") or (f"{YOUTHCENTER_BASE}/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/{policy_id}" if policy_id else ""),
                    "source_record_id": policy_id,
                }


class YouthInternCollector:
    def __init__(self, client: HttpClient, target: Target) -> None:
        self.client, self.target = client, target

    def collect(self, max_pages: int) -> Iterable[dict[str, str]]:
        for page_number in range(1, max_pages + 1):
            html = self.client.get(f"{DB2030_BASE}/user/youthIntern/selectYouthInternList.do", params={"pageIndex": page_number}).text
            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("#contents table tbody tr, .board-list tbody tr")
            if not rows:
                break
            for row in rows:
                link = row.select_one("a[onclick*='fn_selectYouthInternDetail']")
                cells = [text_of(cell) for cell in row.select("td")]
                if not link:
                    continue
                match = re.search(r"fn_selectYouthInternDetail\('([^']+)'", link.get("onclick", ""))
                if not match:
                    continue
                original_link = f"{DB2030_BASE}/user/youthIntern/selectYouthInternDetail.do"
                detail_html = self.client.post(original_link, data={"pageIndex": page_number, "searchUseYn": "Y", "youthId": match.group(1), "searchCondition": "1", "searchUpprInst": "", "searchWrkRegn": "", "searchRecruitStatus": "", "searchKeyword": ""}).text
                body = text_of(BeautifulSoup(detail_html, "html.parser").select_one("#contents, main, .contents"))
                yield {"title": text_of(link), "content": body or "\n".join(cells), "target_condition": "", "period": cells[-2] if len(cells) >= 2 else "", "application_method": "", "organization": cells[-3] if len(cells) >= 3 else "", "original_link": original_link, "source_record_id": match.group(1)}


class MokpoCenterPolicyCollector:
    """목포청년센터 누리의 취·창업사업 및 자립지원 정적 정책 페이지."""
    def __init__(self, client: HttpClient, target: Target) -> None:
        self.client, self.target = client, target

    def collect(self, max_pages: int) -> Iterable[dict[str, str]]:
        url = MOKPO_CENTER_URLS[self.target.key]
        html = self.client.get(url).text
        soup = BeautifulSoup(html, "html.parser")
        content = select_first(soup, ("#right #content", "#content", "main .content", "main", "article"))
        page_text = content_text(content) if content is not None else ""
        # 현재 두 페이지는 빈 목록을 제공한다. 빈 페이지를 정책으로 저장하지 않는다.
        if has_empty_result_marker(page_text):
            return
        if not page_text:
            raise structure_error(self.target.key, url, html, "본문 영역")
        # 각 h4와 바로 뒤의 정책 설명 블록이 하나의 독립 정책이다.
        headings = select_all(content, ("h4.bgno", ".policy-list h3", ".policy-list h4", ".business-list h3", ".business-list h4"))
        if not headings:
            raise structure_error(self.target.key, url, html, "정책 제목 목록")
        for heading in headings:
            detail_block = heading.find_next_sibling(lambda tag: tag.name in {"div", "section", "ul", "table"})
            policy_text = content_text(detail_block)
            if not policy_text:
                continue
            condition_lines = [line for line in policy_text.splitlines() if re.search(r"지원대상|사업대상|신청자격|자격", line)]
            period_lines = [line for line in policy_text.splitlines() if re.search(r"사업기간|신청기간|모집기간|접수기간", line)]
            links = collect_attachment_links(detail_block, MOKPO_BASE) if detail_block else []
            yield {"title": text_of(heading), "content": policy_text, "target_condition": "\n".join(condition_lines), "period": "\n".join(period_lines), "application_method": "", "organization": "목포청년센터 누리", "attachment_links": "\n".join(dict.fromkeys(links)), "original_link": url}


class MokpoCityYouthSupportCollector:
    """목포시청 청년지원사업 게시판의 목록·상세·첨부 링크 수집기."""
    list_url = f"{MOKPO_BASE}/www/life_welfare/job/youth/youth_support"

    def __init__(self, client: HttpClient, target: Target) -> None:
        self.client, self.target = client, target

    def _detail(self, url: str, list_cells: list[str]) -> dict[str, str]:
        html = self.client.get(url).text
        soup = BeautifulSoup(html, "html.parser")
        title_node = select_first(soup, (".view_titlebox h3", ".board_view .title", ".view-title", "article h1", "main h2"))
        body_node = select_first(soup, (".text_viewbox", ".board_view .content", ".view-content", "article .content", "article"))
        if title_node is None or body_node is None:
            raise structure_error(self.target.key, url, html, "게시물 제목/본문")
        title = text_of(title_node)
        body = content_text(body_node)
        fields = extract_labeled_fields(soup)
        attachment_links = collect_attachment_links(
            soup,
            MOKPO_BASE,
            (".file_viewbox a[href], .file-list a[href], .attach a[href], .attachment a[href], a[href*='file_download']",),
        )
        period_lines = [
            line for line in body.splitlines()
            if re.search(r"(?:신청|접수|모집|제출).{0,30}(?:20\d{2}\s*[.년/-]|\d{1,2}\s*[./월]\s*\d{1,2})", line)
        ]
        source_record_id = (parse_qs(urlparse(url).query).get("idx") or [""])[0]
        return {
            "title": title,
            "content": body,
            "target_condition": field_value(fields, "지원대상", "신청자격", "사업대상"),
            "period": field_value(fields, "신청기간", "접수기간", "모집기간", "제출기한") or "\n".join(period_lines) or (list_cells[3] if len(list_cells) > 3 else ""),
            "application_method": field_value(fields, "신청방법", "접수방법"),
            "organization": field_value(fields, "담당부서", "담당기관") or (list_cells[2] if len(list_cells) > 2 else "목포시청"),
            "attachment_links": "\n".join(dict.fromkeys(attachment_links)),
            "original_link": url,
            "source_record_id": source_record_id,
        }

    def collect(self, max_pages: int) -> Iterable[dict[str, str]]:
        for page_number in range(1, max_pages + 1):
            response = self.client.get(self.list_url, params={"page": page_number})
            html = response.text
            soup = BeautifulSoup(html, "html.parser")
            rows = select_all(soup, ("#content .board_basic tbody tr", ".board-list tbody tr", ".board_basic tbody tr", "table tbody tr"))
            if not rows:
                if has_empty_result_marker(text_of(soup)):
                    break
                raise structure_error(self.target.key, response.url, html, "게시판 행")
            for row in rows:
                link = select_first(row, ("a.basic_cont[href]", "a[href*='view']", "a[href]"))
                if not link:
                    continue
                cells = [text_of(cell) for cell in row.select("td")]
                yield self._detail(urljoin(MOKPO_BASE, link["href"]), cells)


def validate_raw_record(raw: dict[str, str]) -> list[str]:
    """Return quality problems that make a scraped item unsafe to persist."""
    problems: list[str] = []
    title = normalize_whitespace(raw.get("title", ""))
    body = normalize_whitespace("\n".join((raw.get("content", ""), raw.get("target_condition", ""))))
    link = raw.get("original_link", "").strip()
    if len(title) < 2:
        problems.append("제목 누락")
    if len(body) < 10:
        problems.append("상세 내용 부족")
    if not link.startswith(("http://", "https://")):
        problems.append("원문 URL 누락")
    return problems


def normalize(target: Target, raw: dict[str, str]) -> dict[str, Any]:
    combined = "\n".join((raw.get("content", ""), raw.get("target_condition", ""), raw.get("period", "")))
    min_age, max_age = extract_age_conditions(combined)
    start_date, end_date = extract_application_dates(raw.get("period", ""))
    record = {
        "source_site": target.source_site,
        "category": target.category,
        "title": normalize_whitespace(raw.get("title", "")),
        "target_region": classify_region(raw.get("title", ""), combined, raw.get("target_condition", "")),
        "target_condition": normalize_whitespace(raw.get("target_condition", "")),
        "qualification_text": extract_qualification_text(combined),
        "min_age": min_age,
        "max_age": max_age,
        "residency_condition": extract_residency_condition(combined),
        "period": normalize_whitespace(raw.get("period", "")),
        "application_start_date": start_date,
        "application_end_date": end_date,
        "content": normalize_whitespace(raw.get("content", "")),
        "application_method": normalize_whitespace(raw.get("application_method", "")),
        "organization": normalize_whitespace(raw.get("organization", "")),
        "attachment_links": raw.get("attachment_links", ""),
        "attachment_files": "",
        "attachment_text": "",
        "attachment_status": "미처리",
        "content_hash": "",
        "original_link": raw.get("original_link", ""),
        "source_record_id": raw.get("source_record_id", ""),
        "collected_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    refresh_structured_fields(record)
    return record


def make_collector(target: Target, client: HttpClient, delay: float):
    if target.key in YOUTHCENTER_BOARDS:
        return YouthCenterCollector(target, delay)
    if target.key == "youth_intern":
        return YouthInternCollector(client, target)
    if target.key in MOKPO_CENTER_URLS:
        return MokpoCenterPolicyCollector(client, target)
    if target.key == "mokpo_city_youth_support":
        return MokpoCityYouthSupportCollector(client, target)
    return GwangjuCollector(client, target)


def main() -> None:
    parser = argparse.ArgumentParser(description="목포·전체청년 대상 공고 수집기")
    parser.add_argument("--targets", nargs="+", choices=TARGETS, default=list(DEFAULT_CRAWL_TARGETS), help="수집 대상 키 (기본: 크롤링 대상 전체)")
    parser.add_argument("--max-pages", type=int, default=1, help="대상별 목록 페이지 수 (기본 1)")
    parser.add_argument("--delay", type=float, default=5.0, help="요청 또는 상세 열람 사이 대기 시간(초, 기본 5초)")
    parser.add_argument("--api-query", default="", help="청년정책 API 검색어 (예: 목포, 주거)")
    parser.add_argument("--api-display", type=int, default=100, help="청년정책 API의 페이지당 수 (기본 100)")
    parser.add_argument("--include-review", action="store_true", help="지역 판정이 확인필요인 항목도 저장")
    parser.add_argument("--output-dir", type=Path, default=Path("data/collected"))
    parser.add_argument("--download-attachments", action="store_true", help="PDF/HWPX 등 공개 첨부파일을 내려받아 텍스트를 추출")
    parser.add_argument("--attachment-dir", type=Path, default=Path("data/attachments"), help="첨부파일 저장 폴더")
    parser.add_argument("--max-attachments-per-policy", type=int, default=3, help="정책 1건당 처리할 첨부파일 최대 수 (기본 3)")
    parser.add_argument("--max-items", type=int, default=0, help="시험 수집 최대 건수 (0은 제한 없음)")
    parser.add_argument("--database", "--mysql", dest="database", action="store_true", help="정규화한 결과를 PostgreSQL에 저장하고 신규·변경 공고를 감지")
    parser.add_argument("--allow-structure-errors", action="store_true", help="사이트 구조 이상이 있어도 성공 종료(기본은 진단 파일 저장 후 오류 종료)")
    args = parser.parse_args()
    if args.max_pages < 1 or args.delay < 3.0:
        parser.error("--max-pages는 1 이상, --delay는 서버 보호를 위해 3초 이상이어야 합니다.")
    if args.api_display < 1 or args.max_attachments_per_policy < 1 or args.max_items < 0:
        parser.error("--api-display와 --max-attachments-per-policy는 1 이상, --max-items는 0 이상이어야 합니다.")
    load_local_env()
    client, records = HttpClient(args.delay), []
    quality_report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "targets": {},
        "structure_failures": [],
    }
    attachment_processor = AttachmentProcessor(client, args.attachment_dir, args.max_attachments_per_policy) if args.download_attachments else None
    for key in args.targets:
        target = TARGETS[key]
        stats = {"raw": 0, "accepted": 0, "filtered": 0, "rejected": 0, "errors": []}
        quality_report["targets"][key] = stats
        print(f"수집 시작: {target.source_site} / {target.category}")
        collector = YouthPolicyApiCollector(client, target, args.api_query, args.api_display) if key == "youth_policy_api" else make_collector(target, client, args.delay)
        try:
            for raw in collector.collect(args.max_pages):
                stats["raw"] += 1
                problems = validate_raw_record(raw)
                if problems:
                    stats["rejected"] += 1
                    message = f"품질 검증 제외: {target.category} / {raw.get('original_link', '')} ({', '.join(problems)})"
                    stats["errors"].append(message)
                    print(message, file=sys.stderr)
                    continue
                record = normalize(target, raw)
                if record["target_region"] in {"목포", "전남(목포 포함)", "전국(목포 포함)"} or args.include_review:
                    if attachment_processor:
                        attachment_processor.process(record)
                        refresh_structured_fields(record)
                    records.append(record)
                    stats["accepted"] += 1
                    if args.max_items and len(records) >= args.max_items:
                        break
                else:
                    stats["filtered"] += 1
        except StructureChangedError as error:
            stats["errors"].append(str(error))
            quality_report["structure_failures"].append({"target": key, "message": str(error)})
            print(f"사이트 구조 이상: {target.category} ({error})", file=sys.stderr)
        except (RuntimeError, requests.RequestException) as error:
            stats["errors"].append(str(error))
            print(f"수집 건너뜀: {target.category} ({error})")
        if args.max_items and len(records) >= args.max_items:
            break
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path, csv_path = args.output_dir / f"youth_records_{stamp}.json", args.output_dir / f"youth_records_{stamp}.csv"
    quality_path = args.output_dir / f"collection_quality_{stamp}.json"
    json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame(records, columns=EXPORT_COLUMNS).to_csv(csv_path, index=False, encoding="utf-8-sig")
    quality_report["finished_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    quality_report["saved_records"] = len(records)
    quality_path.write_text(json.dumps(quality_report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"완료: {len(records)}건\nJSON: {json_path}\nCSV:  {csv_path}\n품질 보고서: {quality_path}")
    if args.database:
        try:
            from postgres_policy_repository import PostgresPolicyRepository

            counts = PostgresPolicyRepository().sync(records)
            print(f"PostgreSQL 동기화: 신규 {counts['new']}건 / 변경 {counts['updated']}건 / 동일 {counts['unchanged']}건")
        except Exception as error:
            print(f"PostgreSQL 저장 실패: {error}")
    if quality_report["structure_failures"] and not args.allow_structure_errors:
        print(f"사이트 구조 이상 {len(quality_report['structure_failures'])}건이 감지되어 오류 종료합니다.", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
