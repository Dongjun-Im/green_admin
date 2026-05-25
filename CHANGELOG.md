# CHANGELOG

## v1.3.3 (2026-05-19)

### 수정 — 대시보드 '?' 가 회원 목록 받은 뒤에도 안 갱신되던 버그
- 메인 화면 대시보드의 '신규 가입 대기' / '장기미접속 후보' 가 회원 캐시
  (`_cached_members`) 에 의존하는데, Ctrl+F 등으로 회원 목록을 받아도 캐시는
  채워졌지만 대시보드 갱신 함수는 안 불려 '?' 표시가 계속 남았음.
- `MainFrame._set_cached_members(members)` 헬퍼 신규 — 캐시 갱신 + 워커
  스레드에서도 안전한 `wx.CallAfter(self._refresh_dashboard)` 호출을 묶음.
- 캐시를 갱신하는 5개 지점 (검색·자동 승급·신규 가입자 승인·MVP·HTML 리포트)
  모두 헬퍼를 쓰도록 교체. 검색 다이얼로그 닫을 때도 추가 갱신 — 그 안에서
  등급·탈퇴 처리가 일어났을 수 있어 대시보드 숫자가 바뀔 수 있음.


## v1.3.2 (2026-05-19)

### 수정 — 자동 스케줄러 관리 화면이 한국어 Windows 에서 등록 상태를 못 잡던 버그
- v1.3.1 의 스케줄러 관리 다이얼로그는 작업을 등록해도 목록에 `.` (미등록) 으로
  계속 보이는 문제가 있었음. 원인: `schtasks /Query /FO LIST /V` 의 전체 출력을
  파싱했는데, 'TaskName' / 'Next Run Time' / 'Last Result' 영문 필드명만 찾고
  한국어 Windows 의 '작업 이름' / '다음 실행 시간' / '마지막 결과' 표기를
  못 잡아 등록된 작업도 미등록으로 표시됐음.
- 수정 — 작업당 `schtasks /Query /TN ChorokGreenAdmin_<key>` 로 개별 조회:
  · 종료 코드 0 → 등록됨 (필드에서 다음 실행 시각·마지막 결과 추출)
  · 종료 코드 ≠0 → 미등록 (한국어/영문 메시지 모두 무시)
  필드명 별칭(다음 실행 시간 / 마지막 결과 + Next Run Time / Last Result) 모두 인식.
- 부수 — 전각 콜론(:) 도 split 대상에 포함 (한국어 IME 가 섞어 넣는 경우 대비).

### 테스트
- `tests/test_scheduler_setup.py` 13개: 한국어/영문 schtasks 출력 파싱, 전각
  콜론 처리, _query_one 등록/미등록/OSError 분기, query_status 가 작업당 한
  번씩만 호출, register/unregister 의 schtasks 인자 구성(MONTHLY 는 /D, DAILY
  는 /D 없음, 미지원 키 거부, schtasks 실패 메시지 전파), DEFAULT_SCHEDULES
  와 scheduler_runner.ALL_TASKS 일관성 회귀.
- 총 pytest 585/585 통과.


## v1.3.1 (2026-05-19)

### 추가 — 자동 스케줄러 관리 GUI
- 작업 메뉴 → '자동 스케줄러 관리(&Y)...' 항목 신규. 네 가지 자동 작업
  (활동 안내·장기미접속 경고·구독 만료 7/3일 알림) 의 등록 상태를 한 화면에서
  보고 등록·해제·새로고침 가능. 이제 PowerShell 명령 없이도 GUI 로 설정.
- 다이얼로그: 줄 맨 앞에 `[V]` 등록됨 / `[ . ]` 미등록 마커 + 다음 실행 시각
  + 마지막 결과 코드를 상세 패널에 표시. 선택 등록 / 선택 해제 / 모두 등록 /
  모두 해제 / 새로고침 / 닫기 6개 버튼.

### 코드 구조
- `core/scheduler_setup.py` 신규 — schtasks.exe 래퍼 (register_task /
  unregister_task / query_status / TaskStatus / DEFAULT_SCHEDULES). CLI(tools/)
  와 UI(ui/) 가 모두 같은 함수를 호출.
- `tools/register_scheduler.py` — 비즈니스 로직을 core/ 로 이전. argparse +
  출력 포맷팅만 담당하는 얇은 CLI 래퍼로 정리.
- `ui/scheduler_dialog.py` 신규 — SchedulerDialog.
- `ui/main_frame.py` — ID_SCHEDULER_SETUP + 메뉴 항목 + on_scheduler_setup 핸들러.

### 매뉴얼
- 신규 챕터 '자동 스케줄러 (정해진 시각에 자동 실행)' — 무엇을 자동으로 하는지,
  기본 권장 시각, GUI/CLI 등록 방법, 전제(rtgreen·2FA·PC 켜짐), 로그 파일
  위치, 해제·재등록 절차. '프로그램 업데이트' 와 '단축키 한눈에 보기' 사이.

### 빌드
- `chorok_green_admin.spec` hiddenimports 에 core.scheduler_setup /
  ui.scheduler_dialog 추가.

### 테스트 (11 신규, 총 pytest 583/583 통과)
- register_task: 미지원 작업 키 거부 / schtasks 인자 구성 (TN, Create, /D
  modifier for MONTHLY, no /D for DAILY) / schtasks 실패 시 메시지 전파.
- unregister_task: /Delete /TN ChorokGreenAdmin_<key>.
- query_status: schtasks 샘플 출력 파싱 — 등록·미등록 작업 분류, 다음 실행·
  마지막 결과 추출, 무관한 작업 무시, schtasks 실패 폴백.
- DEFAULT_SCHEDULES 키 ↔ scheduler_runner.ALL_TASKS 일관성 (회귀 보호).


## v1.3.0 (2026-05-19)

자동화·요약 라운드 — 운영자가 컴퓨터를 켜고 메뉴를 누르지 않아도 정기 업무가
돌아가도록.

### 추가 — 운영자 대시보드 (메인 화면 상단)
- 타이틀 바로 아래 "오늘 해야 할 일(&O):" 박스 — 도래·임박 작업(백업/조정/MVP),
  신규 가입 대기 회원 수, 장기미접속 후보 예상 수가 한눈에. `_refresh_status` 가
  호출될 때마다 자동 갱신. 회원 목록 캐시가 없으면 'Ctrl+F 한 번 열면 채워집니다'
  안내.
