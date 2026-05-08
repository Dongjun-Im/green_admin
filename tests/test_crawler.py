"""크롤러 — 페이지 종료 조건 강건성 검증.

실제 HTTP 호출 대신 `MemberCrawler.fetch_page` 를 모킹해 종료 조건을 본다.
- has_next=False 라도 회원이 더 있으면 계속 진행해야 함 (v1.0)
- 빈 페이지/같은 페이지 반복으로만 종료
"""
from __future__ import annotations

from typing import Optional

import pytest


class _FakeCrawler:
    """fetch_page 만 오버라이드하는 크롤러 더블."""

    MAX_PAGES = 200

    def __init__(self, page_data: list[tuple[list, bool]]):
        # page_data[i] = (members on page i+1, has_next_flag)
        self.page_data = page_data
        self.pages_called: list[int] = []

    def fetch_page(self, page: int):
        self.pages_called.append(page)
        if page <= len(self.page_data):
            return self.page_data[page - 1]
        return ([], False)


def _make_member(uid: str):
    from core.models import Member
    return Member(user_id=uid, name=uid, nickname=uid, level=6, level_label="")


@pytest.fixture
def fetch_all():
    from core.crawler import MemberCrawler
    return MemberCrawler.fetch_all_members


def test_collects_all_pages_when_has_next_false_per_page(fetch_all):
    """has_next 가 항상 False 여도 회원이 있으면 계속 진행해야 한다 (v1.0 핵심)."""
    pages = [
        ([_make_member(f"a{i}") for i in range(15)], False),  # page 1: 15명
        ([_make_member(f"b{i}") for i in range(15)], False),  # page 2: 15명
        ([_make_member(f"c{i}") for i in range(10)], False),  # page 3: 10명
        ([], False),                                          # page 4: 끝
    ]
    crawler = _FakeCrawler(pages)
    members = fetch_all(crawler)
    assert len(members) == 40
    # 4페이지까지 호출
    assert crawler.pages_called == [1, 2, 3, 4]


def test_stops_on_empty_page(fetch_all):
    pages = [
        ([_make_member(f"a{i}") for i in range(20)], True),
        ([], True),
    ]
    crawler = _FakeCrawler(pages)
    members = fetch_all(crawler)
    assert len(members) == 20


def test_stops_on_repeated_page(fetch_all):
    """마지막 페이지를 사이트가 반복 반환할 때 종료."""
    page_a = [_make_member(f"a{i}") for i in range(15)]
    page_b = [_make_member(f"b{i}") for i in range(10)]
    pages = [
        (page_a, True),
        (page_b, True),
        (page_b, True),  # 반복 — 종료해야 함
        (page_b, True),
        (page_b, True),
    ]
    crawler = _FakeCrawler(pages)
    members = fetch_all(crawler)
    assert len(members) == 25
    # 페이지 3에서 멈춤 (반복 감지)
    assert crawler.pages_called[-1] in (3,)


def test_dedup_across_pages(fetch_all):
    """같은 user_id 가 여러 페이지에 나와도 한 번만 카운트."""
    pages = [
        ([_make_member("a"), _make_member("b")], True),
        ([_make_member("b"), _make_member("c")], True),
        ([], True),
    ]
    crawler = _FakeCrawler(pages)
    members = fetch_all(crawler)
    user_ids = [m.user_id for m in members]
    assert sorted(user_ids) == ["a", "b", "c"]


def test_empty_first_page_raises(fetch_all):
    from core.member_parser import EmptyParseError
    pages = [([], False)]
    crawler = _FakeCrawler(pages)
    with pytest.raises(EmptyParseError):
        fetch_all(crawler)
