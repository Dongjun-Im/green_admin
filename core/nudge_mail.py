"""안내 메일(nudge) 대상 회원 추림 + 메일 본문 템플릿 (v1.2.10).

두 가지 안내 메일을 자동 발송:
  1) **활동 안내 메일** (`KIND_ACTIVITY_NUDGE`)
       — 'green3' 게시판에 최근 6개월간 글이 0건인 회원에게 "활동해 주세요"
         안내. (댓글은 따지지 않음 — 사용자 요청대로 '게시물' 기준.)
  2) **장기미접속 사전 경고** (`KIND_INACTIVE_WARNING`)
       — 마지막 접속이 1년 이상 지난 회원에게 "다음 정리 작업에서 등급이
         조정될 수 있습니다" 사전 안내.

두 함수 모두 같은 패턴:
  · 회원 목록 + 기준일을 받아 NudgeTarget 리스트 반환.
  · 본인 계정·관리자·LEVEL_TRANSITIONS 에 없는 등급은 자동 제외.
  · `history` 가 주어지면 `was_sent_within(user_id, kind, 30)` 인 회원도 제외.

본문 템플릿은 기존 `core/mail_sender.py:template_*` 와 시그니처 통일:
  template_activity_nudge(member, *, months=6) -> (subject, body)
  template_inactive_warning(member, *, months=12) -> (subject, body)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

from dateutil.relativedelta import relativedelta

from config import (
    ADMIN_LEVELS,
    GREEN3_BOARD,
    INACTIVITY_MONTHS,
    INACTIVITY_MONTHS_HARD,
    LEVEL_TRANSITIONS,
)
from core.activity_counter import ActivityCounter
from core.models import Member
from core.nudge_history import (
    KIND_ACTIVITY_NUDGE,
    KIND_INACTIVE_WARNING,
    NudgeHistoryStore,
)


# 활동 안내 메일 — 최근 N개월 글이 0건이면 대상.
ACTIVITY_NUDGE_MONTHS = INACTIVITY_MONTHS  # = 6
# 장기미접속 경고 메일 — 마지막 접속이 N개월+ 지났으면 대상.
INACTIVE_WARNING_MONTHS = INACTIVITY_MONTHS_HARD  # = 12
# 같은 회원에게 N일 이내 다시 안 보냄.
DEFAULT_SKIP_WITHIN_DAYS = 30

ProgressCB = Callable[[int, int], None]


@dataclass(frozen=True)
class NudgeTarget:
    """안내 메일 대상 한 명."""
    member: Member
    # 활동 안내용: 최근 N개월 글 수 (0 = 아예 없음, None = 조회 실패 — 제외 후보).
    recent_post_count: Optional[int] = None
    # 장기미접속 경고용: 마지막 접속일 (None = 파싱 실패 — 안전 제외).
    last_login_date: Optional[date] = None

    def display_activity(self) -> str:
        m = self.member
        nick = m.nickname or m.name or m.user_id
        last = m.last_login_date.isoformat() if m.last_login_date else "?"
        return (
            f"{m.user_id} / {nick} / 최근 6개월 글 "
            f"{self.recent_post_count if self.recent_post_count is not None else '?'}"
            f"건 / 마지막접속 {last}"
        )

    def display_warning(self) -> str:
        m = self.member
        nick = m.nickname or m.name or m.user_id
        last = (
            self.last_login_date.isoformat()
            if self.last_login_date else "?"
        )
        days = (
            (date.today() - self.last_login_date).days
            if self.last_login_date else 0
        )
        return f"{m.user_id} / {nick} / {days}일 미접속 (마지막 {last})"


# ---------------------------------------------------------------------------
# 대상 추림
# ---------------------------------------------------------------------------


def _is_eligible_member(
    member: Member, admin_user_id: str,
) -> bool:
    """본인/관리자/LEVEL_TRANSITIONS 외 등급은 안내 메일에서도 자동 제외."""
    if member.user_id.lower() == (admin_user_id or "").lower():
        return False
    if getattr(member, "is_admin", False):
        return False
    if member.level in ADMIN_LEVELS:
        return False
    # LEVEL_TRANSITIONS 에 있는 등급(4~7) 만 안내 대상 — 8 명예/9 운영진은 빼고,
    # 0~3 가입 단계도 빼고.
    if member.level not in LEVEL_TRANSITIONS:
        return False
    return True


def find_activity_nudge_targets(
    members: list[Member],
    activity_counter: ActivityCounter,
    *,
    admin_user_id: str,
    today: Optional[date] = None,
    months: int = ACTIVITY_NUDGE_MONTHS,
    history: Optional[NudgeHistoryStore] = None,
    skip_within_days: int = DEFAULT_SKIP_WITHIN_DAYS,
    board: str = GREEN3_BOARD,
    progress_cb: Optional[ProgressCB] = None,
) -> list[NudgeTarget]:
    """green3 에 최근 `months` 개월간 글이 0건인 회원 목록.

    · 본인/관리자/조정 제외 등급은 자동 빠짐.
    · history 가 있으면 최근 skip_within_days 이내 이미 보낸 회원도 제외.
    · 글 카운트 조회 실패(None) 항목도 안전하게 제외 — 잘못 보내느니 보내지 않는다.
    · progress_cb(c, t) 로 진행률 알림.
    """
    today = today or date.today()
    since = today - relativedelta(months=months)
    eligible = [m for m in members if _is_eligible_member(m, admin_user_id)]
    # 이력 필터 (HTTP 호출 전에 미리 줄여서 빠르게)
    if history is not None:
        eligible = [
            m for m in eligible
            if not history.was_sent_within(
                m.user_id, KIND_ACTIVITY_NUDGE,
                skip_within_days, today=today,
            )
        ]

    targets: list[NudgeTarget] = []
    total = len(eligible)
    for i, m in enumerate(eligible, start=1):
        if progress_cb is not None:
            try:
                progress_cb(i, total)
            except Exception:
                pass
        try:
            count = activity_counter.fetch_post_count(
                m.user_id, board, since=since,
            )
        except Exception:
            count = None
        if count is None:
            continue
        if count > 0:
            continue
        targets.append(NudgeTarget(member=m, recent_post_count=count))
    return targets


def find_inactive_warning_targets(
    members: list[Member],
    *,
    admin_user_id: str,
    today: Optional[date] = None,
    months: int = INACTIVE_WARNING_MONTHS,
    history: Optional[NudgeHistoryStore] = None,
    skip_within_days: int = DEFAULT_SKIP_WITHIN_DAYS,
) -> list[NudgeTarget]:
    """마지막 접속이 `months` 개월+ 지난 회원 목록.

    · 본인/관리자/조정 제외 등급은 자동 빠짐.
    · last_login_date 가 None 인 회원은 안전 제외 (잘못 분류 위험).
    · history 가 있으면 최근 skip_within_days 이내 이미 보낸 회원도 제외.
    """
    today = today or date.today()
    cutoff = today - relativedelta(months=months)
    targets: list[NudgeTarget] = []
    for m in members:
        if not _is_eligible_member(m, admin_user_id):
            continue
        if m.last_login_date is None:
            continue
        if m.last_login_date > cutoff:
            continue
        if history is not None and history.was_sent_within(
            m.user_id, KIND_INACTIVE_WARNING,
            skip_within_days, today=today,
        ):
            continue
        targets.append(NudgeTarget(
            member=m, last_login_date=m.last_login_date,
        ))
    return targets


# ---------------------------------------------------------------------------
# 본문 템플릿
# ---------------------------------------------------------------------------


def template_activity_nudge(
    member: Member, *, months: int = ACTIVITY_NUDGE_MONTHS,
) -> tuple[str, str]:
    """6개월 동안 'green3' 게시판에 글이 없는 회원용 활동 안내."""
    nick = member.nickname or member.name or member.user_id
    subject = "[초록등대] 게시판 활동 안내 — 회원님의 글을 기다리고 있어요"
    body = (
        f"{nick} 회원님 안녕하세요.\n\n"
        f"초록등대 동호회 운영진입니다.\n\n"
        f"최근 {months}개월 동안 '우리들의 이야기' 게시판에 회원님의 글이 "
        f"올라오지 않은 것으로 확인됩니다.\n\n"
        f"회원님의 생각·이야기·소식을 게시판에서 만날 수 있기를 기다리고 있습니다. "
        f"가벼운 인사 한 줄, 짧은 이야기 한 편이라도 큰 힘이 됩니다.\n\n"
        f"게시판 활동은 자료실 이용·등급 유지에도 도움이 됩니다. "
        f"6개월 이상 글·댓글이 없으면 다음 정리 작업에서 등급이 한 단계 조정될 수 있습니다.\n\n"
        f"언제든 소리샘 초록등대 동호회로 찾아주세요. 회원님의 한 줄 글이 기다려집니다.\n\n"
        f"감사합니다.\n"
        f"초록등대 운영진 드림"
    )
    return subject, body


def template_inactive_warning(
    member: Member, *, months: int = INACTIVE_WARNING_MONTHS,
) -> tuple[str, str]:
    """1년 이상 사이트 미접속자에게 보내는 사전 경고."""
    nick = member.nickname or member.name or member.user_id
    subject = "[초록등대] 장기 미접속 안내 — 등급 조정이 예정되어 있습니다"
    days = (
        (date.today() - member.last_login_date).days
        if member.last_login_date else None
    )
    days_line = (
        f"마지막 접속일로부터 약 {days}일 ({days // 30}개월) 지났습니다.\n\n"
        if days is not None else ""
    )
    body = (
        f"{nick} 회원님 안녕하세요.\n\n"
        f"초록등대 동호회 운영진입니다.\n\n"
        f"회원님께서 {months}개월(약 1년) 이상 소리샘에 접속하지 않으신 것으로 "
        f"확인됩니다.\n"
        f"{days_line}"
        f"동호회 규정에 따라, 다음 정리 작업(분기/반기 주기)에서 회원님의 등급이 "
        f"한 단계 강등되거나, 준회원의 경우 탈퇴 처리될 예정입니다.\n\n"
        f"회원 상태를 그대로 유지하길 원하시면 가까운 시일 내 한 번 접속해 "
        f"주세요. 접속만으로도 '활동 회원' 으로 다시 분류되어 등급 조정 대상에서 "
        f"빠집니다.\n\n"
        f"언제든 소리샘 초록등대 동호회로 찾아주세요.\n\n"
        f"감사합니다.\n"
        f"초록등대 운영진 드림"
    )
    return subject, body
