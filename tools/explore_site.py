"""사이트 탐색: 게시판 목록, 메일 폼, 게시물 검색 구조 파악.

사이트에 영향 없음. GET 요청만 수행하고 HTML을 덤프 폴더에 저장.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from datetime import datetime

from green_auth.authenticator import Authenticator
from green_auth.credentials import load_credentials
from config import SORISEM_BASE_URL, DUMPS_DIR


TARGETS = [
    # (URL, 저장명)
    (f"{SORISEM_BASE_URL}/plugin/ar.club/?cl=green", "club_home"),
    (f"{SORISEM_BASE_URL}/bbs/message.php", "message_inbox"),
    (f"{SORISEM_BASE_URL}/bbs/memo_form.php", "memo_form_noid"),
    (f"{SORISEM_BASE_URL}/message/write.php?mb_ids=zzz&cl=green", "message_write_form"),
    (f"{SORISEM_BASE_URL}/bbs/search.php?sfl=wr_name&stx=zzz&sop=and", "search_wrname"),
]


def main() -> int:
    creds = load_credentials()
    if not creds:
        print("NO CREDENTIALS")
        return 1
    user_id, password = creds
    print(f"login: {user_id}")
    auth = Authenticator()
    r = auth.authenticate(user_id, password)
    if not r.is_success:
        print(f"AUTH FAIL: {r.message}")
        return 1
    session = auth.session

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(DUMPS_DIR, exist_ok=True)

    for url, name in TARGETS:
        try:
            resp = session.get(url, timeout=20, allow_redirects=True)
        except Exception as e:
            print(f"  FAIL  {name}  {e}")
            continue
        path = os.path.join(DUMPS_DIR, f"explore_{ts}_{name}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(resp.text or "")
        print(f"  OK    {name}  status={resp.status_code}  bytes={len(resp.text)}  final={resp.url}")
        print(f"        saved: {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