- `core/dashboard_summary.py:build_dashboard_lines` 순수 함수 — wx 의존 없이
  텍스트 라인 리스트 반환. tracker 메서드가 던져도 대시보드 자체는 안 죽음.

### 추가 — 자료실 구독 만료 조기 알림 메일
- 작업 메뉴 → '자료실 구독 만료 알림 — 7일 전(&7)' / '— 3일 전(&3)': 정확히
  N일 후 구독이 만료될 회원을 자동으로 찾아 한 번에 안내 메일 발송.
- 같은 만료일에 같은 종류의 메일은 한 번만 (재구독 시 새 만료일에는 다시 발송).
- `core/expiry_reminder.py` + `ui/expiry_reminder_dialog.py` 신규.
- `NudgeHistoryStore.was_sent_for(user_id, kind, target_date)` 정확 매칭 메서드 추가.

### 추가 — 자동 스케줄러 (Windows 작업 스케줄러 연동)
- `초록등대회원관리.exe --task <name>` 헤드리스 모드 — UI 없이 한 작업만
  수행하고 종료. 지원 작업: `activity_nudge` / `inactive_warning` /
  `expiry_remind_7` / `expiry_remind_3`.
- `core/scheduler_runner.py:run_task(name)` — green_auth 저장 자격증명 자동
  로그인 → 권한 체크 → 작업 디스패치 → `data/logs/scheduler_<task>_YYYYMM.log`
  기록. 메일 작업은 'rtgreen' 아이디일 때만 실제 발송 (MailSender 안전 장치).
- `tools/register_scheduler.py` 신규 — `schtasks.exe` 로 작업 등록/해제/조회.
  네 작업의 권장 시각: 활동/장기미접속 월 1회, 구독 만료 매일 09:00.

### 수정 — 메인 화면 버튼 클릭이 안 잡히던 버그
- `_build_menu` 의 `self.Bind(wx.EVT_MENU, ..., id=ID_BACKUP_NOW)` 등은 메뉴
  항목에만 적용된다. 같은 ID 를 가진 panel 의 `wx.Button` 을 클릭하면
  `EVT_BUTTON` 이벤트가 발생하지만 핸들러가 없어 무시됐음.
- `_build_ui` 끝에서 같은 핸들러를 `EVT_BUTTON` 으로도 바인딩 — 메뉴·단축키·
  버튼 모두 작동. 우수회원 백업/장기미접속 조정/마지막 작업 정보 3개 버튼.

### 매뉴얼
- `ui/manual_dialog.py` + 동봉 매뉴얼 TXT — '메일 보내기' 챕터에 구독 만료
  알림 + 자동 스케줄러 등록 방법 단락 추가.

### 빌드
- `chorok_green_admin.spec` hiddenimports 에 `core.dashboard_summary` /
  `core.expiry_reminder` / `core.scheduler_runner` / `ui.expiry_reminder_dialog`
  추가.

### 테스트 (총 pytest 572/572 통과, 신규 32)
- `tests/test_dashboard_summary.py` 10: 도래/임박/없음, pending None/0/양수,
  본인·관리자 제외, tracker 예외 안전.
- `tests/test_expiry_reminder.py` 13: 정확히 N일 후 만료자, 본인·관리자 제외,
  history 중복 차단(같은 period_to 는 한 번만, 다른 period_to 는 재발송),
  payment_store 예외 스킵, 템플릿 7/3 차이, was_sent_for 매칭.
- `tests/test_scheduler_runner.py` 9: 미지원 작업 키, 대상 0명 시 정상 success,
  발송+history 기록, MailSender disabled 시 skipped 집계, log_event 기록,
  ALL_TASKS 일관성, main._parse_args 분기.


## v1.2.10 (2026-05-17)

장기미접속 판정에 1년 안전 상한 추가 + 안내 메일 두 종류(자동 추림) + 미리보기 표시 강화.

### 추가 — 안내 메일 자동 추림·발송 (rtgreen 전용)
- 작업 메뉴 → '활동 안내 메일 (green3 6개월 글 없음)': '우리들의 이야기' 게시판에
  최근 6개월간 글이 0건인 회원을 자동으로 찾아 한 번에 안내 메일 발송. 댓글은
  카운트하지 않음 (사용자 요청대로 '게시물' 기준).
- 작업 메뉴 → '장기미접속 사전 경고 메일 (1년+ 미접속)': 마지막 접속이 1년 넘게
  지난 회원에게 '다음 정리 작업에서 등급이 조정될 수 있다' 사전 경고.
- 두 메일 모두 한 번 보낸 회원은 30일 이내 자동 제외 (`data/nudge_history.json`).
- 대상 회원 미리보기 + 본문 미리보기 + 스페이스 체크 해제로 일부만 보내기 가능.
- 발송 진행률 게이지 + 상승 비프음. 발송은 회원별 개별 모드(수신자 ID 비노출).
- 신규 모듈: `core/nudge_history.py`, `core/nudge_mail.py`, `ui/nudge_dialog.py`.
- 기존 인프라 그대로 재사용: `MailSender` (rtgreen 전용 발송) + `ActivityCounter`
  (`fetch_post_count` 공개 헬퍼 신규 추가) + `ProgressTaskDialog`.

### 변경 — 장기미접속 판정에 1년 안전 상한
- 6개월 미접속이지만 green3 게시판 글 3건·댓글 3건 이상 남긴 회원은 '접속자'로
  인정해 면제하던 v1.2.7 규칙에 안전 상한 추가: **1년 이상 미접속이면 활동량과
  무관하게 등급 조정 대상**. (게시판으로만 활동하고 사이트 자체에는 1년 넘게
  안 들어오는 계정은 '관리되지 않는 계정' 으로 봄.)
- `config.py:INACTIVITY_MONTHS_HARD = 12` 추가.
- `core/level_adjustment.py:LevelAdjustmentService` — `hard_cutoff_provider` 생성자
  인자 추가. 후보가 1년+ 미접속이면 사유에 "12개월 초과 — 활동량 무관" 명시.

### 추가 — 장기미접속 미리보기에 활동 정보
- `AdjustmentItem` 에 `green3_posts` / `green3_comments` 필드 (Optional[int]).
  미리보기 목록 한 줄 끝에 "마지막접속 YYYY-MM-DD / 글 N건 / 댓글 M건" 표시.
