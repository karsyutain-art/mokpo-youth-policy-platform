"""Windows one-click launcher for the Docker-based Mokpo youth-policy service."""

from __future__ import annotations

import base64
import os
import secrets
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from tkinter import END, DISABLED, NORMAL, Button, Label, Text, Tk, messagebox


BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ROOT_DIR = Path(os.getenv("LOCALAPPDATA", Path.home())) / "MokpoYouthPolicy" if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / ".env"
ENV_EXAMPLE_PATH = ROOT_DIR / ".env.example"
COMPOSE_FILES = (ROOT_DIR / "docker-compose.yml", ROOT_DIR / "Dockerfile.backend", ROOT_DIR / "frontend" / "Dockerfile")


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def generate_vapid_keys() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private_key = ec.generate_private_key(ec.SECP256R1())
    private_number = private_key.private_numbers().private_value.to_bytes(32, "big")
    public_point = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64url(public_point), base64url(private_number)


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def write_env(values: dict[str, str]) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    output: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in values:
                output.append(f"{key}={values[key]}")
                seen.add(key)
                continue
        output.append(line)
    for key, value in values.items():
        if key not in seen:
            output.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(output) + "\n", encoding="utf-8")


def ensure_env() -> tuple[bool, str]:
    if not ENV_PATH.exists():
        if not ENV_EXAMPLE_PATH.exists():
            return False, ".env.example 파일을 찾지 못했습니다."
        shutil.copyfile(ENV_EXAMPLE_PATH, ENV_PATH)
    values = parse_env(ENV_PATH)
    updates: dict[str, str] = {}
    placeholders = {"", "change_this_database_password", "change_this_root_password", "change_this_to_a_long_random_secret"}
    if values.get("POSTGRES_PASSWORD", "") in placeholders and values.get("MYSQL_PASSWORD", "") not in placeholders:
        # 기존 MySQL 설치에서 업그레이드한 사용자는 이전 시 사용한 동일 비밀번호를 이어 쓴다.
        updates["POSTGRES_PASSWORD"] = values["MYSQL_PASSWORD"]
    for key in ("POSTGRES_PASSWORD", "FLASK_SECRET_KEY"):
        if values.get(key, "") in placeholders and key not in updates:
            updates[key] = secrets.token_urlsafe(32)
    if not values.get("VAPID_PUBLIC_KEY") or not values.get("VAPID_PRIVATE_KEY"):
        updates["VAPID_PUBLIC_KEY"], updates["VAPID_PRIVATE_KEY"] = generate_vapid_keys()
    if updates:
        write_env(updates)
        values.update(updates)
    missing_kakao = not values.get("KAKAO_REST_API_KEY")
    return True, "초기 설정을 만들었습니다." + (" 카카오 로그인을 쓰려면 .env에 KAKAO_REST_API_KEY를 입력하세요." if missing_kakao else "")


def prepare_runtime_files() -> tuple[bool, str]:
    """Install bundled source beside the user's profile without bundling secrets or data."""
    if not getattr(sys, "frozen", False):
        return True, ""
    source_dir = BUNDLE_DIR / "app"
    if not source_dir.exists():
        return False, "실행파일에 포함된 서비스 파일을 찾지 못했습니다."
    try:
        ROOT_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_dir, ROOT_DIR, dirs_exist_ok=True)
        return True, f"서비스 파일 위치: {ROOT_DIR}"
    except OSError as error:
        return False, f"서비스 파일을 준비하지 못했습니다: {error}"


def compose_command(*args: str) -> list[str]:
    return ["docker", "compose", "--project-name", "mokpo_youth_policy", "--parallel", "1", *args]


