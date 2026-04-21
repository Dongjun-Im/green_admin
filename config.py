"""초록등대 회원관리 앱 설정값.

⚠ 등급 번호는 사이트(소리샘 admin.member.php?cl=green) 실제 값 기준:
    0 손님 / 1 탈퇴 / 2 거부 / 3 대기 / 4 신청
    5 일반회원 / 6 우수회원 / 7 최우수회원 / 8 명예회원 / 9 동호회관리자
"""
import os
import sys

APP_NAME = "초록등대 회원관리"
APP_VERSION = "0.3.0"

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

for _d in (DATA_DIR, LOGS_DIR, DUMPS_DIR, BACKUPS_DIR):
    os.makedirs(_d, exist_ok=True)

# 소리샘 / 초록등대 동호회 URL
SORISEM_BASE_URL = "https://www.sorisem.net"
ADMIN_MEMBER_URL = f"{SORISEM_BASE_URL}/plugin/ar.club/admin.member.php?cl=green"
PUBLIC_MEMBER_URL = f"{SORISEM_BASE_URL}/plugin/ar.club/?cl=green"

# "우리들의 이야기" 게시판 (자동 승급 게시물 카운팅 대상)
GREEN3_BOARD = "green3"
GREEN3_SEARCH_URL = f"{SORISEM_BASE_URL}/bbs/board.php?bo_table={GREEN3_BOARD}&cl=green"

# 메일 발송 엔드포인트 (/message/write.php, POST multipart)
MAIL_WRITE_URL = f"{SORISEM_BASE_URL}/message/write.php"
MAIL_SENDER_USER_ID = "rtgreen"   # 이 아이디로 로그인했을 때만 메일 자동 발송

# 작업 주기
BACKUP_INTERVAL_MONTHS = 3
ADJUSTMENT_INTERVAL_MONTHS = 6
INACTIVITY_MONTHS = 6  # 6개월 이상 미접속 → 조정 대상

# 등급 라벨 매핑 (사이트 실제 값)
LEVEL_LABELS = {
    0: "손님",
    1: "탈퇴",
    2: "거부",
    3: "대기",
    4: "신청",
    5: "일반회원",
    6: "우수회원",
    7: "최우수회원",
    8: "명예회원",
    9: "동호회관리자",
}

LEVEL_TEXT_MAP = {
    "동호회관리자": 9, "클럽관리자": 9,
    "명예 회원": 8, "명예회원": 8,
    "최우수 회원": 7, "최우수회원": 7,
    "우수 회원": 6, "우수회원": 6,
    "일반 회원": 5, "일반회원": 5,
    "신청": 4,
    "대기": 3,
    "거부": 2,
    "탈퇴": 1,
    "손님": 0,
}

# 우수회원 백업 대상 등급 (6=우수, 7=최우수)
OUTSTANDING_LEVELS = (6, 7)

# 등급 9 = 동호회관리자 (모든 자동 조정에서 제외)
ADMIN_LEVEL = 9

# 탈퇴 처리 시 설정할 등급 (1 = 탈퇴 옵션)
WITHDRAW_LEVEL = 1

# 장기미접속 등급 조정 규칙 (사이트 실제 등급 기준)
# from_level: ("action", to_level)
# action 은 표시용 ("delete" = 탈퇴, "demote" = 강등). 사이트 처리는 모두 cl_level 변경.
LEVEL_TRANSITIONS = {
    5: ("delete", WITHDRAW_LEVEL),  # 일반회원 → 탈퇴(1)
    6: ("demote", 5),               # 우수회원 → 일반회원
    7: ("demote", 6),               # 최우수회원 → 우수회원
    # 8 (명예), 9 (관리자) 는 조정 대상 아님
}

# 우수회원 자동 승급 (구버전: 접속수 기반):
# 일반회원(5) 중 접속수가 PROMOTION_LOGIN_THRESHOLD 이상이면 우수회원(6)으로 승급.
PROMOTION_LOGIN_THRESHOLD = 50
PROMOTION_FROM_LEVEL = 5
PROMOTION_TO_LEVEL = 6

# 게시물 기반 자동 승급 규칙 ("우리들의 이야기" 게시판 기준)
# 가입 초기(3=대기, 4=신청) 회원이 3건 이상 쓰면 일반회원(5) 으로 승급
INITIAL_PROMOTION_MIN_POSTS = 3
INITIAL_FROM_LEVELS = (3, 4)
INITIAL_TO_LEVEL = 5

# 일반회원(5) 이 글 수에 따라 도달하는 등급 (누적 임계값)
# 100 건 이상 → 명예회원(8), 50 건 이상 → 최우수회원(7), 30 건 이상 → 우수회원(6)
POST_COUNT_PROMOTION_TABLE = [
    (100, 8),  # 명예회원
    (50, 7),   # 최우수회원
    (30, 6),   # 우수회원
]

USER_AGENT = "ChorokGreenAdmin/0.3"
HTTP_TIMEOUT = 20
