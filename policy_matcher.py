"""Create personalized policy-alert candidates from MySQL change events."""

from __future__ import annotations

import argparse
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
                """SELECT event.id AS event_id, policy.*
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
    commands.add_parser("list", help="아직 알리지 않은 후보 목록 출력")
    args = parser.parse_args()
    load_local_env()
    matcher = PolicyMatcher()
    if args.command == "add-user":
        print(f"사용자 프로필 추가: id={matcher.add_user(args.name, args.birth_date, args.interests)}")
    elif args.command == "run":
        print(f"생성된 알림 후보: {matcher.create_candidates()}건")
    else:
        for candidate in matcher.pending_candidates():
            print(f"[{candidate['change_type']}] {candidate['display_name']} / {candidate['title']} / {candidate['match_reason']}\n{candidate['original_link']}")


if __name__ == "__main__":
    main()
