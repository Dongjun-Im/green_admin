"""메일 발송 응답 구조 파악용 테스트.

현재 로그인된 계정(anycall)으로 본인에게 메일을 1건 실제 발송해
응답 본문을 파일로 저장한다. 본인에게 보내는 거라 외부 영향 없음.
"""
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from green_auth.authenticator import Authenticator
from green_auth.credentials import load_credentials
from config import DUMPS_DIR, HTTP_TIMEOUT, MAIL_WRITE_URL, USER_AGENT


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

    # 본인에게 메일 1건 실제 발송
    data = {
        "reply": "0",
        "cl": "green",
        "receivers": user_id,
        "ms_subject": f"[TEST] 응답 구조 확인 {datetime.now():%H:%M:%S}",
        "ms_content": "이 메일은 응답 구조 파악을 위한 테스트입니다. 자동 생성됨.",
    }
    files = {"ms_file[]": ("", b"", "application/octet-stream")}
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": MAIL_WRITE_URL,
    }

    print(f"sending to: {user_id}")
    resp = session.post(
        MAIL_WRITE_URL,
        data=data,
        files=files,
        headers=headers,
        timeout=HTTP_TIMEOUT * 2,
        allow_redirects=True,
    )

    print(f"status    : {resp.status_code}")
    print(f"history   : {[(r.status_code, r.url) for r in resp.history]}")
    print(f"final url : {resp.url}")
    print(f"body len  : {len(resp.text or '')}")

    # 전체 저장
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(DUMPS_DIR, f"mail_resp_{ts}.html")
    os.makedirs(DUMPS_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(resp.text or "")
    print(f"saved     : {path}")

    body = resp.text or ""
    print()
    print("=== 본문 앞 2000자 ===")
    print(body[:2000])
    print("=" * 60)

    print()
    print("=== 마커 검사 ===")
    markers = {
        "쪽지가 발송": "쪽지가 발송" in body,
        "메일이 발송": "메일이 발송" in body,
        "메시지를 발송": "메시지를 발송" in body,
        "전송되었습니다": "전송되었습니다" in body,
        "완료": "완료" in body[:500],
        "오류": "오류" in body[:500],
        "실패": "실패" in body[:500],
        "권한": "권한" in body[:500],
        "inbox (url)": "inbox" in resp.url,
        "message (url)": "message" in resp.url,
        "write.php (url)": "write.php" in resp.url,
        "g5_is_member=1": 'g5_is_member = "1"' in body,
    }
    for k, v in markers.items():
        print(f"  {'YES' if v else 'no ':>4}  {k}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
