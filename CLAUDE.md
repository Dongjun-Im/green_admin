# 초록등대 회원관리 (green_admin)

초록등대 동호회 **관리자 전용** 회원관리 프로그램 v0.3.

## 주요 기능
- 우수회원 명단 자동 백업 (3개월 주기, TXT + XLSX)
- "우리들의 이야기" 게시판 글 수 기반 자동 승급
- 장기(6개월) 미접속 회원 등급 조정 (미리보기 → 적용)
- 수동 메일 발송 (rtgreen 계정 전용)
- 회원 검색/조회
- 자동 스케줄 (1/4/7/10월 1일 이후 실행 시)

## 진입점·흐름
`main.py` → wx.App → green_auth 로그인 → `core/permission.admin_permission_check`
(동호회관리자 등급 9만 허용) → `ui/main_frame.MainFrame` → `run_scheduled_tasks_if_due`

## 모듈 구조
- `core/`: 크롤러, 파서, 서비스
  - `crawler.py`, `member_parser.py`, `member_admin.py`
  - `backup_service.py`: 우수회원 백업
  - `promotion_service.py`, `post_counter.py`, `post_count_green3.py`: 자동 승급
  - `level_adjustment.py`: 장기 미접속 조정
  - `mail_sender.py`: 메일 발송
  - `schedule_tracker.py`, `log_writer.py`, `permission.py`, `models.py`
- `ui/`: wxPython UI
  - `main_frame.py`, `search_dialog.py`, `mail_dialog.py`,
    `confirm_dialog.py`, `help_dialog.py`, `item_text_ctrl.py`
- `tools/`: 개발자용 덤프·탐색 스크립트
- `green_auth/`: 공용 인증 패키지 **번들 복사본** (원본 리포는 별도)

## 등급 체계 (소리샘 admin.member.php 실제 값)
0 손님 / 1 탈퇴 / 2 거부 / 3 대기 / 4 신청 / 5 일반 / 6 우수 / 7 최우수 / 8 명예 / 9 동호회관리자

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

## green_auth 동기화 주의
이 리포의 `green_auth/` 하위는 `\\mac\Home\Downloads\My program\green_auth`
리포의 복사본이다. 원본이 업데이트되면 이쪽 복사본도 수동으로 맞춰야 한다.
