"""회원 통계 대시보드 다이얼로그.

- 등급별 회원 수 (현재 캐시된 회원 목록 기반)
- 최근 30일 / 90일 활동 카운트 (operation_*.log 기반)
- 백업 이벤트 카운트
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import wx

from config import LEVEL_LABELS
from core.log_reader import (
    count_actions,
    count_events,
    load_recent_entries,
)
from core.models import Member
from screen_reader import speak
from ui.item_text_ctrl import ItemTextCtrl


class StatsDialog(wx.Dialog):
    def __init__(self, parent, members: Optional[list[Member]] = None):
        super().__init__(
            parent,
            title="회원 통계",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.members = members or []
        self._build_ui()
        self._refresh()
        self.SetMinSize(wx.Size(560, 540))
        self.Fit()
        self.Centre()
        wx.CallAfter(speak, "회원 통계 화면이 열렸습니다.")

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(
            wx.StaticText(panel, label="등급별 인원수(&G):"),
            0, wx.LEFT | wx.RIGHT | wx.TOP, 8,
        )
        self.level_text = ItemTextCtrl(
            panel, value="",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_DONTWRAP,
            name="등급별 인원",
        )
        sizer.Add(self.level_text, 1, wx.EXPAND | wx.ALL, 8)

        sizer.Add(
            wx.StaticText(panel, label="최근 활동(&A):"),
            0, wx.LEFT | wx.RIGHT, 8,
        )
        self.activity_text = ItemTextCtrl(
            panel, value="",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_DONTWRAP,
            name="최근 활동",
        )
        sizer.Add(self.activity_text, 1, wx.EXPAND | wx.ALL, 8)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        refresh_btn = wx.Button(panel, wx.ID_ANY, "새로고침(&R)")
        close_btn = wx.Button(panel, wx.ID_CLOSE, "닫기(&C)")
        btn_sizer.Add(refresh_btn, 0, wx.ALL, 5)
        btn_sizer.Add(close_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 8)

        panel.SetSizer(sizer)

        refresh_btn.Bind(wx.EVT_BUTTON, lambda e: self._refresh())
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

    # ---------- 데이터 ----------

    def _refresh(self) -> None:
        self._render_levels()
        self._render_activity()

    def _render_levels(self) -> None:
        if not self.members:
            self.level_text.SetValue(
                "회원 데이터가 아직 수집되지 않았습니다.\n"
                "Ctrl+F (회원 검색) 또는 Ctrl+B (백업) 을 한 번 실행한 뒤\n"
                "다시 통계를 열거나 새로고침을 누르면 채워집니다."
            )
            return

        # v1.0+: LEVEL_LABELS 사용 (사이트 매핑과 일치 확인됨).
        counts: dict[int, int] = {}
        last_login_within_30 = 0
        from datetime import date as _date
        today = _date.today()
        for m in self.members:
            counts[m.level] = counts.get(m.level, 0) + 1
            if m.last_login_date is not None:
                if (today - m.last_login_date).days <= 30:
                    last_login_within_30 += 1

        lines = [f"총 회원 수: {len(self.members)}명"]
        lines.append("")
        for lv in sorted(counts.keys(), reverse=True):
            label = LEVEL_LABELS.get(lv, f"레벨 {lv}")
            lines.append(f"  레벨 {lv}  ({label:>6})  {counts[lv]:>4}명")
        lines.append("")
        lines.append(f"최근 30일 이내 접속: {last_login_within_30}명")
        self.level_text.SetValue("\n".join(lines))

    def _render_activity(self) -> None:
        try:
            recent_30 = load_recent_entries(days=30)
            recent_90 = load_recent_entries(days=90)
            c30 = count_actions(recent_30)
            c90 = count_actions(recent_90)
            keys = sorted(set(c30.keys()) | set(c90.keys()))

            lines = [f"기준 시각: {datetime.now():%Y-%m-%d %H:%M}"]
            lines.append("")
            lines.append(f"{'유형':<8} {'30일':>6} {'90일':>6}")
            lines.append("-" * 24)
            if keys:
                for k in keys:
                    lines.append(f"{k:<8} {c30.get(k, 0):>6} {c90.get(k, 0):>6}")
            else:
                lines.append("(최근 활동 기록 없음)")
            backup_30 = count_events(recent_30, "backup")
            backup_90 = count_events(recent_90, "backup")
            lines.append("")
            lines.append(
                f"백업 이벤트: 30일 {backup_30}회 / 90일 {backup_90}회"
            )
            self.activity_text.SetValue("\n".join(lines))
        except Exception as e:
            self.activity_text.SetValue(f"활동 로그 로드 실패: {e}")

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CLOSE)
            return
        event.Skip()
