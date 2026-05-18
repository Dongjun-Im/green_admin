"""안내 메일 대상 추림 + 본문 템플릿 (v1.2.10).

ActivityCounter 는 가짜로 채워서 HTTP 호출 없이 검증.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import pytest

from core.models import Member


# ---------------------------------------------------------------------------
# 도우미
# ---------------------------------------------------------------------------


def _make_member(
    user_id: str,
    level: int = 5,
    *,
    last_login: Optional[date] = None,
    is_admin: bool = False,
    nickname: str = "",
) -> Member:
    return Member(
        user_id=user_id,
        name=user_id.upper(),
        nickname=nickname or f"닉_{user_id}",
        level=level,
        level_label="",
        last_login_date=last_login,
        is_admin=is_admin,
    )


class _FakeActivityCounter:
    """fetch_post_count 만 흉내내는 가짜. counts[user_id] = posts_since.

    raises_for 에 있는 user_id 는 RuntimeError 를 발생 — 조회 실패 흐름 테스트.
    """

    def __init__(self, counts: dict, raises_for=()):
        self.counts = counts
        self.raises_for = set(raises_for)
        self.calls: list[tuple] = []

    def fetch_post_count(self, user_id, board, *, since=None):
        self.calls.append((user_id, board, since))
        if user_id in self.raises_for:
            raise RuntimeError("simulated")
        return self.counts.get(user_id, 0)


# ---------------------------------------------------------------------------
# find_activity_nudge_targets
# ---------------------------------------------------------------------------


def test_activity_nudge_excludes_admin_self_and_non_adjustable_levels():
    from core.nudge_mail import find_activity_nudge_targets
    counter = _FakeActivityCounter(counts={})
    members = [
        _make_member("rtgreen", level=5, last_login=date(2026, 5, 1)),    # 본인 — 제외
        _make_member("boss", level=9, is_admin=True),                      # 관리자 — 제외
        _make_member("honor", level=8),                                    # 명예회원 (LEVEL_TRANSITIONS 외) — 제외
        _make_member("guest", level=3),                                    # 대기 — 제외
        _make_member("u", level=5),                                        # 일반회원 — 후보
    ]
    targets = find_activity_nudge_targets(
        members, counter, admin_user_id="rtgreen",
        today=date(2026, 5, 17),
    )
    # u 만 검사받았는지 (다른 회원은 fetch_post_count 호출조차 안 됨)
    user_ids_called = {c[0] for c in counter.calls}
    assert user_ids_called == {"u"}
    # u 는 글이 0 이라 대상에 포함
    assert [t.member.user_id for t in targets] == ["u"]


def test_activity_nudge_includes_only_zero_post_members():
    from core.nudge_mail import find_activity_nudge_targets
    counter = _FakeActivityCounter(counts={
        "active": 3,   # 6개월간 3건 — 대상 아님
        "edge": 1,     # 6개월간 1건 — 대상 아님
        "silent": 0,   # 6개월간 0건 — 대상
    })
    members = [
        _make_member("active", 5),
        _make_member("edge", 5),
        _make_member("silent", 5),
    ]
    targets = find_activity_nudge_targets(
        members, counter, admin_user_id="rtgreen",
        today=date(2026, 5, 17),
    )
    assert [t.member.user_id for t in targets] == ["silent"]
    assert targets[0].recent_post_count == 0


def test_activity_nudge_fetch_failure_safe_skip():
    """count is None 이면 대상에서 안전 제외 — 잘못 보내느니 보내지 않는다."""
    from core.nudge_mail import find_activity_nudge_targets
    counter = _FakeActivityCounter(counts={"u": 0}, raises_for=["u"])
    members = [_make_member("u", 5)]
    targets = find_activity_nudge_targets(
        members, counter, admin_user_id="rtgreen",
        today=date(2026, 5, 17),
    )
    assert targets == []


def test_activity_nudge_uses_since_filter(monkeypatch):
    """fetch_post_count 가 today - months 로 since 인자를 받는지."""
    from core.nudge_mail import find_activity_nudge_targets
    counter = _FakeActivityCounter(counts={"u": 0})
    members = [_make_member("u", 5)]
    find_activity_nudge_targets(
        members, counter, admin_user_id="rtgreen",
        today=date(2026, 5, 17), months=6,
    )
    assert counter.calls, "fetch_post_count 가 호출되지 않았다"
    user_id, board, since = counter.calls[0]
    # since = 2026-05-17 - 6 months = 2025-11-17
    assert since == date(2025, 11, 17)
    assert board == "green3"


def test_activity_nudge_history_skip_30days(tmp_path):
    """history 가 있고 최근 30일 이내 발송한 회원은 후보에서 제외 (HTTP 호출도 안 함)."""
    from core.nudge_history import KIND_ACTIVITY_NUDGE, NudgeHistoryStore
    from core.nudge_mail import find_activity_nudge_targets

    today = date(2026, 5, 17)
    h = NudgeHistoryStore(tmp_path / "h.json")
    # u1 = 10일 전 발송 (제외 대상), u2 = 60일 전 발송 (다시 대상)
    h.mark_sent("u1", KIND_ACTIVITY_NUDGE, when=today - timedelta(days=10))
    h.mark_sent("u2", KIND_ACTIVITY_NUDGE, when=today - timedelta(days=60))

    counter = _FakeActivityCounter(counts={"u1": 0, "u2": 0})
    members = [_make_member("u1", 5), _make_member("u2", 5)]
    targets = find_activity_nudge_targets(
        members, counter, admin_user_id="rtgreen",
        today=today, history=h, skip_within_days=30,
    )
    # u1 은 history 로 사전 제외 → fetch 도 안 부름. u2 는 카운트 조회 후 포함.
    assert [t.member.user_id for t in targets] == ["u2"]
    # HTTP 호출은 u2 만
    assert [c[0] for c in counter.calls] == ["u2"]


# ---------------------------------------------------------------------------
# find_inactive_warning_targets
# ---------------------------------------------------------------------------


def test_inactive_warning_picks_1_year_plus_only():
    from core.nudge_mail import find_inactive_warning_targets
    today = date(2026, 5, 17)
    members = [
        _make_member("recent", 5, last_login=date(2026, 4, 1)),           # 최근 — 제외
        _make_member("oneyear", 5, last_login=date(2025, 5, 17)),          # 정확히 1년 — 경계 포함
        _make_member("longgone", 5, last_login=date(2024, 12, 1)),         # 1년+ — 대상
    ]
    targets = find_inactive_warning_targets(
        members, admin_user_id="rtgreen", today=today,
    )
    user_ids = {t.member.user_id for t in targets}
    assert "oneyear" in user_ids
    assert "longgone" in user_ids
    assert "recent" not in user_ids


def test_inactive_warning_skips_admin_self_and_no_login_date():
    from core.nudge_mail import find_inactive_warning_targets
    today = date(2026, 5, 17)
    members = [
        _make_member("rtgreen", 5, last_login=date(2024, 1, 1)),           # 본인 — 제외
        _make_member("boss", level=9, is_admin=True),                      # 관리자 — 제외
        _make_member("noinfo", 5, last_login=None),                        # 접속일 미상 — 안전 제외
        _make_member("ok", 5, last_login=date(2024, 1, 1)),                # 1년+ — 대상
    ]
    targets = find_inactive_warning_targets(
        members, admin_user_id="rtgreen", today=today,
    )
    assert [t.member.user_id for t in targets] == ["ok"]


def test_inactive_warning_history_skip_30days(tmp_path):
    from core.nudge_history import KIND_INACTIVE_WARNING, NudgeHistoryStore
    from core.nudge_mail import find_inactive_warning_targets
    today = date(2026, 5, 17)
    h = NudgeHistoryStore(tmp_path / "h.json")
    h.mark_sent("u1", KIND_INACTIVE_WARNING, when=today - timedelta(days=5))
    h.mark_sent("u2", KIND_INACTIVE_WARNING, when=today - timedelta(days=45))

    members = [
        _make_member("u1", 5, last_login=date(2024, 1, 1)),
        _make_member("u2", 5, last_login=date(2024, 1, 1)),
    ]
    targets = find_inactive_warning_targets(
        members, admin_user_id="rtgreen", today=today,
        history=h, skip_within_days=30,
    )
    assert [t.member.user_id for t in targets] == ["u2"]


# ---------------------------------------------------------------------------
# 본문 템플릿
# ---------------------------------------------------------------------------


def test_template_activity_nudge_contains_nickname_and_months():
    from core.nudge_mail import template_activity_nudge
    m = _make_member("u", 5, nickname="동준")
    subject, body = template_activity_nudge(m, months=6)
    assert "동준" in body
    assert "6개월" in body
    assert "우리들의 이야기" in body
    assert "[초록등대]" in subject


def test_template_inactive_warning_contains_nickname_and_warning():
    from core.nudge_mail import template_inactive_warning
    m = _make_member("u", 5, nickname="동준", last_login=date(2024, 1, 1))
    subject, body = template_inactive_warning(m, months=12)
    assert "동준" in body
    assert "12개월" in body or "1년" in body
    # '강등' 또는 '탈퇴' 언급 — 사전 경고 메시지의 핵심 정보
    assert ("강등" in body) or ("탈퇴" in body)
    assert "[초록등대]" in subject


def test_template_inactive_warning_without_last_login_still_renders():
    """last_login_date=None 이어도 템플릿이 깨지지 않음 (날짜 줄만 생략)."""
    from core.nudge_mail import template_inactive_warning
    m = _make_member("u", 5, last_login=None)
    subject, body = template_inactive_warning(m)
    assert subject
    assert body
