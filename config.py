"""초록등대 회원관리 앱 설정값.

⚠ 등급 번호는 사이트(소리샘 admin.member.php?cl=green) 실제 cl_level select
값을 기준으로 한다 (data/dumps/ 에 보관된 HTML 덤프로 확정):

    0 손님 / 1 탈퇴 / 2 거부 / 3 대기 / 4 준회원
    5 일반회원 / 6 우수회원 / 7 최우수회원 / 8 명예회원 / 9 동호회관리자

(과거 v1.0.x 시점에는 "신청"(4) 옵션 + 동호회관리자=10 매핑이 있었으나
사이트 폼의 실제 옵션은 0~9 이며 신청은 빠져 있다 — 매핑이 1만큼 어긋나
사이트 POST 가 의도와 다른 등급으로 적용되던 버그가 있었다.)

동호회관리자(9)는 사이트의 정식 cl_level 값이지만, 권한 체크는 등급 번호가
아니라 admin.member.php 페이지 접근 가능 여부로 판정한다. core/permission.py 참고.
"""
import os
import sys

APP_NAME = "초록등대 회원관리"
APP_VERSION = "1.4.0"

# 실행 위치 (PyInstaller 호환)
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

# 데이터/백업/로그/덤프 폴더
DATA_DIR = os.path.join(APP_DIR, "data")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
DUMPS_DIR = os.path.join(DATA_DIR, "dumps")
BACKUPS_DIR = os.path.join(APP_DIR, "backups")
SOUNDS_DIR = os.path.join(APP_DIR, "sounds")

LAST_RUN_FILE = os.path.join(DATA_DIR, "last_run.json")
# 예약 공지 큐 — 정해진 시각에 자동 발송할 공지 목록 (v1.4)
SCHEDULED_NOTICES_FILE = os.path.join(DATA_DIR, "scheduled_notices.json")

for _d in (DATA_DIR, LOGS_DIR, DUMPS_DIR, BACKUPS_DIR):
    os.makedirs(_d, exist_ok=True)

# 소리샘 / 초록등대 동호회 URL
SORISEM_BASE_URL = "https://www.sorisem.net"
ADMIN_MEMBER_URL = f"{SORISEM_BASE_URL}/plugin/ar.club/admin.member.php?cl=green"
PUBLIC_MEMBER_URL = f"{SORISEM_BASE_URL}/plugin/ar.club/?cl=green"

# "우리들의 이야기" 게시판 (자동 승급 게시물 카운팅 대상)
GREEN3_BOARD = "green3"
GREEN3_SEARCH_URL = f"{SORISEM_BASE_URL}/bbs/board.php?bo_table={GREEN3_BOARD}&cl=green"

# 질문게시판 (v0.5: 활동점수·MVP 산정 대상)
QNA_BOARD = "green9"
QNA_SEARCH_URL = f"{SORISEM_BASE_URL}/bbs/board.php?bo_table={QNA_BOARD}&cl=green"

# 시리즈 및 정보 게시판 (v1.2.8: 회원 검색 화면 활동량 표시용)
SERIES_BOARD = "green7"

# 활동점수·MVP 산정 시 사용할 게시판 목록 (글·댓글 모두)
ACTIVITY_BOARDS = (GREEN3_BOARD, QNA_BOARD)

# 회원 검색(Ctrl+F) 다이얼로그에서 '활동량 불러오기' 시 조회할 게시판.
# 우리들의 이야기 + 시리즈/정보 + 질문게시판 — 세 곳의 글·댓글 수를 보여 준다.
SEARCH_DIALOG_BOARDS = (GREEN3_BOARD, SERIES_BOARD, QNA_BOARD)

# 메일 발송 엔드포인트 (/message/write.php, POST multipart)
MAIL_WRITE_URL = f"{SORISEM_BASE_URL}/message/write.php"
MAIL_SENDER_USER_ID = "rtgreen"   # 이 아이디로 로그인했을 때만 메일 자동 발송

# 작업 주기
BACKUP_INTERVAL_MONTHS = 3
ADJUSTMENT_INTERVAL_MONTHS = 6
INACTIVITY_MONTHS = 6  # 6개월 이상 미접속 → 조정 대상 (단, 활동량으로 면제 가능)
# v1.2.10: 1년 이상 미접속이면 green3 활동량과 관계없이 무조건 조정 대상.
# "6개월 미접속이지만 green3 글·댓글이 많아 면제" 흐름의 안전 상한.
INACTIVITY_MONTHS_HARD = 12
MVP_INTERVAL_MONTHS = 3  # 분기마다 MVP TOP 10 자동 분석

# 등급 라벨 매핑 (사이트 cl_level select 실제 값)
LEVEL_LABELS = {
    0: "손님",
    1: "탈퇴",
    2: "거부",
    3: "대기",
    4: "준회원",
    5: "일반회원",
    6: "우수회원",
    7: "최우수회원",
    8: "명예회원",
    9: "동호회관리자",
}

LEVEL_TEXT_MAP = {
    "동호회 관리자": 9, "동호회관리자": 9,
    "명예 회원": 8, "명예회원": 8,
    "최우수 회원": 7, "최우수회원": 7,
    "우수 회원": 6, "우수회원": 6,
    "일반 회원": 5, "일반회원": 5,
    "준 회원": 4, "준회원": 4,
    "대기": 3,
    "거부": 2,
    "탈퇴": 1,
    "손님": 0,
}

