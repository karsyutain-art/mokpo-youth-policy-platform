"""Create personalized policy-alert candidates from MySQL change events."""

from __future__ import annotations

import argparse
import re
from datetime import date, datetime
from typing import Any

from mysql_policy_repository import MySQLPolicyRepository
from youth_data_collector import load_local_env


TAG_KEYWORDS = {
    "취업": ("취업", "일자리", "인턴", "근로", "고용"),
    "창업": ("창업", "사업자", "사업화"),
    "주거": ("주거", "월세", "전세", "임대"),
    "교육": ("교육", "훈련", "학습", "자격증"),
    "복지": ("복지", "생활", "금융", "지원금"),
    "문화": ("문화", "예술", "여가", "관광"),
}


def current_age(birth_date: date) -> int:
    today = date.today()
    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))


def policy_tags(policy: dict[str, Any]) -> set[str]:
    text = "\n".join(str(policy.get(field) or "") for field in ("category", "title", "content", "qualification_text"))
    return {tag for tag, keywords in TAG_KEYWORDS.items() if any(keyword in text for keyword in keywords)}


def eligible_for_policy(user: dict[str, Any], policy: dict[str, Any], interests: set[str]) -> tuple[bool, str]:
    if user["residency_city"] != "목포":
        return False, "목포 거주 조건 불일치"
    age = current_age(user["birth_date"])
    if policy["min_age"] is not None and age < policy["min_age"]:
        return False, "최소 연령 미달"
    if policy["max_age"] is not None and age > policy["max_age"]:
        return False, "최대 연령 초과"
    tags = policy_tags(policy)
    common = tags & interests
    if interests and not common:
        return False, "관심 분야 불일치"
    reason = [f"목포 거주", f"나이 {age}세"]
    if common:
        reason.append(f"관심 분야: {', '.join(sorted(common))}")
    return True, " / ".join(reason)


