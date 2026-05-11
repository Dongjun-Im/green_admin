"""MemberAdminAdapter._translate_level_map — 사이트 폼의 실제 옵션 매핑이
LEVEL_LABELS 와 어긋날 때 우리가 요청한 등급(예: '탈퇴') 을 실제 옵션 value 로
변환해 주는지 확인.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from core.member_admin import MemberAdminAdapter


def _adapter() -> MemberAdminAdapter:
    return MemberAdminAdapter(session=MagicMock(), dry_run=False)


def test_translate_uses_form_value_when_mapping_differs():
    """폼이 '탈퇴' 라벨에 value='8' 을 쓴다면, WITHDRAW_LEVEL=1 요청은 '8' 로 변환돼야 한다."""
    a = _adapter()
    options = {
        "동호회관리자": "0",
        "명예회원": "1",
        "최우수회원": "2",
        "우수회원": "3",
        "일반회원": "4",
        "준회원": "5",
        "대기": "6",
        "거부": "7",
        "탈퇴": "8",
    }
    eff, labels, notes = a._translate_level_map({"alice": 1}, options)  # 1 = 우리 기준 '탈퇴'
    assert eff == {"alice": "8"}
    assert labels == {"alice": "탈퇴"}
    assert notes  # 매핑이 다른 경우 메모가 남는다


def test_translate_partial_label_match():
    """옵션 라벨 텍스트가 ' 탈퇴 ' 처럼 공백·괄호로 변형돼 있어도 매칭한다."""
    a = _adapter()
    options = {"탈퇴(자동)": "9"}
    eff, labels, notes = a._translate_level_map({"x": 1}, options)
    assert eff["x"] == "9"
    assert labels["x"] == "탈퇴(자동)"


def test_translate_falls_back_when_label_missing():
    """옵션맵에 우리 라벨이 없으면 값을 그대로 보내고 경고 메모가 남는다."""
    a = _adapter()
    options = {"운영진": "0", "정회원": "5"}   # '탈퇴' 옵션 없음
    eff, labels, notes = a._translate_level_map({"x": 1}, options)
    assert eff["x"] == "1"           # 폴백
    assert any("찾지 못해" in n for n in notes)


def test_translate_empty_options_returns_pass_through():
    """폼에서 옵션맵을 못 읽었으면 변환 없이 값 그대로 보낸다 (옛 동작)."""
    a = _adapter()
    eff, labels, notes = a._translate_level_map({"x": 5, "y": 1}, {})
    assert eff == {"x": "5", "y": "1"}
    assert notes  # 옵션맵 못 읽음 경고가 들어 있다


def test_translate_matching_mapping_no_changes_no_notes():
    """폼 옵션 매핑이 LEVEL_LABELS 와 일치하면 변환이 일어나도 '값이 다름' 메모는 없다."""
    a = _adapter()
    options = {
        "탈퇴": "1", "거부": "2", "대기": "3", "준회원": "4",
        "일반회원": "5", "우수회원": "6", "최우수회원": "7",
        "명예회원": "8", "동호회관리자": "9",
    }
    eff, labels, notes = a._translate_level_map({"a": 1, "b": 6}, options)
    assert eff == {"a": "1", "b": "6"}
    assert labels == {"a": "탈퇴", "b": "우수회원"}
    # 값이 같으니 'site 실제 값' 차이 메모는 없어야 한다
    assert not any("실제 값" in n for n in notes)
