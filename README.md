# 청년 지원 정보 수집기

서비스 사용자는 **목포 거주 청년**입니다. 데이터는 목포 정책뿐 아니라 목포 거주자가 신청할 수 있는 전라남도·전국 단위 청년 지원·콘텐츠까지 수집해 JSON과 CSV로 저장합니다. 정책의 게시 기관이 다른 지역이라도 목포 거주자가 신청할 수 있는 경우에만 서비스 데이터로 남깁니다.

- 온통청년: 청년 꿀팁, 청년 정책 채널
- 온통청년: 공식 청년정책 API
- 청년인재DB: 청년 인턴 채용
- 목포청년센터 누리: 청년 취업·창업 사업, 청년 자립 지원
- 목포시청: 청년 지원사업
- 전남광주청년통합플랫폼: 청년정책, 일자리·주거·교육·복지 드림

기본 실행은 API를 제외한 모든 크롤링 대상의 첫 목록 페이지입니다. `목포`, `전남(목포 포함)`, `전국(목포 포함)`으로 판정된 항목만 저장하며, 목록·상세 요청 사이에는 서버 보호를 위해 기본 5초를 대기합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\playwright install chromium
.\.venv\Scripts\python youth_data_collector.py --max-pages 1
```

기본 실행 대상은 `youth_tip`, `youth_channel`, `youth_intern`, `gwangju_policy`, `gwangju_job`, `gwangju_reside`, `gwangju_education`, `gwangju_welfare`입니다. API 서버 문제가 해결될 때까지 `youth_policy_api`는 명시적으로 지정했을 때만 실행됩니다.

## 첨부파일 수집

기본 수집은 첨부파일 **링크만** 기록합니다. 실제 파일까지 수집하려면 `--download-attachments`를 지정합니다. PDF는 텍스트를 추출하고, 공개 표준 형식인 HWPX는 내부 XML에서 본문을 추출합니다. 구형 HWP는 파일을 보관하며 `attachment_status`에 미지원 상태를 남깁니다. 이미지형 PDF의 OCR은 다음 단계에서 추가할 수 있습니다.

아래 명령은 목포시청 게시판 첫 페이지에서 정책 1건만 시험 수집하며, 요청 간격을 5초로 유지합니다.

```powershell
.\.venv\Scripts\python youth_data_collector.py --targets mokpo_city_youth_support --max-pages 1 --max-items 1 --delay 5 --download-attachments
```

첨부 원본은 `data/attachments/`에, 추출된 내용과 처리 결과는 수집 JSON·CSV의 `attachment_files`, `attachment_text`, `attachment_status` 컬럼에 저장됩니다. 정책별 처리 파일 수는 기본 3건이며, `--max-attachments-per-policy`로 조정할 수 있습니다.

## 정제·구조화

수집기에서 페이지 본문과 첨부파일 텍스트를 함께 분석해 `qualification_text`(지원 자격 요약), `min_age`, `max_age`, `residency_condition`, `application_start_date`, `application_end_date`를 생성합니다. 날짜는 `YYYY-MM-DD` 형식으로 저장하며, `content_hash`는 다음 단계의 변경 공고 감지에 사용합니다.

## MySQL 저장·변경 감지

실제 저장을 하려면 **MySQL Server 8.0 이상**을 먼저 설치하고 실행해야 합니다. 설치 뒤 `.env`에 `MYSQL_PASSWORD`를 설정한 다음 아래처럼 실행합니다. 처음 실행하면 `youth_policy` 데이터베이스와 테이블을 자동으로 만듭니다.

```powershell
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python youth_data_collector.py --targets mokpo_city_youth_support --max-pages 1 --delay 5 --mysql
```

`policy_records`는 원문 URL 등을 바탕으로 정책을 식별하고, `content_hash`가 바뀐 경우에만 갱신합니다. `policy_change_events`에는 `new` 또는 `updated` 이벤트만 기록되므로 이후 관심 정책 알림에 바로 사용할 수 있습니다. 상세 테이블 설계는 `schema_mysql.sql`에 있습니다.

## 매일 자동 수집

APScheduler를 사용해 매일 자동 수집할 수 있습니다. 아래 명령은 매일 오전 3시(한국 시간)에 기본 수집 대상, 첨부파일, MySQL 동기화를 실행합니다.

```powershell
.\.venv\Scripts\python scheduled_collector.py
```

실행 시각을 바꾸려면 다음처럼 지정합니다.

```powershell
.\.venv\Scripts\python scheduled_collector.py --hour 9 --minute 0
```

APScheduler는 이 명령이 실행 중인 동안만 동작합니다. PC를 재시작하거나 창을 닫아도 매일 실행하려면, 다음 단계에서 Windows 작업 스케줄러에 이 명령을 등록하면 됩니다.

Windows 작업 스케줄러에 등록된 작업은 `scheduled_collector.py --once`를 호출해 수집을 한 번 실행한 뒤 종료합니다. 따라서 작업 스케줄러 방식에서는 별도의 터미널을 계속 열어둘 필요가 없습니다.

## 맞춤 정책 매칭 후보

사용자 프로필은 생년월일, 목포 거주 여부, 관심 분야만 저장합니다. 아래 명령으로 프로필을 추가한 뒤, 새 공고·변경 공고에 대한 알림 후보를 생성할 수 있습니다. 실제 이름이나 생년월일은 채팅에 공유하지 말고 로컬 터미널에서만 입력하세요.

```powershell
.\.venv\Scripts\python policy_matcher.py add-user --name 표시이름 --birth-date 2000-01-01 --interests 취업 주거
.\.venv\Scripts\python policy_matcher.py run
.\.venv\Scripts\python policy_matcher.py list
```

관심 분야는 `취업`, `창업`, `주거`, `교육`, `복지`, `문화`입니다. 자동 수집이 성공하면 후보 생성도 함께 실행되며, 후보는 `policy_match_candidates` 테이블에 `pending` 상태로 저장됩니다. 이후 실제 알림 채널을 연결하면 이 목록의 항목만 전송하면 됩니다.

## 웹 서비스 실행 (React + Vite / FastAPI)

화면은 React + Vite, API·카카오 로그인 처리는 FastAPI로 분리했습니다. 터미널 두 개에서 아래 명령을 각각 실행한 뒤 브라우저에서 `http://localhost:5173`을 여세요.

