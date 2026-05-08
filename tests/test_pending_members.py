"""신규 가입자 식별 + seen 영속."""
from __future__ import annotations

import os
import tempfile
from datetime import date

import pytest


@pytest.fixture
def tmp_seen():
    from core.pending_members import PendingSeenStore
    with tempfile.TemporaryDirectory() as td:
        yield PendingSeenStore(path=os.path.join(td, "seen.json"))


def _make_member(user_id, level, join_date=None):
    from core.models import Member
    return Member(
        user_id=user_id,
        name=user_id.upper(),
        nickname=f"닉_{user_id}",
        level=level,
        level_label="",
        join_date=join_date,
    )


def test_seen_store_basic(tmp_seen):
    assert not tmp_seen.has_seen("hong")
    tmp_seen.mark_seen("hong")
    assert tmp_seen.has_seen("hong")
    tmp_seen.clear("hong")
    assert not tmp_seen.has_seen("hong")


def test_seen_store_persistence():
    from core.pending_members import PendingSeenStore
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "seen.json")
        s1 = PendingSeenStore(path=path)
        s1.mark_seen("a")
        s2 = PendingSeenStore(path=path)
        assert s2.has_seen("a")


def test_find_pending_filters_by_level(tmp_seen):
    from core.pending_members import find_pending
    members = [
        _make_member("a", 3),
        _make_member("b", 4),
        _make_member("c", 5),  # 준회원 — 대상 아님
        _make_member("d", 6),
    ]
    result = find_pending(members, seen_store=tmp_seen)
    user_ids = {pm.member.user_id for pm in result}
    assert user_ids == {"a", "b"}


def test_find_pending_excludes_seen_by_default(tmp_seen):
    from core.pending_members import find_pending
    members = [_make_member("a", 4), _make_member("b", 4)]
    tmp_seen.mark_seen("a")
    result = find_pending(members, seen_store=tmp_seen, only_unseen=True)
    assert {pm.member.user_id for pm in result} == {"b"}


def test_find_pending_includes_seen_when_only_unseen_false(tmp_seen):
    from core.pending_members import find_pending
    members = [_make_member("a", 4), _make_member("b", 4)]
    tmp_seen.mark_seen("a")
    result = find_pending(members, seen_store=tmp_seen, only_unseen=False)
    seen_flags = {pm.member.user_id: pm.seen_before for pm in result}
    assert seen_flags == {"a": True, "b": False}


def test_find_pending_sorted_recent_join_first(tmp_seen):
    from core.pending_members import find_pending
    members = [
        _make_member("old", 4, join_date=date(2024, 1, 1)),
        _make_member("new", 4, join_date=date(2026, 5, 1)),
        _make_member("mid", 4, join_date=date(2025, 6, 1)),
    ]
    result = find_pending(members, seen_store=tmp_seen)
    order = [pm.member.user_id for pm in result]
    assert order == ["new", "mid", "old"]
