"""PostgreSQL/pgvector persistence and change detection for youth-policy records."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Any


class PostgresPolicyRepository:
    def __init__(self) -> None:
        self.connection_config = {
            "host": os.getenv("POSTGRES_HOST", "127.0.0.1"),
            "port": int(os.getenv("POSTGRES_PORT", "55432")),
            "dbname": os.getenv("POSTGRES_DB", "youth_policy"),
            "user": os.getenv("POSTGRES_USER", "youth_policy"),
            "password": os.getenv("POSTGRES_PASSWORD") or os.getenv("MYSQL_PASSWORD", ""),
        }

    @staticmethod
    def record_key(record: dict[str, Any]) -> str:
        stable_id = record.get("source_record_id") or record.get("original_link") or record.get("title")
        identity = "\n".join((record.get("source_site", ""), record.get("category", ""), stable_id))
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def connect(self):
        import psycopg2
        from psycopg2.extras import RealDictCursor

        return psycopg2.connect(**self.connection_config, cursor_factory=RealDictCursor)

    def initialize(self, connection) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute(Path(__file__).with_name("schema_postgres.sql").read_text(encoding="utf-8"))
            connection.commit()
        finally:
            cursor.close()

    def sync(self, records: list[dict[str, Any]]) -> dict[str, int]:
        connection = self.connect()
        try:
            self.initialize(connection)
            cursor = connection.cursor()
            counts = {"new": 0, "updated": 0, "unchanged": 0}
            now = datetime.now().replace(microsecond=0)
            for record in records:
                key = self.record_key(record)
                cursor.execute("SELECT id, content_hash FROM policy_records WHERE record_key = %s", (key,))
                existing = cursor.fetchone()
                params = {
                    **record,
                    "application_start_date": record.get("application_start_date") or None,
                    "application_end_date": record.get("application_end_date") or None,
                    "min_age": record.get("min_age") if record.get("min_age") not in (None, "") else None,
                    "max_age": record.get("max_age") if record.get("max_age") not in (None, "") else None,
                    "record_key": key,
                    "now": now,
                }
                if existing is None:
                    cursor.execute(
                        """INSERT INTO policy_records (
                            record_key, source_site, source_record_id, category, title, target_region, target_condition,
                            qualification_text, min_age, max_age, residency_condition, period_text, application_start_date,
                            application_end_date, content, application_method, organization, attachment_links,
                            attachment_files, attachment_text, attachment_status, content_hash, original_link,
                            review_status, is_public, first_seen_at, last_seen_at, updated_at
                        ) VALUES (
                            %(record_key)s, %(source_site)s, %(source_record_id)s, %(category)s, %(title)s, %(target_region)s, %(target_condition)s,
                            %(qualification_text)s, %(min_age)s, %(max_age)s, %(residency_condition)s, %(period)s, %(application_start_date)s,
                            %(application_end_date)s, %(content)s, %(application_method)s, %(organization)s, %(attachment_links)s,
                            %(attachment_files)s, %(attachment_text)s, %(attachment_status)s, %(content_hash)s, %(original_link)s,
                            'pending', FALSE, %(now)s, %(now)s, %(now)s
                        ) RETURNING id""",
                        params,
                    )
                    policy_id = cursor.fetchone()["id"]
                    cursor.execute(
                        "INSERT INTO policy_change_events (policy_id, change_type, previous_content_hash, current_content_hash, detected_at) VALUES (%s, 'new', NULL, %s, %s)",
                        (policy_id, record["content_hash"], now),
                    )
                    counts["new"] += 1
                elif existing["content_hash"] != record["content_hash"]:
                    cursor.execute(
                        """UPDATE policy_records SET
                            title=%(title)s, target_region=%(target_region)s, target_condition=%(target_condition)s,
                            qualification_text=%(qualification_text)s, min_age=%(min_age)s, max_age=%(max_age)s,
                            residency_condition=%(residency_condition)s, period_text=%(period)s,
                            application_start_date=%(application_start_date)s, application_end_date=%(application_end_date)s,
                            content=%(content)s, application_method=%(application_method)s, organization=%(organization)s,
                            attachment_links=%(attachment_links)s, attachment_files=%(attachment_files)s,
                            attachment_text=%(attachment_text)s, attachment_status=%(attachment_status)s,
                            content_hash=%(content_hash)s, original_link=%(original_link)s,
                            review_status='pending', is_public=FALSE, reviewed_at=NULL, reviewed_by=NULL,
                            last_seen_at=%(now)s, updated_at=%(now)s WHERE id=%(policy_id)s""",
                        {**params, "policy_id": existing["id"]},
                    )
                    cursor.execute(
                        "INSERT INTO policy_change_events (policy_id, change_type, previous_content_hash, current_content_hash, detected_at) VALUES (%s, 'updated', %s, %s, %s)",
                        (existing["id"], existing["content_hash"], record["content_hash"], now),
                    )
                    counts["updated"] += 1
                else:
                    cursor.execute("UPDATE policy_records SET last_seen_at = %s WHERE id = %s", (now, existing["id"]))
                    counts["unchanged"] += 1
            connection.commit()
            return counts
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
