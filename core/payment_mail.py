"""자료실 구독비 — 메일 템플릿 모음.

`core/mail_sender.py` 의 MailSender 와 함께 사용. 발송은 rtgreen 계정으로
로그인했을 때만 동작 (MailSender 자체 가드).

3종 템플릿:
    template_subscription_welcome     — 신규/연장 활성화 환영
    template_subscription_expiring    — 만료 N일 전 안내
    template_reapply_notice           — 잘못된 신청 재신청 안내

DSM 계정 정보 삽입은 그룹 B (DSM 통합) 단계에서 추가 예정. 지금은 운영진이
별도 안내한다는 가정 하에 일반 안내 텍스트만 담는다.
"""
from __future__ import annotations

from datetime import date

from core.models import Member


def _nick(member: Member) -> str:
    return member.nickname or member.name or member.user_id


def template_subscription_welcome(
    member: Member,
    period_to: date,
    months: int,
    *,
    is_renewal: bool = False,
) -> tuple[str, str]:
    """자료실 구독 활성화 환영 메일.

    is_renewal=True 면 연장 안내 톤, 아니면 신규 활성화 톤.
    """
    nick = _nick(member)
    if is_renewal:
        subject = f"[초록등대 자료실] 구독 연장이 확인되었습니다 ({months}개월)"
        intro = "자료실 구독 연장이 확인되어 안내드립니다."
    else:
        subject = "[초록등대 자료실] 구독이 활성화되었습니다"
        intro = "자료실 구독이 활성화되어 안내드립니다."

    body = (
        f"{nick} 회원님, 안녕하세요.\n\n"
        f"초록등대 자료실 운영진입니다.\n\n"
        f"{intro}\n\n"
        f"  - 구독 기간 : {months}개월\n"
        f"  - 만료 예정일 : {period_to.isoformat()}\n\n"
        f"자료실 접속 방법과 이용 동의 사항은 별도 안내 드립니다.\n"
        f"문의 사항은 이 메일로 회신해 주세요.\n\n"
        f"감사합니다.\n"
        f"초록등대 운영진 드림"
    )
    return subject, body


def template_subscription_expiring(
    member: Member,
    period_to: date,
    days_left: int,
) -> tuple[str, str]:
    """만료 N일 전 안내 메일."""
    nick = _nick(member)
    subject = f"[초록등대 자료실] 구독 만료 {days_left}일 전 안내"
    body = (
        f"{nick} 회원님, 안녕하세요.\n\n"
        f"초록등대 자료실 운영진입니다.\n\n"
        f"회원님의 자료실 구독 만료가 {days_left}일 남아 안내드립니다.\n\n"
        f"  - 만료 예정일 : {period_to.isoformat()}\n\n"
        f"계속 이용을 원하시면 만료일 전에 연장 입금을 부탁드립니다.\n"
        f"단가표:\n"
        f"  - 1개월   : 3,000원\n"
        f"  - 3개월   : 9,000원\n"
        f"  - 6개월   : 12,000원\n"
        f"  - 12개월  : 24,000원\n\n"
        f"입금이 확인되면 만료일 다음날부터 자동으로 연장됩니다.\n\n"
        f"감사합니다.\n"
        f"초록등대 운영진 드림"
    )
    return subject, body


def template_reapply_notice(
    member: Member,
    reason: str = "",
) -> tuple[str, str]:
    """잘못 신청한 사용자에게 재신청 안내."""
    nick = _nick(member)
    subject = "[초록등대 자료실] 재신청 안내드립니다"
    reason_block = (
        f"\n오류 사유:\n  - {reason}\n"
        if reason else ""
    )
    body = (
        f"{nick} 회원님, 안녕하세요.\n\n"
        f"초록등대 자료실 운영진입니다.\n\n"
        f"앞서 제출하신 자료실 신청서에 일부 항목이 정확하지 않아\n"
        f"재신청을 부탁드리고자 안내드립니다.\n"
        f"{reason_block}\n"
        f"신청 폼 링크와 작성 안내는 별도로 전달드립니다.\n"
        f"문의 사항은 이 메일로 회신해 주세요.\n\n"
        f"감사합니다.\n"
        f"초록등대 운영진 드림"
    )
    return subject, body
