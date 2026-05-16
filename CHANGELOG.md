# CHANGELOG

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
