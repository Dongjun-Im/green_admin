"""메일 템플릿 — 환영/승급/강등/탈퇴."""
from __future__ import annotations


class _FakeMember:
    def __init__(self, user_id="hong", nickname="홍이", name="홍길동"):
        self.user_id = user_id
        self.nickname = nickname
        self.name = name


def test_template_welcome_subject_and_body():
    from core.mail_sender import template_welcome
    subject, body = template_welcome(_FakeMember())
    assert "초록등대" in subject
    assert "환영" in subject
    assert "홍이" in body  # 닉네임 포함
    # 등급 안내 문구 포함
    assert "활동점수" in body
    assert "일반회원" in body and "명예회원" in body


def test_template_welcome_uses_user_id_when_no_nickname():
    from core.mail_sender import template_welcome
    m = _FakeMember(user_id="kim", nickname="", name="")
    _, body = template_welcome(m)
    assert "kim" in body


def test_template_welcome_returns_str_pair():
    from core.mail_sender import template_welcome
    s, b = template_welcome(_FakeMember())
    assert isinstance(s, str) and isinstance(b, str)
    assert len(s) > 0 and len(b) > 0