# 관리자 권한 등급 — 자동 승급/조정/MVP/통계 후보에서 제외해야 할 레벨.
# 사이트는 cl_level=9 로 동호회관리자를 표현함.
ADMIN_LEVELS = (9,)

# 회원 개별 등급 변경 시 UI 콤보에 노출할 등급 (낮음 → 높음).
# 0~3 (손님/탈퇴/거부/대기)는 가입 단계용이라 수동 변경 메뉴에 보이지 않음.
SELECTABLE_LEVELS = (4, 5, 6, 7, 8)

# 분기 백업(우수회원 명단) 대상 등급 — 우수(6), 최우수(7)
OUTSTANDING_LEVELS = (6, 7)

# 탈퇴 처리 시 설정할 등급 (1 = 탈퇴 옵션)
WITHDRAW_LEVEL = 1

# 장기미접속 등급 조정 규칙 (사이트 실제 등급 기준)
# from_level: ("action", to_level)
# action 은 표시용 ("delete" = 탈퇴, "demote" = 강등). 사이트 처리는 모두 cl_level 변경.
LEVEL_TRANSITIONS = {
    4: ("delete", WITHDRAW_LEVEL),  # 준회원 → 탈퇴(1)
    5: ("demote", 4),               # 일반회원 → 준회원
    6: ("demote", 5),               # 우수회원 → 일반회원
    7: ("demote", 6),               # 최우수회원 → 우수회원
    # 8 (명예회원) 는 조정 대상 아님
}

# 활동점수 (Activity Score) 산정식 — v0.5 신규
#   활동점수 = (게시판 글 수 합) × 1.0 + (댓글 수 합) × COMMENT_WEIGHT
# 두 게시판(우리들의 이야기 + 질문게시판)을 모두 합산.
# 댓글 가중치 0.3 = 글 1건이 댓글 약 3.3건과 동등.
COMMENT_WEIGHT = 0.3

# 가입 초기(3=대기) 회원이 활동점수 임계 이상이면 준회원(4)으로 승급
INITIAL_PROMOTION_MIN_SCORE = 3.0
INITIAL_FROM_LEVELS = (3,)
INITIAL_TO_LEVEL = 4

# 준회원(4) → 일반회원(5) 자동 승급
INTERMEDIATE_PROMOTION_FROM_LEVEL = 4
INTERMEDIATE_PROMOTION_TO_LEVEL = 5
INTERMEDIATE_PROMOTION_MIN_SCORE = 5.0

# 일반회원(5) 이 활동점수에 따라 도달하는 등급 (누적 임계값)
# 30점 이상 → 우수(6), 60점 이상 → 최우수(7), 300점 이상 → 명예(8)
ACTIVITY_PROMOTION_BASE_LEVEL = 5
ACTIVITY_PROMOTION_TABLE = [
    (300.0, 8),  # 명예회원
    (60.0,  7),  # 최우수회원
    (30.0,  6),  # 우수회원
]
# 구버전 호환 별칭 (남은 참조 보호용)
POST_COUNT_PROMOTION_BASE_LEVEL = ACTIVITY_PROMOTION_BASE_LEVEL
POST_COUNT_PROMOTION_TABLE = ACTIVITY_PROMOTION_TABLE
INITIAL_PROMOTION_MIN_POSTS = INITIAL_PROMOTION_MIN_SCORE

# MVP TOP N — 분기 산정
MVP_TOP_N = 10
# MVP 산정에서 제외할 등급
#   8 = 명예회원 (이미 최고 등급)
#   9 = 동호회관리자 (운영진은 산정 대상 아님)
MVP_EXCLUDED_LEVELS = (8, 9)
# MVP 산정 대상 기간 — 이 날짜 이후(포함) 작성된 글·댓글만 카운트.
# 우리들의 이야기(green3) + 질문게시판(green9) 두 곳에서만 집계.
# 게시판 검색은 sfl=mb_id 로 회원의 모든 글·댓글을 가져온 뒤 행마다 작성일을
# 파싱해 since 이전 글이 나오는 시점에서 페이지 순회를 종료한다.
import datetime as _dt  # noqa: E402
MVP_SINCE_DATE = _dt.date(2026, 6, 1)
del _dt

# 신규 가입 승인 처리
# 가입 후 아직 승인되지 않은 회원의 사이트 등급 (대기)
PENDING_LEVELS = (3,)
# 승인 시 부여할 등급
APPROVE_TO_LEVEL = 4         # 준회원
# 거부 시 부여할 등급 (1=탈퇴, 2=거부)
REJECT_TO_LEVEL = 2          # 거부

USER_AGENT = "ChorokGreenAdmin/1.0"
HTTP_TIMEOUT = 20

# 자료실 구독비 단가표 — 입금액(원) → 구독 개월 수
# 토스 거래내역 import 시 이 표와 정확히 일치하는 입금만 구독으로 인정.
# 표에 없는 금액(예: 1원 인증, 찬조금)은 "기타 입금" 으로 분리되어 구독 적용 안 됨.
SUBSCRIPTION_PRICING: dict[int, int] = {
    3000: 1,
    9000: 3,
    12000: 6,
    24000: 12,
}
