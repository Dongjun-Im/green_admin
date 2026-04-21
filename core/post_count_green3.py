"""'우리들의 이야기' (green3) 게시판의 회원별 글 수 조회.

방법:
    GET /bbs/board.php?bo_table=green3&cl=green&sfl=mb_id&stx=USER_ID

사이트가 검색 결과의 첫 행에 "글 전체 순번" 을 td.td_num 으로 표시하는데,
첫 행의 번호 = 해당 회원의 게시판 내 전체 글 수 (역순 정렬). 이걸 파싱한다.

예시: anycall 검색 → 첫 행 td_num = 106 → 회원 anycall 의 글 106개.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

import requests
from bs4 import BeautifulSoup

from config import GREEN3_SEARCH_URL, HTTP_TIMEOUT


ProgressCB = Callable[[int, int], None]


class Green3PostCounter:
    def __init__(self, session: requests.Session) -> None:
        self.session = session

    def fetch(self, user_id: str) -> Optional[int]:
        """단일 회원의 글 수 조회. 실패 시 None."""
        try:
            resp = self.session.get(
                GREEN3_SEARCH_URL,
                params={"sfl": "mb_id", "stx": user_id},
                timeout=HTTP_TIMEOUT,
            )
        except requests.exceptions.RequestException:
            return None
        if not resp.ok:
            return None
        return self._parse_count(resp.text or "", user_id)

    def _parse_count(self, html: str, user_id: str) -> Optional[int]:
        soup = BeautifulSoup(html, "lxml")

        # 1) 결과 테이블에서 첫 번째 td.td_num 의 텍스트(숫자)를 찾는다.
        #    이 게시판은 가장 최근 글이 첫 행에 오고, td_num 이 "해당 회원의 N번째 글" 순번.
        form = (
            soup.find("form", id="fboardlist")
            or soup.find("form", attrs={"name": "fboardlist"})
            or soup.find("table")
        )
        scope = form or soup

        first_num_td = scope.find("td", class_=lambda c: c and "td_num" in c)
        if first_num_td is not None:
            txt = first_num_td.get_text(" ", strip=True)
            m = re.search(r"\d+", txt)
            if m:
                try:
                    return int(m.group(0))
                except ValueError:
                    pass

        # 2) 폴백: 검색 결과 행 수를 직접 센다.
        tbody = scope.find("tbody")
        if tbody is not None:
            rows = [tr for tr in tbody.find_all("tr") if tr.find("td") is not None]
            if rows:
                # 단, 첫 페이지만이라 전체 수는 아닐 수 있음. 그래도 폴백.
                # 페이지네이션이 있으면 맨끝 페이지 번호 × 15 + 마지막 페이지 행수 추정
                pg_end = scope.find("a", class_=re.compile(r"pg_end"))
                if pg_end is not None and pg_end.get("href"):
                    mp = re.search(r"page=(\d+)", pg_end["href"])
                    if mp:
                        last_page = int(mp.group(1))
                        # 보수적으로 (last_page - 1) * 15 + 현재 페이지 행수? 현재 페이지는 1페이지라 15
                        # 차라리 마지막 페이지를 직접 조회
                        return None  # 호출측에서 재시도 안내
                return len(rows)

        return None

    def fetch_many(
        self,
        user_ids: list[str],
        progress_cb: Optional[ProgressCB] = None,
    ) -> dict[str, int]:
        """여러 회원 순차 조회. 실패한 회원은 결과 dict 에서 제외."""
        result: dict[str, int] = {}
        total = len(user_ids)
        for idx, uid in enumerate(user_ids, start=1):
            if progress_cb:
                try:
                    progress_cb(idx, total)
                except Exception:
                    pass
            n = self.fetch(uid)
            if n is not None:
                result[uid] = n
        return result
