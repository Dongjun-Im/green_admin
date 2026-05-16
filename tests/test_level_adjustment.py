"""장기미접속 등급 조정 — 로그인 날짜 기준 + green3 활동 기반 면제 (v1.2.7)."""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest


def _make_member(user_id, level, last_login):
    from core.models import Member
    return Member(
        user_id=user_id,
        name=user_id.upper(),
        nickname=f"닉_{user_id}",
        level=level,
        level_label="",
        last_login_date=last_login,
    )


def _service(activity_counter=None):
    from core.level_adjustment import LevelAdjustmentService

    class _FakeAdmin:
        dry_run = True
        def bulk_apply(self, level_map, action_label=""):
            return MagicMock(success=True, message="ok")

    return LevelAdjustmentService(
        crawler=None,
        admin=_FakeAdmin(),
        admin_user_id="rtgreen",
        cutoff_provider=lambda: date(2026, 1, 1),
        activity_counter=activity_counter,
    )


class _FakeActivityCounter:
    """fetch_member(user_id, boards=...) → MemberActivity with predetermined counts.

    counts: {user_id: (posts, comments)} on green3 board.
    Missing keys → returns (0, 0).
    raises_for: set of user_ids that should make fetch_member raise.
    """

    def __init__(self, counts, raises_for=()):
        self.counts = counts
        self.raises_for = set(raises_for)
        self.calls = []

    def fetch_member(self, user_id, boards=None):
        self.calls.append((user_id, boards))
        if user_id in self.raises_for:
            raise RuntimeError("simulated network error")
        from core.activity_counter import BoardActivity, MemberActivity
        ma = MemberActivity(user_id=user_id)
        for bo in boards or ():
            posts, comments = self.counts.get(user_id, (0, 0))
            ma.by_board[bo] = BoardActivity(board=bo, posts=posts, comments=comments)
        return ma


# ---------------------------------------------------------------------------
# 1. 로그인 날짜만 보던 예전 동작 보존 (activity_counter 미주입)
# ---------------------------------------------------------------------------


def test_login_only_mode_still_works_without_activity_counter():
    svc = _service(activity_counter=None)
    members = [
        _make_member("stale", 5, last_login=date(2025, 5, 1)),    # 6개월+ 미접속
        _make_member("recent", 5, last_login=date(2026, 4, 1)),   # 최근 접속
    ]
    plan = svc.build_plan(members=members)
    actionable = [it for it in plan.actionable]
    assert len(actionable) == 1
    assert actionable[0].member.user_id == "stale"


# ---------------------------------------------------------------------------
# 2. 활동 충분 (글>=3 AND 댓글>=3) → '접속자' 로 인정, 조정 대상에서 빠짐
# ---------------------------------------------------------------------------


def test_active_member_with_enough_green3_posts_and_comments_is_skipped():
    counter = _FakeActivityCounter(counts={"active": (5, 10)})
    svc = _service(activity_counter=counter)
    members = [_make_member("active", 5, last_login=date(2025, 1, 1))]
    plan = svc.build_plan(members=members)
    # 6개월+ 미접속이지만 green3 활동 충분 → 빈 계획
    assert plan.actionable == []
    assert counter.calls == [("active", ("green3",))]


@pytest.mark.parametrize("posts,comments", [(3, 3), (3, 100), (100, 3), (50, 50)])
def test_threshold_is_inclusive(posts, comments):
    counter = _FakeActivityCounter(counts={"u": (posts, comments)})
    svc = _service(activity_counter=counter)
    members = [_make_member("u", 5, last_login=date(2025, 1, 1))]
    plan = svc.build_plan(members=members)
    # 임계값(3) 포함 — 정확히 3이어도 면제
    assert plan.actionable == []