class Launcher:
    def __init__(self) -> None:
        self.window = Tk()
        self.window.title("목포 청년 정책 - 실행 도우미")
        self.window.geometry("610x430")
        self.window.resizable(False, False)
        self.status = Label(self.window, text="서비스 상태를 확인하는 중입니다.", font=("Malgun Gothic", 11))
        self.status.pack(pady=(20, 8))
        self.start_button = Button(self.window, text="서비스 시작", width=18, command=self.start)
        self.start_button.pack(pady=4)
        self.open_button = Button(self.window, text="서비스 열기", width=18, command=self.open_service)
        self.open_button.pack(pady=4)
        self.config_button = Button(self.window, text=".env 설정 열기", width=18, command=self.open_config)
        self.config_button.pack(pady=4)
        self.stop_button = Button(self.window, text="서비스 중지", width=18, command=self.stop)
        self.stop_button.pack(pady=4)
        self.log = Text(self.window, height=13, width=76, state=DISABLED, font=("Consolas", 9))
        self.log.pack(padx=16, pady=(12, 16))
        ready, note = prepare_runtime_files()
        if not ready:
            self.status.config(text="실행파일 초기화에 실패했습니다.")
            self.append(note)
        else:
            if note:
                self.append(note)
            self.check_ready()

    def append(self, message: str) -> None:
        self.log.config(state=NORMAL)
        self.log.insert(END, message.rstrip() + "\n")
        self.log.see(END)
        self.log.config(state=DISABLED)

    def set_busy(self, busy: bool) -> None:
        state = DISABLED if busy else NORMAL
        for button in (self.start_button, self.open_button, self.config_button, self.stop_button):
            button.config(state=state)

    def check_ready(self) -> None:
        missing = [str(path.relative_to(ROOT_DIR)) for path in COMPOSE_FILES if not path.exists()]
        if missing:
            self.status.config(text="실행에 필요한 파일을 찾지 못했습니다.")
            self.append("누락 파일: " + ", ".join(missing))
            return
        try:
            result = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"], cwd=ROOT_DIR, capture_output=True, text=True, timeout=12)
            if result.returncode == 0:
                self.status.config(text=f"Docker Desktop 준비됨 · 엔진 {result.stdout.strip()}")
                return
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        self.status.config(text="Docker Desktop을 실행한 뒤 다시 시도하세요.")
        self.append("Docker Desktop이 설치·실행 중인지 확인하세요.")

    def run_background(self, title: str, command: list[str], after=None) -> None:
        self.set_busy(True)
        self.status.config(text=title)

        def worker() -> None:
            environment = os.environ.copy()
            environment["COMPOSE_PARALLEL_LIMIT"] = "1"
            try:
                process = subprocess.Popen(command, cwd=ROOT_DIR, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
                assert process.stdout is not None
                for line in process.stdout:
                    self.window.after(0, self.append, line)
                code = process.wait()
                if code:
                    self.window.after(0, self.status.config, {"text": f"실행 실패 (종료 코드 {code})"})
                else:
                    self.window.after(0, self.status.config, {"text": "완료되었습니다."})
                    if after:
                        self.window.after(0, after)
            except Exception as error:
                self.window.after(0, self.append, f"오류: {error}")
                self.window.after(0, self.status.config, {"text": "실행 중 오류가 발생했습니다."})
            finally:
                self.window.after(0, self.set_busy, False)

        threading.Thread(target=worker, daemon=True).start()

    def start(self) -> None:
        ready, note = ensure_env()
        if not ready:
            messagebox.showerror("초기 설정 실패", note)
            return
        self.append(note)
        self.run_background("Docker 서비스 이미지를 준비하고 시작합니다…", compose_command("up", "-d", "--build"), self.open_service)

    def stop(self) -> None:
        if messagebox.askyesno("서비스 중지", "실행 중인 서비스 컨테이너를 중지할까요? 데이터는 삭제되지 않습니다."):
            self.run_background("서비스를 중지하고 있습니다…", compose_command("down"))

    def open_config(self) -> None:
        ready, note = ensure_env()
        if not ready:
            messagebox.showerror("초기 설정 실패", note)
            return
        self.append(note)
        os.startfile(ENV_PATH)  # type: ignore[attr-defined]

    def open_service(self) -> None:
        ready, _ = ensure_env()
        if ready:
            webbrowser.open(f"http://localhost:{parse_env(ENV_PATH).get('WEB_PORT', '8080')}")

    def run(self) -> None:
        self.window.mainloop()


if __name__ == "__main__":
    Launcher().run()
