"""자료실 구독 만료 조기 알림 — 대상 추림 + 본문 템플릿 (v1.2.11).

두 시점에 회원에게 알림 메일:
  · 만료 7일 전 (`KIND_EXPIRY_REMINDER_7`) — 재결제 준비할 여유
  · 만료 3일 전 (`KIND_EXPIRY_REMINDER_3`) — 마지막 독촉

대상 추림:
  · `PaymentStore.latest_period_to(user_id)` 로 회원별 최신 만료일 조회.
  · `period_to - today == days_before` 인 회원이 대상.
  · 같은 만료일(period_to) 에 대해 같은 종류의 메일을 두 번 안 보내도록
    `history.was_sent_for(user_id, kind, period_to)` 로 중복 차단.
  · 본인 계정·관리자·LEVEL_TRANSITIONS 외 등급은 자동 제외 (메일 받을 회원
    상태가 아님).

본문 템플릿:
  · 닉네임 + 만료일 + 결제 안내 한 줄.
  · 7일 전 / 3일 전 두 가지 톤.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from config import ADMIN_LEVELS, LEVEL_TRANSITIONS
from core.models import Member
from core.nudge_history import (
    KIND_EXPIRY_REMINDER_3,
    KIND_EXPIRY_REMINDER_7,
    NudgeHistoryStore,
)


# 알림 시점 — 매핑 (일수 → kind).
REMINDER_DAYS_BEFORE = {7: KIND_EXPIRY_REMINDER_7, 3: KIND_EXPIRY_REMINDER_3}


@dataclass(frozen=True)
class ExpiryTarget:
    """만료 알림 대상 한 명."""
    member: Member
    expiry_date: date
    days_left: int  # today 기준 남은 일수 (음수면 이미 만료)

    def display(self) -> str:
        m = self.member
        nick = m.nickname or m.name or m.user_id
        return (
            f"{m.user_id} / {nick} / 만료 {self.expiry_date.isoformat()} "
            f"({self.days_left}일 남음)"
        )


def _is_eligible_member(member: Member, admin_user_id: str) -> bool:
    """본인·관리자·LEVEL_TRANSITIONS 외 등급은 자동 제외 (nudge_mail 과 동일)."""
    if member.user_id.lower() == (admin_user_id or "").lower():
        return False
    if getattr(member, "is_admin", False):
        return False
    if member.level in ADMIN_LEVELS:
        return False
    if member.level not in LEVEL_TRANSITIONS:
        return False
    return True


def find_expiry_targets(
    members: Iterable[Member],
    *,
    payment_store,
    days_before: int,
    admin_user_id: str,
    today: Optional[date] = None,
    history: Optional[NudgeHistoryStore] = None,
) -> list[ExpiryTarget]:
    """`days_before` 일 후 만료될 회원 목록.

    - members: 전체 회원 (캐시).
    - payment_store: `latest_period_to(user_id) -> date | None` 메서드 보유.
    - days_before: 보통 7 또는 3 (REMINDER_DAYS_BEFORE 의 키).
    - history: 주어지면 같은 만료일에 대해 이미 보낸 회원은 자동 제외.

    반환: `ExpiryTarget` 리스트. expiry_date 오름차순 (가까운 만료일 먼저).
    """
    today = today or date.today()
    kind = REMINDER_DAYS_BEFORE.get(days_before)
    if kind is None:
        raise ValueError(f"지원하지 않는 days_before: {days_before}. "
                         f"지원값: {sorted(REMINDER_DAYS_BEFORE.keys())}")

    targets: list[ExpiryTarget] = []
    for m in members:
        if not _is_eligible_member(m, admin_user_id):
            continue
        try:
            period_to = payment_store.latest_period_to(m.user_id)
        except Exception:
            continue
        if period_to is None:
            continue
        # 정확히 days_before 일 후 만료될 때만 대상.
        if (period_to - today).days != days_before:
            continue
        # 같은 만료일에 같은 종류 이미 보냈으면 스킵.
        if history is not None and history.was_sent_for(m.user_id, kind, period_to):
            continue
        targets.append(ExpiryTarget(
            member=m, expiry_date=period_to, days_left=days_before,
        ))
    targets.sort(key=lambda t: t.expiry_date)
    return targets


def template_expiry_reminder_7(
    member: Member, expiry_date: date,
) -> tuple[str, str]:
    """만료 7일 전 — 여유 있게 재결제 안내."""
    nick = member.nickname or member.name or member.user_id
    subject = "[초록등대] 자료실 구독 만료 안내 — 7일 후 만료"
    body = (
        f"{nick} 회원님 안녕하세요.\n\n"
        f"초록등대 동호회 운영진입니다.\n\n"
        f"회원님의 자료실 구독이 {expiry_date.isoformat()} 에 만료될 예정입니다 "
        f"(7일 후).\n\n"
        f"계속 이용하실 분께서는 만료일 전에 자료실 구독비 입금을 부탁드립니다. "
        f"입금 안내(단가표)는 동호회 안내 글을 참고해 주세요.\n\n"
        f"만료 후에도 회원 자격은 그대로 유지되며, 언제든 다시 구독하실 수 있습니다.\n"
        f"문의는 운영진 메일로 주시면 됩니다.\n\n"
        f"감사합니다.\n"
        f"초록등대 운영진 드림"
    )
    return subject, body


def template_expiry_reminder_3(
    member: Member, expiry_date: date,
) -> tuple[str, str]:
    """만료 3일 전 — 마지막 독촉."""
    nick = member.nickname or member.name or member.user_id
    subject = "[초록등대] 자료실 구독 만료 임박 — 3일 후 만료"
    body = (
        f"{nick} 회원님 안녕하세요.\n\n"
        f"초록등대 동호회 운영진입니다.\n\n"
        f"회원님의 자료실 구독 만료가 임박했습니다.\n"
        f"만료일: {expiry_date.isoformat()} (3일 후).\n\n"
        f"계속 이용하실 분께서는 늦지 않게 자료실 구독비 입금을 부탁드립니다. "
        f"만료일이 지나면 자료실 접근이 일시적으로 제한될 수 있습니다.\n\n"
        f"이미 입금하셨다면 처리에 며칠 걸릴 수 있으니 양해 부탁드리며, "
        f"문의는 운영진 메일로 주시면 됩니다.\n\n"
        f"감사합니다.\n"
        f"초록등대 운영진 드림"
    )
    return subject, body


def template_for_kind(kind: str):
    """kind 에 맞는 본문 템플릿 함수를 돌려준다."""
    if kind == KIND_EXPIRY_REMINDER_7:
        return template_expiry_reminder_7
    if kind == KIND_EXPIRY_REMINDER_3:
        return template_expiry_reminder_3
    raise ValueError(f"지원하지 않는 kind: {kind}")
