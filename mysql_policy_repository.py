"""MySQL persistence and change detection for normalized youth-policy records."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any


class MySQLPolicyRepository:
    def __init__(self) -> None:
        self.database = os.getenv("MYSQL_DATABASE", "youth_policy")
        if not re.fullmatch(r"[A-Za-z0-9_]+", self.database):
            raise ValueError("MYSQL_DATABASE는 영문·숫자·밑줄만 사용할 수 있습니다.")
        self.connection_config = {
            "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
            "port": int(os.getenv("MYSQL_PORT", "3306")),
            "user": os.getenv("MYSQL_USER", "root"),
            "password": os.getenv("MYSQL_PASSWORD", ""),
            "charset": "utf8mb4",
        }

    @staticmethod
    def record_key(record: dict[str, Any]) -> str:
        stable_id = record.get("source_record_id") or record.get("original_link") or record.get("title")
        identity = "\n".join((record.get("source_site", ""), record.get("category", ""), stable_id))
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def connect(self):
        import mysql.connector

        server_connection = mysql.connector.connect(**self.connection_config)
        try:
            cursor = server_connection.cursor()
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{self.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            server_connection.commit()
            cursor.close()
        finally:
            server_connection.close()
        return mysql.connector.connect(**self.connection_config, database=self.database)

    def initialize(self, connection) -> None:
        cursor = connection.cursor()
        try:
            for statement in Path("schema_mysql.sql").read_text(encoding="utf-8").split(";"):
                if statement.strip():
                    cursor.execute(statement)
            cursor.execute("SHOW COLUMNS FROM policy_change_events")
            event_columns = {row[0] for row in cursor.fetchall()}
            cursor.execute("ALTER TABLE policy_change_events MODIFY change_type ENUM('new', 'updated', 'deadline') NOT NULL")
            if "event_key" not in event_columns:
                cursor.execute("ALTER TABLE policy_change_events ADD COLUMN event_key VARCHAR(150) NULL AFTER change_type")
            cursor.execute("SHOW INDEX FROM policy_change_events WHERE Key_name = 'uq_policy_change_events_event_key'")
            if cursor.fetchone() is None:
                cursor.execute("ALTER TABLE policy_change_events ADD UNIQUE KEY uq_policy_change_events_event_key (event_key)")
            # 이전 단계에서 생성된 프로필 테이블도 카카오 로그인용 구조로 안전하게 확장한다.
            cursor.execute("SHOW COLUMNS FROM user_profiles")
            columns = {row[0] for row in cursor.fetchall()}
            if "kakao_user_id" not in columns:
                cursor.execute("ALTER TABLE user_profiles ADD COLUMN kakao_user_id BIGINT UNSIGNED NULL AFTER id")
            if "email" not in columns:
                cursor.execute("ALTER TABLE user_profiles ADD COLUMN email VARCHAR(255) NULL AFTER display_name")
            profile_columns = {
                "residency_months": "SMALLINT UNSIGNED NULL AFTER residency_city",
                "employment_status": "VARCHAR(50) NULL AFTER residency_months",
                "income_band": "VARCHAR(50) NULL AFTER employment_status",
                "education_level": "VARCHAR(50) NULL AFTER income_band",
                "household_status": "VARCHAR(50) NULL AFTER education_level",
            }
            for column, definition in profile_columns.items():
                if column not in columns:
                    cursor.execute(f"ALTER TABLE user_profiles ADD COLUMN {column} {definition}")
            cursor.execute("ALTER TABLE user_profiles MODIFY birth_date DATE NULL")
            cursor.execute("SHOW INDEX FROM user_profiles WHERE Key_name = 'uq_user_profiles_kakao_user_id'")
            if cursor.fetchone() is None:
                cursor.execute("ALTER TABLE user_profiles ADD UNIQUE KEY uq_user_profiles_kakao_user_id (kakao_user_id)")
            connection.commit()
        finally:
            cursor.close()

    def sync(self, records: list[dict[str, Any]]) -> dict[str, int]:
        connection = self.connect()
        try:
            self.initialize(connection)
            cursor = connection.cursor(dictionary=True)
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
                            first_seen_at, last_seen_at, updated_at
                        ) VALUES (
                            %(record_key)s, %(source_site)s, %(source_record_id)s, %(category)s, %(title)s, %(target_region)s, %(target_condition)s,
                            %(qualification_text)s, %(min_age)s, %(max_age)s, %(residency_condition)s, %(period)s, %(application_start_date)s,
                            %(application_end_date)s, %(content)s, %(application_method)s, %(organization)s, %(attachment_links)s,
                            %(attachment_files)s, %(attachment_text)s, %(attachment_status)s, %(content_hash)s, %(original_link)s,
                            %(now)s, %(now)s, %(now)s
                        )""",
                        params,
                    )
                    policy_id = cursor.lastrowid
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
                            last_seen_at=%(now)s, updated_at=%(now)s
                            WHERE id=%(policy_id)s""",
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
