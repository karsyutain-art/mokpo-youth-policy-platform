"""Local Flask server for Kakao OAuth login and policy-profile onboarding."""

from __future__ import annotations

import argparse
import hmac
import os
import secrets
from datetime import date, datetime
from urllib.parse import urlencode

import requests
from flask import Flask, redirect, render_template_string, request, session, url_for
from markupsafe import escape

from mysql_policy_repository import MySQLPolicyRepository
from policy_matcher import TAG_KEYWORDS, eligible_for_policy
from youth_data_collector import load_local_env


KAKAO_AUTHORIZE_URL = "https://kauth.kakao.com/oauth/authorize"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_USER_URL = "https://kapi.kakao.com/v2/user/me"
DEFAULT_REDIRECT_URI = "http://localhost:5000/auth/kakao/callback"


PAGE = """<!doctype html><html lang='ko'><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
<title>{{ title }} | 목포 청년 정책</title><link rel='stylesheet' href='{{ url_for("static", filename="app.css") }}'>
<body><div class='site-shell'><header class='topbar'><a class='brand' href='/'><span class='brand-mark'>M</span><span>목포 청년 정책</span></a><span class='topbar-note'>나에게 꼭 맞는 지원을 찾는 시간</span></header>
<main>{{ body|safe }}</main><footer>목포 거주 청년을 위한 맞춤 정책 알림 서비스</footer></div></body></html>"""


def page(body: str, title: str = "목포 청년 정책"):
    return render_template_string(PAGE, body=body, title=title)


class KakaoUserRepository:
    def __init__(self) -> None:
        self.repository = MySQLPolicyRepository()

    def _connection(self):
        connection = self.repository.connect()
        self.repository.initialize(connection)
        return connection

    def upsert_kakao_user(self, kakao_user_id: int, nickname: str, email: str | None) -> int:
        connection = self._connection()
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT id FROM user_profiles WHERE kakao_user_id = %s", (kakao_user_id,))
            existing = cursor.fetchone()
            now = datetime.now().replace(microsecond=0)
            if existing:
                cursor.execute(
                    "UPDATE user_profiles SET display_name = %s, email = %s, updated_at = %s WHERE id = %s",
                    (nickname, email, now, existing["id"]),
                )
                user_id = existing["id"]
            else:
                cursor.execute(
                    """INSERT INTO user_profiles
                    (kakao_user_id, display_name, email, residency_city, created_at, updated_at)
                    VALUES (%s, %s, %s, '목포', %s, %s)""",
                    (kakao_user_id, nickname, email, now, now),
                )
                user_id = cursor.lastrowid
            connection.commit()
            return user_id
        finally:
            connection.close()

    def update_profile(self, user_id: int, birth_date: date, interests: list[str]) -> None:
        connection = self._connection()
        try:
            cursor = connection.cursor()
            cursor.execute("UPDATE user_profiles SET birth_date = %s, updated_at = %s WHERE id = %s", (birth_date, datetime.now().replace(microsecond=0), user_id))
            cursor.execute("DELETE FROM user_interests WHERE user_id = %s", (user_id,))
            cursor.executemany("INSERT INTO user_interests (user_id, interest_tag) VALUES (%s, %s)", [(user_id, tag) for tag in sorted(set(interests))])
            connection.commit()
        finally:
            connection.close()

    def user(self, user_id: int):
        connection = self._connection()
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT id, display_name, birth_date FROM user_profiles WHERE id = %s", (user_id,))
            user = cursor.fetchone()
            if user:
                cursor.execute("SELECT interest_tag FROM user_interests WHERE user_id = %s ORDER BY interest_tag", (user_id,))
                user["interests"] = [row["interest_tag"] for row in cursor.fetchall()]
            return user
        finally:
            connection.close()

    def active_policies_for(self, user: dict) -> list[dict]:
        if user["birth_date"] is None:
            return []
        connection = self._connection()
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT * FROM policy_records
                WHERE target_region IN ('목포', '전남(목포 포함)', '전국(목포 포함)')
                  AND (application_end_date IS NULL OR application_end_date >= CURDATE())
                ORDER BY application_end_date IS NULL DESC, application_end_date ASC, updated_at DESC"""
            )
            interests = set(user["interests"])
            return [policy for policy in cursor.fetchall() if eligible_for_policy(user, policy, interests)[0]]
        finally:
            connection.close()

    def pending_notifications_for(self, user_id: int) -> list[dict]:
        connection = self._connection()
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT event.change_type, policy.title, policy.original_link, policy.application_end_date,
                          candidate.match_reason, candidate.created_at
                FROM policy_match_candidates AS candidate
                JOIN policy_records AS policy ON policy.id = candidate.policy_id
                JOIN policy_change_events AS event ON event.id = candidate.event_id
                WHERE candidate.user_id = %s AND candidate.status = 'pending'
                ORDER BY candidate.created_at DESC""",
                (user_id,),
            )
            return cursor.fetchall()
        finally:
            connection.close()


