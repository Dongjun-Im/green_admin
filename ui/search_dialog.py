"""회원 검색 대화상자 — 이름/아이디/닉네임 부분 일치 검색."""
from __future__ import annotations

import wx

from config import LEVEL_LABELS
from core.models import Member
from screen_reader import speak
from ui.item_text_ctrl import ItemTextCtrl


class MemberSearchDialog(wx.Dialog):
    def __init__(self, parent, members: list[Member]):
        super().__init__(
            parent,
            title="회원 검색",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.all_members = members
        self.filtered: list[Member] = list(members)

        self._build_ui()
        self.SetMinSize(wx.Size(640, 480))
        self.Fit()
        self.Centre()
        wx.CallAfter(self._announce_initial)

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        label = wx.StaticText(panel, label="검색어(&S) — 아이디, 이름, 닉네임 부분 일치:")
        sizer.Add(label, 0, wx.ALL, 8)

        self.search_input = wx.TextCtrl(
            panel,
            value="",
            style=wx.TE_PROCESS_ENTER,
            name="검색어",
        )
        sizer.Add(self.search_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        list_label = wx.StaticText(panel, label="결과 목록(&L):")
        sizer.Add(list_label, 0, wx.ALL, 8)

        self.list_box = wx.ListBox(
            panel,
            choices=[self._format_entry(m) for m in self.all_members],
            style=wx.LB_SINGLE,
            name="회원 목록",
        )
        if self.all_members:
            self.list_box.SetSelection(0)
        sizer.Add(self.list_box, 1, wx.EXPAND | wx.ALL, 8)

        detail_label = wx.StaticText(panel, label="선택한 회원 상세(&D):")
        sizer.Add(detail_label, 0, wx.LEFT | wx.RIGHT, 8)

        self.detail = ItemTextCtrl(
            panel,
            value="",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_DONTWRAP,
            name="회원 상세",
        )
        sizer.Add(self.detail, 0, wx.EXPAND | wx.ALL, 8)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        close_btn = wx.Button(panel, wx.ID_CLOSE, "닫기(&C)")
        btn_sizer.Add(close_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 8)

        panel.SetSizer(sizer)

        self.search_input.Bind(wx.EVT_TEXT, self._on_search_change)
        self.search_input.Bind(wx.EVT_TEXT_ENTER, self._on_enter)
        self.list_box.Bind(wx.EVT_LISTBOX, self._on_select)
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

        self.search_input.SetFocus()

    def _announce_initial(self) -> None:
        speak(f"회원 검색. 총 {len(self.all_members)}명. 검색어를 입력하세요.")
        if self.all_members:
            self._update_detail(0)

    def _format_entry(self, m: Member) -> str:
        level_label = m.level_label or LEVEL_LABELS.get(m.level, f"레벨 {m.level}")
        return f"{m.user_id} / {m.name} / {m.nickname} / {level_label}"

    def _on_search_change(self, event):
        query = self.search_input.GetValue().strip().lower()
        if not query:
            self.filtered = list(self.all_members)
        else:
            self.filtered = [
                m for m in self.all_members
                if query in m.user_id.lower()
                or query in (m.name or "").lower()
                or query in (m.nickname or "").lower()
            ]
        self.list_box.Set([self._format_entry(m) for m in self.filtered])
        if self.filtered:
            self.list_box.SetSelection(0)
            self._update_detail(0)
        else:
            self.detail.SetValue("(일치하는 회원 없음)")
            speak("검색 결과 없음")
        # 결과 개수 발화 (너무 자주 안 나오게 개수만)
        if query:
            wx.CallLater(200, lambda: speak(f"{len(self.filtered)}명 일치"))

    def _on_enter(self, event):
        # 엔터 누르면 포커스를 목록으로 이동
        if self.filtered:
            self.list_box.SetFocus()

    def _on_select(self, event):
        idx = self.list_box.GetSelection()
        if idx >= 0:
            self._update_detail(idx)

    def _update_detail(self, idx: int) -> None:
        if not (0 <= idx < len(self.filtered)):
            return
        m = self.filtered[idx]
        last = m.last_login_date.isoformat() if m.last_login_date else "알 수 없음"
        join = m.join_date.isoformat() if m.join_date else "알 수 없음"
        level_label = m.level_label or LEVEL_LABELS.get(m.level, f"레벨 {m.level}")
        lines = [
            f"아이디: {m.user_id}",
            f"이름: {m.name}",
            f"닉네임: {m.nickname}",
            f"등급: {level_label} (레벨 {m.level})",
            f"마지막 접속일: {last}",
            f"가입일: {join}",
            f"접속수: {m.login_count if m.login_count is not None else '알 수 없음'}",
        ]
        self.detail.SetValue("\n".join(lines))
        speak(self._format_entry(m))

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        code = event.GetKeyCode()
        if code == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CLOSE)
            return
        event.Skip()
