"""green3 '우리들의 이야기' 게시판 구조 + 회원별 글 수 카운팅 방법 탐색."""
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
    (f"{SORISEM_BASE_URL}/bbs/board.php?bo_table=green3&cl=green", "green3_page1"),
    (f"{SORISEM_BASE_URL}/bbs/board.php?bo_table=green3&cl=green&sfl=mb_id&stx=anycall", "green3_mbid_anycall"),
    (f"{SORISEM_BASE_URL}/bbs/board.php?bo_table=green3&cl=green&sfl=mb_id&stx=zzz", "green3_mbid_zzz"),
    (f"{SORISEM_BASE_URL}/bbs/new.php?mb_id=anycall", "new_mbid_anycall"),
    (f"{SORISEM_BASE_URL}/bbs/new.php?mb_id=anycall&bo_table=green3", "new_mbid_anycall_green3"),
    (f"{SORISEM_BASE_URL}/bbs/board.php?bo_table=green33&cl=green&sfl=mb_id&stx=anycall", "green33_mbid_anycall"),
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
        path = os.path.join(DUMPS_DIR, f"g3_{ts}_{name}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(resp.text or "")
        # 간단 마커 분석
        body = resp.text or ""
        markers = []
        import re
        m = re.search(r"전체\s*(\d+)\s*건|총\s*(\d+)\s*건|\(\s*(\d+)\s*\)", body)
        if m:
            markers.append(f"count_hit={m.group(0)!r}")
        if "subject" in body.lower():
            markers.append("has_subject")
        markers_str = " ".join(markers)
        print(f"  {name}  status={resp.status_code}  bytes={len(body)}  {markers_str}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
