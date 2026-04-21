"""회원의 게시물 수를 외부 검색 API 로 조회하는 보조 모듈.

회원 목록 페이지에 게시물 수 컬럼이 없거나 파서가 추출하지 못한 경우,
사이트의 통합 검색(/bbs/search.php?sfl=mb_id&stx=...) 결과 페이지에서
"검색결과 N건" 또는 paginate 정보로 카운트를 추정한다.

⚠ 회원 수 × 1회 호출이 발생하므로 회원이 많을 경우 느릴 수 있음.
   - 기본 동작은 회원 목록 페이지의 post_count 우선, 없을 때만 폴백.
   - 폴백 호출 사이에 약간의 슬립을 두어 사이트 부하 최소화.
"""
from __future__ import annotations

import re
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

from config import HTTP_TIMEOUT, SORISEM_BASE_URL


SEARCH_URL = f"{SORISEM_BASE_URL}/bbs/search.php"
_COUNT_PATTERNS = (
    re.compile(r"검색\s*결과\s*[:：]?\s*([0-9,]+)"),
    re.compile(r"전체\s*[:：]?\s*([0-9,]+)\s*건"),
    re.compile(r"총\s*([0-9,]+)\s*건"),
    re.compile(r"\(\s*([0-9,]+)\s*\)"),
)


class PostCountResolver:
    def __init__(
        self,
        session: requests.Session,
        delay_sec: float = 0.3,
    ) -> None:
        self.session = session
        self.delay_sec = delay_sec

    def fetch(self, user_id: str) -> Optional[int]:
        """검색 결과 페이지에서 게시물 수를 추출. 실패 시 None."""
        params = {
            "sfl": "mb_id",
            "stx": user_id,
            "sop": "and",
        }
        try:
            resp = self.session.get(
                SEARCH_URL, params=params, timeout=HTTP_TIMEOUT
            )
        except requests.exceptions.RequestException:
            return None

        if not resp.ok:
            return None

        # 1) 텍스트 패턴
        text = resp.text or ""
        for pat in _COUNT_PATTERNS:
            m = pat.search(text)
            if m:
                try:
                    return int(m.group(1).replace(",", ""))
                except ValueError:
                    continue

        # 2) 결과 행 직접 카운트 (페이지 1의 행 수만)
        try:
            soup = BeautifulSoup(text, "lxml")
            rows = soup.select("li, tr.list-tb, .search_result li, .post")
            cnt = sum(1 for r in rows if user_id.lower() in r.get_text(" ", strip=True).lower())
            if cnt > 0:
                return cnt
        except Exception:
            pass
        return None

    def fetch_many(self, user_ids: list[str], progress_cb=None) -> dict[str, int]:
        """여러 회원의 게시물 수를 순차적으로 조회."""
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
            if self.delay_sec > 0:
                time.sleep(self.delay_sec)
        return result
