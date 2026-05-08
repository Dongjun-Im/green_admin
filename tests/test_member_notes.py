"""회원 메모/태그 SQLite CRUD."""
from __future__ import annotations

import pytest


@pytest.fixture
def tmp_store(tmp_path):
    from core.member_notes import MemberNotesStore
    return MemberNotesStore(path=tmp_path / "notes.db")


def test_get_missing_returns_none(tmp_store):
    assert tmp_store.get("nobody") is None


def test_upsert_and_get(tmp_store):
    from core.member_notes import MemberNote
    n = MemberNote(user_id="hong", note="오프모임 단골", tags=["VIP", "서울"])
    tmp_store.upsert(n)
    got = tmp_store.get("hong")
    assert got.note == "오프모임 단골"
    assert got.tags == ["VIP", "서울"]
    assert got.has_content
    assert got.updated_at  # 시각 기록됨


def test_upsert_overwrites(tmp_store):
    from core.member_notes import MemberNote
    tmp_store.upsert(MemberNote(user_id="lee", note="원본"))
    tmp_store.upsert(MemberNote(user_id="lee", note="수정", tags=["new"]))
    got = tmp_store.get("lee")
    assert got.note == "수정"
    assert got.tags == ["new"]


def test_get_many(tmp_store):
    from core.member_notes import MemberNote
    tmp_store.upsert(MemberNote(user_id="a", note="x"))
    tmp_store.upsert(MemberNote(user_id="b", tags=["t"]))
    got = tmp_store.get_many(["a", "b", "missing"])
    assert set(got.keys()) == {"a", "b"}


def test_delete(tmp_store):
    from core.member_notes import MemberNote
    tmp_store.upsert(MemberNote(user_id="x", note="삭제 대상"))
    tmp_store.delete("x")
    assert tmp_store.get("x") is None


def test_all_tagged_excludes_empty(tmp_store):
    from core.member_notes import MemberNote
    tmp_store.upsert(MemberNote(user_id="empty", note="", tags=[]))
    tmp_store.upsert(MemberNote(user_id="full", note="x", tags=[]))
    rows = tmp_store.all_tagged()
    user_ids = {r.user_id for r in rows}
    assert "full" in user_ids
    assert "empty" not in user_ids


def test_display_summary_truncates_long_note(tmp_store):
    from core.member_notes import MemberNote
    n = MemberNote(user_id="x", note="가" * 100)
    tmp_store.upsert(n)
    got = tmp_store.get("x")
    s = got.display_summary()
    assert "…" in s or len(s) <= 35
