"""작업 로그 뷰어 다이얼로그.

월별 operation_*.log 파일을 UI에서 조회.
검색어로 사용자 ID·액션 종류·이벤트 텍스트를 빠르게 필터링.
"""
from __future__ import annotations

import wx

from core.log_reader import LogEntry, list_log_files, load_log_file
from screen_reader import speak
from ui.item_text_ctrl import ItemTextCtrl


class LogViewerDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(
            parent,
            title="작업 로그 뷰어",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.files = list_log_files()
        self.entries: list[LogEntry] = []
        self.filtered: list[LogEntry] = []
        self._build_ui()
        if self.files:
            self.file_choice.SetSelection(len(self.files) - 1)
            self._load_selected()
        self.SetMinSize(wx.Size(760, 560))
        self.Fit()
        self.Centre()
        wx.CallAfter(self._announce)

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        if not self.files:
            sizer.Add(
                wx.StaticText(panel, label="로그 파일이 아직 없습니다."),
                0, wx.ALL, 20,
            )
            close_btn = wx.Button(panel, wx.ID_CLOSE, "닫기(&C)")
            sizer.Add(close_btn, 0, wx.ALIGN_CENTER | wx.ALL, 10)
            close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
            panel.SetSizer(sizer)
            return

        top = wx.BoxSizer(wx.HORIZONTAL)
        top.Add(
            wx.StaticText(panel, label="월별 로그(&M):"),
            0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5,
        )
        labels = [f.stem.replace("operation_", "") for f in self.files]
        self.file_choice = wx.Choice(panel, choices=labels, name="월별 로그")
        top.Add(self.file_choice, 1, wx.ALL, 5)
        top.Add(
            wx.StaticText(panel, label="검색(&S):"),
            0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5,
        )
        self.search_input = wx.TextCtrl(panel, name="로그 검색")
        top.Add(self.search_input, 1, wx.ALL, 5)
        sizer.Add(top, 0, wx.EXPAND | wx.ALL, 5)

        sizer.Add(
            wx.StaticText(panel, label="결과(&L):"),
            0, wx.LEFT | wx.RIGHT, 5,
        )
        self.list_box = wx.ListBox(
            panel, choices=[], style=wx.LB_SINGLE, name="로그 목록"
        )
        sizer.Add(self.list_box, 1, wx.EXPAND | wx.ALL, 5)

        sizer.Add(
            wx.StaticText(panel, label="상세(&D):"),
            0, wx.LEFT | wx.RIGHT, 5,
        )
        self.detail = ItemTextCtrl(
            panel, value="",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_DONTWRAP,
            name="로그 상세",
        )
        sizer.Add(self.detail, 0, wx.EXPAND | wx.ALL, 5)

        close_btn = wx.Button(panel, wx.ID_CLOSE, "닫기(&C)")
        sizer.Add(close_btn, 0, wx.ALIGN_CENTER | wx.ALL, 5)

        panel.SetSizer(sizer)
        self.file_choice.Bind(wx.EVT_CHOICE, lambda e: self._load_selected())
        self.search_input.Bind(wx.EVT_TEXT, lambda e: self._refilter())
        self.list_box.Bind(wx.EVT_LISTBOX, self._on_select)
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

    # ---------- 동작 ----------

    def _load_selected(self) -> None:
        idx = self.file_choice.GetSelection()
        if idx == wx.NOT_FOUND:
            return
        self.entries = load_log_file(self.files[idx])
        self._refilter()

    def _refilter(self) -> None:
        query = self.search_input.GetValue().strip().lower()
        if not query:
            self.filtered = list(self.entries)
        else:
            self.filtered = [e for e in self.entries if query in e.raw.lower()]
        self.filtered.sort(key=lambda e: e.timestamp, reverse=True)
        self.list_box.Set([self._format(e) for e in self.filtered])
        if self.filtered:
            self.list_box.SetSelection(0)
            self._show_detail(0)
        else:
            self.detail.SetValue("(검색 결과 없음)")

    def _format(self, e: LogEntry) -> str:
        ts = e.timestamp.strftime("%Y-%m-%d %H:%M")
        if e.kind == "action":
            status = "OK" if e.success else "FAIL"
            return (
                f"{ts} [{status}] {e.action} {e.user_id} "
                f"{e.from_level}→{e.to_level}"
            )
        return f"{ts} [EVENT] {e.message}"

    def _on_select(self, event) -> None:
        idx = self.list_box.GetSelection()
        if idx >= 0:
            self._show_detail(idx)

    def _show_detail(self, idx: int) -> None:
        if not (0 <= idx < len(self.filtered)):
            return
        e = self.filtered[idx]
        self.detail.SetValue(e.raw)
        speak(self._format(e))

    def _announce(self) -> None:
        if self.entries:
            speak(
                f"로그 항목 {len(self.entries)}건. "
                f"위·아래 화살표로 탐색하세요."
            )

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CLOSE)
            return
        event.Skip()
