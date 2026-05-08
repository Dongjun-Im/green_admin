"""ScheduleTracker — 분기/반기 도래 + MVP."""
from __future__ import annotations

import os
import tempfile
from datetime import date

import pytest


@pytest.fixture
def tmp_tracker():
    from core.schedule_tracker import ScheduleTracker
    with tempfile.TemporaryDirectory() as td:
        yield ScheduleTracker(path=os.path.join(td, "last_run.json"))


def test_initial_all_due(tmp_tracker):
    assert tmp_tracker.is_backup_due()
    assert tmp_tracker.is_adjustment_due()
    assert tmp_tracker.is_mvp_due()


def test_due_months_split():
    """3개월 주기 → [1,4,7,10]. 6개월 → [1,7]."""
    from core.schedule_tracker import _due_months
    assert _due_months(3) == [1, 4, 7, 10]
    assert _due_months(6) == [1, 7]


def test_after_mark_not_due(tmp_tracker):
    tmp_tracker.mark_backup_done(count=10, today=date(2026, 4, 5))
    # 같은 분기 내(4월 5일 이후)면 더 이상 도래 안 함
    assert not tmp_tracker.is_backup_due(today=date(2026, 4, 30))
    # 다음 분기(7월)는 다시 도래
    assert tmp_tracker.is_backup_due(today=date(2026, 7, 2))


def test_mvp_mark_and_due(tmp_tracker):
    tmp_tracker.mark_mvp_done(top_n=10, quarter="2026-Q2", today=date(2026, 4, 5))
    assert not tmp_tracker.is_mvp_due(today=date(2026, 4, 30))
    assert tmp_tracker.is_mvp_due(today=date(2026, 7, 2))


def test_history_grows(tmp_tracker):
    tmp_tracker.mark_backup_done(count=1)
    tmp_tracker.mark_adjustment_done(demoted=2, deleted=1)
    tmp_tracker.mark_mvp_done(top_n=10, quarter="X")
    # 내부 history 가 3건
    assert len(tmp_tracker._data["history"]) == 3
