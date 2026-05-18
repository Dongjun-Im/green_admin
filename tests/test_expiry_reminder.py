"""자료실 구독 만료 조기 알림 — 대상 추림 + 본문 + 중복 방지 (v1.2.11)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import pytest

from core.models import Member


def _make_member(user_id, level=5, *, is_admin=False, nickname=""):
    return Member(
        user_id=user_id, name=user_id.upper(),
        nickname=nickname or f"닉_{user_id}",
        level=level, level_label="", is_admin=is_admin,
    )


class _FakeStore:
    """latest_period_to 만 흉내내는 가짜 PaymentStore."""

    def __init__(self, period_tos: dict, raises_for=()):
        self.period_tos = period_tos
        self.raises_for = set(raises_for)
        self.calls: list[str] = []

    def latest_period_to(self, user_id) -> Optional[date]:
        self.calls.append(user_id)
        if user_id in self.raises_for:
            raise RuntimeError("simulated")
        return self.period_tos.get(user_id)


# ---------------------------------------------------------------------------
# find_expiry_targets
# ---------------------------------------------------------------------------


def test_picks_members_exactly_days_before_expiry():
    from core.expiry_reminder import find_expiry_targets
    today = date(2026, 5, 17)
    store = _FakeStore({
        "u_7": today + timedelta(days=7),    # 7일 후 만료 — 대상
        "u_3": today + timedelta(days=3),    # 3일 후 — 7일 대상 아님
        "u_10": today + timedelta(days=10),  # 10일 후 — 대상 아님
        "u_0": today,                         # 오늘 만료 — 대상 아님
        "u_neg": today - timedelta(days=1),   # 이미 만료 — 대상 아님
    })
    members = [_make_member(uid) for uid in ("u_7", "u_3", "u_10", "u_0", "u_neg")]
    targets = find_expiry_targets(
        members, payment_store=store, days_before=7,
        admin_user_id="rtgreen", today=today,
    )
    assert [t.member.user_id for t in targets] == ["u_7"]
    assert targets[0].expiry_date == today + timedelta(days=7)
    assert targets[0].days_left == 7


def test_3_days_window():
    from core.expiry_reminder import find_expiry_targets
    today = date(2026, 5, 17)
    store = _FakeStore({
        "u_7": today + timedelta(days=7),
        "u_3": today + timedelta(days=3),
        "u_2": today + timedelta(days=2),
    })
    members = [_make_member("u_7"), _make_member("u_3"), _make_member("u_2")]
    targets = find_expiry_targets(
        members, payment_store=store, days_before=3,
        admin_user_id="rtgreen", today=today,
    )
    assert [t.member.user_id for t in targets] == ["u_3"]


def test_excludes_admin_self_and_non_adjustable():
    from core.expiry_reminder import find_expiry_targets
    today = date(2026, 5, 17)
    expiry = today + timedelta(days=7)
    store = _FakeStore({
        "rtgreen": expiry,                    # 본인 — 제외
        "boss": expiry,                        # 관리자 — 제외
        "honor": expiry,                       # 명예 (LEVEL_TRANSITIONS 외) — 제외
        "u": expiry,                           # 일반 — 포함
    })
    members = [
        _make_member("rtgreen", 5),
        _make_member("boss", 9, is_admin=True),
        _make_member("honor", 8),
        _make_member("u", 5),
    ]
    targets = find_expiry_targets(
        members, payment_store=store, days_before=7,
        admin_user_id="rtgreen", today=today,
    )
    assert [t.member.user_id for t in targets] == ["u"]


def test_no_subscription_means_no_target():
    from core.expiry_reminder import find_expiry_targets
    today = date(2026, 5, 17)
    store = _FakeStore({})  # 아무 회원도 구독 없음
    members = [_make_member("u", 5)]
    targets = find_expiry_targets(
        members, payment_store=store, days_before=7,
        admin_user_id="rtgreen", today=today,
    )
    assert targets == []


def test_store_exception_skips_member_silently():
    from core.expiry_reminder import find_expiry_targets
    today = date(2026, 5, 17)
    store = _FakeStore({"u": today + timedelta(days=7)}, raises_for=["u"])
    members = [_make_member("u", 5)]
    targets = find_expiry_targets(
        members, payment_store=store, days_before=7,
        admin_user_id="rtgreen", today=today,
    )
    assert targets == []


def test_history_dedup_for_same_period_to(tmp_path):
    """같은 period_to 에 대해 한 번 보내고 나면 다음 호출에서 제외."""
    from core.expiry_reminder import find_expiry_targets
    from core.nudge_history import KIND_EXPIRY_REMINDER_7, NudgeHistoryStore

    today = date(2026, 5, 17)
    expiry = today + timedelta(days=7)
    store = _FakeStore({"u": expiry, "v": expiry})
    members = [_make_member("u", 5), _make_member("v", 5)]
    history = NudgeHistoryStore(tmp_path / "h.json")
    # u 는 이 만료일에 대해 이미 보냈음
    history.mark_sent("u", KIND_EXPIRY_REMINDER_7, when=expiry)

    targets = find_expiry_targets(
        members, payment_store=store, days_before=7,
        admin_user_id="rtgreen", today=today, history=history,
    )
    # u 빠지고 v 만 남음
    assert [t.member.user_id for t in targets] == ["v"]


def test_history_does_not_block_different_expiry_date(tmp_path):
    """이전 구독 기간에 7일전 메일을 보냈더라도, 새 구독으로 새 period_to 가
    생기면 그 만료일에 대해서는 다시 보낼 수 있어야 함 (재구독 회원 케이스)."""
    from core.expiry_reminder import find_expiry_targets
    from core.nudge_history import KIND_EXPIRY_REMINDER_7, NudgeHistoryStore

    today = date(2026, 5, 17)
    old_expiry = date(2025, 6, 1)
    new_expiry = today + timedelta(days=7)
    store = _FakeStore({"u": new_expiry})
    history = NudgeHistoryStore(tmp_path / "h.json")
    history.mark_sent("u", KIND_EXPIRY_REMINDER_7, when=old_expiry)

    targets = find_expiry_targets(
        [_make_member("u", 5)], payment_store=store, days_before=7,
        admin_user_id="rtgreen", today=today, history=history,
    )
    assert [t.member.user_id for t in targets] == ["u"]


def test_invalid_days_before_raises():
    from core.expiry_reminder import find_expiry_targets
    with pytest.raises(ValueError):
        find_expiry_targets(
            [], payment_store=_FakeStore({}),
            days_before=5,  # 7/3 만 지원
            admin_user_id="rtgreen",
        )


def test_targets_sorted_by_expiry_date():
    from core.expiry_reminder import find_expiry_targets
    today = date(2026, 5, 17)
    expiry = today + timedelta(days=7)
    # 둘 다 같은 7일 후 만료 — 정렬 안정성만 확인
    store = _FakeStore({"a": expiry, "b": expiry})
    members = [_make_member("b"), _make_member("a")]  # 의도적으로 b 가 먼저
    targets = find_expiry_targets(
        members, payment_store=store, days_before=7,
        admin_user_id="rtgreen", today=today,
    )
    # 같은 날짜라 입력 순서 유지 (sorted 는 stable)
    assert [t.member.user_id for t in targets] == ["b", "a"]


# ---------------------------------------------------------------------------
# 본문 템플릿
# ---------------------------------------------------------------------------


def test_template_7_contains_nickname_and_date():
    from core.expiry_reminder import template_expiry_reminder_7
    m = _make_member("u", nickname="동준")
    expiry = date(2026, 6, 15)
    subject, body = template_expiry_reminder_7(m, expiry)
    assert "[초록등대]" in subject
    assert "7일 후" in subject or "7일" in subject
    assert "동준" in body
    assert "2026-06-15" in body
    assert "7일" in body


def test_template_3_uses_urgency_wording():
    from core.expiry_reminder import template_expiry_reminder_3
    m = _make_member("u", nickname="동준")
    expiry = date(2026, 6, 15)
    subject, body = template_expiry_reminder_3(m, expiry)
    assert "임박" in subject or "3일" in subject
    assert "3일" in body
    assert "동준" in body


def test_template_for_kind_dispatch():
    from core.expiry_reminder import template_for_kind
    from core.nudge_history import KIND_EXPIRY_REMINDER_3, KIND_EXPIRY_REMINDER_7
    assert template_for_kind(KIND_EXPIRY_REMINDER_7).__name__ == "template_expiry_reminder_7"
    assert template_for_kind(KIND_EXPIRY_REMINDER_3).__name__ == "template_expiry_reminder_3"
    with pytest.raises(ValueError):
        template_for_kind("nonsense")


# ---------------------------------------------------------------------------
# NudgeHistoryStore.was_sent_for
# ---------------------------------------------------------------------------


def test_was_sent_for_exact_date_match(tmp_path):
    from core.nudge_history import KIND_EXPIRY_REMINDER_7, NudgeHistoryStore
    h = NudgeHistoryStore(tmp_path / "h.json")
    target = date(2026, 6, 15)
    assert h.was_sent_for("u", KIND_EXPIRY_REMINDER_7, target) is False
    h.mark_sent("u", KIND_EXPIRY_REMINDER_7, when=target)
    assert h.was_sent_for("u", KIND_EXPIRY_REMINDER_7, target) is True
    # 다른 날짜는 False
    assert h.was_sent_for("u", KIND_EXPIRY_REMINDER_7, date(2026, 6, 16)) is False
    # 다른 회원은 False
    assert h.was_sent_for("v", KIND_EXPIRY_REMINDER_7, target) is False
