"""게시판별 회원 활동(글+댓글) 카운터 — v0.5.

게시판 한 곳에 대해:
  · 글 수      : board.php?bo_table=X&sfl=mb_id&stx=USER_ID
                  (기존 Green3PostCounter 와 동일한 패턴)
  · 댓글 수    : board.php?bo_table=X&sfl=mb_id&stx=USER_ID&search_type=comment
                  (g5 표준. 사이트가 지원하지 않으면 None 으로 폴백)

회원 한 명에 대해 여러 게시판을 합산한 활동점수도 제공한다.
점수식:  score = sum(글수) + sum(댓글수) × COMMENT_WEIGHT

since 매개변수 (v1.1):
    fetch_member(user_id, since=date(2026,6,1)) 로 호출하면 검색 결과를
    페이지 순회하면서 행 작성일이 since 이상인 것만 카운트한다. 결과는
    날짜 desc 정렬을 가정하므로 since 미만 행을 만나면 그 페이지에서 종료.
    since=None 이면 기존 빠른 경로(전체 카운트 텍스트 파싱)를 사용한다.

⚠ 댓글 검색은 사이트 호환성에 따라 작동하지 않을 수 있다. 그 경우
   comment_count=0 으로 잡히고 활동점수는 사실상 글 수만 반영된다.
   첫 실행 후 통계가 0 으로만 잡히면 사이트가 다른 패턴을 쓰는 것이므로
   tools/ 진단 스크립트로 게시판 검색 응답을 덤프해 분석한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional

import requests
from bs4 import BeautifulSoup

from config import (
    ACTIVITY_BOARDS,
    COMMENT_WEIGHT,
    HTTP_TIMEOUT,
    SORISEM_BASE_URL,
)


# 페이지 순회 안전 상한 — 한 회원·게시판 조합당 최대 페이지 수.
# 모든 검색 결과가 since 이상이면 끝까지 가야 하므로 비정상 무한 루프 보호용.
_MAX_PAGES = 200


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
        self,
        user_id: str,
        boards: Optional[tuple[str, ...]] = None,
        *,
        since: Optional[date] = None,
    ) -> MemberActivity:
        ma = MemberActivity(user_id=user_id)
        for bo in boards or self.boards:
            posts = self._fetch_posts(user_id, bo, since=since)
            comments = self._fetch_comments(user_id, bo, since=since)
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
        *,
        since: Optional[date] = None,
    ) -> list[MemberActivity]:
        out: list[MemberActivity] = []
        total = len(user_ids)
        for idx, uid in enumerate(user_ids, start=1):
            if progress_cb:
                try:
                    progress_cb(idx, total)
                except Exception:
                    pass
            out.append(self.fetch_member(uid, boards=boards, since=since))
        return out

    # ---------- 글 ----------

    def _fetch_posts(
        self, user_id: str, bo_table: str, *, since: Optional[date] = None,
    ) -> Optional[int]:
        """since 가 None 이면 기존 td_num 빠른 경로, 아니면 페이지 순회 카운트."""
        base_params = {
            "bo_table": bo_table,
            "cl": "green",
            "sfl": "mb_id",
            "stx": user_id,
        }
        if since is None:
            return self._fetch_count_quick(base_params)
        return self._fetch_count_since(base_params, since)

    # ---------- 댓글 ----------

    def _fetch_comments(
        self, user_id: str, bo_table: str, *, since: Optional[date] = None,
    ) -> Optional[int]:
        """g5 표준 댓글 검색.

        since 가 None: 텍스트 카운트 빠른 경로. since 설정: 페이지 순회.
        검색 패턴이 사이트마다 다를 수 있어 두 가지 변형을 차례로 시도한다.
        """
        attempts = [
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
        ]
        for attempt in attempts:
            if since is None:
                n = self._fetch_count_quick(attempt)
            else:
                n = self._fetch_count_since(attempt, since)
            if n is not None:
                return n
        return None

    # ---------- 빠른 경로 (전체 카운트 텍스트) ----------

    def _fetch_count_quick(self, params: dict[str, str]) -> Optional[int]:
        url = f"{SORISEM_BASE_URL}/bbs/board.php"
        try:
            resp = self.session.get(url, params=params, timeout=HTTP_TIMEOUT)
        except requests.exceptions.RequestException:
            return None
        if not resp.ok:
            return None
        return _parse_search_count(resp.text or "")

    # ---------- since 경로 (페이지 순회) ----------

    def _fetch_count_since(
        self, base_params: dict[str, str], since: date,
    ) -> Optional[int]:
        """since 이후(포함) 작성된 행만 카운트.

        검색 결과는 작성일 desc 정렬을 가정. 한 페이지에서 since 미만 행을
        만나면 더 오래된 페이지를 가져올 필요 없이 누적 합을 반환한다.
        파싱 실패(날짜 컬럼 없음) 시 None — 호출자가 다른 변형을 시도한다.
        """
        url = f"{SORISEM_BASE_URL}/bbs/board.php"
        total = 0
        any_date_parsed = False
        for page in range(1, _MAX_PAGES + 1):
            params = dict(base_params)
            if page > 1:
                params["page"] = str(page)
            try:
                resp = self.session.get(url, params=params, timeout=HTTP_TIMEOUT)
            except requests.exceptions.RequestException:
                return None
            if not resp.ok:
                return None
            count, found_older, parsed, more = _count_rows_since(
                resp.text or "", since,
            )
            if parsed:
                any_date_parsed = True
            total += count
            if found_older or not more:
                break
        if not any_date_parsed:
            # 모든 페이지에서 날짜 파싱이 한 번도 안 됐으면 — 사이트 변종으로 보고
            # None 반환 → 호출자가 다른 검색 변형을 시도하거나 0 으로 떨어진다.
            return None
        return total


# ---------- 게시판 행 작성일 파싱 (since 모드) ----------

# 게시판 검색 결과의 td 작성일 후보 클래스
_DATE_TD_CLASSES = ("td_date", "td_datetime", "datetime", "wr_datetime")


def parse_board_date(text: str, today: Optional[date] = None) -> Optional[date]:
    """g5 게시판 작성일 셀 텍스트를 date 로.

    지원 형식:
        HH:MM           오늘 (시간만 표시)
        어제 / 오늘     today - 1 / today
        YYYY-MM-DD      절대일 (구분자 - / .)
        YY-MM-DD        2자리 연도 → 2000+
        MM-DD / MM.DD   당해년 (연도 생략)
    """
    if not text:
        return None
    today = today or date.today()
    s = text.strip()
    if not s:
        return None

    if re.fullmatch(r"\d{1,2}:\d{2}", s):
        return today
    if "어제" in s:
        return today - timedelta(days=1)
    if "오늘" in s:
        return today

    m = re.match(r"^(\d{2,4})[-./](\d{1,2})[-./](\d{1,2})", s)
    if m:
        y = int(m.group(1))
        if y < 100:
            y += 2000
        try:
            return date(y, int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})[-./](\d{1,2})$", s)
    if m:
        try:
            return date(today.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def _row_date(tr) -> Optional[date]:
    for cls in _DATE_TD_CLASSES:
        td = tr.find("td", class_=lambda c: c and cls in c)
        if td is not None:
            d = parse_board_date(td.get_text(" ", strip=True))
            if d is not None:
                return d
    # 폴백 — td 의 title 속성에 풀 날짜가 있으면 사용.
    for td in tr.find_all("td"):
        title = td.get("title") or ""
        if title:
            d = parse_board_date(title)
            if d is not None:
                return d
    return None


def _count_rows_since(
    html: str, since: date,
) -> tuple[int, bool, bool, bool]:
    """한 페이지에서 since 이상 행을 센다.

    Returns: (count, found_older, parsed_any, more_pages)
        count         — since 이상 행 수
        found_older   — since 미만 행을 만나 페이지 순회를 멈춰야 하는지
        parsed_any    — 한 행 이상에서 날짜 파싱 성공
        more_pages    — pg_next/pg_end 링크 존재 여부
    """
    if not html:
        return 0, True, False, False
    soup = BeautifulSoup(html, "lxml")
    form = (
        soup.find("form", id="fboardlist")
        or soup.find("form", attrs={"name": "fboardlist"})
        or soup.find("table")
    )
    scope = form or soup
    tbody = scope.find("tbody")
    if tbody is None:
        return 0, True, False, False

    count = 0
    found_older = False
    parsed_any = False
    for tr in tbody.find_all("tr"):
        if not tr.find("td"):
            continue
        d = _row_date(tr)
        if d is None:
            continue
        parsed_any = True
        if d >= since:
            count += 1
        else:
            found_older = True
            break

    # 페이지네이션 링크는 보통 form 바깥에 있어 soup 전체에서 탐색.
    more_pages = False
    pg_next = soup.find("a", class_=re.compile(r"pg_next|pg_end"))
    if pg_next is not None and pg_next.get("href"):
        more_pages = True
    return count, found_older, parsed_any, more_pages


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
