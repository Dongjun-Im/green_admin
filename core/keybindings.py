"""사용자 정의 단축키 (data/keybindings.json).

기본 단축키는 메뉴 라벨에 하드코딩되어 있고 wxPython 이 자동 처리한다.
이 모듈은 "사용자가 추가로 등록하고 싶은" 단축키를 AcceleratorTable 로 얹는 용도.

JSON 형식:
{
  "search":         "Ctrl+F",
  "stats":          "Ctrl+T",
  "promote":        "Ctrl+U",
  ...
}

지원 액션은 ACTION_NAMES 에 정의. 키 문자열은 wxPython 의 일반 형식
("Ctrl+Shift+X", "Alt+F1" 등) 을 따른다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import wx

from config import DATA_DIR


KEYBINDINGS_FILE = Path(DATA_DIR) / "keybindings.json"


# 행동 키 → 사람이 읽는 설명 (템플릿 생성용)
ACTION_NAMES: dict[str, str] = {
    "search":         "회원 검색",
    "stats":          "회원 통계",
    "backup":         "우수회원 백업 + 자동 승급",
    "promote":        "자동 승급 미리보기",
    "adjust_preview": "장기미접속 조정 미리보기",
    "adjust_apply":   "장기미접속 조정 즉시 적용",
    "manual_mail":    "수동 메일 발송",
    "check_due":      "지금 작업 가능 여부",
    "last_info":      "마지막 작업 정보",
    "open_backup":    "백업 폴더 열기",
    "logout":         "로그아웃",
    "log_viewer":     "작업 로그 뷰어",
    "backup_diff":    "백업 비교",
    "undo_last":      "마지막 작업 되돌리기",
    "help_keys":      "단축키 안내",
    "promotion_imminent": "승급 임박 회원 분석",
    "html_report":    "HTML 리포트 만들기",
}

# 기본값 (참고용 / 템플릿 출력용)
DEFAULT_BINDINGS: dict[str, str] = {
    "search":         "Ctrl+F",
    "stats":          "Ctrl+T",
    "backup":         "Ctrl+B",
    "promote":        "Ctrl+U",
    "adjust_preview": "Ctrl+R",
    "adjust_apply":   "Ctrl+Shift+R",
    "manual_mail":    "Ctrl+M",
    "check_due":      "Ctrl+D",
    "last_info":      "Ctrl+I",
    "open_backup":    "Ctrl+O",
    "logout":         "Ctrl+L",
    "log_viewer":     "Ctrl+Shift+L",
    "backup_diff":    "Ctrl+Shift+D",
    "undo_last":      "Ctrl+Z",
    "help_keys":      "Ctrl+K",
    "promotion_imminent": "",
    "html_report":    "",
}


def load_user_bindings(path: Optional[Path] = None) -> dict[str, str]:
    p = Path(path or KEYBINDINGS_FILE)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(k): str(v) for k, v in data.items()
        if k in ACTION_NAMES and isinstance(v, str)
    }


def write_template(path: Optional[Path] = None) -> Path:
    """기본값을 그대로 써 두는 템플릿 JSON 생성."""
    p = Path(path or KEYBINDINGS_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_help": (
            "각 액션 키의 단축키를 자유롭게 바꿔 저장하세요. "
            "지원 액션과 기본값은 DEFAULT_BINDINGS 와 같습니다. "
            "빈 문자열로 두면 단축키가 비활성화됩니다. "
            "프로그램을 재시작하면 적용됩니다."
        ),
        "_actions": {k: ACTION_NAMES[k] for k in ACTION_NAMES},
        **DEFAULT_BINDINGS,
    }
    p.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return p


# ---------- AcceleratorTable 변환 ----------

_MOD_MAP = {
    "ctrl":  wx.ACCEL_CTRL,
    "control": wx.ACCEL_CTRL,
    "shift": wx.ACCEL_SHIFT,
    "alt":   wx.ACCEL_ALT,
    "cmd":   wx.ACCEL_CMD,
    "meta":  wx.ACCEL_CMD,
}

_KEY_MAP = {
    "f1": wx.WXK_F1, "f2": wx.WXK_F2, "f3": wx.WXK_F3, "f4": wx.WXK_F4,
    "f5": wx.WXK_F5, "f6": wx.WXK_F6, "f7": wx.WXK_F7, "f8": wx.WXK_F8,
    "f9": wx.WXK_F9, "f10": wx.WXK_F10, "f11": wx.WXK_F11, "f12": wx.WXK_F12,
    "esc": wx.WXK_ESCAPE, "escape": wx.WXK_ESCAPE,
    "tab": wx.WXK_TAB,
    "enter": wx.WXK_RETURN, "return": wx.WXK_RETURN,
    "space": wx.WXK_SPACE,
    "del": wx.WXK_DELETE, "delete": wx.WXK_DELETE,
    "back": wx.WXK_BACK, "backspace": wx.WXK_BACK,
    "home": wx.WXK_HOME, "end": wx.WXK_END,
    "pgup": wx.WXK_PAGEUP, "pgdn": wx.WXK_PAGEDOWN,
    "up": wx.WXK_UP, "down": wx.WXK_DOWN,
    "left": wx.WXK_LEFT, "right": wx.WXK_RIGHT,
}


def parse_accel(spec: str) -> Optional[tuple[int, int]]:
    """'Ctrl+Shift+X' → (flags, keycode). 빈 문자열은 None."""
    if not spec or not spec.strip():
        return None
    parts = [p.strip() for p in spec.replace("-", "+").split("+") if p.strip()]
    if not parts:
        return None
    flags = wx.ACCEL_NORMAL
    key_part = parts[-1].lower()
    for mod in parts[:-1]:
        m = _MOD_MAP.get(mod.lower())
        if m is None:
            return None
        flags |= m
    if key_part in _KEY_MAP:
        keycode = _KEY_MAP[key_part]
    elif len(key_part) == 1:
        keycode = ord(key_part.upper())
    else:
        return None
    return (flags, keycode)


def build_accelerator_entries(
    bindings: dict[str, str], action_to_id: dict[str, int]
) -> list[wx.AcceleratorEntry]:
    out: list[wx.AcceleratorEntry] = []
    for action, spec in bindings.items():
        target_id = action_to_id.get(action)
        if target_id is None:
            continue
        parsed = parse_accel(spec)
        if parsed is None:
            continue
        flags, keycode = parsed
        out.append(wx.AcceleratorEntry(flags, keycode, target_id))
    return out
