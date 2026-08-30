"""Fetch youth-policy pages from youth.gwangju.go.kr with requests + BS4.

This is the first collection stage only: it discovers policy IDs from the
site's HTML listing and stores raw policy-detail fields as JSON.  Normalizing
those fields and downloading attachments are deliberately separate stages.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://youth.gwangju.go.kr"
LIST_PATH = "/www/policy/gjYgPolicyList"
DETAIL_PATH = "/www/policy/gjYgPolicyView"
USER_AGENT = "YouthPolicyResearchBot/0.1 (educational data collection)"


def clean_text(element) -> str:
    """Return visible text while retaining meaningful line breaks."""
    if element is None:
        return ""
    return "\n".join(line.strip() for line in element.stripped_strings if line.strip())


class YouthPolicyScraper:
    def __init__(self, delay_seconds: float = 0.5) -> None:
        self.delay_seconds = delay_seconds
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def get_html(self, path: str, params: dict[str, str | int]) -> str:
        response = self.session.get(f"{BASE_URL}{path}", params=params, timeout=30)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or response.encoding
        return response.text

    def list_page(self, page_index: int) -> tuple[list[str], int]:
        html = self.get_html(LIST_PATH, {"siteId": "www", "pageIndex": page_index})
        soup = BeautifulSoup(html, "html.parser")
        policy_ids: list[str] = []
        for node in soup.select("[onclick]"):
            match = re.search(r"policyView\('([0-9]+)'\)", node.get("onclick", ""))
            if match and match.group(1) not in policy_ids:
                policy_ids.append(match.group(1))

        page_info = soup.select_one(".pageinfo")
        page_text = clean_text(page_info) if page_info else ""
        total_pages_match = re.search(r"총\s*\d+\s*/\s*(\d+)\s*페이지", page_text)
        if not policy_ids or not total_pages_match:
            raise ValueError(f"목록 구조를 인식하지 못했습니다 (page={page_index}).")
        return policy_ids, int(total_pages_match.group(1))

    def policy_detail(self, policy_id: str) -> dict[str, str]:
        html = self.get_html(DETAIL_PATH, {"siteId": "www", "policyId": policy_id})
        soup = BeautifulSoup(html, "html.parser")
        fields: dict[str, str] = {}
        for definition in soup.select(".dt-list dl"):
            key = clean_text(definition.find("dt"))
            value = clean_text(definition.find("dd"))
            if key and value:
                fields[key] = value

        def section_text(section_id: str) -> str:
            section = soup.select_one(f"#{section_id}")
            return clean_text(section) if section else ""

        return {
            "policy_id": policy_id,
            "title": clean_text(soup.select_one(".dt-tit")),
            "summary": clean_text(soup.select_one(".dt-desc")),
            "application_period": fields.get("신청기간", ""),
            "organization": fields.get("담당기관", ""),
            "support": fields.get("지원내용", ""),
            "eligibility_raw": section_text("section_02"),
            "application_method_raw": section_text("section_03"),
            "source_url": f"{BASE_URL}/www/50?policyId={policy_id}",
        }

    def crawl(self, max_pages: int) -> Iterable[dict[str, str]]:
        policy_ids, total_pages = self.list_page(1)
        pages_to_fetch = min(max_pages, total_pages)
        for page in range(1, pages_to_fetch + 1):
            ids = policy_ids if page == 1 else self.list_page(page)[0]
            for policy_id in ids:
                yield self.policy_detail(policy_id)
                time.sleep(self.delay_seconds)
            if page < pages_to_fetch:
                time.sleep(self.delay_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="전남광주청년통합플랫폼 정책 HTML 수집")
    parser.add_argument("--max-pages", type=int, default=1, help="수집할 목록 페이지 수 (기본 1)")
    parser.add_argument("--output", type=Path, default=Path("data/raw_policies.json"))
    parser.add_argument("--delay", type=float, default=0.5, help="요청 사이 대기 시간(초)")
    args = parser.parse_args()
    if args.max_pages < 1 or args.delay < 0:
        parser.error("--max-pages는 1 이상, --delay는 0 이상이어야 합니다.")

    scraper = YouthPolicyScraper(delay_seconds=args.delay)
    policies = list(scraper.crawl(args.max_pages))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(policies, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(policies)}건 저장: {args.output}")


if __name__ == "__main__":
    main()
