"""MVP TOP N 결과 표시 다이얼로그."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import wx

from core.mvp_service import MvpReport, write_mvp_report
from screen_reader import speak
from ui.item_text_ctrl import ItemTextCtrl


class MvpDialog(wx.Dialog):
    def __init__(self, parent, report: MvpReport):
        super().__init__(
            parent,
            title=f"MVP TOP {len(report.items)} — {report.quarter}",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.report = report
        self._build_ui()
        self.SetMinSize(wx.Size(720, 520))
        self.Fit()
        self.Centre()
        wx.CallAfter(self._announce)

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        summary = (
            f"분기: {self.report.quarter} · "
            f"분석 대상 {self.report.total_scanned}명 / 산정 {self.report.total_counted}명 · "
            f"게시판: {', '.join(self.report.boards)}"
        )
        sizer.Add(wx.StaticText(panel, label=summary), 0, wx.ALL, 10)

        sizer.Add(
            wx.StaticText(panel, label="MVP 목록(&L):"),
            0, wx.LEFT | wx.RIGHT, 10,
        )

        choices = [it.display() for it in self.report.items] or ["(MVP 후보 없음)"]
        self.list_box = wx.ListBox(
            panel, choices=choices, style=wx.LB_SINGLE, name="MVP 목록",
        )
        if self.report.items:
            self.list_box.SetSelection(0)
        sizer.Add(self.list_box, 1, wx.EXPAND | wx.ALL, 10)

        self.detail = ItemTextCtrl(
            panel, value="",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_DONTWRAP,
            name="MVP 상세",
        )
        sizer.Add(self.detail, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        export_btn = wx.Button(panel, wx.ID_ANY, "TXT 내보내기(&E)")
        close_btn = wx.Button(panel, wx.ID_CLOSE, "닫기(&C)")
        btn_sizer.Add(export_btn, 0, wx.ALL, 5)
        btn_sizer.Add(close_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 10)

        panel.SetSizer(sizer)
        self.list_box.Bind(wx.EVT_LISTBOX, self._on_select)
        export_btn.Bind(wx.EVT_BUTTON, self._on_export)
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

        if self.report.items:
            self._show_detail(0)

    def _announce(self) -> None:
        speak(self.report.speak_summary())

    def _on_select(self, event):
        idx = self.list_box.GetSelection()
        if 0 <= idx < len(self.report.items):
            self._show_detail(idx)

    def _show_detail(self, idx: int) -> None:
        if not (0 <= idx < len(self.report.items)):
            return
        it = self.report.items[idx]
        nick = it.member.nickname or it.member.name or it.member.user_id
        # v1.0.2: LEVEL_LABELS 우선 (사용자 확정 매핑)
        from config import LEVEL_LABELS as _LL
        level_text = _LL.get(it.member.level, f"레벨 {it.member.level}")
        lines = [
            f"순위: {it.rank}위",
            f"아이디: {it.member.user_id}",
            f"닉네임: {nick}",
            f"등급: {level_text} (레벨 {it.member.level})",
            f"활동점수: {it.score:.1f}",
            f"  · 총 글 수: {it.posts}건",
            f"  · 총 댓글 수: {it.comments}건",
        ]
        for bo, c in it.by_board.items():
            lines.append(f"  · {bo}: 글 {c['posts']}건 / 댓글 {c['comments']}건")
        self.detail.SetValue("\n".join(lines))
        speak(it.display())

    def _on_export(self, event):
        try:
            path = write_mvp_report(self.report)
        except Exception as e:
            wx.MessageBox(f"저장 실패: {e}", "오류", wx.OK | wx.ICON_ERROR)
            return
        speak("MVP 리포트를 저장했습니다.")
        wx.MessageBox(
            f"저장되었습니다.\n{path}",
            "내보내기 완료",
            wx.OK | wx.ICON_INFORMATION,
        )

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CLOSE)
            return
        event.Skip()