def diagnose_eligibility(user: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Conservatively diagnose structured conditions and flag ambiguous text for review."""
    checks: list[dict[str, str]] = []

    def add(key: str, label: str, status: str, detail: str, evidence: str = "") -> None:
        checks.append({"key": key, "label": label, "status": status, "detail": detail, "evidence": evidence})

    birth_date = user.get("birth_date")
    if birth_date is None:
        add("age", "연령", "review", "생년월일을 입력하면 연령 조건을 확인할 수 있습니다.")
    else:
        age = current_age(birth_date)
        min_age, max_age = policy.get("min_age"), policy.get("max_age")
        if min_age is not None and age < min_age:
            add("age", "연령", "ineligible", f"현재 만 {age}세로 최소 연령 {min_age}세에 미달합니다.", policy.get("qualification_text") or "")
        elif max_age is not None and age > max_age:
            add("age", "연령", "ineligible", f"현재 만 {age}세로 최대 연령 {max_age}세를 초과합니다.", policy.get("qualification_text") or "")
        elif min_age is None and max_age is None:
            add("age", "연령", "review", f"현재 만 {age}세입니다. 공고에서 명확한 연령 범위를 찾지 못했습니다.")
        else:
            add("age", "연령", "eligible", f"현재 만 {age}세로 구조화된 연령 조건에 맞습니다.")

    region = policy.get("target_region")
    if user.get("residency_city") != "목포":
        add("residency", "거주지", "ineligible", "현재 프로필의 거주지가 목포가 아닙니다.")
    elif region not in {"목포", "전남(목포 포함)", "전국(목포 포함)"}:
        add("residency", "거주지", "ineligible", "목포 거주자가 신청할 수 있는 지역 범위가 아닙니다.")
    else:
        add("residency", "거주지", "eligible", f"목포 거주자는 '{region}' 범위에 포함됩니다.", policy.get("residency_condition") or "")

    condition_text = "\n".join(str(policy.get(field) or "") for field in ("qualification_text", "target_condition", "residency_condition"))
    duration_match = re.search(r"(?:거주\s*)?(\d+)\s*년\s*이상(?:\s*거주)?", condition_text)
    if duration_match:
        required_months = int(duration_match.group(1)) * 12
        months = user.get("residency_months")
        if months is None:
            add("residency_duration", "거주기간", "review", f"목포 거주기간을 입력하면 {required_months // 12}년 이상 조건을 확인할 수 있습니다.", duration_match.group(0))
        elif months < required_months:
            add("residency_duration", "거주기간", "ineligible", f"입력한 거주기간 {months}개월이 요구기간 {required_months}개월보다 짧습니다.", duration_match.group(0))
        else:
            add("residency_duration", "거주기간", "eligible", f"입력한 거주기간 {months}개월이 요구기간을 충족합니다.", duration_match.group(0))
    else:
        add("residency_duration", "거주기간", "review", "공고에서 구조화 가능한 거주기간 조건을 찾지 못했습니다.")

    employment = user.get("employment_status")
    if re.search(r"미취업|미취업자|구직자|구직\s*중", condition_text):
        if not employment:
            add("employment", "취업 상태", "review", "취업 상태를 입력하면 미취업·구직 조건을 확인할 수 있습니다.")
        elif employment in {"미취업", "구직 중"}:
            add("employment", "취업 상태", "eligible", f"입력한 상태 '{employment}'가 공고 조건과 맞습니다.")
        else:
            add("employment", "취업 상태", "ineligible", f"공고는 미취업·구직자를 대상으로 하지만 현재 상태는 '{employment}'입니다.")
    elif re.search(r"재직자|취업자|근로자", condition_text):
        if not employment:
            add("employment", "취업 상태", "review", "취업 상태를 입력하면 재직 조건을 확인할 수 있습니다.")
        elif employment in {"재직 중", "프리랜서"}:
            add("employment", "취업 상태", "eligible", f"입력한 상태 '{employment}'가 재직 조건에 부합할 가능성이 높습니다.")
        else:
            add("employment", "취업 상태", "ineligible", f"공고는 재직·취업자를 대상으로 하지만 현재 상태는 '{employment}'입니다.")
    else:
        add("employment", "취업 상태", "review", "공고에서 구조화 가능한 취업 상태 조건을 찾지 못했습니다.")

    review_fields = (
        ("income", "소득", "income_band", r"소득|중위소득|건강보험|보험료"),
        ("education", "학력", "education_level", r"학력|대학|대학교|졸업|재학|휴학"),
        ("household", "가구 상황", "household_status", r"가구|한부모|신혼|부모|부양"),
    )
    for key, label, profile_field, pattern in review_fields:
        profile_value = user.get(profile_field)
        if re.search(pattern, condition_text):
            detail = f"프로필 값은 '{profile_value}'입니다. 공고의 {label} 조건은 원문 대조가 필요합니다." if profile_value else f"{label} 정보를 입력하고 공고 원문과 대조해야 합니다."
            add(key, label, "review", detail, condition_text[:500])
        else:
            add(key, label, "review", f"공고에서 구조화 가능한 {label} 조건을 찾지 못했습니다.")

    if any(check["status"] == "ineligible" for check in checks):
        overall = "대상 아님"
    elif any(check["status"] == "review" for check in checks):
        overall = "추가 확인 필요"
    else:
        overall = "신청 가능"
    return {
        "overall": overall,
        "checks": checks,
        "disclaimer": "이 결과는 참고용이며 최종 신청 가능 여부는 반드시 담당기관의 공식 공고에서 확인해야 합니다.",
    }


class PolicyMatcher:
    def __init__(self) -> None:
        self.repository = MySQLPolicyRepository()

    def _connection(self):
        connection = self.repository.connect()
        self.repository.initialize(connection)
        return connection

    def add_user(self, display_name: str, birth_date: date, interests: list[str]) -> int:
        connection = self._connection()
        try:
            cursor = connection.cursor()
            now = datetime.now().replace(microsecond=0)
            cursor.execute(
                "INSERT INTO user_profiles (display_name, birth_date, residency_city, created_at, updated_at) VALUES (%s, %s, '목포', %s, %s)",
                (display_name, birth_date, now, now),
            )
            user_id = cursor.lastrowid
            cursor.executemany(
                "INSERT INTO user_interests (user_id, interest_tag) VALUES (%s, %s)",
                [(user_id, tag) for tag in sorted(set(interests))],
            )
            connection.commit()
            return user_id
        finally:
            connection.close()

    def create_candidates(self) -> int:
        connection = self._connection()
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT event.id AS event_id, event.change_type, policy.*
                FROM policy_change_events AS event
                JOIN policy_records AS policy ON policy.id = event.policy_id
                WHERE policy.application_end_date IS NULL OR policy.application_end_date >= CURDATE()"""
            )
            events = cursor.fetchall()
            cursor.execute("SELECT * FROM user_profiles WHERE is_active = TRUE AND residency_city = '목포' AND birth_date IS NOT NULL")
            users = cursor.fetchall()
            created = 0
            now = datetime.now().replace(microsecond=0)
            for user in users:
                cursor.execute("SELECT interest_tag FROM user_interests WHERE user_id = %s", (user["id"],))
                interests = {row["interest_tag"] for row in cursor.fetchall()}
                for policy in events:
                    eligible, reason = eligible_for_policy(user, policy, interests)
                    if not eligible:
                        continue
                    if policy.get("change_type") == "deadline" and policy.get("application_end_date"):
                        days_left = max(0, (policy["application_end_date"] - date.today()).days)
                        reason = f"신청 마감 {days_left}일 전 / {reason}"
                    cursor.execute(
                        """INSERT IGNORE INTO policy_match_candidates
                        (event_id, policy_id, user_id, match_reason, created_at)
                        VALUES (%s, %s, %s, %s, %s)""",
                        (policy["event_id"], policy["id"], user["id"], reason, now),
                    )
                    created += cursor.rowcount
            connection.commit()
            return created
        finally:
            connection.close()

    def create_deadline_candidates(self, thresholds: tuple[int, ...] = (7, 3, 1)) -> dict[str, int]:
        """Create one deduplicated deadline event at each approaching threshold."""
        valid_thresholds = tuple(sorted({day for day in thresholds if day >= 0}))
        if not valid_thresholds:
            return {"events": 0, "candidates": 0}
        connection = self._connection()
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT * FROM policy_records
                WHERE application_end_date BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL %s DAY)
                  AND target_region IN ('목포', '전남(목포 포함)', '전국(목포 포함)')""",
                (max(valid_thresholds),),
            )
            policies = cursor.fetchall()
            events = 0
            now = datetime.now().replace(microsecond=0)
            for policy in policies:
                days_left = (policy["application_end_date"] - date.today()).days
                threshold = next((day for day in valid_thresholds if days_left <= day), None)
                if threshold is None:
                    continue
                event_key = f"deadline:{policy['id']}:{policy['application_end_date'].isoformat()}:D{threshold}"
                cursor.execute(
                    """INSERT IGNORE INTO policy_change_events
                    (policy_id, change_type, event_key, previous_content_hash, current_content_hash, detected_at)
                    VALUES (%s, 'deadline', %s, NULL, %s, %s)""",
                    (policy["id"], event_key, policy["content_hash"], now),
                )
                events += cursor.rowcount
            connection.commit()
        finally:
            connection.close()
        candidates = self.create_candidates() if events else 0
        return {"events": events, "candidates": candidates}

    def pending_candidates(self) -> list[dict[str, Any]]:
        connection = self._connection()
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT candidate.id, user.display_name, policy.title, policy.original_link,
                          candidate.match_reason, event.change_type, candidate.created_at
                FROM policy_match_candidates AS candidate
                JOIN user_profiles AS user ON user.id = candidate.user_id
                JOIN policy_records AS policy ON policy.id = candidate.policy_id
                JOIN policy_change_events AS event ON event.id = candidate.event_id
                WHERE candidate.status = 'pending'
                ORDER BY candidate.created_at DESC"""
            )
            return cursor.fetchall()
        finally:
            connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="목포 청년 맞춤 정책 후보 생성기")
    commands = parser.add_subparsers(dest="command", required=True)
    add_user = commands.add_parser("add-user", help="목포 거주 사용자 프로필 추가")
    add_user.add_argument("--name", required=True, help="화면에 표시할 이름")
    add_user.add_argument("--birth-date", required=True, type=date.fromisoformat, help="생년월일 YYYY-MM-DD")
    add_user.add_argument("--interests", nargs="+", required=True, choices=sorted(TAG_KEYWORDS), help="관심 분야")
    commands.add_parser("run", help="신규·변경 공고에 대한 매칭 후보 생성")
    commands.add_parser("deadline", help="7일·3일·1일 전 마감 임박 알림 후보 생성")
    commands.add_parser("list", help="아직 알리지 않은 후보 목록 출력")
    args = parser.parse_args()
    load_local_env()
    matcher = PolicyMatcher()
    if args.command == "add-user":
        print(f"사용자 프로필 추가: id={matcher.add_user(args.name, args.birth_date, args.interests)}")
    elif args.command == "run":
        print(f"생성된 알림 후보: {matcher.create_candidates()}건")
    elif args.command == "deadline":
        result = matcher.create_deadline_candidates()
        print(f"마감 임박 이벤트: {result['events']}건 / 알림 후보: {result['candidates']}건")
    else:
        for candidate in matcher.pending_candidates():
            print(f"[{candidate['change_type']}] {candidate['display_name']} / {candidate['title']} / {candidate['match_reason']}\n{candidate['original_link']}")


if __name__ == "__main__":
    main()
