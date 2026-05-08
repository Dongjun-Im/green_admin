"""작업 로그 파싱·분류."""
from __future__ import annotations

from datetime import datetime


def test_parse_action_line():
    from core.log_reader import parse_line
    line = (
        "[2026-05-08T12:34:56] OK action=demote user=hong "
        "from=7 to=6 reason=180일 미접속 url=https://x.com msg=etc"
    )
    e = parse_line(line)
    assert e is not None
    assert e.kind == "action"
    assert e.success is True
    assert e.action == "demote"
    assert e.user_id == "hong"
    assert e.from_level == 7
    assert e.to_level == 6
    assert e.timestamp == datetime(2026, 5, 8, 12, 34, 56)


def test_parse_failed_action():
    from core.log_reader import parse_line
    line = (
        "[2026-01-02T00:00:00] FAIL action=demote user=lee "
        "from=8 to=7 reason=test url=u msg=m"
    )
    e = parse_line(line)
    assert e is not None
    assert e.success is False


def test_parse_event_line():
    from core.log_reader import parse_line
    line = "[2026-03-01T10:00:00] EVENT backup count=120 txt=outstanding.txt"
    e = parse_line(line)
    assert e is not None
    assert e.kind == "event"
    assert "backup" in e.message


def test_parse_garbage_returns_none():
    from core.log_reader import parse_line
    assert parse_line("garbage") is None
    assert parse_line("") is None


def test_classify_action_promote_demote_delete():
    from core.log_reader import LogEntry, classify_action
    promote = LogEntry(
        timestamp=datetime.now(), success=True, raw="", kind="action",
        action="demote", user_id="x", from_level=6, to_level=7,
    )
    demote = LogEntry(
        timestamp=datetime.now(), success=True, raw="", kind="action",
        action="demote", user_id="x", from_level=7, to_level=6,
    )
    delete = LogEntry(
        timestamp=datetime.now(), success=True, raw="", kind="action",
        action="demote", user_id="x", from_level=6, to_level=1,
    )
    event = LogEntry(
        timestamp=datetime.now(), success=True, raw="", kind="event",
        message="backup x",
    )
    assert classify_action(promote) == "승급"
    assert classify_action(demote) == "강등"
    assert classify_action(delete) == "탈퇴"
    assert classify_action(event) is None


def test_count_actions_excludes_failures_by_default():
    from core.log_reader import LogEntry, count_actions
    entries = [
        LogEntry(timestamp=datetime.now(), success=True, raw="", kind="action",
                 action="demote", user_id="a", from_level=7, to_level=6),
        LogEntry(timestamp=datetime.now(), success=False, raw="", kind="action",
                 action="demote", user_id="b", from_level=7, to_level=6),
    ]
    counts = count_actions(entries)
    assert counts == {"강등": 1}
