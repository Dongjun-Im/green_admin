"""payment_mail — 자료실 구독비 메일 템플릿 단위 테스트."""
from __future__ import annotations

from datetime import date

from core.models import Member
from core.payment_mail import (
    template_reapply_notice,
    template_subscription_expiring,
    template_subscription_welcome,
)


def _m() -> Member:
    return Member(user_id="hong", name="홍길동", nickname="홍이", level=5)


# ---------- 환영 ----------

def test_welcome_new_includes_member_and_period():
    subj, body = template_subscription_welcome(_m(), date(2026, 6, 7), 1)
    assert "[초록등대 자료실]" in subj
    assert "활성화" in subj
    assert "홍이" in body            # 닉 우선
    assert "1개월" in body
    assert "2026-06-07" in body


def test_welcome_renewal_uses_renewal_tone():
    subj, body = template_subscription_welcome(
        _m(), date(2027, 5, 7), 12, is_renewal=True,
    )
    assert "연장" in subj
    assert "12개월" in subj
    assert "연장" in body


def test_welcome_falls_back_to_name_then_user_id():
    only_user_id = Member(user_id="anon", name="", nickname="", level=5)
    only_name = Member(user_id="x", name="실명만", nickname="", level=5)
    _, b1 = template_subscription_welcome(only_user_id, date(2026, 6, 7), 1)
    _, b2 = template_subscription_welcome(only_name, date(2026, 6, 7), 1)
    assert "anon" in b1
    assert "실명만" in b2


# ---------- 만료 임박 ----------

def test_expiring_includes_days_left_and_pricing():
    subj, body = template_subscription_expiring(_m(), date(2026, 5, 16), 7)
    assert "7일 전" in subj
    assert "7일 남아" in body
    assert "2026-05-16" in body
    # 단가표가 본문에 들어있어 회원이 바로 입금 가능
    assert "3,000원" in body
    assert "24,000원" in body


# ---------- 재신청 ----------

def test_reapply_with_reason_inserts_block():
    subj, body = template_reapply_notice(_m(), reason="이메일 오타")
    assert "재신청" in subj
    assert "오류 사유" in body
    assert "이메일 오타" in body


def test_reapply_without_reason_omits_block():
    _, body = template_reapply_notice(_m())
    # reason 미입력 → "오류 사유" 라인 없이도 메일 본문 자연스럽게 나옴
    assert "오류 사유" not in body
    assert "재신청" in body
