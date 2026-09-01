"""Optional, idempotent email delivery for matched policy notifications."""

from __future__ import annotations

import argparse
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage

from mysql_policy_repository import MySQLPolicyRepository
from youth_data_collector import load_local_env


def configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_FROM"))


def _message(row: dict) -> EmailMessage:
    labels = {"new": "새 공고", "updated": "변경 공고", "deadline": "마감 임박"}
    msg = EmailMessage()
    msg["Subject"] = f"[목포 청년 정책] {labels.get(row['change_type'], '정책 알림')}: {row['title']}"
    msg["From"] = os.environ["SMTP_FROM"]
    msg["To"] = row["email"]
    msg.set_content(f"""목포 청년 정책 알림입니다.

{labels.get(row['change_type'], '정책 알림')}: {row['title']}
사유: {row['match_reason']}
신청 마감: {row['application_end_date'] or '공식 공고 확인'}
공식 원문: {row['original_link'] or '링크 확인 필요'}

최종 신청 자격과 일정은 반드시 공식 공고에서 확인해 주세요.
""")
    return msg


def deliver_pending(*, dry_run: bool = False, limit: int = 100) -> dict[str, int]:
    load_local_env()
    if not dry_run and not configured():
        return {"sent": 0, "failed": 0, "skipped": 0, "reason": "SMTP 미설정"}
    repository = MySQLPolicyRepository(); connection = repository.connect(); repository.initialize(connection)
    counts = {"sent": 0, "failed": 0, "skipped": 0}
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""SELECT candidate.id, candidate.match_reason, profile.email, event.change_type,
            policy.title, policy.application_end_date, policy.original_link
            FROM policy_match_candidates candidate
            JOIN user_profiles profile ON profile.id = candidate.user_id
            JOIN policy_change_events event ON event.id = candidate.event_id
            JOIN policy_records policy ON policy.id = candidate.policy_id
            LEFT JOIN notification_deliveries delivery ON delivery.candidate_id = candidate.id AND delivery.channel = 'email'
            WHERE candidate.status = 'pending' AND (delivery.id IS NULL OR delivery.status = 'failed')
            ORDER BY candidate.created_at ASC LIMIT %s""", (limit,))
        rows = cursor.fetchall(); smtp = None
        if not dry_run and rows:
            smtp = smtplib.SMTP(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587")), timeout=20)
            if os.getenv("SMTP_STARTTLS", "true").lower() != "false": smtp.starttls()
            if os.getenv("SMTP_USERNAME"): smtp.login(os.environ["SMTP_USERNAME"], os.getenv("SMTP_PASSWORD", ""))
        for row in rows:
            now = datetime.now().replace(microsecond=0)
            if not row["email"]:
                cursor.execute("INSERT INTO notification_deliveries (candidate_id, channel, destination, status, error_message, created_at) VALUES (%s, 'email', '', 'skipped', %s, %s)", (row["id"], "카카오 계정 이메일 미동의 또는 미제공", now)); counts["skipped"] += 1; continue
            if dry_run:
                counts["sent"] += 1; continue
            try:
                smtp.send_message(_message(row))
                cursor.execute("""INSERT INTO notification_deliveries (candidate_id, channel, destination, status, sent_at, created_at)
                    VALUES (%s, 'email', %s, 'sent', %s, %s)
                    ON DUPLICATE KEY UPDATE destination=VALUES(destination), status='sent', error_message=NULL, sent_at=VALUES(sent_at)""", (row["id"], row["email"], now, now))
                cursor.execute("UPDATE policy_match_candidates SET status='notified' WHERE id=%s", (row["id"],)); counts["sent"] += 1
            except Exception as error:
                cursor.execute("""INSERT INTO notification_deliveries (candidate_id, channel, destination, status, error_message, created_at)
                    VALUES (%s, 'email', %s, 'failed', %s, %s)
                    ON DUPLICATE KEY UPDATE destination=VALUES(destination), status='failed', error_message=VALUES(error_message), sent_at=NULL""", (row["id"], row["email"], str(error)[:1000], now)); counts["failed"] += 1
        if smtp: smtp.quit()
        connection.commit(); return counts
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="정책 알림 이메일 발송")
    parser.add_argument("--dry-run", action="store_true", help="이메일을 보내지 않고 대상 수만 확인")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(); print(deliver_pending(dry_run=args.dry_run, limit=args.limit))
