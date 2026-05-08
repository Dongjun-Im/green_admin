"""회원 등급 변경 영구 이력 뷰어 (v1.0).

전체 이력을 시간순으로 보여주고, 회원 ID·source 별 필터링 가능.
search_dialog 에서 특정 회원 선택 후 호출 시 그 회원 이력만 표시.
"""
from __future__ import annotations

from typing import Optional

import wx

from config import LEVEL_LABELS
from core.level_history import LevelHistoryStore
from screen_reader import speak
from ui.item_text_ctrl import ItemTextCtrl


_SOURCE_LABELS = {
    "":              "(전체)",
    "auto_promote":  "자동 승급",
    "level_adjust":  "장기미접속 조정",
    "manual":        "수동 등급 변경",
    "approve":       "가입 승인",
    "reject":        "가입 거부",
    "undo":          "되돌리기",
}


class LevelHistoryDialog(wx.Dialog):
    def __init__(
        self,
        parent,
        store: Optional[LevelHistoryStore] = None,
        focus_user_id: Optional[str] = None,
    ):
        super().__init__(
            parent,
            title="회원 등급 변경 이력",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.store = store or LevelHistoryStore()
        self.focus_user_id = (focus_user_id or "").strip()
        self.results: list = []
        self._build_ui()
        self._load()
        self.SetMinSize(wx.Size(820, 540))
        self.Fit()
        self.Centre()
        wx.CallAfter(self._announce)

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        top = wx.BoxSizer(wx.HORIZONTAL)
        top.Add(
            wx.StaticText(panel, label="회원 아이디(&U):"),
            0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5,
        )
        self.user_input = wx.TextCtrl(panel, value=self.focus_user_id, name="회원 아이디 필터")
        top.Add(self.user_input, 1, wx.ALL, 5)

        top.Add(
            wx.StaticText(panel, label="유형(&S):"),
            0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5,
        )
        self.source_choice = wx.Choice(
            panel,
            choices=list(_SOURCE_LABELS.values()),
            name="유형",
        )
        self.source_choice.SetSelection(0)
        top.Add(self.source_choice, 0, wx.ALL, 5)

        refresh_btn = wx.Button(panel, wx.ID_ANY, "새로고침(&R)")
        top.Add(refresh_btn, 0, wx.ALL, 5)

        sizer.Add(top, 0, wx.EXPAND | wx.ALL, 5)

        sizer.Add(
            wx.StaticText(panel, label="결과(&L):"),
            0, wx.LEFT | wx.RIGHT, 5,
        )
        self.list_box = wx.ListBox(
            panel, choices=[], style=wx.LB_SINGLE, name="이력 목록",
        )
        sizer.Add(self.list_box, 1, wx.EXPAND | wx.ALL, 5)

        sizer.Add(
            wx.StaticText(panel, label="상세(&D):"),
            0, wx.LEFT | wx.RIGHT, 5,
        )
        self.detail = ItemTextCtrl(
            panel, value="",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_DONTWRAP,
            name="이력 상세",
        )
        sizer.Add(self.detail, 0, wx.EXPAND | wx.ALL, 5)

        close_btn = wx.Button(panel, wx.ID_CLOSE, "닫기(&C)")
        sizer.Add(close_btn, 0, wx.ALIGN_CENTER | wx.ALL, 5)

        panel.SetSizer(sizer)
        refresh_btn.Bind(wx.EVT_BUTTON, lambda e: self._load())
        self.user_input.Bind(wx.EVT_TEXT, lambda e: self._load())
        self.source_choice.Bind(wx.EVT_CHOICE, lambda e: self._load())
        self.list_box.Bind(wx.EVT_LISTBOX, self._on_select)
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

    def _announce(self) -> None:
        speak(
            f"회원 등급 변경 이력. 총 {len(self.results)}건 표시됨. "
            f"위·아래 화살표로 탐색하세요."
        )

    def _selected_source_key(self) -> str:
        idx = self.source_choice.GetSelection()
        if idx == wx.NOT_FOUND:
            return ""
        keys = list(_SOURCE_LABELS.keys())
        return keys[idx] if idx < len(keys) else ""

    def _load(self) -> None:
        user = self.user_input.GetValue().strip()
        src = self._selected_source_key()
        try:
            self.results = self.store.search(
                user_id=user or None,
                source=src or None,
                limit=500,
            )
        except Exception as e:
            self.detail.SetValue(f"조회 실패: {e}")
            return
        self.list_box.Set([self._format_row(r) for r in self.results])
        if self.results:
            self.list_box.SetSelection(0)
            self._show_detail(0)
        else:
            self.detail.SetValue("(이력 없음)")

    def _format_row(self, r) -> str:
        from_lb = LEVEL_LABELS.get(r.from_level, str(r.from_level))
        to_lb = LEVEL_LABELS.get(r.to_level, str(r.to_level))
        src_lb = _SOURCE_LABELS.get(r.source, r.source) or r.source
        return (
            f"{r.timestamp} | {r.user_id} | {from_lb} → {to_lb} | {src_lb}"
        )

    def _on_select(self, event):
        idx = self.list_box.GetSelection()
        if 0 <= idx < len(self.results):
            self._show_detail(idx)

    def _show_detail(self, idx: int) -> None:
        r = self.results[idx]
        from_lb = LEVEL_LABELS.get(r.from_level, str(r.from_level))
        to_lb = LEVEL_LABELS.get(r.to_level, str(r.to_level))
        src_lb = _SOURCE_LABELS.get(r.source, r.source) or "(미지정)"
        lines = [
            f"시각: {r.timestamp}",
            f"회원: {r.user_id} ({r.nickname})",
            f"등급: {from_lb} (레벨 {r.from_level}) → {to_lb} (레벨 {r.to_level})",
            f"유형: {src_lb}",
            f"사유: {r.reason or '(미기재)'}",
            f"행위자: {r.actor or '(미기재)'}",
        ]
        self.detail.SetValue("\n".join(lines))
        speak(self._format_row(r))

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CLOSE)
            return
        event.Skip()
