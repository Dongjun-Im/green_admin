# 초록등대 회원관리 (green_admin)

초록등대 동호회 **관리자 전용** 회원관리 프로그램 v1.0 (안정 버전).

## 주요 기능
- 우수회원 명단 자동 백업 (3개월 주기, TXT + XLSX)
- 활동점수(글+댓글) 기반 자동 승급 — green3 + green9 합산 (v0.5)
- 분기 MVP TOP 10 자동 분석 (1/4/7/10월 도래, v0.5)
- 장기(6개월) 미접속 회원 등급 조정 (미리보기 → 적용)
- 회원 개별 승급/강등 (검색 → Ctrl+G)
- 수동 메일 발송 (rtgreen 계정 전용, 항상 개별 발송 — v0.5)
- 회원 검색/조회
- 자동 스케줄 (1/4/7/10월 1일 이후 실행 시)

## 진입점·흐름
`main.py` → wx.App → green_auth 로그인 → `core/permission.admin_permission_check`
(관리자 페이지 접근권 — `cl_level` 셀렉트 노출 여부로 판정) → `ui/main_frame.MainFrame` → `run_scheduled_tasks_if_due`

## 모듈 구조
- `core/`: 크롤러, 파서, 서비스
  - `crawler.py`, `member_parser.py`, `member_admin.py`
  - `backup_service.py`: 우수회원 백업
  - `promotion_service.py`, `post_counter.py`, `post_count_green3.py`: 자동 승급
  - `activity_counter.py` (v0.5): 글+댓글 통합 카운터 (여러 게시판 지원)
  - `mvp_service.py` (v0.5): 분기 MVP TOP N 산정
  - `pending_members.py` (v0.5): 가입 신청·대기 회원 식별
  - `withdrawn_blocklist.py` (v1.0.x): 장기미접속으로 '탈퇴'(WITHDRAW_LEVEL)
    처리된 회원 아이디 명단. 재가입(대기 등급) 으로 다시 나타나면 승인 화면에서
    '승인' 버튼이 막힌다. `data/inactivity_withdrawn.json` 에 보관.
  - `level_history.py` (v1.0): 영구 등급 변경 이력 SQLite
  - `site_diagnostics.py` (v1.0): 사이트 구조 변경 감지·진단
  - `update_check.py` (v1.0): GitHub Releases 자동 업데이트 확인
  - `level_adjustment.py`: 장기 미접속 조정
  - `mail_sender.py`: 메일 발송
  - `schedule_tracker.py`, `log_writer.py`, `permission.py`, `models.py`
  - `log_reader.py` (v0.4): operation_*.log 파싱 (대시보드/뷰어용)
  - `backup_diff.py` (v0.4): 두 분기 백업 비교
  - `backup_retention.py` (v0.4): 12개월 이상 백업 zip 압축
  - `undo_stack.py` (v0.4): 등급 변경 작업 LIFO 스택 (Ctrl+Z)
  - `member_notes.py` (v0.4): 로컬 SQLite 회원 메모/태그
  - `html_report.py` (v0.4): 분기 운영보고 HTML 생성
  - `keybindings.py` (v0.4): 사용자 정의 단축키 로드/저장
- `ui/`: wxPython UI
  - `main_frame.py`, `search_dialog.py`, `mail_dialog.py`,
    `confirm_dialog.py`, `help_dialog.py`, `item_text_ctrl.py`
  - `level_change_dialog.py` (v0.4): 회원 단건 등급 변경
  - `confirm_promotion_dialog.py` (v0.4): 자동 승급 미리보기
  - `stats_dialog.py` (v0.4): 회원 통계 대시보드
  - `backup_diff_dialog.py` (v0.4): 백업 비교
  - `log_viewer_dialog.py` (v0.4): 작업 로그 뷰어
  - `member_note_dialog.py` (v0.4): 회원 메모/태그 편집
  - `promotion_imminent_dialog.py` (v0.4): 승급 임박자 분석
  - `mvp_dialog.py` (v0.5): MVP TOP N 결과 표시
  - `pending_member_dialog.py` (v0.5): 가입자 승인 워크플로
  - `level_history_dialog.py` (v1.0): 영구 등급 변경 이력 뷰어
- `tools/`: 개발자용 덤프·탐색 스크립트
- `green_auth/`: 공용 인증 패키지 **번들 복사본** (원본 리포는 별도)

