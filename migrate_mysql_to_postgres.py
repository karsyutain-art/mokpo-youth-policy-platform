"""Copy the complete application database from MySQL to PostgreSQL/pgvector.

The source is read-only. Run against an empty PostgreSQL database by default;
pass --replace-target only when the target data may be safely replaced.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from psycopg2.extras import Json, execute_values

from postgres_policy_repository import PostgresPolicyRepository
from youth_data_collector import load_local_env


TABLES = (
    "policy_records",
    "user_profiles",
    "policy_change_events",
    "user_interests",
    "policy_wishlists",
    "policy_chat_messages",
    "collection_runs",
    "application_preparations",
    "application_requirements",
    "application_form_fields",
    "application_preparation_versions",
    "policy_match_candidates",
    "notification_deliveries",
    "push_subscriptions",
)
ID_TABLES = tuple(table for table in TABLES if table not in {"user_interests", "policy_wishlists"})


def mysql_connection():
    import mysql.connector

    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DATABASE", "youth_policy"),
        charset="utf8mb4",
    )


def target_columns(cursor, table: str) -> list[dict[str, str]]:
    cursor.execute(
        """SELECT column_name, data_type FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position""",
        (table,),
    )
    return list(cursor.fetchall())


def source_columns(cursor, table: str) -> set[str]:
    cursor.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema = %s AND table_name = %s""",
        (os.getenv("MYSQL_DATABASE", "youth_policy"), table),
    )
    return {next(iter(row.values())) for row in cursor.fetchall()}


def convert_value(value: Any, data_type: str) -> Any:
    if value is None:
        return None
    if data_type == "boolean":
        return bool(value)
    if data_type == "jsonb":
        if isinstance(value, (str, bytes, bytearray)):
            value = json.loads(value)
        return Json(value, dumps=lambda item: json.dumps(item, ensure_ascii=False, default=str))
    return value


def reset_sequences(cursor) -> None:
    for table in ID_TABLES:
        cursor.execute(
            """SELECT setval(pg_get_serial_sequence(%s, 'id'),
                     GREATEST(COALESCE((SELECT MAX(id) FROM {}), 0), 1),
                     EXISTS(SELECT 1 FROM {}))""".format(table, table),
            (table,),
        )


def migrate(replace_target: bool = False) -> dict[str, int]:
    source = mysql_connection()
    target_repository = PostgresPolicyRepository()
    target = target_repository.connect()
    target_repository.initialize(target)
    counts: dict[str, int] = {}
    try:
        source_cursor = source.cursor(dictionary=True)
        target_cursor = target.cursor()
        target_cursor.execute("SELECT COUNT(*) AS count FROM policy_records")
        existing = int(target_cursor.fetchone()["count"])
        if existing and not replace_target:
            raise RuntimeError(
                f"대상 PostgreSQL에 정책 {existing}건이 있습니다. 비어 있는 DB를 사용하거나 --replace-target을 지정하세요."
            )
        if replace_target:
            target_cursor.execute(
                "TRUNCATE TABLE " + ", ".join(reversed(TABLES)) + " RESTART IDENTITY CASCADE"
            )

        for table in TABLES:
            source_cols = source_columns(source_cursor, table)
            if not source_cols:
                counts[table] = 0
                continue
            target_defs = target_columns(target_cursor, table)
            columns = [item["column_name"] for item in target_defs if item["column_name"] in source_cols]
            types = {item["column_name"]: item["data_type"] for item in target_defs}
            source_cursor.execute(f"SELECT {', '.join(f'`{name}`' for name in columns)} FROM `{table}` ORDER BY 1")
            rows = source_cursor.fetchall()
            if rows:
                values = [tuple(convert_value(row[name], types[name]) for name in columns) for row in rows]
                execute_values(
                    target_cursor,
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s",
                    values,
                    page_size=500,
                )
            counts[table] = len(rows)

        reset_sequences(target_cursor)
        target.commit()
        return counts
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="MySQL 전체 데이터를 PostgreSQL + pgvector로 복사")
    parser.add_argument("--replace-target", action="store_true", help="대상 PostgreSQL의 기존 데이터를 지우고 다시 복사")
    args = parser.parse_args()
    load_local_env()
    counts = migrate(args.replace_target)
    for table, count in counts.items():
        print(f"{table}: {count}건")
    from rag_policy_search import PolicyRAG

    result = PolicyRAG().rebuild()
    print(f"pgvector 재구축: 정책 {result['policies']}건 / 청크 {result['chunks']}건")


if __name__ == "__main__":
    main()
