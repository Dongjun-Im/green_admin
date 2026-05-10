"""ActivityCounter — since 모드(날짜 필터) 단위 테스트.

빠른 경로(전체 카운트)는 사이트 호출이 필요해 통합 테스트가 어렵고,
이 파일은 since 도입으로 새로 추가된 행 단위 카운트 / 날짜 파싱만 회귀 보호.
"""
from __future__ import annotations

from datetime import date

import pytest

from core.activity_counter import (
    _count_rows_since,
    parse_board_date,
)


# ---------- parse_board_date ----------

def test_parse_board_date_full_dash():
    assert parse_board_date("2026-06-15") == date(2026, 6, 15)


def test_parse_board_date_full_dot():
    assert parse_board_date("2026.06.15") == date(2026, 6, 15)


def test_parse_board_date_short_year():
    assert parse_board_date("26-06-15") == date(2026, 6, 15)
    assert parse_board_date("26.06.15") == date(2026, 6, 15)


def test_parse_board_date_month_day_uses_today_year():
    today = date(2026, 7, 1)
    assert parse_board_date("06-15", today=today) == date(2026, 6, 15)
    assert parse_board_date("6.15", today=today) == date(2026, 6, 15)


def test_parse_board_date_time_only_returns_today():
    today = date(2026, 6, 15)
    assert parse_board_date("12:34", today=today) == today
    assert parse_board_date("9:05", today=today) == today


def test_parse_board_date_yesterday():
    today = date(2026, 6, 15)
    assert parse_board_date("어제", today=today) == date(2026, 6, 14)


def test_parse_board_date_invalid():
    assert parse_board_date("") is None
    assert parse_board_date("아무말") is None
    assert parse_board_date("2026-13-01") is None


# ---------- _count_rows_since ----------

def _row_html(rows: list[tuple[str, str]]) -> str:
    """(td_num, td_date) 튜플 리스트로 td_num + td_date 행만 있는 간단 HTML 생성."""
    body_rows = "".join(
        f'<tr><td class="td_num">{n}</td><td class="td_date">{d}</td></tr>'
        for n, d in rows
    )
    return (
        "<html><body>"
        '<form id="fboardlist"><table><tbody>'
        f"{body_rows}"
        "</tbody></table></form>"
        "</body></html>"
    )


def test_count_rows_since_all_after():
    """모든 행이 since 이상이면 행 전부 카운트, found_older=False."""
    html = _row_html([("3", "2026-08-01"), ("2", "2026-07-15"), ("1", "2026-06-02")])
    count, found_older, parsed, more = _count_rows_since(html, date(2026, 6, 1))
    assert count == 3
    assert found_older is False
    assert parsed is True
    assert more is False  # 페이지 링크 없음


def test_count_rows_since_stops_when_older_row_found():
    """오래된 행을 만나면 그 시점에서 멈추고 found_older=True."""
    html = _row_html([
        ("3", "2026-07-15"), ("2", "2026-06-02"),
        ("1", "2026-05-31"),  # since 미만
        ("0", "2025-12-01"),
    ])
    count, found_older, parsed, more = _count_rows_since(html, date(2026, 6, 1))
    assert count == 2
    assert found_older is True


def test_count_rows_since_no_tbody():
    """빈 페이지 — 0건, found_older=True (계속 다음 페이지 받을 필요 없음)."""
    html = "<html><body></body></html>"
    count, found_older, parsed, more = _count_rows_since(html, date(2026, 6, 1))
    assert count == 0
    assert found_older is True
    assert parsed is False


def test_count_rows_since_dates_unparseable():
    """날짜 셀이 못 알아볼 텍스트만 들어있으면 parsed=False — 호출자가 재시도하도록."""
    html = (
        "<html><body><form id='fboardlist'><table><tbody>"
        "<tr><td class='td_num'>1</td><td class='td_date'>알 수 없음</td></tr>"
        "</tbody></table></form></body></html>"
    )
    count, found_older, parsed, more = _count_rows_since(html, date(2026, 6, 1))
    assert count == 0
    assert parsed is False


def test_count_rows_since_detects_more_pages():
    """pg_next 링크가 있으면 more_pages=True."""
    html = (
        "<html><body><form id='fboardlist'><table><tbody>"
        "<tr><td class='td_num'>3</td><td class='td_date'>2026-08-01</td></tr>"
        "</tbody></table></form>"
        "<a class='pg_next' href='?page=2'>다음</a>"
        "</body></html>"
    )
    count, found_older, parsed, more = _count_rows_since(html, date(2026, 6, 1))
    assert count == 1
    assert found_older is False
    assert more is True
