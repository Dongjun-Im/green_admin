"""운영자 대시보드 — '오늘 해야 할 일' 요약 라인 생성 로직 (v1.2.11)."""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Optional

from core.models import Member


def _make_member(
    user_id: str, level: int = 5,
    *, last_login: Optional[date] = None, is_admin: bool = False,
) -> Member:
    return Member(
        user_id=user_id, name=user_id.upper(), nickname=f"닉_{user_id}",
        level=level, level_label="",
        last_login_date=last_login, is_admin=is_admin,
    )


def _tracker(
    *, backup_due=False, adjust_due=False, mvp_due=False,
    days_until_backup=30, days_until_adjustment=60, days_until_mvp=90,
):
    """build_dashboard_lines 가 호출하는 메서드만 흉내내는 가짜 tracker."""
    return SimpleNamespace(
        is_backup_due=lambda: backup_due,
        is_adjustment_due=lambda: adjust_due,
        is_mvp_due=lambda: mvp_due,
        next_backup_date=lambda: date.today() + timedelta(days=days_until_backup),
        next_adjustment_date=lambda: date.today() + timedelta(days=days_until_adjustment),
        next_mvp_date=lambda: date.today() + timedelta(days=days_until_mvp),
        days_until_backup=lambda: days_until_backup,
        days_until_adjustment=lambda: days_until_adjustment,
        days_until_mvp=lambda: days_until_mvp,
    )


# ---------------------------------------------------------------------------
# 1. 작업 도래 라인
# ---------------------------------------------------------------------------


def test_overdue_tasks_shown_with_warning():
    from core.dashboard_summary import build_dashboard_lines
    lines = build_dashboard_lines(
        tracker=_tracker(backup_due=True, adjust_due=True),
        members=[], pending_count=0, admin_user_id="rtgreen",
    )
    work_line = lines[0]
    assert "도래한 작업" in work_line
    assert "백업" in work_line
    assert "조정" in work_line


def test_upcoming_tasks_within_7_days_shown():
    from core.dashboard_summary import build_dashboard_lines
    lines = build_dashboard_lines(
        tracker=_tracker(days_until_backup=3),
        members=[], pending_count=0, admin_user_id="rtgreen",
    )
    work_line = lines[0]
    assert "임박" in work_line
    assert "백업 3일 후" in work_line


def test_no_tasks_due_or_upcoming():
    from core.dashboard_summary import build_dashboard_lines
    lines = build_dashboard_lines(
        tracker=_tracker(days_until_backup=30, days_until_adjustment=60, days_until_mvp=90),
        members=[], pending_count=0, admin_user_id="rtgreen",
    )
    assert "도래: 없음" in lines[0]


# ---------------------------------------------------------------------------
# 2. 가입 대기
# ---------------------------------------------------------------------------


def test_pending_count_none_shows_question_mark():
    from core.dashboard_summary import build_dashboard_lines
    lines = build_dashboard_lines(
        tracker=_tracker(),
        members=None, pending_count=None, admin_user_id="rtgreen",
    )
    assert any("신규 가입 대기: ?" in l for l in lines)


def test_pending_count_positive_shows_warning():
    from core.dashboard_summary import build_dashboard_lines
    lines = build_dashboard_lines(
        tracker=_tracker(),
        members=[], pending_count=3, admin_user_id="rtgreen",
    )
    assert any("신규 가입 대기: 3명" in l for l in lines)


def test_pending_count_zero_shows_none():
    from core.dashboard_summary import build_dashboard_lines
    lines = build_dashboard_lines(
        tracker=_tracker(),
        members=[], pending_count=0, admin_user_id="rtgreen",
    )
    assert any("신규 가입 대기: 없음" in l for l in lines)


# ---------------------------------------------------------------------------
# 3. 장기미접속 후보
# ---------------------------------------------------------------------------


def test_inactive_candidates_counted_only_for_eligible_levels():
    from core.dashboard_summary import build_dashboard_lines
    cutoff = date(2026, 1, 1)
    members = [
        _make_member("u1", 5, last_login=date(2025, 6, 1)),         # 일반, 미접속 — 포함
        _make_member("rtgreen", 5, last_login=date(2025, 6, 1)),     # 본인 — 제외
        _make_member("boss", 9, last_login=date(2025, 6, 1), is_admin=True),  # 관리자 — 제외
        _make_member("honor", 8, last_login=date(2025, 6, 1)),       # 명예 (LEVEL_TRANSITIONS 외) — 제외
        _make_member("recent", 5, last_login=date(2026, 4, 1)),      # 최근 접속 — 제외
        _make_member("u2", 5, last_login=date(2024, 12, 1)),         # 일반, 미접속 — 포함
    ]
    lines = build_dashboard_lines(
        tracker=_tracker(),
        members=members, pending_count=0, admin_user_id="rtgreen",
        inactivity_cutoff=cutoff,
    )
    # u1, u2 만 카운트 → 2명
    inactive_line = next(l for l in lines if "장기미접속" in l)
    assert "약 2명" in inactive_line


def test_inactive_candidates_zero_shows_none():
    from core.dashboard_summary import build_dashboard_lines
    lines = build_dashboard_lines(
        tracker=_tracker(),
        members=[_make_member("u", 5, last_login=date(2026, 4, 1))],
        pending_count=0, admin_user_id="rtgreen",
        inactivity_cutoff=date(2026, 1, 1),
    )
    assert any("장기미접속 후보: 없음" in l for l in lines)


def test_no_member_cache_shows_help_hint():
    """회원 캐시가 없을 때는 Ctrl+F 안내 한 줄이 추가됨."""
    from core.dashboard_summary import build_dashboard_lines
    lines = build_dashboard_lines(
        tracker=_tracker(),
        members=None, pending_count=None, admin_user_id="rtgreen",
    )
    assert any("Ctrl+F" in l for l in lines)


# ---------------------------------------------------------------------------
# 4. 안전성 — tracker 메서드가 던져도 죽지 않음
# ---------------------------------------------------------------------------


def test_tracker_method_exception_does_not_crash():
    from core.dashboard_summary import build_dashboard_lines

    bad = SimpleNamespace(
        is_backup_due=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        is_adjustment_due=lambda: False,
        is_mvp_due=lambda: False,
        next_backup_date=lambda: date.today(),
        next_adjustment_date=lambda: date.today(),
        next_mvp_date=lambda: date.today(),
        days_until_backup=lambda: 30,
        days_until_adjustment=lambda: 60,
        days_until_mvp=lambda: 90,
    )
    # 죽지 않고 무언가 라인을 돌려준다.
    lines = build_dashboard_lines(
        tracker=bad,
        members=[], pending_count=0, admin_user_id="rtgreen",
    )
    assert lines  # 빈 리스트가 아님
