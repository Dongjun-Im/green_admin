"""회원 목록 크롤러: 페이지네이션 순회 + parser 호출."""
from __future__ import annotations

from typing import Callable, Optional

import requests

from config import HTTP_TIMEOUT
from core.member_parser import EmptyParseError, MemberListParser
from core.models import Member


ProgressCB = Callable[[int, int], None]


class MemberCrawler:
    MAX_PAGES = 200

    def __init__(
        self,
        session: requests.Session,
        base_url: str,
        parser: Optional[MemberListParser] = None,
    ) -> None:
        self.session = session
        self.base_url = base_url
        self.parser = parser or MemberListParser()

    def fetch_all_members(
        self, progress_cb: Optional[ProgressCB] = None
    ) -> list[Member]:
        """모든 페이지를 순회해 회원을 수집.

        v1.0 강건화:
          · `has_next` 검사(페이지 링크 단순 문자열 매치)는 사이트 마크업 변화에
            취약해 false negative 가 자주 발생. 이를 의존하지 않고 다음 두 조건
            으로만 종료한다.
              1) 페이지에 회원이 0명 (실제로 끝)
              2) 직전 페이지와 동일한 회원 ID 집합 (페이지 끝 반복)
        """
        all_members: list[Member] = []
        seen_ids: set[str] = set()
        prev_page_ids: set[str] = set()

        for page in range(1, self.MAX_PAGES + 1):
            if progress_cb:
                try:
                    progress_cb(page, -1)
                except Exception:
                    pass

            members, _has_next = self.fetch_page(page)

            if not members:
                break

            current_ids = {m.user_id for m in members}
            # 같은 페이지가 반복되면 종료 (마지막 페이지를 사이트가 반복 반환)
            if current_ids and current_ids == prev_page_ids:
                break
            prev_page_ids = current_ids

            new_in_page = 0
            for m in members:
                if m.user_id in seen_ids:
                    continue
                seen_ids.add(m.user_id)
                all_members.append(m)
                new_in_page += 1

            # 새 회원이 0명이면 페이지 반복으로 간주 (이중 안전망)
            if new_in_page == 0:
                break

        if not all_members:
            raise EmptyParseError(
                "회원 목록을 분석하지 못했습니다. "
                "사이트 구조가 변경되었을 수 있습니다. "
                "도움말 메뉴의 '관리자 페이지 HTML 덤프'를 실행해 개발자에게 전달해 주세요."
            )

        return all_members

    def fetch_page(self, page: int) -> tuple[list[Member], bool]:
        sep = "&" if "?" in self.base_url else "?"
        url = self.base_url if page == 1 else f"{self.base_url}{sep}page={page}"

        try:
            resp = self.session.get(url, timeout=HTTP_TIMEOUT)
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"네트워크 오류: {e}") from e

        if not resp.ok:
            return [], False

        members = self.parser.parse(resp.text)
        has_next = self._has_next_page(resp.text, page)
        return members, has_next

    def _has_next_page(self, html: str, current_page: int) -> bool:
        next_marker = f"page={current_page + 1}"
        return next_marker in html
