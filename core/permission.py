"""동호회 관리자 권한 검사.

판별 전략:
  admin.member.php?cl=green 페이지에 접근해서
  - "동호회 회원 관리" 텍스트가 있고
  - <form name="fmemberlist"> 폼이 있으면
  → 동호회관리자로 판정.

일반 회원이 같은 URL 에 접근하면 g5_is_admin 비어있고 form 도 없으므로 거부.
"""
from __future__ import annotations

from typing import Tuple

import requests
from bs4 import BeautifulSoup

from config import ADMIN_MEMBER_URL, HTTP_TIMEOUT


def admin_permission_check(
    session: requests.Session, user_id: str
) -> Tuple[bool, str]:
    try:
        resp = session.get(ADMIN_MEMBER_URL, timeout=HTTP_TIMEOUT, allow_redirects=True)
    except requests.exceptions.RequestException as e:
        return False, f"네트워크 오류로 권한을 확인할 수 없습니다: {e}"

    if not resp.ok:
        return False, f"관리자 페이지 접근 실패 (HTTP {resp.status_code})"

    text = resp.text or ""

    # 명시 거부
    if "권한이 없습니다" in text:
        return False, "동호회관리자 권한이 필요합니다."

    # 강한 마커: 동호회 회원 관리 + 일괄 폼
    has_title = "동호회 회원 관리" in text
    soup = BeautifulSoup(text, "lxml")
    has_form = soup.find("form", id="fmemberlist") is not None or soup.find(
        "form", attrs={"name": "fmemberlist"}
    ) is not None

    if has_title and has_form:
        return True, "동호회관리자 권한이 확인되었습니다."

    # 약한 마커: cl_level select 가 보이면 관리자
    if soup.find("select", attrs={"name": lambda v: v and v.startswith("cl_level[")}):
        return True, "동호회관리자 권한이 확인되었습니다."

    return (
        False,
        "동호회관리자 권한이 필요합니다. 이 프로그램은 초록등대 동호회 관리자만 사용할 수 있습니다.",
    )