- `ui/confirm_dialog.py` 상세 패널에 "green3 활동: 글 N건 / 댓글 M건" 줄 추가.

### 추가 — 회원 검색(Ctrl+F)에 게시판 활동량
- 결과 목록 한 줄에 마지막 접속일이 같이 보임 ("접속 YYYY-MM-DD").
- 'F5' 또는 '활동량 불러오기' 버튼: green3 + green7 + green9 글·댓글 수를 한
  번에 받아옴 ("G3 글5 댓12 G7 글0 댓3 G9 글2 댓0" 짧은 요약 + 상세 패널에
  게시판 한 줄씩). 다이얼로그 열려 있는 동안 캐시, 닫으면 비워짐.
- `config.py:SERIES_BOARD = "green7"` + `SEARCH_DIALOG_BOARDS` 튜플.

### 매뉴얼
- `ui/manual_dialog.py` + 동봉 매뉴얼 TXT — '회원 찾기' / '오래 안 들어온 회원
  등급 조정' / '메일 보내기' 챕터 업데이트. 6~12개월 / 1년+ 두 분기 판정 기준,
  F5 활동량 불러오기, 안내 메일 두 종류 사용법 명시.

### 테스트
- 신규 31개 (level_adjustment 6 + search_dialog_activity 6 + 모델 5 + nudge_history 9
  + nudge_mail 11). 기존 화면 회귀 12개 보정. 총 pytest **540/540 통과**.


## v1.2.9 (2026-05-17)

자동 업데이트의 '지금 설치' 흐름을 **클릭 없이** 진행되도록 무인(silent) 설치로 전환.

### 변경 — 무인 자동 설치
- 새 버전 알림에서 '지금 받기' → 다운로드 진행률 → "지금 설치하고 재시작" 클릭
  한 번이면 설치관리자가 자동으로 진행되고, 끝나면 새 버전이 자동으로 켜집니다.
  설치 마법사의 "Next, Next, Install" 클릭 단계가 모두 사라졌습니다.
- `ui/main_frame.py:on_install_update` — `subprocess.Popen` 으로 설치관리자를
  실행할 때 `/SP- /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS
  /RESTARTAPPLICATIONS` 플래그를 전달. DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP
  로 부모-자식 관계 분리.
- `installer.iss` — `[Run]` 항목에서 `skipifsilent` 플래그 제거. /VERYSILENT
  모드에서도 새 EXE 가 자동으로 실행되도록.

### 매뉴얼
- `ui/manual_dialog.py` + 동봉 매뉴얼 TXT — '프로그램 업데이트' 챕터에
  "설치는 자동으로 진행됨" 안내 추가.

### 운영자 안내
- 본 변경은 **v1.2.9 부터** 적용됨. v1.2.7 이하 사용자는 이번 업데이트에서는
  설치 마법사가 한 번 더 보임. v1.2.9 가 깔린 뒤 다음 업데이트부터 무인 설치 동작.


## v1.2.8 (2026-05-17)

회원 검색(Ctrl+F) 다이얼로그에 게시판 활동량 + 마지막 접속일 표시 추가.

### 추가 — 회원 검색에 green3·green7·green9 글·댓글 보기
- 결과 목록 한 줄에 마지막 접속일이 같이 보입니다 ("접속 YYYY-MM-DD").
- 'F5' 또는 '활동량 불러오기' 버튼을 누르면, 지금 필터에 보이는 회원들의 우리들의
  이야기(green3) / 시리즈·정보(green7) / 질문게시판(green9) 글·댓글 수를 한 번에
  받아 옵니다. 진행률 게이지 + 상승 비프음 표시, 취소 가능.
- 받아 온 뒤 목록 행 끝에 "G3 글5 댓12 G7 글0 댓3 G9 글2 댓0" 짧은 요약, 상세
  패널에는 게시판 이름과 함께 한 줄씩 표시.
- 다이얼로그를 열어 둔 동안 캐시 유지 — 같은 회원을 다시 누르면 즉시 보임. 닫으면
  캐시 비워짐. 50명 이상 받을 때는 예상 시간을 미리 알리고 Yes/No 확인.

### 코드 / 테스트
- `config.py` — `SERIES_BOARD = "green7"` + `SEARCH_DIALOG_BOARDS = (green3, 7, 9)`.
- `ui/search_dialog.py` — `_activity_cache` dict + `_format_activity_summary_short`
  + `_on_load_activity` (ProgressTaskDialog + ActivityCounter 활용) + F5/Alt+I 액셀러레이터.
- `ui/manual_dialog.py` + 동봉 매뉴얼 TXT — '회원 찾기' 챕터에 사용법 추가.
- `tests/test_search_dialog_activity.py` 신규 6개: 게시판 라벨, 빈 캐시·세 게시판·
  부분 캐시·다른 사용자·SEARCH_DIALOG_BOARDS 순서. 총 509/509 통과.


## v1.2.7 (2026-05-16)

장기미접속 등급 조정의 판정 기준 보강 + 미리보기 화면 정보 추가.

### 추가/변경 — 장기미접속 판정에 활동 기반 면제
- 기존: 마지막 접속이 6개월 넘으면 무조건 등급 조정 대상.
- 변경: 6개월 미접속 **그리고** '우리들의 이야기'(green3) 게시판 글 < 3 또는
  댓글 < 3 일 때만 미접속자로 분류. 6개월 미접속이어도 green3 글·댓글이
  각각 3건 이상이면 '접속자' 로 인정 → 조정 대상에서 빠짐.
- `core/level_adjustment.py:LevelAdjustmentService` — `activity_counter`
  주입점 + `build_plan` 2단계 처리(로그인 기준 1차 → green3 활동 점검 2차).
  활동 부족 사유는 "green3 글 N건/댓글 M건 (기준 미만)" 으로 표시. 임계값은
  `GREEN3_MIN_POSTS=3`, `GREEN3_MIN_COMMENTS=3` 클래스 상수.
- `ui/main_frame.py` — 미리보기·실제 적용 양쪽에 `ActivityCounter(self.session)`
  주입, 진행 게이지에 'green3 활동 점검 N/M' 메시지.

### 추가 — 미리보기 목록상자에 마지막 접속일·green3 활동량 표시
- 목록상자 한 줄에 마지막 접속 날짜와 green3 글·댓글 수가 같이 보입니다:
  `"anycall / 임동준 / 일반회원 → 준회원 / 마지막접속 2025-04-12 / 글 1건 / 댓글 0건"`