# ---------------------------------------------------------------------------
# 3. 활동 부족 → 미접속자로 분류 (글 부족 / 댓글 부족 / 둘 다 부족)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("posts,comments,why", [
    (2, 5, "글 부족"),
    (5, 2, "댓글 부족"),
    (0, 0, "둘 다 0"),
    (2, 2, "둘 다 부족"),
])
def test_insufficient_activity_keeps_member_in_plan(posts, comments, why):
    counter = _FakeActivityCounter(counts={"u": (posts, comments)})
    svc = _service(activity_counter=counter)
    members = [_make_member("u", 5, last_login=date(2025, 1, 1))]
    plan = svc.build_plan(members=members)
    actionable = plan.actionable
    assert len(actionable) == 1, why
    # 사유에 green3 글·댓글 카운트가 포함돼야 함
    reason = actionable[0].reason
    assert f"green3 글 {posts}건" in reason
    assert f"댓글 {comments}건" in reason
    assert "기준 미만" in reason


# ---------------------------------------------------------------------------
# 4. activity_counter 가 예외를 던지면 안전하게 로그인 기준만으로 처리
# ---------------------------------------------------------------------------


def test_activity_counter_failure_falls_back_to_login_only():
    counter = _FakeActivityCounter(counts={}, raises_for={"u"})
    svc = _service(activity_counter=counter)
    members = [_make_member("u", 5, last_login=date(2025, 1, 1))]
    plan = svc.build_plan(members=members)
    # 활동 조회 실패 → 안전한 쪽으로 조정 대상 유지 (예전 동작)
    actionable = plan.actionable
    assert len(actionable) == 1
    assert "기준 미만" not in actionable[0].reason  # 활동 정보 없으므로 일반 사유만


# ---------------------------------------------------------------------------
# 5. 활동 점검은 6개월 이내 접속자에게 안 부른다 (불필요한 HTTP 절약)
# ---------------------------------------------------------------------------


def test_recent_login_skips_activity_check_entirely():
    counter = _FakeActivityCounter(counts={"recent": (0, 0)})
    svc = _service(activity_counter=counter)
    members = [_make_member("recent", 5, last_login=date(2026, 4, 1))]
    plan = svc.build_plan(members=members)
    assert plan.actionable == []
    # 6개월 이내 접속자에 대해서는 activity_counter 호출 자체가 일어나면 안 됨
    assert counter.calls == []


# ---------------------------------------------------------------------------
# 6. 관리자/본인/조정 제외 등급은 활동 점검도 건너뜀
# ---------------------------------------------------------------------------


def test_admin_self_and_excluded_levels_never_hit_activity_counter():
    from core.models import Member
    counter = _FakeActivityCounter(counts={})
    svc = _service(activity_counter=counter)
    members = [
        _make_member("rtgreen", 5, last_login=date(2025, 1, 1)),    # 본인
        Member(user_id="boss", name="BOSS", nickname="",
               level=9, level_label="", last_login_date=date(2025, 1, 1),
               is_admin=True),                                       # 관리자
        _make_member("honor", 8, last_login=date(2025, 1, 1)),       # 명예회원 (조정 대상 아님)
    ]
    plan = svc.build_plan(members=members)
    assert plan.actionable == []
    assert counter.calls == []


# ---------------------------------------------------------------------------
# 7. activity_progress_cb 가 후보 수만큼 호출됨
# ---------------------------------------------------------------------------


def test_activity_progress_cb_called_per_candidate():
    counter = _FakeActivityCounter(counts={"a": (0, 0), "b": (5, 5), "c": (1, 1)})
    svc = _service(activity_counter=counter)
    members = [
        _make_member("a", 5, last_login=date(2025, 1, 1)),
        _make_member("b", 5, last_login=date(2025, 1, 1)),
        _make_member("c", 5, last_login=date(2025, 1, 1)),
    ]
    progress = []
    svc.build_plan(
        members=members,
        activity_progress_cb=lambda c, t: progress.append((c, t)),
    )
    assert progress == [(1, 3), (2, 3), (3, 3)]
