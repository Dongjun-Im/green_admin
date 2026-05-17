"""회원 검색 다이얼로그(Ctrl+F)의 green3/7/9 활동량 캐시·표시 로직 (v1.2.8).

다이얼로그 UI 자체는 wx 가 필요해 테스트하기 어렵지만, 다음은 wx 없이도
검증할 수 있다 — 모듈을 import 한 뒤 메서드를 unbound 로 호출하면서
self 자리에 가짜 객체를 넣어 비즈니스 로직만 분리해서 본다.

대상:
  · _format_activity_summary_short — 캐시 유무에 따라 행 끝에 붙는 문자열
  · 캐시 dict 구조와 키 매핑
  · _BOARD_SHORT_LABELS / _BOARD_LONG_LABELS 가 green3·7·9 만 다루는지
"""
from __future__ import annotations

import sys
import types

import pytest


@pytest.fixture(autouse=True)
def _stub_wx_and_others(monkeypatch):
    """search_dialog 가 wx 와 ui.* / core.* 의존성을 import 하므로 가짜로 채움."""
    if "wx" not in sys.modules:
        wx_stub = types.ModuleType("wx")

        # wx.Dialog 같은 베이스 클래스는 진짜 class 여야 한다 (subclassable).
        class _Dialog: pass
        class _KeyEvent: pass

        wx_stub.Dialog = _Dialog
        wx_stub.KeyEvent = _KeyEvent
        # 상수·기타 심볼은 정수/None 으로 채움 — 메서드 본문이 실행되지 않으면
        # 값은 무관.
        for attr in (
            "DEFAULT_DIALOG_STYLE", "RESIZE_BORDER", "ALL", "EXPAND", "LEFT",
            "RIGHT", "TOP", "BOTTOM", "ALIGN_CENTER_VERTICAL", "ALIGN_CENTER",
            "TE_PROCESS_ENTER", "TE_READONLY", "TE_MULTILINE", "TE_DONTWRAP",
            "ID_ANY", "ID_OK", "ID_CANCEL", "ID_CLOSE", "ID_YES", "ID_NO",
            "OK", "YES", "NO", "YES_NO", "CANCEL", "ICON_ERROR", "ICON_WARNING",
            "ICON_INFORMATION", "ICON_QUESTION", "NO_DEFAULT",
            "ACCEL_CTRL", "ACCEL_ALT", "ACCEL_NORMAL", "ACCEL_SHIFT",
            "MOD_CONTROL", "MOD_SHIFT", "MOD_ALT",
            "WXK_F2", "WXK_F5", "WXK_ESCAPE", "WXK_SPACE",
            "EVT_TEXT", "EVT_TEXT_ENTER", "EVT_CHOICE", "EVT_LISTBOX",
            "EVT_LISTBOX_DCLICK", "EVT_CHECKLISTBOX", "EVT_BUTTON",
            "EVT_CHAR_HOOK", "LB_SINGLE", "GA_HORIZONTAL",
        ):
            setattr(wx_stub, attr, 0)
        # 함수/팩토리 — 부르면 None 반환하면 됨.
        for fn in (
            "NewIdRef", "MessageBox", "MessageDialog", "RichMessageDialog",
            "SingleChoiceDialog", "FileDialog", "CallAfter", "CallLater",
            "AcceleratorTable",
        ):
            setattr(wx_stub, fn, lambda *a, **kw: None)
        # 클래스 형태 위젯 — subclass 까진 안 만들고 호출하면 더미 객체.
        for cls in (
            "Panel", "BoxSizer", "StaticText", "TextCtrl", "Choice", "Button",
            "ListBox", "CheckListBox", "Gauge", "Size",
        ):
            setattr(wx_stub, cls, type(cls, (), {"__init__": lambda self, *a, **kw: None}))
        sys.modules["wx"] = wx_stub
    # UI/코어 의존 모듈도 가짜로 — 모듈 자체가 로드 가능하기만 하면 됨.
    for name in (
        "ui.item_text_ctrl",
        "ui.level_change_dialog",
        "ui.member_note_dialog",
        "ui.progress_dialog",
        "core.member_admin",
        "core.member_notes",
        "core.admin_flags",
        "core.activity_counter",
        "screen_reader",
    ):
        if name in sys.modules:
            continue
        m = types.ModuleType(name)
        # 흔히 import 되는 심볼들에 더미 부착.
        for sym in (
            "ItemTextCtrl", "LevelChangeDialog", "MemberNoteDialog",
            "ProgressTaskDialog", "MemberAdminAdapter", "MemberNotesStore",
            "AdminFlagsStore", "ActivityCounter",
        ):
            setattr(m, sym, type(sym, (), {}))
        # screen_reader.speak
        m.speak = lambda *a, **kw: None
        sys.modules[name] = m
    yield


