"""NudgeHistoryStore — 안내 메일 발송 이력 JSON 저장소 (v1.2.10)."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def tmp_history(tmp_path):
    from core.nudge_history import NudgeHistoryStore
    return NudgeHistoryStore(tmp_path / "nudge_history.json")


def test_mark_and_last_sent(tmp_history):
    from core.nudge_history import KIND_ACTIVITY_NUDGE
    assert tmp_history.last_sent("anycall", KIND_ACTIVITY_NUDGE) is None
    tmp_history.mark_sent("anycall", KIND_ACTIVITY_NUDGE, when=date(2026, 5, 10))
    assert tmp_history.last_sent("anycall", KIND_ACTIVITY_NUDGE) == date(2026, 5, 10)
    # user_id 는 lower-case 정규화
    assert tmp_history.last_sent("ANYCALL", KIND_ACTIVITY_NUDGE) == date(2026, 5, 10)


def test_was_sent_within_boundary(tmp_history):
    """30일 경계: 29일 전 발송 = True, 30일 전 발송 = False."""
    from core.nudge_history import KIND_ACTIVITY_NUDGE
    today = date(2026, 5, 17)
    # 29일 전 발송 → 30일 이내
    tmp_history.mark_sent("a", KIND_ACTIVITY_NUDGE, when=today - timedelta(days=29))
    assert tmp_history.was_sent_within("a", KIND_ACTIVITY_NUDGE, 30, today=today) is True
    # 30일 전 발송 → 30일 이내가 아님 (< 비교)
    tmp_history.mark_sent("b", KIND_ACTIVITY_NUDGE, when=today - timedelta(days=30))
    assert tmp_history.was_sent_within("b", KIND_ACTIVITY_NUDGE, 30, today=today) is False
    # 발송 기록 없음 → False
    assert tmp_history.was_sent_within("c", KIND_ACTIVITY_NUDGE, 30, today=today) is False


def test_persistence_across_reload(tmp_path):
    """디스크 기록 후 새 인스턴스로 읽어도 유지."""
    from core.nudge_history import KIND_INACTIVE_WARNING, NudgeHistoryStore
    path = tmp_path / "nudge.json"
    s1 = NudgeHistoryStore(path)
    s1.mark_sent("hong", KIND_INACTIVE_WARNING, when=date(2026, 4, 1))
    s2 = NudgeHistoryStore(path)
    assert s2.last_sent("hong", KIND_INACTIVE_WARNING) == date(2026, 4, 1)


def test_kinds_are_independent(tmp_history):
    """같은 회원이라도 다른 종류는 따로 추적."""
    from core.nudge_history import KIND_ACTIVITY_NUDGE, KIND_INACTIVE_WARNING
    tmp_history.mark_sent("u", KIND_ACTIVITY_NUDGE, when=date(2026, 5, 1))
    assert tmp_history.last_sent("u", KIND_ACTIVITY_NUDGE) == date(2026, 5, 1)
    assert tmp_history.last_sent("u", KIND_INACTIVE_WARNING) is None


def test_mark_sent_many(tmp_path):
    from core.nudge_history import KIND_ACTIVITY_NUDGE, NudgeHistoryStore
    s = NudgeHistoryStore(tmp_path / "h.json")
    s.mark_sent_many(["a", "B", "c"], KIND_ACTIVITY_NUDGE, when=date(2026, 5, 17))
    for uid in ("a", "b", "c"):
        assert s.last_sent(uid, KIND_ACTIVITY_NUDGE) == date(2026, 5, 17)


def test_clear(tmp_history):
    from core.nudge_history import KIND_ACTIVITY_NUDGE
    tmp_history.mark_sent("a", KIND_ACTIVITY_NUDGE)
    tmp_history.clear()
    assert tmp_history.last_sent("a", KIND_ACTIVITY_NUDGE) is None


def test_corrupt_json_recovers_empty(tmp_path):
    """잘못된 JSON 파일도 빈 상태로 회복 (앱이 죽지 않도록)."""
    from core.nudge_history import NudgeHistoryStore
    path = tmp_path / "h.json"
    path.write_text("this is not json {", encoding="utf-8")
    s = NudgeHistoryStore(path)
    from core.nudge_history import KIND_ACTIVITY_NUDGE
    assert s.last_sent("a", KIND_ACTIVITY_NUDGE) is None


def test_unknown_user_id_returns_none(tmp_history):
    from core.nudge_history import KIND_ACTIVITY_NUDGE
    assert tmp_history.last_sent("", KIND_ACTIVITY_NUDGE) is None
    assert tmp_history.last_sent(None, KIND_ACTIVITY_NUDGE) is None  # type: ignore[arg-type]


def test_invalid_isoformat_returns_none(tmp_path):
    """디스크에 이상한 날짜 문자열이 있어도 None 으로 안전 처리."""
    from core.nudge_history import KIND_ACTIVITY_NUDGE, NudgeHistoryStore
    path = tmp_path / "h.json"
    path.write_text(
        json.dumps({"u": {KIND_ACTIVITY_NUDGE: "yesterday"}}),
        encoding="utf-8",
    )
    s = NudgeHistoryStore(path)
    assert s.last_sent("u", KIND_ACTIVITY_NUDGE) is None
