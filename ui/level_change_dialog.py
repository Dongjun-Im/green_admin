"""회원 개별 등급 변경 다이얼로그.

검색 다이얼로그에서 선택된 회원의 등급을 한 단계씩(또는 임의로) 변경한다.
사이트 처리는 MemberAdminAdapter.change_level 단건 호출로 즉시 반영된다.
"""
from __future__ import annotations

import wx

from config import LEVEL_LABELS, SELECTABLE_LEVELS
from core.models import Member
from screen_reader import speak


class LevelChangeDialog(wx.Dialog):
    """선택된 새 등급은 ShowModal 종료 후 self.new_level 에 들어 있다.
    취소되었거나 변경이 필요 없으면 None.
    """

    def __init__(self, parent, member: Member):
        super().__init__(
            parent,
            title=f"등급 변경 — {member.user_id}",
            style=wx.DEFAULT_DIALOG_STYLE,
        )
        self.member = member
        self.new_level: int | None = None

        self._build_ui()
        self.SetMinSize(wx.Size(440, 340))
        self.Fit()
        self.Centre()
        wx.CallAfter(
            speak,
            f"등급 변경. 현재 등급은 "
            f"{LEVEL_LABELS.get(member.level, member.level)} 입니다."
        )

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        cur_label = LEVEL_LABELS.get(self.member.level, str(self.member.level))
        info = (
            f"아이디: {self.member.user_id}\n"
            f"이름: {self.member.name}\n"
            f"닉네임: {self.member.nickname}\n"
            f"현재 등급: {cur_label} (레벨 {self.member.level})"
        )
        info_label = wx.StaticText(panel, label=info)
        sizer.Add(info_label, 0, wx.ALL, 10)

        choice_label = wx.StaticText(panel, label="새 등급(&L):")
        sizer.Add(choice_label, 0, wx.LEFT | wx.RIGHT, 10)

        choices = [
            f"레벨 {lv} - {LEVEL_LABELS.get(lv, str(lv))}"
            for lv in SELECTABLE_LEVELS
        ]
        self.level_choice = wx.Choice(panel, choices=choices, name="새 등급")
        if self.member.level in SELECTABLE_LEVELS:
            self.level_choice.SetSelection(SELECTABLE_LEVELS.index(self.member.level))
        else:
            self.level_choice.SetSelection(0)
        sizer.Add(self.level_choice, 0, wx.EXPAND | wx.ALL, 10)

        warning = wx.StaticText(
            panel,
            label="이 작업은 사이트에 즉시 반영되며 되돌릴 수 없습니다.\n"
                  "(Phase 3 의 실행 취소 기능 도입 전까지는 신중히 적용해 주세요.)",
        )
        sizer.Add(warning, 0, wx.ALL, 10)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        ok_btn = wx.Button(panel, wx.ID_OK, "변경(&O)")
        cancel_btn = wx.Button(panel, wx.ID_CANCEL, "취소(&C)")
        btn_sizer.Add(ok_btn, 0, wx.ALL, 5)
        btn_sizer.Add(cancel_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 10)

        panel.SetSizer(sizer)
        ok_btn.Bind(wx.EVT_BUTTON, self._on_ok)
        cancel_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CANCEL))
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

        self.level_choice.SetFocus()

    def _on_ok(self, event):
        idx = self.level_choice.GetSelection()
        if idx == wx.NOT_FOUND:
            speak("새 등급을 선택해 주세요.")
            return
        new_level = SELECTABLE_LEVELS[idx]
        if new_level == self.member.level:
            speak("같은 등급입니다. 변경이 필요 없습니다.")
            self.EndModal(wx.ID_CANCEL)
            return

        cur_label = LEVEL_LABELS.get(self.member.level, str(self.member.level))
        new_label = LEVEL_LABELS.get(new_level, str(new_level))
        nick = self.member.nickname or self.member.name or self.member.user_id
        direction = "승급" if new_level > self.member.level else "강등"

        confirm = wx.MessageBox(
            f"{self.member.user_id} ({nick}) 회원의 등급을\n"
            f"{cur_label} → {new_label} 로 {direction}합니다.\n\n"
            f"이 작업은 사이트에 즉시 반영되며 되돌릴 수 없습니다.\n"
            f"계속하시겠습니까?",
            f"등급 {direction} 최종 확인",
            wx.YES_NO | wx.ICON_WARNING | wx.NO_DEFAULT,
        )
        if confirm != wx.YES:
            speak("변경을 취소했습니다.")
            return

        self.new_level = new_level
        self.EndModal(wx.ID_OK)

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        code = event.GetKeyCode()
        if code == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        event.Skip()
