"""안전 테스트: 본인 계정을 본인 현재 등급으로 다시 설정.

목적:
  - MemberAdminAdapter.bulk_apply 가 실제로 사이트에 도달하는지
  - 사이트 응답을 성공/실패로 정확히 판정하는지
  - 본인 등급을 그대로 덮어쓰므로 **사이트에 아무 변화 없음**

사용:
    python tools/self_test_adapter.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from green_auth.authenticator import Authenticator
from green_auth.credentials import load_credentials
from config import ADMIN_MEMBER_URL
from core.crawler import MemberCrawler
from core.member_admin import MemberAdminAdapter
from core.member_parser import MemberListParser


def main() -> int:
    creds = load_credentials()
    if not creds:
        print("NO CREDENTIALS — first run main.py once to save login.")
        return 1
    user_id, password = creds
    print(f"[1/4] 로그인 시도: {user_id}")

    auth = Authenticator()
    result = auth.authenticate(user_id, password)
    if not result.is_success:
        print(f"AUTH FAIL: {result.status} / {result.message}")
        return 1
    session = auth.session
    print(f"     OK ({result.message})")

    print("[2/4] 본인 회원 정보 조회 (admin.member.php 검색)")
    # 검색 파라미터로 빠르게 본인 1명만 조회
    import requests
    resp = session.get(
        ADMIN_MEMBER_URL,
        params={"sfl": "mb_id", "stx": user_id, "sop": "and"},
        timeout=20,
    )
    if not resp.ok:
        print(f"     HTTP {resp.status_code}")
        return 1

    parser = MemberListParser()
    members = parser.parse(resp.text)
    me = next((m for m in members if m.user_id == user_id), None)
    if me is None:
        # 검색 안 되면 크롤러로 전체 확인
        print("     검색 결과에 본인 없음 — 전체 크롤로 재시도")
        crawler = MemberCrawler(session, ADMIN_MEMBER_URL, parser=parser)
        all_members = crawler.fetch_all_members()
        me = next((m for m in all_members if m.user_id == user_id), None)
        if me is None:
            print(f"     본인 아이디 {user_id} 를 회원 목록에서 찾지 못함")
            return 1

    print(f"     현재 등급: {me.level} ({me.level_label})")
    print(f"     이름={me.name!r} 닉네임={me.nickname!r}")
    print(f"     최종접속={me.last_login_date} 접속수={me.login_count}")

    print("[3/4] 본인 등급 재설정 POST (no-op)")

    # 어댑터 사용 시 응답 전체를 잘라서 보관하므로, 원시 requests POST 로 직접 호출해 전체 응답 확보
    from config import HTTP_TIMEOUT, USER_AGENT
    payload = [
        ("cl", "green"),
        ("sst", "cl_datetime"),
        ("sod", "desc"),
        ("sfl", ""),
        ("stx", ""),
        ("page", "1"),
        ("token", ""),
        ("act_button", "등급변경"),
        (f"cl_level[{user_id}]", str(me.level)),
    ]
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": ADMIN_MEMBER_URL,
    }
    resp_post = session.post(
        ADMIN_MEMBER_URL,
        data=payload,
        headers=headers,
        timeout=HTTP_TIMEOUT,
        allow_redirects=True,
    )

    # 전체 응답을 파일로 저장
    from datetime import datetime
    out_path = os.path.join(ROOT, "data", "dumps", f"post_resp_{datetime.now():%Y%m%d_%H%M%S}.html")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(resp_post.text or "")

    body = resp_post.text or ""
    print(f"     status   : {resp_post.status_code}")
    print(f"     history  : {[(r.status_code, r.url) for r in resp_post.history]}")
    print(f"     final_url: {resp_post.url}")
    print(f"     body len : {len(body)}")
    print(f"     saved to : {out_path}")

    print()
    print("[4/4] 본문 마커 분석")
    markers = {
        "canonical login.php": 'canonical" href="https://www.sorisem.net/bbs/login.php' in body,
        "g5_is_member=1": 'g5_is_member = "1"' in body,
        "g5_is_member=0": 'g5_is_member = "0"' in body,
        "g5_is_admin (non-empty)": 'g5_is_admin  = "1"' in body,
        "동호회 회원 관리": "동호회 회원 관리" in body,
        "fmemberlist form": 'name="fmemberlist"' in body or 'id="fmemberlist"' in body,
        "권한 없음": "권한이 없습니다" in body or "접근 권한" in body,
        "alert JS": "alert(" in body[:3000],
        "로그인 form": 'name="mb_id"' in body and 'name="mb_password"' in body,
    }
    for k, v in markers.items():
        print(f"     {'YES' if v else 'no ':>4}  {k}")

    # 사이트 실제 재확인
    print()
    print("[재확인] 사이트 재조회 - 본인 등급 변화 여부")
    try:
        resp2 = session.get(
            ADMIN_MEMBER_URL,
            params={"sfl": "mb_id", "stx": user_id, "sop": "and"},
            timeout=20,
        )
        members2 = parser.parse(resp2.text)
        me2 = next((m for m in members2 if m.user_id == user_id), None)
        if me2 is None:
            print("     [경고] 본인 재조회 실패")
        else:
            same = me2.level == me.level
            tag = "OK: 변경 없음" if same else "[경고] 등급이 바뀜"
            print(f"     변경 후 등급: {me2.level} ({me2.level_label})  {tag}")
    except Exception as e:
        print(f"     재확인 실패: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