def create_app() -> Flask:
    load_local_env()
    secret_key = os.getenv("FLASK_SECRET_KEY")
    if not secret_key:
        raise RuntimeError("FLASK_SECRET_KEY를 .env에 설정하세요.")
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secret_key,
        KAKAO_REST_API_KEY=os.getenv("KAKAO_REST_API_KEY", ""),
        KAKAO_CLIENT_SECRET=os.getenv("KAKAO_CLIENT_SECRET", ""),
        KAKAO_REDIRECT_URI=os.getenv("KAKAO_REDIRECT_URI", DEFAULT_REDIRECT_URI),
    )
    users = KakaoUserRepository()

    @app.get("/")
    def home():
        user_id = session.get("user_id")
        if not user_id:
            return page("""<section class='hero'><div class='hero-copy'><span class='eyebrow'>MOKPO YOUTH POLICY</span><h1>목포 청년의 오늘에<br><em>딱 맞는 정책</em>을.</h1><p>흩어진 청년 지원 정보를 모으고, 나의 조건에 맞는 공고만 골라 알려드립니다.</p><a class='button button-kakao' href='/auth/kakao'><span class='kakao-dot'>●</span> 카카오로 3초 만에 시작하기</a><p class='helper'>로그인 후 관심 분야와 생년월일을 설정하면 맞춤 추천이 시작됩니다.</p></div><div class='hero-panel'><span class='panel-label'>오늘의 서비스</span><strong>정책 탐색부터<br>새 공고 알림까지</strong><div class='panel-tags'><span>취업</span><span>주거</span><span>창업</span><span>교육</span></div></div></section><section class='feature-grid'><article><span>01</span><h2>맞춤 추천</h2><p>연령, 목포 거주, 관심 분야를 바탕으로 정책을 추립니다.</p></article><article><span>02</span><h2>변경 감지</h2><p>매일 수집해 새 공고와 수정된 내용을 찾아냅니다.</p></article><article><span>03</span><h2>한눈에 확인</h2><p>지원 조건과 마감일, 원문 링크까지 한 화면에서 봅니다.</p></article></section>""")
        user = users.user(user_id)
        if not user:
            session.clear()
            return redirect(url_for("home"))
        interests = ", ".join(user["interests"]) or "관심 분야를 설정해 주세요"
        policy_count = len(users.active_policies_for(user)) if user["birth_date"] else 0
        notification_count = len(users.pending_notifications_for(user_id))
        profile_link = "프로필 설정" if user["birth_date"] is None else "프로필 수정"
        return page(f"""<section class='dashboard-head'><div><span class='eyebrow'>MY POLICY DESK</span><h1>반가워요, <em>{escape(user['display_name'])}</em>님</h1><p>관심 분야 <strong>{escape(interests)}</strong>을 기준으로 정책을 살피고 있어요.</p></div><a class='text-link' href='/profile'>{profile_link} →</a></section><section class='stat-grid'><a class='stat-card' href='/policies'><span>나에게 맞는 정책</span><strong>{policy_count}<small>건</small></strong><p>신청 가능한 공고 보기 →</p></a><a class='stat-card accent' href='/notifications'><span>새 알림</span><strong>{notification_count}<small>건</small></strong><p>신규·변경 공고 확인 →</p></a></section><section class='guide-card'><div><span class='eyebrow'>NEXT STEP</span><h2>정책 정보는 매일 새로워집니다.</h2><p>자동 수집기가 새 공고를 발견하면 관심 분야와 조건을 비교해 이곳에 알려드려요.</p></div><a class='button button-dark' href='/policies'>맞춤 정책 보러가기</a></section><form method='post' action='/logout' class='logout-form'><button>로그아웃</button></form>""")

    @app.get("/auth/kakao")
    def kakao_login():
        if not app.config["KAKAO_REST_API_KEY"]:
            return page("<p>카카오 REST API 키가 설정되지 않았습니다. .env를 확인하세요.</p>"), 503
        state = secrets.token_urlsafe(32)
        session["kakao_oauth_state"] = state
        query = urlencode({"client_id": app.config["KAKAO_REST_API_KEY"], "redirect_uri": app.config["KAKAO_REDIRECT_URI"], "response_type": "code", "state": state})
        return redirect(f"{KAKAO_AUTHORIZE_URL}?{query}")

    @app.get("/auth/kakao/callback")
    def kakao_callback():
        expected_state = session.pop("kakao_oauth_state", "")
        received_state = request.args.get("state", "")
        if not expected_state or not received_state or not hmac.compare_digest(received_state, expected_state):
            return page("<p>로그인 요청 시간이 만료되었거나 이전 로그인 탭에서 돌아왔습니다.</p><p><a href='/auth/kakao'>카카오 로그인 다시 시작</a></p>"), 400
        if request.args.get("error"):
            return page("<p>카카오 로그인이 취소되었거나 실패했습니다.</p><p><a href='/'>처음으로</a></p>"), 400
        code = request.args.get("code")
        if not code:
            abort(400, "인가 코드가 없습니다.")
        data = {"grant_type": "authorization_code", "client_id": app.config["KAKAO_REST_API_KEY"], "redirect_uri": app.config["KAKAO_REDIRECT_URI"], "code": code}
        if app.config["KAKAO_CLIENT_SECRET"]:
            data["client_secret"] = app.config["KAKAO_CLIENT_SECRET"]
        try:
            token_response = requests.post(KAKAO_TOKEN_URL, data=data, timeout=15)
            token_response.raise_for_status()
            access_token = token_response.json()["access_token"]
        except (requests.RequestException, KeyError):
            # Client Secret이 콘솔에서 활성화됐지만 .env에 없을 때도 이 경로로 처리된다.
            return page("<p>카카오 토큰 발급에 실패했습니다. 카카오 콘솔에서 Client Secret 사용 여부를 확인하고, 활성화했다면 <code>KAKAO_CLIENT_SECRET</code>을 .env에 설정하세요.</p><p><a href='/auth/kakao'>카카오 로그인 다시 시작</a></p>"), 502
        try:
            user_response = requests.get(KAKAO_USER_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
            user_response.raise_for_status()
            kakao_user = user_response.json()
            account = kakao_user.get("kakao_account", {})
            profile = account.get("profile") or {}
            user_id = users.upsert_kakao_user(int(kakao_user["id"]), profile.get("nickname") or "카카오 사용자", account.get("email"))
        except (requests.RequestException, KeyError, ValueError):
            return page("<p>카카오 사용자 정보를 처리하지 못했습니다. 카카오 로그인 동의항목과 MySQL 연결 상태를 확인하세요.</p><p><a href='/auth/kakao'>카카오 로그인 다시 시작</a></p>"), 502
        except Exception:
            return page("<p>카카오 계정을 MySQL 프로필로 저장하지 못했습니다. MySQL 서비스와 계정 설정을 확인하세요.</p><p><a href='/auth/kakao'>카카오 로그인 다시 시작</a></p>"), 502
        session["user_id"] = user_id
        return redirect(url_for("profile"))

    @app.route("/profile", methods=["GET", "POST"])
    def profile():
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("home"))
        if request.method == "POST":
            try:
                birth_date = date.fromisoformat(request.form["birth_date"])
            except (KeyError, ValueError):
                return page("<section class='empty-state'><h1>생년월일을 다시 확인해 주세요.</h1><a class='button button-dark' href='/profile'>돌아가기</a></section>"), 400
            interests = [tag for tag in request.form.getlist("interests") if tag in TAG_KEYWORDS]
            if not interests:
                return page("<section class='empty-state'><h1>관심 분야를 하나 이상 선택해 주세요.</h1><a class='button button-dark' href='/profile'>돌아가기</a></section>"), 400
            users.update_profile(user_id, birth_date, interests)
            from policy_matcher import PolicyMatcher

            PolicyMatcher().create_candidates()
            return redirect(url_for("home"))
        user = users.user(user_id)
        if not user:
            session.clear()
            return redirect(url_for("home"))
        checks = "".join(f"<label class='interest-chip'><input type='checkbox' name='interests' value='{tag}' {'checked' if tag in user['interests'] else ''}><span>{tag}</span></label>" for tag in TAG_KEYWORDS)
        birth = user["birth_date"].isoformat() if user["birth_date"] else ""
        return page(f"""<section class='page-heading'><span class='eyebrow'>PERSONALIZE</span><h1>나에게 맞게<br><em>정책 추천을 설정</em>해요.</h1><p>입력한 정보는 맞춤 정책 추천과 알림 후보 생성에만 사용됩니다.</p></section><form method='post' class='profile-card'><label class='field-label'>생년월일<input required type='date' name='birth_date' value='{birth}'></label><div class='residence'><span>거주 지역</span><strong>목포시</strong><small>현재 서비스는 목포 거주 청년을 대상으로 합니다.</small></div><fieldset><legend>관심 분야 <small>복수 선택 가능</small></legend><div class='interest-grid'>{checks}</div></fieldset><button class='button button-dark'>저장하고 맞춤 정책 보기</button></form>""", "프로필 설정")

    @app.get("/policies")
    def policies():
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("home"))
        user = users.user(user_id)
        if not user or user["birth_date"] is None:
            return page("<section class='empty-state'><span class='eyebrow'>POLICY MATCHING</span><h1>먼저 맞춤 조건을 설정해 주세요.</h1><p>생년월일과 관심 분야를 바탕으로 신청 가능한 정책만 보여드려요.</p><a class='button button-dark' href='/profile'>프로필 설정하기</a></section>", "내게 맞는 정책")
        policies = users.active_policies_for(user)
        if not policies:
            return page("<section class='empty-state'><span class='eyebrow'>NO ACTIVE POLICY</span><h1>지금은 새 공고를 기다리고 있어요.</h1><p>매일 자동 수집을 통해 신청 가능한 새 정책을 찾는 즉시 이곳에 보여드릴게요.</p><a class='text-link' href='/'>대시보드로 돌아가기 →</a></section>", "내게 맞는 정책")
        cards = []
        for policy in policies:
            deadline = policy["application_end_date"].isoformat() if policy["application_end_date"] else "상시 또는 별도 확인"
            url = escape(policy["original_link"] or "#")
            cards.append(f"<article class='policy-card'><div class='policy-meta'><span>{escape(policy['category'])}</span><span>마감 {deadline}</span></div><h2>{escape(policy['title'])}</h2><p>{escape((policy['content'] or '')[:250])}</p><a class='text-link' href='{url}' target='_blank' rel='noopener'>공고 원문 보기 ↗</a></article>")
        return page("<section class='list-heading'><span class='eyebrow'>PERSONAL MATCH</span><h1>내게 맞는 정책</h1><p>지금 신청 가능한 정책만 모았습니다.</p></section><div class='policy-list'>" + "".join(cards) + "</div>", "내게 맞는 정책")

    @app.get("/notifications")
    def notifications():
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("home"))
        notifications = users.pending_notifications_for(user_id)
        if not notifications:
            return page("<section class='empty-state'><span class='eyebrow'>ALL CAUGHT UP</span><h1>확인할 새 알림이 없어요.</h1><p>새 정책 또는 변경 공고가 발견되면 여기에 가장 먼저 알려드릴게요.</p><a class='text-link' href='/'>대시보드로 돌아가기 →</a></section>", "새 알림")
        rows = []
        for item in notifications:
            label = "새 공고" if item["change_type"] == "new" else "변경 공고"
            url = escape(item["original_link"] or "#")
            rows.append(f"<article class='notification-card'><span class='notification-label'>{label}</span><h2>{escape(item['title'])}</h2><p>{escape(item['match_reason'])}</p><a class='text-link' href='{url}' target='_blank' rel='noopener'>공고 원문 보기 ↗</a></article>")
        return page("<section class='list-heading'><span class='eyebrow'>POLICY UPDATES</span><h1>새 알림</h1><p>나의 조건과 관심사에 맞춘 신규·변경 공고입니다.</p></section><div class='notification-list'>" + "".join(rows) + "</div>", "새 알림")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("home"))

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="카카오 로그인 로컬 개발 서버")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    create_app().run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