- `core/models.py:AdjustmentItem` — `green3_posts` / `green3_comments` 필드
  추가 (Optional[int]). `display()` 가 마지막접속 + (있을 때만) 글·댓글 표시.
- `ui/confirm_dialog.py:_format_detail` — 상세 패널에 "green3 활동: 글 N건 /
  댓글 M건" 줄 추가. 카운터 조회 안 했거나 실패면 "(조회 안 함 또는 조회 실패)".

### 매뉴얼
- `ui/manual_dialog.py` + 동봉 매뉴얼 TXT — '오래 안 들어온 회원 등급 조정'
  챕터에 새 판정 기준(AND 조건) 명시.

### 테스트
- `tests/test_level_adjustment.py` 신규 19개: legacy 모드 동작 보존,
  임계값 경계(=3), 글·댓글 한쪽만 부족, 카운터 예외 시 안전 폴백, 6개월 이내
  접속자/관리자/명예회원에 활동 조회 안 부름, 진행 콜백 호출 횟수, 새 필드 전달,
  display 의 마지막접속·활동 노출. 총 503/503 통과.


## v1.2.6 (2026-05-16)

자동 업데이트가 그동안은 "새 버전 있습니다" 알림 + 릴리스 페이지 링크만 보여 줘서
사용자가 직접 zip/setup.exe 를 받아 실행해야 했음. 이제 **앱이 직접 받아 설치까지**
이어 갈 수 있게 됨. 같이 매뉴얼도 동기화.

### 추가 — 자동 다운로드·설치 흐름
- `core/update_check.py` — GitHub Releases 응답의 `assets[]` 를 파싱.
  `_setup.exe` (Inno Setup 설치관리자) 우선, 없으면 `_portable.zip` 의 직접 다운로드 URL 을
  `UpdateInfo.download_url` / `asset_name` / `asset_size` / `is_installer` 로 노출.
- `download_release_asset(url, dest, progress_cb, fallback_total)` — `requests.get(stream=True)`
  로 64KB 청크 단위 다운로드. 청크마다 progress_cb 호출, 임시 `.part` 에 받고 완료 시
  rename, 실패 시 `.part` 정리. `Content-Length` 가 없으면 릴리스 API 의 asset size 로 대체.
- `ui/main_frame.py:_show_update_info` — 자산이 있으면 3-버튼
  ("지금 받기 / 릴리스 페이지 / 닫기") 으로 표시. 자산이 없으면 기존 2-버튼 유지.
- `on_install_update(info)` — `ProgressTaskDialog` 로 임시 폴더에 받기 → 끝나면
  "지금 설치하고 재시작 / 나중에" 묻기. '지금 설치' 면 `os.startfile(setup.exe)` 후
  `wx.CallLater(500, self.Close)` 로 잠시 늦춰 종료(설치관리자가 본 EXE 를 잠금 풀린 상태에서
  덮어쓸 수 있도록). 포터블 ZIP 은 자동 설치 안 함, 받은 파일 폴더만 열어 줌.

### 추가 — 매뉴얼 동기화
- `ui/manual_dialog.py` — 새 챕터 "**프로그램 업데이트 (자동 받기·설치)**" 추가
  ("기록 보기" 와 "단축키 한눈에 보기" 사이).
- 챕터 "자료실 접속 로그 보기" — 회원 검색칸이 아이디·이름·닉네임을 모두 받는다는 점,
  매칭 안 된 동작은 '기타' 로 분류된다는 점 명시.
- `초록등대 회원관리 사용자설명서.txt` — v1.0 시점에서 멈춰 있던 동봉 텍스트 매뉴얼을
  새 이름으로 갈아 끼우고, `CHAPTERS` 와 1대1로 동기화하는 export 스크립트로 다시 만듦.
  (옛 `초록등대 회원관리 v1.0 사용자설명서.txt` 는 삭제.)

### 부수
- `installer.iss` — `AppVersion` 을 v1.1.0 시점에서 멈춰 있었던 것을 **1.2.6** 으로 정정.
  이후로는 `config.py:APP_VERSION` 과 함께 매 릴리스 동기화 필요.
- 테스트: `tests/test_update_check.py` 신규 — `_pick_asset` 우선순위(setup.exe > zip > None),
  `download_release_asset` 의 청크 콜백·`.part` rename·실패 정리, `check_for_updates` 가
  assets 가 있는 응답에서 `UpdateInfo.download_url` 등을 채우는지 검증.


## v1.2.5 (2026-05-16)

실제 NAS WebDAV 응답(20260516_084207) 보고 파서를 그 형식에 맞춤 — 파일 동작이
모두 들어오기 시작했는데 OTHER 로만 분류되던 문제 해결 + 회원 검색 이름/닉네임 지원.

### 수정 — 모두 '기타' 로 분류되던 원인
- 소리샘 NAS 의 WebDAV 로그는 `cmd` 필드에 동작 동사를 담고 있는데(`"delete"`,
  `"download"`, `"upload"` 등), 제 파서는 `event_type/type/op/event/action` 만 보고
  있었음. **`cmd` / `command`** 도 우선 검사하도록 추가.