```powershell
# 1. API 서버
.\.venv\Scripts\python -m uvicorn backend.main:app --host 127.0.0.1 --port 5000
```

```powershell
# 2. React 화면 서버
& "C:\Users\karsy\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd" --dir frontend dev --host 127.0.0.1
```

개발 화면에서는 `추천 정책`, `알림`, `프로필` 메뉴와 카카오 로그인 흐름을 사용할 수 있습니다. 기존 `kakao_login_server.py`는 초기 Flask 시제품으로 남겨 두었으며, 앞으로는 FastAPI 서버를 실행합니다.

## 카카오 로그인 (로컬 개발)

개발 중에는 로컬 서버를 사용합니다. 카카오 개발자 콘솔에서 앱을 만든 뒤 **카카오 로그인 사용 설정**을 켜고, 다음 Redirect URI를 등록하세요.

```text
http://localhost:5000/auth/kakao/callback
```

콘솔의 **REST API 키**를 `.env`의 `KAKAO_REST_API_KEY`에, 충분히 긴 임의 문자열을 `FLASK_SECRET_KEY`에 저장합니다. 카카오 로그인용 Client Secret을 켠 경우에만 `KAKAO_CLIENT_SECRET`도 설정합니다. Redirect URI는 인가 요청 값과 콘솔 등록 값이 정확히 같아야 합니다.

브라우저에서 React 화면인 `http://localhost:5173`을 열어 카카오 로그인과 관심 분야 설정을 진행합니다. 카카오 인증 후에는 FastAPI의 `http://localhost:5000/auth/kakao/callback`으로 돌아온 뒤 화면으로 복귀합니다. 운영 환경으로 배포할 때는 localhost가 아닌 HTTPS 도메인의 Redirect URI를 카카오 콘솔에 별도로 등록해야 합니다.

청년정책 API 키는 `.env`에만 저장합니다. 이미 키를 받았다면 `.env.example`을 참고해 아래처럼 설정하세요. `.env`는 Git에서 제외됩니다.

```text
YOUTHCENTER_POLICY_API_KEY=발급받은-키
```

공식 API만 실행하고 목포 정책을 검색하려면:

```powershell
.\.venv\Scripts\python youth_data_collector.py --targets youth_policy_api --api-query 목포 --max-pages 1 --include-review
```

특정 대상만 실행하려면 다음처럼 키를 전달합니다.

```powershell
.\.venv\Scripts\python youth_data_collector.py --targets youth_tip youth_intern gwangju_job --max-pages 1 --delay 3
```

수집 결과는 `data/collected/`에 시간별 JSON·CSV 파일로 저장됩니다. `target_region`이 `확인필요`인 항목도 검토용으로 함께 보관하려면 `--include-review`를 추가합니다.

## 저장 컬럼

`source_site`, `category`, `title`, `target_region`, `target_condition`, `qualification_text`, `min_age`, `max_age`, `residency_condition`, `period`, `application_start_date`, `application_end_date`, `content`, `application_method`, `organization`, `attachment_links`, `attachment_files`, `attachment_text`, `attachment_status`, `content_hash`, `original_link`, `source_record_id`, `collected_at`

로그인이나 접근 제한을 우회하지 않으며, 사이트 구조가 바뀌어 목록을 찾지 못할 경우 해당 대상 수집을 중단하고 오류를 표시합니다.
