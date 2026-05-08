"""게시판별 회원 활동(글+댓글) 카운터 — v0.5.

게시판 한 곳에 대해:
  · 글 수      : board.php?bo_table=X&sfl=mb_id&stx=USER_ID
                  (기존 Green3PostCounter 와 동일한 패턴)
  · 댓글 수    : board.php?bo_table=X&sfl=mb_id&stx=USER_ID&search_type=comment
                  (g5 표준. 사이트가 지원하지 않으면 None 으로 폴백)

회원 한 명에 대해 여러 게시판을 합산한 활동점수도 제공한다.
점수식:  score = sum(글수) + sum(댓글수) × COMMENT_WEIGHT

⚠ 댓글 검색은 사이트 호환성에 따라 작동하지 않을 수 있다. 그 경우
   comment_count=0 으로 잡히고 활동점수는 사실상 글 수만 반영된다.
   첫 실행 후 통계가 0 으로만 잡히면 사이트가 다른 패턴을 쓰는 것이므로
   tools/ 진단 스크립트로 게시판 검색 응답을 덤프해 분석한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

import requests
from bs4 import BeautifulSoup

from config import (
    ACTIVITY_BOARDS,
    COMMENT_WEIGHT,
    HTTP_TIMEOUT,
    SORISEM_BASE_URL,
)


ProgressCB = Callable[[int, int], None]


@dataclass
class BoardActivity:
    """한 게시판에서 한 회원의 글/댓글 카운트."""
    board: str
    posts: int = 0
    comments: int = 0


@dataclass
class MemberActivity:
    """한 회원의 게시판별 활동 + 합산 점수."""
    user_id: str
    by_board: dict[str, BoardActivity] = field(default_factory=dict)

    @property
    def total_posts(self) -> int:
        return sum(b.posts for b in self.by_board.values())

    @property
    def total_comments(self) -> int:
        return sum(b.comments for b in self.by_board.values())

    @property
    def score(self) -> float:
        return self.total_posts + self.total_comments * COMMENT_WEIGHT

    def display(self) -> str:
        return (
            f"{self.user_id} / 글 {self.total_posts}건 / "
            f"댓글 {self.total_comments}건 / 활동점수 {self.score:.1f}"
        )


class ActivityCounter:
    """글·댓글 카운트 단일 진입점.

    fetch_member(user_id, boards) → MemberActivity
    fetch_many(user_ids, boards) → list[MemberActivity]
    """

    def __init__(
        self,
        session: requests.Session,
        boards: tuple[str, ...] = ACTIVITY_BOARDS,
    ) -> None:
        self.session = session
        self.boards = boards

    # ---------- 단일 회원 ----------

    def fetch_member(
        self, user_id: str, boards: Optional[tuple[str, ...]] = None
    ) -> MemberActivity:
        ma = MemberActivity(user_id=user_id)
        for bo in boards or self.boards:
            posts = self._fetch_posts(user_id, bo)
            comments = self._fetch_comments(user_id, bo)
            ma.by_board[bo] = BoardActivity(
                board=bo,
                posts=posts or 0,
                comments=comments or 0,
            )
        return ma

    def fetch_many(
        self,
        user_ids: list[str],
        boards: Optional[tuple[str, ...]] = None,
        progress_cb: Optional[ProgressCB] = None,
    ) -> list[MemberActivity]:
        out: list[MemberActivity] = []
        total = len(user_ids)
        for idx, uid in enumerate(user_ids, start=1):
            if progress_cb:
                try:
                    progress_cb(idx, total)
                except Exception:
                    pass
            out.append(self.fetch_member(uid, boards=boards))
        return out

    # ---------- 글 ----------

    def _fetch_posts(self, user_id: str, bo_table: str) -> Optional[int]:
        """기존 Green3PostCounter 와 동일한 td_num 패턴."""
        url = f"{SORISEM_BASE_URL}/bbs/board.php"
        params = {
            "bo_table": bo_table,
            "cl": "green",
            "sfl": "mb_id",
            "stx": user_id,
        }
        try:
            resp = self.session.get(url, params=params, timeout=HTTP_TIMEOUT)
        except requests.exceptions.RequestException:
            return None
        if not resp.ok:
            return None
        return _parse_search_count(resp.text or "")

    # ---------- 댓글 ----------

    def _fetch_comments(self, user_id: str, bo_table: str) -> Optional[int]:
        """g5 표준 댓글 검색 패턴 시도.

        시도 순서:
          1) bo_table=X&search_type=comment&sfl=mb_id&stx=USER
          2) bo_table=X&sca=&sop=and&sfl=mb_id&stx=USER&search_type=comment
        둘 다 실패하면 None.
        """
        url = f"{SORISEM_BASE_URL}/bbs/board.php"
        for attempt in (
            {
                "bo_table": bo_table,
                "cl": "green",
                "search_type": "comment",
                "sfl": "mb_id",
                "stx": user_id,
            },
            {
                "bo_table": bo_table,
                "cl": "green",
                "search_type": "comment",
                "sca": "",
                "sop": "and",
                "sfl": "mb_id",
                "stx": user_id,
            },
        ):
            try:
                resp = self.session.get(url, params=attempt, timeout=HTTP_TIMEOUT)
            except requests.exceptions.RequestException:
                continue
            if not resp.ok:
                continue
            n = _parse_search_count(resp.text or "")
            if n is not None:
                return n
        return None


# ---------- 공통 파싱 ----------

_COUNT_TEXT_PATTERNS = (
    re.compile(r"검색\s*결과\s*[:：]?\s*([0-9,]+)\s*건"),
    re.compile(r"전체\s*[:：]?\s*([0-9,]+)\s*건"),
    re.compile(r"총\s*([0-9,]+)\s*건"),
)


def _parse_search_count(html: str) -> Optional[int]:
    """그누보드 g5 게시판 검색 결과에서 카운트 추출.

    1) 결과 페이지 헤더에 "검색결과 N건" 같은 텍스트가 있으면 그걸 사용.
    2) 첫 행의 td.td_num (글 일련번호) — Green3PostCounter 와 동일한 트릭.
    3) 폴백: 결과 행 수.
    """
    if not html:
        return None

    # 1) 텍스트 카운트
    for pat in _COUNT_TEXT_PATTERNS:
        m = pat.search(html)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                pass

    soup = BeautifulSoup(html, "lxml")
    form = (
        soup.find("form", id="fboardlist")
        or soup.find("form", attrs={"name": "fboardlist"})
        or soup.find("table")
    )
    scope = form or soup

    # 2) 첫 행 td.td_num
    first_num_td = scope.find("td", class_=lambda c: c and "td_num" in c)
    if first_num_td is not None:
        txt = first_num_td.get_text(" ", strip=True)
        m = re.search(r"\d+", txt)
        if m:
            try:
                return int(m.group(0))
            except ValueError:
                pass

    # 3) 결과 행 수 폴백 (불완전 — 페이지네이션 무시)
    tbody = scope.find("tbody")
    if tbody is not None:
        rows = [tr for tr in tbody.find_all("tr") if tr.find("td") is not None]
        if rows:
            return len(rows)

    return None
