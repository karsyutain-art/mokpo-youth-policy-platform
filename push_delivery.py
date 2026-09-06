"""Idempotent Web Push delivery for matched policy notifications."""

from __future__ import annotations

import json
import os
from datetime import datetime

from mysql_policy_repository import MySQLPolicyRepository
from youth_data_collector import load_local_env


def configured() -> bool:
    return bool(
        os.getenv("VAPID_PUBLIC_KEY")
        and os.getenv("VAPID_PRIVATE_KEY")
        and os.getenv("VAPID_SUBJECT")
    )


def _payload(row: dict) -> str:
    labels = {"new": "새 공고", "updated": "변경 공고", "deadline": "마감 임박"}
    base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")
    return json.dumps(
        {
            "title": f"[{labels.get(row['change_type'], '정책 알림')}] {row['title']}",
            "body": row["match_reason"][:180],
            "url": f"{base_url}/?notification={row['id']}&policy={row['policy_id']}",
            "tag": f"policy-candidate-{row['id']}",
        },
        ensure_ascii=False,
    )


def deliver_pending(*, dry_run: bool = False, limit: int = 100) -> dict[str, int | str]:
    """Deliver each candidate once to the user's most recently subscribed browser."""
    load_local_env()
    if not dry_run and not configured():
        return {"sent": 0, "failed": 0, "skipped": 0, "reason": "VAPID 미설정"}
    if not dry_run:
        from pywebpush import WebPushException, webpush

    repository = MySQLPolicyRepository()
    connection = repository.connect()
    repository.initialize(connection)
    counts: dict[str, int | str] = {"sent": 0, "failed": 0, "skipped": 0}
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """SELECT candidate.id, candidate.policy_id, candidate.match_reason, event.change_type,
                policy.title, subscription.id AS subscription_id, subscription.endpoint,
                subscription.p256dh, subscription.auth_secret
                FROM policy_match_candidates candidate
                JOIN policy_change_events event ON event.id = candidate.event_id
                JOIN policy_records policy ON policy.id = candidate.policy_id
                JOIN push_subscriptions subscription ON subscription.user_id = candidate.user_id
                LEFT JOIN notification_deliveries delivery
                  ON delivery.candidate_id = candidate.id AND delivery.channel = 'web_push'
                WHERE candidate.status != 'dismissed'
                  AND (delivery.id IS NULL OR delivery.status = 'failed')
                ORDER BY candidate.created_at ASC LIMIT %s""",
            (limit,),
        )
        rows = cursor.fetchall()
        for row in rows:
            now = datetime.now().replace(microsecond=0)
            if dry_run:
                counts["sent"] = int(counts["sent"]) + 1
                continue
            try:
                webpush(
                    subscription_info={
                        "endpoint": row["endpoint"],
                        "keys": {"p256dh": row["p256dh"], "auth": row["auth_secret"]},
                    },
                    data=_payload(row),
                    vapid_private_key=os.environ["VAPID_PRIVATE_KEY"],
                    vapid_claims={"sub": os.environ["VAPID_SUBJECT"]},
                    ttl=60 * 60 * 24,
                )
                cursor.execute(
                    """INSERT INTO notification_deliveries
                        (candidate_id, channel, destination, status, sent_at, created_at)
                        VALUES (%s, 'web_push', %s, 'sent', %s, %s)
                        ON DUPLICATE KEY UPDATE destination=VALUES(destination), status='sent',
                          error_message=NULL, sent_at=VALUES(sent_at)""",
                    (row["id"], row["endpoint"], now, now),
                )
                cursor.execute("UPDATE policy_match_candidates SET status='notified' WHERE id=%s", (row["id"],))
                counts["sent"] = int(counts["sent"]) + 1
            except WebPushException as error:
                status_code = getattr(getattr(error, "response", None), "status_code", None)
                if status_code in {404, 410}:
                    cursor.execute("DELETE FROM push_subscriptions WHERE id=%s", (row["subscription_id"],))
                cursor.execute(
                    """INSERT INTO notification_deliveries
                        (candidate_id, channel, destination, status, error_message, created_at)
                        VALUES (%s, 'web_push', %s, 'failed', %s, %s)
                        ON DUPLICATE KEY UPDATE destination=VALUES(destination), status='failed',
                          error_message=VALUES(error_message), sent_at=NULL""",
                    (row["id"], row["endpoint"], str(error)[:1000], now),
                )
                counts["failed"] = int(counts["failed"]) + 1
            except Exception as error:
                cursor.execute(
                    """INSERT INTO notification_deliveries
                        (candidate_id, channel, destination, status, error_message, created_at)
                        VALUES (%s, 'web_push', %s, 'failed', %s, %s)
                        ON DUPLICATE KEY UPDATE destination=VALUES(destination), status='failed',
                          error_message=VALUES(error_message), sent_at=NULL""",
                    (row["id"], row["endpoint"], str(error)[:1000], now),
                )
                counts["failed"] = int(counts["failed"]) + 1
        connection.commit()
        return counts
    finally:
        connection.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="웹 푸시 알림 발송")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    print(deliver_pending(dry_run=args.dry_run, limit=args.limit))
