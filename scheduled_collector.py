"""Run the youth-policy collector once a day with APScheduler."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger


PROJECT_DIR = Path(__file__).resolve().parent


def collect_once(max_pages: int, delay: float, download_attachments: bool) -> None:
    command = [
        sys.executable,
        str(PROJECT_DIR / "youth_data_collector.py"),
        "--max-pages", str(max_pages),
        "--delay", str(delay),
        "--mysql",
    ]
    if download_attachments:
        command.append("--download-attachments")
    result = subprocess.run(command, cwd=PROJECT_DIR, check=False)
    if result.returncode:
        raise RuntimeError(f"수집기가 종료 코드 {result.returncode}로 끝났습니다.")
    from policy_matcher import PolicyMatcher

    created = PolicyMatcher().create_candidates()
    print(f"정책 매칭 후보 생성: {created}건")


def main() -> None:
    parser = argparse.ArgumentParser(description="매일 실행하는 청년 정책 수집 스케줄러")
    parser.add_argument("--hour", type=int, default=3, help="매일 실행 시각(0~23, 기본 03시)")
    parser.add_argument("--minute", type=int, default=0, help="매일 실행 분(0~59, 기본 00분)")
    parser.add_argument("--max-pages", type=int, default=1, help="대상별 수집 목록 페이지 수")
    parser.add_argument("--delay", type=float, default=5.0, help="요청 간 대기 시간(초)")
    parser.add_argument("--without-attachments", action="store_true", help="첨부파일 다운로드·텍스트 추출을 생략")
    parser.add_argument("--run-now", action="store_true", help="스케줄 대기 전 수집을 즉시 1회 실행")
    parser.add_argument("--once", action="store_true", help="수집을 1회 실행한 뒤 종료 (Windows 작업 스케줄러용)")
    args = parser.parse_args()
    if not 0 <= args.hour <= 23 or not 0 <= args.minute <= 59 or args.max_pages < 1 or args.delay < 3:
        parser.error("시간은 0~23시, 분은 0~59분, max-pages는 1 이상, delay는 3초 이상이어야 합니다.")

    download_attachments = not args.without_attachments
    if args.once:
        collect_once(args.max_pages, args.delay, download_attachments)
        return
    if args.run_now:
        collect_once(args.max_pages, args.delay, download_attachments)

    scheduler = BlockingScheduler(timezone=ZoneInfo("Asia/Seoul"))
    scheduler.add_job(
        collect_once,
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