- WebDAV 빌드는 `descr` 가 메시지가 아니라 **파일 경로 자체** ("`/2. 엔터테인먼트
  자료실/.../foo.mkv`"). file_path 가 다른 키들에 없고 descr 가 `/` 로 시작하면
  그대로 file_path 로 채택 → 카테고리/파일명 정상 추출.
- `protocol` 도 비어 있으면 `logtype` 값(`"WebDAV"`)을 그대로 사용 (이전엔 descr 내
  키워드 스캔만 했는데 WebDAV 빌드는 descr 가 경로뿐이라 인식 못 함).

### 추가 — 회원 검색이 아이디뿐 아니라 이름·닉네임도
- 다이얼로그 상단 검색칸 라벨을 "**회원 (아이디/이름/닉네임)**" 으로 바꿈.
- 검색어 매칭을 SQL 단계가 아니라 enrich 후 Python 에서 수행 — 입력값이
  `dsm_user_id` / `Member.user_id` / `Member.name` / `Member.nickname` 중 어디에라도
  부분 일치하면 결과에 포함. 한국 이름("임동", "동준")으로도 찾을 수 있게 됨.

### 테스트
- `tests/test_nas_log_service.py` — 사용자가 보낸 실제 WebDAV `cmd=delete` / `cmd=download`
  샘플 회귀 (2개 신규, 총 467개 통과). 매칭 헬퍼 `_row_matches_user_query` 도 검증.


## v1.2.4 (2026-05-16)

진단 덤프(20260516_011046, 20260516_081134)로 진짜 원인이 드러나서 본격 수정:
DSM '로그 센터' 가 별도 **패키지** 라 데이터가 `SYNO.LogCenter.Log` API 에만 있는데,
이전 빌드는 `SYNO.Core.SyslogClient.Log` 가 '성공 + 빈 응답'을 주면 LogCenter API 를
시도하지도 않고 멈췄음.

### 수정
- `DsmClient.list_audit_logs` — **'성공+0건' 응답이어도 다음 변종을 계속 시도**.
  + 시도 순서를 **`SYNO.LogCenter.Log` → `SYNO.Core.SyslogClient.Log` → 구버전**
  순으로 바꿔, 패키지 센터로 설치한 Log Center 가 있으면 먼저 그 쪽에서 데이터를 받음.
- `fetch_and_store_logs` — 파일 동작 logtype 별칭 대폭 확장:
  `file_transfer / FileTransfer / transfer / file / webdav / WebDAV / filestation /
  FileStation / file_station / smb / SMB / audit / audit_log` — 데이터가 들어오는
  첫 logtype 을 채택. 사용 빌드에 맞춰 자동 선택.
- `collect_audit_log_diagnostics` — 위 13개 logtype 을 모두 한 번씩 프로브해
  어떤 logtype 에 몇 건이 들어 있는지 요약(`logtypes_with_data`, `summary`) 까지 함께
  저장. 사용자가 '진단: 응답 원본 저장' 한 번 누르면 어느 logtype 이 맞는지 한눈에 확인.

### UI · 매뉴얼 경로 정정
- '로그 센터' 가 **패키지 센터로 설치되는 별도 패키지** 라는 점을 반영해, 다음 곳의
  안내 문구를 모두 수정:
  - `ui/nas_log_dialog._on_refresh` 의 '파일 동작 로그가 비어 있습니다' 다이얼로그
  - `ui/manual_dialog.py` '자료실 접속 로그 보기' 챕터
  - `DsmClient.list_audit_logs` 의 실패 안내 메시지
  새 안내: "DSM 메인 메뉴 → '로그 센터' (없으면 패키지 센터에서 'Log Center' 설치) →
  '로그 수신/설정' → '로그 형식별 설정' 에서 WebDAV/SMB/File Station/AFP/FTP/NFS 등
  기록할 프로토콜 체크."

### 테스트
- `tests/test_dsm_client.py` — 빈 응답 폴백, 모두 빈 응답 → `[]` 반환, 새 진단 결과
  형식(`logtypes_with_data`, `summary`) 회귀 (2개 신규, 총 465개 통과).


## v1.2.3 (2026-05-16)

파일 동작 로그가 안 보이던 문제 — DSM 의 '파일 전송 로그' 설정이 꺼져 있는 경우를
명시적으로 감지·안내.

### 변경
- `fetch_and_store_logs` 가 로그 종류별 건수(`file_transfer_count`·`connection_count`)
  와 `file_transfer_seems_disabled` 플래그를 반환. 파일 전송 로그가 0건이고 연결 로그만
  들어왔다면 새로고침 후 안내 다이얼로그로 **'DSM 제어판 → 로그 센터 → 일반 → 파일
  전송 로그 활성화'** 절차를 안내.
- DSM 빌드별 logtype 표기 차이(`file_transfer` / `FileTransfer` / `transfer` / `file`
  + `connection` / `Connection` / `conn`) 를 자동 폴백 — 한 표기에서 결과가 안 오면
  다음 표기로 한 번 더 시도.
- 상태 줄·완료 메시지에 "이번 수집: 파일 전송 N건 / 연결 M건" 으로 어떤 종류가 왔는지
  명시.

### 테스트
- `tests/test_nas_log_service.py` — file_transfer 0건일 때 `seems_disabled=True`, 양쪽
  모두 데이터 있을 때 플래그 False, 변종 표기 폴백 회귀 (2개 신규, 총 463개 통과).


## v1.2.2 (2026-05-16)

실제 소리샘 NAS 응답 샘플(`who` 필드 / "failed to sign in to [DSM] via [password] due
to authorization failure")을 받아 보고 회원 매칭·동작 분류 모두 못 잡던 문제를 해결.

### 수정 — 회원 매칭이 안 되던 결정적 원인
- `_parse_entry` 가 사용자 ID 를 `user/username/uid` 키에서만 읽었는데, 소리샘 NAS DSM
  빌드는 **`who`** 필드(또는 `account`, `mb_id`, `user_id`)를 씁니다. 모두 추가로 인식
  하도록 했고, `ip` 도 `host` 키 폴백을 더했습니다. 이제 `who="anycall"` 같은 항목이
  user_id 로 정상 추출되어 회원 매칭이 됩니다.

### 수정 — 모든 항목이 '기타' 로 빠지던 원인
- "**failed to sign in to [DSM] via [password] due to authorization failure**" 문구가
  키워드 목록에 없어 전부 OTHER 로 빠졌습니다. 다음 변종을 모두 ACTION_FAIL 로 등록:
  failed to sign in / failed to log in / failed to logon / authorization failure /
  authorization denied / sign-in failure / login failed / access denied / permission
  denied / 로그인 실패 / 로그인 거부 / 인증 실패 / 인증 거부 / 접근 거부 / 권한 거부.
- 인증 성공 키워드도 보강 (signed in to / sign in to / log on to / connected to ...).
- **`logtype` 폴백** — 키워드 매칭에 실패해도 `logtype="Connection"`(또는 DSM 빌드의
  오타 `orginalLogType="connection"`)이면 본문에 fail/denied/blocked 가 있는지로
  login vs connect_fail 을 추론.

### UI
- 미등록 + `connect_fail` 항목은 **"(외부 시도) <id>"** 로 표시 — 봇의 무차별 로그인
  시도(`93.152.221.x` 등)와 실제 회원의 비밀번호 오타를 한눈에 구분. 실제 회원이
  비밀번호를 틀린 경우엔 그대로 "이름(uid)" 으로 표시되고 "외부" 라벨이 붙지 않음.

### 테스트
- `tests/test_nas_log_service.py` — 사용자가 보낸 실제 샘플(JSON)을 회귀 케이스로 등록
  (7개 신규, 총 461개 통과). `who` 필드 추출, `failed to sign in` → connect_fail,
  `(외부 시도)` 라벨, `2026/05/16 01:10:43` 형식 시간 파싱.


## v1.2.1 (2026-05-16)

자료실 접속 로그 사용감 다듬기 — 매칭 정확도·동작 분류·수집 진행률.

### 변경/개선
- **회원 매칭 우선순위 강화** (`core/nas_log_service.enrich_with_members`):
  - 1순위: DSM user_id ↔ 소리샘 회원 `user_id` 정확 일치 (이름·닉네임으로 표시).
  - 2순위: 소리샘에 없지만 DSM 자료실 그룹 멤버라면 `(자료실 회원) uid` 로 표시.
  - 3순위: 둘 다 아니면 `(미등록) uid`.
  - 새 헬퍼 `_clean_dsm_username()` — `DOMAIN\user` / `CORP/user` / `user@domain.com` 같은
    표기를 모두 단순 `user` 로 정규화해 매칭 적중률 향상. 저장 시점·매칭 시점 양쪽에 적용.
  - DSM 자료실 그룹 멤버 목록을 메타 테이블에 캐시 (`nas_log_meta.dsm_group_members`)
    — 다이얼로그를 열 때 바로 사용. 자동/수동 새로고침에서 함께 가져옴.
- **동작 분류 강화** (`_parse_entry`, `_detect_action`, `_structured_action`):
  - `event_type/type/op/event/action` 등 **구조화 필드 우선** — `_STRUCTURED_ACTION_MAP`
    (download/upload/delete/rename/move/copy/mkdir/login/logout/connect/disconnect/...
    그리고 `FILE_DELETE` 같은 합성 값도 부분 일치) 로 즉시 결정.
  - 영문·한글 키워드 대폭 확장 — 'removed/erased/trashed', 'fetched/get', 'wrote/put',
    'duplicated', '옮김/옮긴', '받음/내려받', '올림/올린' 등.
  - '기타' 가 많이 나오면 원본 샘플(최대 20건)을 `data/dumps/<ts>_nas_log_unknown.json`
    에 자동 저장 — 빌드 차이로 알 수 없는 형식이면 그 파일을 받아 파서 보강.
- **수집 진행률 + 상승 비프음** (`ui/nas_log_dialog._on_refresh`):
  - 기존 무음 새로고침 → `ProgressTaskDialog` 사용. 5단계 (그룹 멤버 확인 → 파일 전송
    로그 → 연결 로그 → 파싱 → 저장) 진행 표시. 진행될수록 비프음 음높이 상승.
  - `fetch_and_store_logs(progress_cb=, dsm_group_name=, dump_dir=)` 신규 인자.
- **'원문' 컬럼 추가** (`ui/nas_log_dialog._build_columns`) — DSM 응답 본문(`raw_message`)
  의 앞부분을 그대로 보여 줘, 동작 분류가 '기타' 로 빠지는 항목의 원인을 한눈에 확인.
- **상태 줄 보강** — '소리샘 회원: N명 / 자료실 그룹: N명' 표시. 소리샘 회원 캐시가
  비어 있으면 'Ctrl+F 로 한 번 회원 검색을 실행하면 이름이 보입니다' 안내.

### 테스트
- `tests/test_nas_log_service.py` — `_clean_dsm_username`, 매칭 우선순위(소리샘·DSM 그룹),
  `_structured_action`, `_parse_entry` 의 구조화 필드 우선, progress_cb 5단계 호출,
  '기타' 샘플 덤프 등 17개 신규 케이스 (총 454개 통과).


## v1.2.0 (2026-05-16)

### 추가
- **자료실(NAS) 접속 로그 화면** (`ui/nas_log_dialog.py`, 작업 메뉴 → "자료실 접속 로그") —
  DSM Log Center 의 파일 전송 로그 + 로그인/로그아웃 로그를 가져와 회원·시간·동작·카테고리
  기준으로 조회·검색·내보내기. ListCtrl REPORT 컬럼: 시간 / 회원(이름과 아이디) / IP /
  프로토콜 / 동작 / **카테고리(폴더 첫 단계)** / **파일명** / 전체 경로. 기간/회원 ID/
  동작 종류/카테고리 필터, '필터 적용·지금 새로고침·내보내기(txt·xlsx·HTML)·진단' 버튼.
- **앱 시작 시 백그라운드 1회 자동 수집** (`MainFrame._maybe_fetch_nas_log_in_bg`) —
  DSM 자격 캐시가 있고 2FA 비활성인 경우에만 무인 수집. 2FA 활성 시 자동 수집은 건너뛰고
  메뉴/상태 줄에 '인증 필요' 안내. 어떤 예외도 사용자 다이얼로그로 띄우지 않고 `last_status`
  에만 사유 기록.
- **영구 보관 SQLite** (`core/nas_log_store.py`, `data/nas_access_log.db`) —
  DSM 의 Log Center 가 일정 기간만 남기므로 앱이 받아 쌓아 둠. UNIQUE
  `(logged_at, dsm_user_id, action, raw_hash)` 로 중복 차단, 인덱스 `logged_at DESC`·
  `dsm_user_id`. 메타 테이블에 `latest_epoch`(증분 수집용) + `last_status_*` 보관.
- **DSM 응답 파서** (`core/nas_log_service.py`) — 구조화 필드(`time/user/ip/descr`) 우선
  + descr 의 대괄호 값과 키워드(영문·한글 모두)로 보조 추출. 동작 정규화
  (`login/logout/upload/download/delete/rename/move/copy/mkdir/other`).
- **DSM 로그 API 호출** (`DsmClient.list_audit_logs`) — `SYNO.Core.SyslogClient.Log` v1/v2,
  `SYNO.LogCenter.Log` v1, `SYNO.Core.SyslogClient.Status.Log` v1 의 4단 fallback 체인
  (기존 `_modify_group_membership` 패턴 그대로). `collect_audit_log_diagnostics()` 가
  모든 시도와 응답 샘플을 JSON 으로 떠 `data/dumps/<ts>_dsm_audit_diag.json` 에 저장 —
  빌드 차이로 비어 나올 때 파서 보강용.
- **자료실 가입 동의서 5번 항목** (`ui/dsm_create_user_dialog.py:_AGREEMENT_TEXT`) —
  "자료실 이용 내역(접속 시각, 접속 IP, 파일 업로드·다운로드·삭제 등)은 부정 이용 방지와
  운영 관리를 위해 기록·보관되며, 회원 본인에 한해 열람·문의가 가능합니다." 동의 체크박스
  를 거치지 않으면 신규 사용자 생성이 막힘.
- **앱 옵션** `auto_fetch_nas_log_on_start: True` (`core/app_options.py`) — 시작 시
  백그라운드 수집 토글.
- **매뉴얼 챕터** — `ui/manual_dialog.py` 에 "자료실 접속 로그 보기" 챕터 (필터 사용법,
  DSM 제어판 → 로그 센터 → 일반 → '파일 전송 로그 활성화' 안내, 약관 변경 안내).
- 단위 테스트 42개 신규 (총 437개) — `tests/test_nas_log_store.py` 14개,
  `tests/test_nas_log_service.py` 22개, `tests/test_dsm_client.py` 에 `list_audit_logs`
  fallback 체인 + 진단 캡처 6개.

### 변경
- `core/app_options.get(key, default=None)` — `default` 인자 추가 (예전엔 키만 받았음).
- `.gitignore` — `data/nas_access_log.db` 제외 (회원 IP·동작 기록 민감).
- `chorok_green_admin.spec` — `core.nas_log_*` 5종 + `ui.nas_log_*` 2종 hiddenimports.

### 운영자 안내
- DSM 제어판 → '로그 센터' → 일반 → **'파일 전송 로그 활성화'** + SMB/WebDAV/File Station
  체크가 켜져 있어야 데이터가 쌓입니다 (꺼져 있으면 앱이 받아 갈 로그가 비어 있음).
- 약관(동의서) 5번 항목 추가에 따라 기존 회원에게도 다음 안내 메일/공지로 한 번
  알려 주시는 것을 권장합니다.


## v1.1.1 (2026-05-12)

### 추가/변경
- 빌드 시 `release/green_admin_v{ver}_portable.zip` / `..._setup.exe` ASCII 이름 사본을
  자동 생성 — GitHub Releases 자산이 한글 파일명을 떼어내 깨지는 문제 회피 (`build_release.py` 4단계).
- 사용 설명서(Shift+F1) 보강, 단축키 안내(Ctrl+K) 를 단축키 목록만으로 정리.
- 빌드 0단계로 `data/google_credentials.json` 이 없으면 리포 밖 마스터 사본
  (`%USERPROFILE%\.green_admin\google_credentials.json` 또는 환경변수
  `GREEN_ADMIN_GOOGLE_CREDENTIALS`)에서 자동 복사 — 재클론 후에도 빌드만 하면 EXE 에 포함.
- 신규 가입자 승인 또는 회원 'DSM 활성화' 시, 구글 폼 응답 시트("설문지 응답 시트1")의
  '상태'(P)뿐 아니라 같은 행의 **'시작일'(J)·'만료일'(K)** 도 자동으로 기록한다
  (활성 구독의 `period_from`/`period_to`, ISO `YYYY-MM-DD`). 폼 시트에 해당 회원 행이
  없으면 지금처럼 조용히 넘어간다. 비활성화는 '상태'만 바꾸고 날짜는 건드리지 않는다.
  - `core/sheets_sync.py`: `update_form_activation()` 추가(`update_form_status` 는 위임),
    모듈 함수 `push_form_status(...)` 에 `period_from`/`period_to` 인자 추가.
  - `core/dsm_workflow.py`: `NewSubscriberCandidate` 에 `period_from` 필드 추가.
- **자료실 구독비 관리 매트릭스 화면 정리** (`ui/payment_dialog.py`):
  - 화면에서 월별 칸(25-06, 25-07 … 12개월)을 제거 — 스크린리더로 한 줄을 읽을 때
    노이즈가 컸음. 월별 표는 TXT/Excel/HTML 내보내기에는 그대로 유지.
  - '구독 상태' 칸을 만료일·남은 일수까지 한 줄에 명확히 표시
    ("구독중 — 2026-06-30 까지 (5일 남음)" / "구독 만료 — 2026-03-31" / "구독 안 함")
    — `core/payment_matrix.py:matrix_status_label()` 추가.
  - DSM 사용자명과 소리샘 아이디가 다를 때(예: DSM `hj06` ↔ 소리샘 `rgw107`, 둘 다 '김혜정'),
    DSM 사용자의 실명(설명 필드)으로 소리샘 회원을 찾아(그 이름의 회원이 딱 한 명일 때만)
    행을 합치고 `rgw107 / 김혜정 / 닉네임  (DSM: hj06)` 처럼 표시. 'DSM 정합' 판정도
    이 매칭을 반영. — `core/dsm_workflow.py:match_sorisem_member_by_name()` /
    `resolve_dsm_username_to_sorisem()` 추가; `core/dsm_client.list_users()` 의
    `description`(실명) 도 함께 읽어 옴.
  - 같은 사람이 소리샘 회원 행 + DSM/신청 아이디 행으로 **중복**되어 나오던 것을 한 줄로 합침 —
    폼 시트의 '이름'(또는 DSM 사용자 설명의 실명)으로 소리샘 회원을 찾아: 같은 이름의 행 중
    소리샘 회원 행이 딱 하나면 → 그 행(소리샘 아이디 우선, 구독 유무 무관)을 대표로,
    나머지 아이디는 `(DSM: …)` 로 부기. 소리샘 회원 행이 없으면 '구독중'인 행이 딱 하나일
    때만, 동명이인(소리샘 회원 2명 이상)이면 그 중 '구독중'이 딱 하나일 때만 합친다 —
    그 외 애매한 경우는 합치지 않고 그대로 둔다. 폼 신청자의 희망아이디가 소리샘 아이디와
    달라도(예: 희망아이디 `books9988` ↔ 소리샘 `books`, 둘 다 '이성제') 이름으로 묶어 준다.


## v1.1.0 (2026-05-11)

### 추가
- **게시판 복사/이동/삭제 동작화** — `curl_cffi` 로 크롬 TLS/HTTP 지문을 위장해
  게시판 관리 요청을 보낸다(사이트 앞단 보안장비가 Python 클라이언트를 다르게
  취급하는 경우 우회). `fboardlist` 폼의 필드 순서·`btn_submit` 값을 그대로 전송하고,
  `move.php` 가 대상 게시판 목록을 비워 보내도 `chk_bo_table[]` 로 `move_update.php`
  에 직진해 처리한 뒤, 대상 게시판 재조회로 실제 반영 여부를 검증한다.
  회원 검색의 "탈퇴 처리"·게시판 관리 요청에 대한 진단 HTML 덤프를 `data/dumps/` 에 저장.
- **장기미접속 '탈퇴' 처리자 재가입 차단 명단** (`data/inactivity_withdrawn.json`,
  `core/withdrawn_blocklist.py`) — 장기미접속 등급 조정에서 `탈퇴`(WITHDRAW_LEVEL) 로
  내려진 회원 아이디를 보관. 그 아이디가 다시 가입 신청(대기 등급)으로 나타나면 신규
  가입자 승인 화면에서 '승인' 버튼이 막히고 경고가 뜬다. 오등록 시 "탈퇴자 명단에서
  빼기" 버튼으로 해제 가능. 신규 모듈/UI/테스트 추가.
- **회원 검색 → "탈퇴 처리(&W)" 버튼 (Alt+W)** — 강조된 회원 1명 또는 스페이스로 체크한
  여러 명을 사이트 등급 '탈퇴' 로 일괄 처리. 확인창에 "이 회원(들)의 재가입 승인을 막기"
  체크박스 포함. Undo/이력/로그 기록.

### 변경/수정
- **회원 등급 변경(`MemberAdminAdapter`) 정확성 개선** — POST 전에 `admin.member.php`
  폼 페이지를 GET 해서 `token`·hidden·submit 버튼·action 을 실제 값으로 스크랩(빈 token
  으로 보내 변경이 무시되던 문제 해소), POST 후 회원을 재조회해 실제 반영 여부를 검증
  (예전엔 응답에 회원 목록 마커만 있으면 "성공"으로 봤던 거짓 성공 제거), 폼의 `cl_level`
  옵션 목록을 직접 읽어 라벨('탈퇴' 등) 기준으로 옵션 값을 변환(스킨이 LEVEL_LABELS 와
  다른 값을 쓰는 경우 보정). `data/dumps/member_admin_*.html` 진단 덤프.
- 게시물 복사/이동/삭제의 실패 판정을 강화 — `<div id="validation_check">` / "오류안내
  페이지" 같은 명백한 에러 표식만 실패로 보고, 게시판 페이지에 박힌 JS `alert()` 문자열·
  팝업 캡션("게시판을 한개 이상 선택해 주십시오") 같은 오탐을 제거.

### 의존성
- `curl_cffi` 추가 (게시판 관리 요청의 브라우저 TLS/HTTP 지문 위장용).


## v1.0.0 — 안정 버전 (2026-05-08)

첫 안정 릴리스. v0.5 기능을 그대로 유지하면서 회귀 보호와 운영 신뢰도를 강화.

### 추가
- **단위 테스트 인프라** (`pytest`) — `tests/` 11개 파일·약 60+ 케이스. 등급 매핑·승급·조정·Undo·메모·로그·diff·파서·schedule·history·site_diagnostics·update_check 회귀 보호.
- **회원 등급 변경 영구 이력** — 모든 등급 변경(자동 승급 / 장기미접속 조정 / 수동 변경 / 가입 승인·거부 / Undo)을 `data/level_history.db`(SQLite)에 영구 보관. 메뉴: 파일 → "등급 변경 이력 (Ctrl+Shift+Y)".
- **사이트 구조 변경 자동 진단** — `EmptyParseError` 시 사이트 응답을 자동 분석해 구체적 원인(form 부재/권한 거부/select option 범위/컬럼 수 등)을 제시. 개발자 메뉴에 "사이트 구조 진단" 추가.
- **GitHub Releases 자동 업데이트 확인** — 시작 시 백그라운드(24시간 캐시), 새 버전 있으면 알림 + 릴리스 페이지 열기. 도움말 → "업데이트 확인"으로 강제 호출 가능.
- `requirements-dev.txt` 분리 (pytest, pyinstaller).

### 변경
- v0.5 신규 기능들 (활동점수, MVP TOP 10, 신규 가입자 승인, 메일 개별 발송 전용)을 검증하는 회귀 테스트로 잠금.

### 구버전 호환
- `determine_target_level_by_posts(level, post_count)` 별칭 유지 — 외부 호출 보호.
- `SEND_MODE_BULK` = `SEND_MODE_INDIVIDUAL` 별칭 유지.

---

## v0.5.0 (2026-05-08)

### 추가
- **활동점수 기반 자동 승급** — 글 + 댓글 가중합 (글×1.0 + 댓글×0.3), green3 + green9 합산.
- **준회원(5) → 일반회원(6) 자동 승급** (활동점수 5 이상).
- **승급 임계값 상향** — 30/60/120 등차로 정리.
- **MVP TOP 10 분기 자동 분석** — 1/4/7/10월 도래 + 수동 메뉴.
- **신규 가입자 승인 다이얼로그** — 시작 시 자동 알림, 한 명씩 승인/거부/미루기.
- **메일 개별 발송 전용** — bulk 모드 제거, 수신자 ID 비공개.
- **MVP·검색 등급 표시 일관화** — `LEVEL_LABELS` 우선 사용.

---

## v0.4.0 (2026-05-08)

### 추가
- 회원 통계 대시보드 (Ctrl+T)
- 백업 비교 다이얼로그 (Ctrl+Shift+D)
- 작업 로그 뷰어 (Ctrl+Shift+L)
- 회원 개별 승급/강등 (Ctrl+G, 더블클릭)
- 회원 메모/태그 (Ctrl+N, 로컬 SQLite)
- 자동 승급 미리보기 단계 도입
- 실행 취소 Undo 스택 (Ctrl+Z)
- 백업 보관 정책 (12개월 이상 zip 압축)
- 승급 임박 회원 분석
- HTML 분기 리포트
- 단축키 커스터마이징 (`data/keybindings.json`)

### 변경
- **등급 매핑 5~9 재정의**: 5=준회원, 6=일반회원, 7=우수회원, 8=최우수회원, 9=명예회원.
- 동호회관리자 권한은 사이트 페이지 접근권으로 판정 (별도 레벨 없음).

---

## v0.3.0 (이전)

기존 기능 — 우수회원 백업, 게시물 기반 자동 승급, 장기미접속 조정, 회원 검색, 수동 메일 발송, 자동 분기 스케줄.
