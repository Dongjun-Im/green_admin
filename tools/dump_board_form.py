"""게시판 관리·글쓰기 페이지 HTML 덤프 (게시판 GUI 구현 전 분석용).

사용법:
    python tools/dump_board_form.py                 # green3, green9 덤프
    python tools/dump_board_form.py green3 green9 free   # 지정한 게시판들 덤프

green_auth.run_authentication() 으로 로그인한 뒤, 각 게시판에 대해 세 페이지를
data/dumps/ 에 저장한다:
    adm_board_form_<bo>.html   — 게시판 관리(설정) 폼
    write_<bo>.html            — 글쓰기 폼 (공지 체크박스·숨김 토큰 확인용)
    board_<bo>.html            — 게시판 목록 (공지글이 어떻게 표시되는지)

이 파일들을 보고 폼 필드·POST 대상 URL·토큰 필드명을 확정해 GUI 를 만든다.
"""
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import wx  # noqa: E402

from config import DUMPS_DIR, SORISEM_BASE_URL  # noqa: E402
from green_auth import run_authentication  # noqa: E402


DEFAULT_BOARDS = ["green3", "green9"]


def _save(session, url: str, path: str) -> bool:
    try:
        resp = session.get(url, timeout=20)
    except Exception as e:
        print(f"  요청 실패 {url} -> {e}")
        return False
    if not resp.ok:
        print(f"  HTTP {resp.status_code}: {url}")
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(resp.text)
    print(f"  저장: {path} ({len(resp.text)} bytes)")
    return True


def main() -> int:
    boards = sys.argv[1:] or DEFAULT_BOARDS

    app = wx.App(False)
    auth = run_authentication("게시판 페이지 덤프 도구")
    if auth is None:
        print("로그인 실패 또는 취소")
        return 1
    session = auth.session

    os.makedirs(DUMPS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    base = SORISEM_BASE_URL.rstrip("/")
    saved = 0
    for bo in boards:
        print(f"[{bo}]")
        targets = [
            (f"{base}/skin/board/ar.common/adm.board_form.php?bo_table={bo}",
             f"{ts}_adm_board_form_{bo}.html"),
            (f"{base}/bbs/write.php?bo_table={bo}&cl=green",
             f"{ts}_write_{bo}.html"),
            (f"{base}/bbs/board.php?bo_table={bo}&cl=green",
             f"{ts}_board_{bo}.html"),
        ]
        for url, fname in targets:
            if _save(session, url, os.path.join(DUMPS_DIR, fname)):
                saved += 1

    print(f"\n총 {saved}개 파일 덤프 완료. 폴더: {DUMPS_DIR}")
    print("이 폴더의 HTML 파일들을 첨부해 주시면 GUI 를 만들겠습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