## 데이터 파일 (모두 .gitignore)
- `data/credentials.ini` — 자격증명
- `data/last_run.json` — 백업·조정 도래 추적
- `data/undo_stack.json` (v0.4) — 등급 변경 Undo 스택 (최근 10개)
- `data/member_notes.db` (v0.4) — 회원 메모/태그 SQLite
- `data/keybindings.json` (v0.4) — 사용자 정의 단축키
- `data/pending_seen.json` (v0.5) — 신규 가입자 "본 적 있음" 기록
- `data/inactivity_withdrawn.json` (v1.0.x) — 장기미접속 '탈퇴' 처리자 명단 (재가입 차단)
- `data/level_history.db` (v1.0) — 영구 등급 변경 이력 SQLite
- `data/last_update_check.json` (v1.0) — GitHub 업데이트 확인 캐시
- `data/dumps/`, `data/logs/`
- `backups/YYYY-MM-DD/` — 분기 백업
- `backups/archives/` (v0.4) — 오래된 백업 zip 보관소
- `backups/mvp_top10_YYYY-Q.txt` (v0.5) — 분기 MVP 리포트

## 등급 체계 (소리샘 admin.member.php cl_level select 실제 값)
0 손님 / 1 탈퇴 / 2 거부 / 3 대기 / 4 준회원 / 5 일반회원 / 6 우수회원 / 7 최우수회원 / 8 명예회원 / 9 동호회관리자
(권위 있는 매핑은 [config.py](config.py) `LEVEL_LABELS` / `LEVEL_TEXT_MAP` — 사이트 폼 옵션을 그대로 따른다.)

동호회관리자 = 사이트 `cl_level=9`. 권한 체크 자체는 등급 번호가 아니라
admin.member.php 페이지 접근 가능 여부로 판정 (core/permission.py).

## Git 워크플로우
원격: https://github.com/Dongjun-Im/green_admin.git (private)
main 브랜치에서 직접 작업.

사용자가 커밋·푸시 요청 시:
1. `git status` 변경 확인
2. `git diff` 검토 — **민감 파일이 실수로 스테이지되지 않았는지 반드시 확인**
3. `git add -A`
4. `git commit -m "<메시지>"`
5. `git push`

## 민감 파일 (절대 커밋 금지 — `.gitignore`에 등록됨)
- `data/dumps/`: 관리자 페이지 HTML 덤프 (회원 전체 개인정보 포함)
- `data/logs/`: 작업 로그 (회원 식별 정보)
- `data/last_run.json`: 런타임 상태
- `data/credentials.ini`: 자격증명
- `backups/`: 우수회원 명단 및 조정 대상자 계획 (개인정보)
- `build/`, `dist/`, `__pycache__/`, `.venv*/`

커밋 전 diff에 위 경로가 보이면 **즉시 중단하고 사용자에게 경고**.

## 테스트 (v1.0 신규)
- `tests/` — pytest 단위 테스트. 핵심 로직 회귀 보호용.
- 실행: `pytest`
- 픽스처는 `tempfile` 로 격리 — 사용자 `data/` 를 절대 건드리지 않음.
- 새 기능 추가 시 같은 폴더에 `test_<name>.py` 추가.

## 빌드 / 릴리스
- 빌드 의존성: `requirements-dev.txt` (pytest + pyinstaller). 런타임 의존성: `requirements.txt`
  (wxPython, requests, bs4, lxml, cryptography, pywin32, openpyxl, dateutil, msoffcrypto-tool,
  google-api-python-client, google-auth-oauthlib, **curl_cffi**).
- onedir 빌드: `py -3.12 -m PyInstaller --noconfirm chorok_green_admin.spec`
  → `dist/초록등대회원관리/초록등대회원관리.exe` (`_internal/` 폴더와 함께 통째로 배포).
  EXE 의 버전 리소스는 `version_info.txt` 에서 읽음 — 버전업 시 `config.py:APP_VERSION` 과
  `version_info.txt` 의 filevers/FileVersion/ProductVersion 을 함께 수정.
- **릴리스 두 가지 형태** (greenmulti 와 동일 방식):
  1. **무설치(포터블)** — `dist/초록등대회원관리/` 폴더를 ZIP 으로 묶은 것.
     사용자는 압축 풀고 `초록등대회원관리.exe` 실행.
  2. **설치 버전** — `installer.iss` 를 Inno Setup 6 의 `ISCC.exe` 로 컴파일한 `..._setup.exe`.
     시작 메뉴·바탕화면 바로가기 생성, 제어판 프로그램 목록에 등록(제거 가능).
  - 한 방에: `py -3.12 build_release.py` → PyInstaller 빌드 + `release/..._portable.zip` +
    (ISCC 가 PATH 나 `C:\Program Files (x86)\Inno Setup 6\` 에 있으면) `installer_out/..._setup.exe`.
  - `release/`, `installer_out/`, `*_portable.zip`, `*_setup.exe`, `build/`, `dist/` 는
    `.gitignore` — 산출물은 커밋하지 않고 GitHub Releases 에만 올린다.

## green_auth 동기화 주의
이 리포의 `green_auth/` 하위는 `\\mac\Home\Downloads\My program\green_auth`
리포의 복사본이다. 원본이 업데이트되면 이쪽 복사본도 수동으로 맞춰야 한다.
