"""관리자 페이지 HTML 덤프 (Phase 0 분석용 단독 실행 스크립트).

사용법:
    python tools/dump_admin_page.py

green_auth.run_authentication() 으로 로그인 후 admin.member.php 의
1~3 페이지를 data/dumps/ 폴더에 저장한다.
"""
import os
import sys
from datetime import datetime

# 프로젝트 루트를 sys.path 에 추가
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import wx  # noqa: E402

from config import ADMIN_MEMBER_URL, DUMPS_DIR  # noqa: E402
from green_auth import run_authentication  # noqa: E402


def main() -> int:
    app = wx.App(False)
    auth = run_authentication("관리자 페이지 덤프 도구")
    if auth is None:
        print("로그인 실패 또는 취소")
        return 1
    session = auth.session

    os.makedirs(DUMPS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    saved = 0
    for page in (1, 2, 3):
        sep = "&" if "?" in ADMIN_MEMBER_URL else "?"
        url = ADMIN_MEMBER_URL if page == 1 else f"{ADMIN_MEMBER_URL}{sep}page={page}"
        try:
            resp = session.get(url, timeout=20)
        except Exception as e:
            print(f"page {page} 요청 실패: {e}")
            continue
        if not resp.ok:
            print(f"page {page} HTTP {resp.status_code}")
            continue
        path = os.path.join(DUMPS_DIR, f"{ts}_page{page}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(resp.text)
        print(f"저장: {path} ({len(resp.text)} bytes)")
        saved += 1

    print(f"총 {saved}개 페이지 덤프 완료. 폴더: {DUMPS_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
