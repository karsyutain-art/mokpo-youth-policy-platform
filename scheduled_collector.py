"""Run the youth-policy collector once a day with APScheduler."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import subprocess
import sys
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger


PROJECT_DIR = Path(__file__).resolve().parent
LOG_DIR = PROJECT_DIR / "logs"


class Tee:
    """Write scheduled-run output to both the console and a persistent log."""

    def __init__(self, *streams: object) -> None:
        self.streams = streams

    def write(self, data: str) -> int:
        for stream in self.streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


def collect_once(max_pages: int, delay: float, download_attachments: bool) -> None:
    load_project_env()
    command = [
        sys.executable,
        str(PROJECT_DIR / "youth_data_collector.py"),
        "--max-pages", str(max_pages),
        "--delay", str(delay),
        "--database",
    ]
    if download_attachments:
        command.append("--download-attachments")
    process = subprocess.Popen(
        command,
        cwd=PROJECT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="")
    returncode = process.wait()
    if returncode:
        raise RuntimeError(f"수집기가 종료 코드 {returncode}로 끝났습니다.")

    run_postprocessing()


def load_project_env() -> None:
    """Load the same project-local configuration for every scheduled step."""
    from youth_data_collector import load_local_env

    load_local_env(PROJECT_DIR / ".env")


def run_postprocessing() -> None:
    """Refresh matching, delivery candidates, and the RAG index after collection."""
    load_project_env()
    from policy_matcher import PolicyMatcher

    matcher = PolicyMatcher()
    created = matcher.create_candidates()
    print(f"정책 매칭 후보 생성: {created}건")
    deadline = matcher.create_deadline_candidates()
    print(f"마감 임박 이벤트 생성: {deadline['events']}건 / 알림 후보 생성: {deadline['candidates']}건")
    from notification_delivery import deliver_pending

    delivered = deliver_pending()
    print(f"이메일 알림 발송: {delivered}")
    from push_delivery import deliver_pending as deliver_web_push_pending

    pushed = deliver_web_push_pending()
    print(f"웹 푸시 알림 발송: {pushed}")
    from rag_policy_search import PolicyRAG

    rag = PolicyRAG().rebuild()
    print(f"RAG 인덱스 갱신: 정책 {rag['policies']}건 / 청크 {rag['chunks']}건")


def start_run_record(run_type: str) -> int | None:
    """Persist scheduler status when PostgreSQL is reachable; never block collection on logging."""
    try:
        load_project_env()
        from postgres_policy_repository import PostgresPolicyRepository

        repository = PostgresPolicyRepository()
        connection = repository.connect()
        try:
            repository.initialize(connection)
            cursor = connection.cursor()
            cursor.execute(
                "INSERT INTO collection_runs (run_type, status, started_at) VALUES (%s, 'running', %s) RETURNING id",
                (run_type, datetime.now().replace(microsecond=0)),
            )
            run_id = int(cursor.fetchone()["id"])
            connection.commit()
            return run_id
        finally:
            connection.close()
    except Exception as error:
        print(f"수집 이력 기록 시작 실패: {error}", file=sys.stderr)
        return None


def finish_run_record(run_id: int | None, status: str, message: str | None = None) -> None:
    if run_id is None:
        return
    try:
        from postgres_policy_repository import PostgresPolicyRepository

        connection = PostgresPolicyRepository().connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "UPDATE collection_runs SET status = %s, finished_at = %s, message = %s WHERE id = %s",
                (status, datetime.now().replace(microsecond=0), message, run_id),
            )
            connection.commit()
        finally:
            connection.close()
    except Exception as error:
        print(f"수집 이력 기록 완료 실패: {error}", file=sys.stderr)


def collect_once_with_log(
    max_pages: int,
    delay: float,
    download_attachments: bool,
    *,
    postprocess_only: bool = False,
) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"scheduled_collector_{datetime.now():%Y%m%d}.log"
    with log_path.open("a", encoding="utf-8") as log_file:
        stdout = Tee(sys.stdout, log_file)
        stderr = Tee(sys.stderr, log_file)
        with redirect_stdout(stdout), redirect_stderr(stderr):
            label = "후처리" if postprocess_only else "자동 수집"
            run_id = start_run_record("postprocess" if postprocess_only else "collection")
            print(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] {label} 시작")
            try:
                if postprocess_only:
                    run_postprocessing()
                else:
                    collect_once(max_pages, delay, download_attachments)
            except Exception:
                traceback.print_exc()
                finish_run_record(run_id, "failed", f"{label} 단계에서 오류 발생")
                print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {label} 실패")
                raise
            finish_run_record(run_id, "succeeded", f"{label} 완료")
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {label} 완료")


def main() -> None:
    parser = argparse.ArgumentParser(description="매일 실행하는 청년 정책 수집 스케줄러")
    parser.add_argument("--hour", type=int, default=3, help="매일 실행 시각(0~23, 기본 03시)")
    parser.add_argument("--minute", type=int, default=0, help="매일 실행 분(0~59, 기본 00분)")
    parser.add_argument("--max-pages", type=int, default=1, help="대상별 수집 목록 페이지 수")
    parser.add_argument("--delay", type=float, default=5.0, help="요청 간 대기 시간(초)")
    parser.add_argument("--without-attachments", action="store_true", help="첨부파일 다운로드·텍스트 추출을 생략")
    parser.add_argument("--run-now", action="store_true", help="스케줄 대기 전 수집을 즉시 1회 실행")
    parser.add_argument("--once", action="store_true", help="수집을 1회 실행한 뒤 종료 (Windows 작업 스케줄러용)")
    parser.add_argument("--postprocess-only", action="store_true", help="수집 없이 매칭·알림·RAG 인덱스만 갱신")
    args = parser.parse_args()
    if not 0 <= args.hour <= 23 or not 0 <= args.minute <= 59 or args.max_pages < 1 or args.delay < 3:
        parser.error("시간은 0~23시, 분은 0~59분, max-pages는 1 이상, delay는 3초 이상이어야 합니다.")
    if args.postprocess_only and (args.once or args.run_now):
        parser.error("--postprocess-only는 --once 또는 --run-now와 함께 사용할 수 없습니다.")

    download_attachments = not args.without_attachments
    load_project_env()
    if args.postprocess_only:
        collect_once_with_log(args.max_pages, args.delay, download_attachments, postprocess_only=True)
        return
    if args.once:
        collect_once_with_log(args.max_pages, args.delay, download_attachments)
        return
    if args.run_now:
        collect_once_with_log(args.max_pages, args.delay, download_attachments)

    scheduler = BlockingScheduler(timezone=ZoneInfo("Asia/Seoul"))
    scheduler.add_job(
        collect_once_with_log,
        CronTrigger(hour=args.hour, minute=args.minute, timezone=ZoneInfo("Asia/Seoul")),
        args=[args.max_pages, args.delay, download_attachments],
        id="daily_youth_policy_collection",
        replace_existing=True,
        misfire_grace_time=60 * 60,
        coalesce=True,
    )
    print(f"스케줄러 실행: 매일 {args.hour:02d}:{args.minute:02d} (Asia/Seoul). 종료하려면 Ctrl+C를 누르세요.")
    scheduler.start()


if __name__ == "__main__":
    main()