def _get_module():
    """search_dialog 모듈을 한 번 import 해서 돌려준다."""
    # 캐시된 거 있으면 그대로 — fixture 가 의존성을 채워 둠.
    if "ui.search_dialog" in sys.modules:
        return sys.modules["ui.search_dialog"]
    import ui.search_dialog as m
    return m


def test_board_label_constants_cover_green3_7_9():
    m = _get_module()
    short_codes = [code for code, _ in m._BOARD_SHORT_LABELS]
    long_codes = [code for code, _ in m._BOARD_LONG_LABELS]
    assert short_codes == ["green3", "green7", "green9"]
    assert long_codes == short_codes
    # 짧은 라벨은 G3/G7/G9
    assert dict(m._BOARD_SHORT_LABELS) == {
        "green3": "G3", "green7": "G7", "green9": "G9",
    }


def test_format_activity_summary_short_empty_when_no_cache():
    m = _get_module()
    fake_self = types.SimpleNamespace(_activity_cache={})
    s = m.MemberSearchDialog._format_activity_summary_short(fake_self, "anycall")
    assert s == ""


def test_format_activity_summary_short_renders_three_boards():
    m = _get_module()
    cache = {
        "anycall": {
            "green3": (5, 12),
            "green7": (0, 3),
            "green9": (2, 0),
        },
    }
    fake_self = types.SimpleNamespace(_activity_cache=cache)
    s = m.MemberSearchDialog._format_activity_summary_short(fake_self, "anycall")
    # 형태: " / G3 글5 댓12 G7 글0 댓3 G9 글2 댓0"
    assert s.startswith(" / ")
    assert "G3 글5 댓12" in s
    assert "G7 글0 댓3" in s
    assert "G9 글2 댓0" in s


def test_format_activity_summary_short_handles_partial_board_data():
    """한 게시판 조회만 실패해 캐시에 부분만 있는 경우 — 있는 것만 표시."""
    m = _get_module()
    cache = {
        "anycall": {
            "green3": (5, 12),
            # green7 누락
            "green9": (2, 0),
        },
    }
    fake_self = types.SimpleNamespace(_activity_cache=cache)
    s = m.MemberSearchDialog._format_activity_summary_short(fake_self, "anycall")
    assert "G3 글5" in s
    assert "G9 글2" in s
    assert "G7" not in s


def test_format_activity_summary_short_user_not_in_cache():
    m = _get_module()
    cache = {"someone_else": {"green3": (1, 2)}}
    fake_self = types.SimpleNamespace(_activity_cache=cache)
    s = m.MemberSearchDialog._format_activity_summary_short(fake_self, "anycall")
    assert s == ""


def test_config_search_dialog_boards_in_expected_order():
    from config import SEARCH_DIALOG_BOARDS, GREEN3_BOARD, SERIES_BOARD, QNA_BOARD
    assert SEARCH_DIALOG_BOARDS == (GREEN3_BOARD, SERIES_BOARD, QNA_BOARD)
    assert SEARCH_DIALOG_BOARDS == ("green3", "green7", "green9")
