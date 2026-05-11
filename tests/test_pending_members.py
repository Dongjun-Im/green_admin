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
    """사이트의 가입 단계는 대기(3) 만 — 준회원(4) 이상은 이미 승인된 상태."""
    from core.pending_members import find_pending
    members = [
        _make_member("a", 3),  # 대기 — 대상
        _make_member("b", 3),  # 대기 — 대상
        _make_member("c", 4),  # 준회원 — 이미 승인됨, 대상 아님
        _make_member("d", 5),  # 일반회원 — 대상 아님
    ]
    result = find_pending(members, seen_store=tmp_seen)
    user_ids = {pm.member.user_id for pm in result}
    assert user_ids == {"a", "b"}


def test_find_pending_excludes_seen_by_default(tmp_seen):
    from core.pending_members import find_pending
    members = [_make_member("a", 3), _make_member("b", 3)]
    tmp_seen.mark_seen("a")
    result = find_pending(members, seen_store=tmp_seen, only_unseen=True)
    assert {pm.member.user_id for pm in result} == {"b"}


def test_find_pending_includes_seen_when_only_unseen_false(tmp_seen):
    from core.pending_members import find_pending
    members = [_make_member("a", 3), _make_member("b", 3)]
    tmp_seen.mark_seen("a")
    result = find_pending(members, seen_store=tmp_seen, only_unseen=False)
    seen_flags = {pm.member.user_id: pm.seen_before for pm in result}
    assert seen_flags == {"a": True, "b": False}


def test_find_pending_sorted_recent_join_first(tmp_seen):
    from core.pending_members import find_pending
    members = [
        _make_member("old", 3, join_date=date(2024, 1, 1)),
        _make_member("new", 3, join_date=date(2026, 5, 1)),
        _make_member("mid", 3, join_date=date(2025, 6, 1)),
    ]
    result = find_pending(members, seen_store=tmp_seen)
    order = [pm.member.user_id for pm in result]
    assert order == ["new", "mid", "old"]


# ---------- 장기미접속 탈퇴자 재가입 차단 ----------

@pytest.fixture
def tmp_blocklist():
    from core.withdrawn_blocklist import WithdrawnBlocklist
    with tempfile.TemporaryDirectory() as td:
        yield WithdrawnBlocklist(path=os.path.join(td, "wd.json"))


def test_blocklist_add_contains_remove_persist():
    from core.withdrawn_blocklist import WithdrawnBlocklist
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "wd.json")
        b = WithdrawnBlocklist(path=path)
        assert not b.contains("Hong")
        b.add("Hong", nickname="홍길동", reason="장기미접속 탈퇴")
        assert b.contains("hong") and b.contains("HONG")   # 대소문자 무시
        assert b.info("hong")["nickname"] == "홍길동"
        # 재로드해도 유지
        b2 = WithdrawnBlocklist(path=path)
        assert b2.contains("hong")
        assert b2.remove("HONG") is True
        assert not b2.contains("hong")
        assert b2.remove("hong") is False


def test_blocklist_add_many():
    from core.withdrawn_blocklist import WithdrawnBlocklist
    with tempfile.TemporaryDirectory() as td:
        b = WithdrawnBlocklist(path=os.path.join(td, "wd.json"))
        n = b.add_many([("a", "닉a", "사유"), ("B", "", ""), ("", "x", "y")])
        assert n == 2
        assert b.contains("a") and b.contains("b") and len(b) == 2


def test_find_pending_flags_withdrawn(tmp_seen, tmp_blocklist):
    from core.pending_members import find_pending
    tmp_blocklist.add("a", nickname="닉a", reason="장기미접속 탈퇴")
    members = [_make_member("a", 3), _make_member("b", 3)]
    result = find_pending(members, seen_store=tmp_seen, blocklist=tmp_blocklist)
    flags = {pm.member.user_id: pm.was_withdrawn_inactive for pm in result}
    assert flags == {"a": True, "b": False}
    # 차단 대상이어도 목록에서 빠지진 않는다 (관리자가 거부 처리하도록)
    a = next(pm for pm in result if pm.member.user_id == "a")
    assert a.withdrawn_info and a.withdrawn_info["reason"] == "장기미접속 탈퇴"


def test_apply_plan_records_withdrawn_to_blocklist(tmp_blocklist):
    """장기미접속 조정에서 '탈퇴'(WITHDRAW_LEVEL) 처리된 회원이 명단에 기록된다."""
    from config import WITHDRAW_LEVEL
    from core.level_adjustment import LevelAdjustmentService
    from core.models import AdjustmentItem, AdjustmentPlan

    class _FakeResult:
        success = True
        message = "ok"

    class _FakeAdmin:
        dry_run = False
        def bulk_apply(self, level_map, action_label=""):
            return _FakeResult()

    svc = LevelAdjustmentService(
        crawler=None, admin=_FakeAdmin(), admin_user_id="rtgreen",
        blocklist=tmp_blocklist,
    )
    m_withdraw = _make_member("gone", 4)        # 준회원 → 탈퇴
    m_demote = _make_member("dn", 6)            # 우수 → 일반 (강등, 탈퇴 아님)
    plan = AdjustmentPlan(
        items=[
            AdjustmentItem(member=m_withdraw, action="delete",
                           from_level=4, to_level=WITHDRAW_LEVEL, reason="200일 미접속"),
            AdjustmentItem(member=m_demote, action="demote",
                           from_level=6, to_level=5, reason="200일 미접속"),
        ],
        total_scanned=2, cutoff_date=date(2025, 1, 1),
    )
    svc.apply_plan(plan)
    assert tmp_blocklist.contains("gone")
    assert not tmp_blocklist.contains("dn")


def test_apply_plan_dry_run_does_not_record(tmp_blocklist):
    from config import WITHDRAW_LEVEL
    from core.level_adjustment import LevelAdjustmentService
    from core.models import AdjustmentItem, AdjustmentPlan

    class _FakeResult:
        success = True
        message = "ok"

    class _FakeAdmin:
        dry_run = True
        def bulk_apply(self, level_map, action_label=""):
            return _FakeResult()

    svc = LevelAdjustmentService(
        crawler=None, admin=_FakeAdmin(), admin_user_id="rtgreen",
        blocklist=tmp_blocklist,
    )
    m = _make_member("gone", 4)
    plan = AdjustmentPlan(
        items=[AdjustmentItem(member=m, action="delete", from_level=4,
                              to_level=WITHDRAW_LEVEL, reason="x")],
        total_scanned=1, cutoff_date=date(2025, 1, 1),
    )
    svc.apply_plan(plan)
    assert not tmp_blocklist.contains("gone")
