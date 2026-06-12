"""예약 공지 목록 관리 다이얼로그 (v1.4).

'공지 작성' 화면에서 예약한 공지들을 한눈에 보고 취소·삭제한다.
스크린리더 친화 ListBox 구조 — 상태 마커가 줄 맨 앞, 한 글자로 상태 인식.

  · 대기:  [예약]  YYYY-MM-DD HH:MM  제목  (게시판: green1, green3)
  · 완료:  [완료]  ...
  · 실패:  [실패]  ...
  · 취소:  [취소]  ...
"""
from __future__ import annotations

from datetime import datetime

import wx

from core.scheduled_notice import (
    STATUS_CANCELED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_POSTED,
    ScheduledNotice,
    ScheduledNoticeStore,
)
from screen_reader import speak
from ui.item_text_ctrl import ItemTextCtrl


_STATUS_MARK = {
    STATUS_PENDING: "[예약]",
    STATUS_POSTED: "[완료]",
    STATUS_FAILED: "[실패]",
    STATUS_CANCELED: "[취소]",
}
_STATUS_TEXT = {
    STATUS_PENDING: "발송 대기",
    STATUS_POSTED: "발송 완료",
    STATUS_FAILED: "발송 실패",
    STATUS_CANCELED: "취소됨",
}


class ScheduledNoticeDialog(wx.Dialog):
    def __init__(self, parent) -> None:
        super().__init__(
            parent,
            title="예약 공지 목록",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.store = ScheduledNoticeStore()
        self._items: list[ScheduledNotice] = []
        self._build_ui()
        self.SetMinSize(wx.Size(760, 500))
        self.Fit()
        self.Centre()
        wx.CallAfter(self._refresh)

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        intro = wx.StaticText(
            panel,
            label=(
                "예약한 공지 목록입니다. 정해진 시각이 지나면 약 10분마다 도는 "
                "자동 작업이 게시판에 올립니다(그 시각에 PC 가 켜져 있어야 함). "
                "위/아래 화살표로 항목을 고르고 '예약 취소' 또는 '목록에서 삭제' 를 누르세요."
            ),
        )
        intro.Wrap(720)
        sizer.Add(intro, 0, wx.ALL, 10)

        list_label = wx.StaticText(
            panel, label="예약 목록(&L) — 줄 맨 앞이 상태입니다:",
        )
        sizer.Add(list_label, 0, wx.LEFT | wx.RIGHT, 10)
        self.list_box = wx.ListBox(
            panel, choices=[], style=wx.LB_SINGLE, name="예약 공지 목록",
        )
        sizer.Add(self.list_box, 1, wx.EXPAND | wx.ALL, 10)

        detail_label = wx.StaticText(panel, label="선택한 예약 상세(&D):")
        sizer.Add(detail_label, 0, wx.LEFT | wx.RIGHT, 10)
        self.detail = ItemTextCtrl(
            panel, value="",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_DONTWRAP,
            name="예약 상세",
        )
        self.detail.SetMinSize(wx.Size(-1, 120))
        sizer.Add(self.detail, 0, wx.EXPAND | wx.ALL, 10)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.cancel_btn = wx.Button(panel, wx.ID_ANY, "예약 취소(&X)")
        self.delete_btn = wx.Button(panel, wx.ID_ANY, "목록에서 삭제(&D)")
        self.refresh_btn = wx.Button(panel, wx.ID_ANY, "새로고침(&F)")
        close_btn = wx.Button(panel, wx.ID_CLOSE, "닫기(&C)")
        for b in (self.cancel_btn, self.delete_btn, self.refresh_btn, close_btn):
            btn_sizer.Add(b, 1, wx.ALL | wx.EXPAND, 4)
        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 4)

        panel.SetSizer(sizer)

        self.list_box.Bind(wx.EVT_LISTBOX, self._on_select)
        self.cancel_btn.Bind(wx.EVT_BUTTON, self._on_cancel)
        self.delete_btn.Bind(wx.EVT_BUTTON, self._on_delete)
        self.refresh_btn.Bind(wx.EVT_BUTTON, lambda e: self._refresh())
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        self.SetEscapeId(wx.ID_CLOSE)

    # ---------- 표시 ----------

    def _refresh(self) -> None:
        self.store.load()
        # 예약 시각 오름차순 정렬.
        self._items = sorted(self.store.all(), key=lambda n: n.scheduled_at)
        self.list_box.Set([self._format_entry(n) for n in self._items])
        if self._items:
            self.list_box.SetSelection(0)
            self._update_detail(0)
        else:
            self.detail.SetValue("")
        pending = sum(1 for n in self._items if n.status == STATUS_PENDING)
        speak(f"예약 공지 전체 {len(self._items)}건, 대기 {pending}건")

    @staticmethod
    def _fmt_when(iso: str) -> str:
        try:
            return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            return iso or "(시각 미정)"

    def _format_entry(self, n: ScheduledNotice) -> str:
        mark = _STATUS_MARK.get(n.status, "[?]")
        boards = ", ".join(n.boards) or "(없음)"
        return f"{mark}  {self._fmt_when(n.scheduled_at)}  {n.subject}  (게시판: {boards})"

    def _update_detail(self, idx: int) -> None:
        if not (0 <= idx < len(self._items)):
            return
        n = self._items[idx]
        lines = [
            f"제목: {n.subject}",
            f"예약 시각: {self._fmt_when(n.scheduled_at)}",
            f"대상 게시판: {', '.join(n.boards) or '(없음)'}",
            f"공지글 등록: {'예' if n.as_notice else '아니오'}",
            f"HTML 본문: {'예' if n.use_html else '아니오'}",
            f"상태: {_STATUS_TEXT.get(n.status, n.status)}",
        ]
        if n.posted_at:
            lines.append(f"처리 시각: {self._fmt_when(n.posted_at)}")
        if n.result:
            lines.append(f"결과: {n.result}")
        body = (n.content or "").strip()
        if body:
            preview = body[:200] + ("..." if len(body) > 200 else "")
            lines.append(f"본문 미리보기: {preview}")
        self.detail.SetValue("\n".join(lines))
        speak(self._format_entry(n))

    def _on_select(self, _event=None) -> None:
        idx = self.list_box.GetSelection()
        if idx >= 0:
            self._update_detail(idx)

    def _selected(self) -> ScheduledNotice | None:
        idx = self.list_box.GetSelection()
        if 0 <= idx < len(self._items):
            return self._items[idx]
        return None

    # ---------- 동작 ----------

    def _on_cancel(self, _event=None) -> None:
        n = self._selected()
        if n is None:
            return
        if n.status != STATUS_PENDING:
            wx.MessageBox("대기 중인 예약만 취소할 수 있습니다.",
                          "취소 불가", wx.OK | wx.ICON_INFORMATION, self)
            return
        ans = wx.MessageBox(
            f"이 예약을 취소합니다.\n\n{self._format_entry(n)}\n\n계속할까요?",
            "예약 취소", wx.YES_NO | wx.ICON_QUESTION | wx.NO_DEFAULT, self,
        )
        if ans != wx.YES:
            return
        self.store.cancel(n.id)
        speak("예약을 취소했습니다.")
        self._refresh()

    def _on_delete(self, _event=None) -> None:
        n = self._selected()
        if n is None:
            return
        ans = wx.MessageBox(
            f"이 예약을 목록에서 완전히 삭제합니다.\n\n{self._format_entry(n)}\n\n계속할까요?",
            "목록에서 삭제", wx.YES_NO | wx.ICON_WARNING | wx.NO_DEFAULT, self,
        )
        if ans != wx.YES:
            return
        self.store.remove(n.id)
        speak("예약을 목록에서 삭제했습니다.")
        self._refresh()
