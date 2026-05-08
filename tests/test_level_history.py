"""영구 등급 변경 이력."""
from __future__ import annotations

import pytest


@pytest.fixture
def tmp_history(tmp_path):
    from core.level_history import LevelHistoryStore
    return LevelHistoryStore(path=tmp_path / "history.db")


def test_record_basic(tmp_history):
    rid = tmp_history.record(
        user_id="hong", from_level=6, to_level=7,
        source="auto_promote", reason="활동점수 35", nickname="홍이",
        actor="admin1",
    )
    assert rid > 0
    rows = tmp_history.recent()
    assert len(rows) == 1
    r = rows[0]
    assert r.user_id == "hong"
    assert r.from_level == 6 and r.to_level == 7
    assert r.source == "auto_promote"
    assert r.actor == "admin1"


def test_record_skips_noop(tmp_history):
    rid = tmp_history.record(
        user_id="x", from_level=6, to_level=6, source="manual",
    )
    assert rid == 0
    assert tmp_history.count() == 0


def test_record_batch(tmp_history):
    items = [
        {"user_id": "a", "from_level": 6, "to_level": 7, "reason": "활동점수 30"},
        {"user_id": "b", "from_level": 6, "to_level": 8, "reason": "활동점수 60"},
        {"user_id": "c", "from_level": 5, "to_level": 5, "reason": "noop"},
    ]
    n = tmp_history.record_batch(items, source="auto_promote", actor="admin1")
    assert n == 2
    assert tmp_history.count() == 2


def test_for_user(tmp_history):
    tmp_history.record("hong", 5, 6, "manual")
    tmp_history.record("lee", 6, 7, "auto_promote")
    tmp_history.record("hong", 6, 7, "auto_promote")
    rows = tmp_history.for_user("hong")
    assert len(rows) == 2
    assert all(r.user_id == "hong" for r in rows)
    # DESC 정렬
    assert rows[0].id > rows[1].id


def test_search_by_source(tmp_history):
    tmp_history.record("a", 6, 7, "auto_promote")
    tmp_history.record("b", 7, 6, "level_adjust")
    promote = tmp_history.search(source="auto_promote")
    assert len(promote) == 1 and promote[0].user_id == "a"


def test_search_by_user_id_partial(tmp_history):
    tmp_history.record("hong123", 5, 6, "manual")
    tmp_history.record("park", 5, 6, "manual")
    rows = tmp_history.search(user_id="hong")
    assert len(rows) == 1


def test_persistence(tmp_path):
    from core.level_history import LevelHistoryStore
    path = tmp_path / "h.db"
    s1 = LevelHistoryStore(path=path)
    s1.record("a", 5, 6, "manual")
    s2 = LevelHistoryStore(path=path)
    assert s2.count() == 1
