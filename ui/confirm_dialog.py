"""장기미접속 조정 미리보기 → 사용자 확인 대화상자."""
from __future__ import annotations

import wx

from core.models import AdjustmentItem, AdjustmentPlan
from screen_reader import speak
from ui.item_text_ctrl import ItemTextCtrl


class ConfirmAdjustmentDialog(wx.Dialog):
    """드라이런 결과를 항목별로 보여주고 실제 적용 여부를 묻는 대화상자.

    버튼:
      - 실제 적용(&A)  → wx.ID_OK
      - 취소(&C)       → wx.ID_CANCEL
      - TXT 내보내기(&E) → 별도 핸들러
    """

    def __init__(self, parent, plan: AdjustmentPlan):
        super().__init__(
            parent,
            title="장기미접속 조정 미리보기",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.plan = plan
        self.exported_path = None

        self._build_ui()
        self.SetMinSize(wx.Size(700, 480))
        self.Fit()
        self.Centre()
        wx.CallAfter(self._announce_initial)

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        actionable = self.plan.actionable
        summary_text = (
            f"총 {len(actionable)}건이 조정될 예정입니다. "
            f"등급 강등 {self.plan.demote_count}명, "
            f"탈퇴 {self.plan.delete_count}명. "
            f"건너뜀 {self.plan.skip_count}명."
        )
        self.summary_label = wx.StaticText(panel, label=summary_text)
        sizer.Add(self.summary_label, 0, wx.ALL, 10)

        list_label = wx.StaticText(panel, label="조정 대상 목록(&L):")
        sizer.Add(list_label, 0, wx.LEFT | wx.RIGHT, 10)

        self.list_box = wx.ListBox(
            panel,
            choices=[item.display() for item in self.plan.items],
            style=wx.LB_SINGLE,
            name="조정 대상 목록",
        )
        if self.plan.items:
            self.list_box.SetSelection(0)
        sizer.Add(self.list_box, 1, wx.EXPAND | wx.ALL, 10)

        self.detail = ItemTextCtrl(
            panel,
            value="",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_DONTWRAP,
            name="선택한 회원 상세",
        )
        sizer.Add(self.detail, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.apply_btn = wx.Button(panel, wx.ID_OK, "실제 적용(&A)")
        self.cancel_btn = wx.Button(panel, wx.ID_CANCEL, "취소(&C)")
        self.export_btn = wx.Button(panel, label="TXT 내보내기(&E)")

        btn_sizer.Add(self.apply_btn, 0, wx.ALL, 5)
        btn_sizer.Add(self.cancel_btn, 0, wx.ALL, 5)
        btn_sizer.Add(self.export_btn, 0, wx.ALL, 5)

        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 10)
        panel.SetSizer(sizer)

        self.list_box.Bind(wx.EVT_LISTBOX, self._on_select)
        self.export_btn.Bind(wx.EVT_BUTTON, self._on_export)

        # 적용 대상이 0건이면 적용 버튼 비활성화
        if not self.plan.actionable:
            self.apply_btn.Disable()

        self.cancel_btn.SetFocus()  # 안전하게 기본 포커스는 취소

    def _announce_initial(self) -> None:
        speak(self.summary_label.GetLabel() + " 위 아래 화살표로 항목을 확인하세요.")

    def _on_select(self, event):
        idx = self.list_box.GetSelection()
        if 0 <= idx < len(self.plan.items):
            item = self.plan.items[idx]
            self.detail.SetValue(self._format_detail(item))
            speak(item.display())

    def _format_detail(self, item: AdjustmentItem) -> str:
        from config import LEVEL_LABELS
        m = item.member
        last = m.last_login_date.isoformat() if m.last_login_date else "알 수 없음"
        join = m.join_date.isoformat() if m.join_date else "알 수 없음"
        if item.action == "delete":
            target = "탈퇴"
        elif item.to_level is not None:
            target = LEVEL_LABELS.get(item.to_level, f"레벨 {item.to_level}")
        else:
            target = "변경 없음"
        if item.green3_posts is None and item.green3_comments is None:
            activity_line = "green3 활동: (조회 안 함 또는 조회 실패)"
        else:
            posts = item.green3_posts if item.green3_posts is not None else "?"
            comments = item.green3_comments if item.green3_comments is not None else "?"
            activity_line = f"green3 활동: 글 {posts}건 / 댓글 {comments}건"
        return (
            f"아이디: {m.user_id}\n"
            f"이름: {m.name}\n"
            f"닉네임: {m.nickname}\n"
            f"현재 등급: {LEVEL_LABELS.get(m.level, m.level_label or f'레벨 {m.level}')} (레벨 {m.level})\n"
            f"조정 후: {target}\n"
            f"마지막 접속일: {last}\n"
            f"{activity_line}\n"
            f"가입일: {join}\n"
            f"사유: {item.reason}\n"
            f"동작: {item.action}"
        )

    def _on_export(self, event):
        with wx.FileDialog(
            self,
            "조정 계획을 TXT로 저장",
            wildcard="텍스트 파일 (*.txt)|*.txt",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
            defaultFile="adjustment_plan.txt",
        ) as fd:
            if fd.ShowModal() != wx.ID_OK:
                return
            path = fd.GetPath()
        try:
            lines = [
                f"장기미접속 조정 미리보기 ({self.plan.cutoff_date} 이전 접속자)",
                f"전체 스캔: {self.plan.total_scanned}명",
                f"등급 강등: {self.plan.demote_count}명, 탈퇴: {self.plan.delete_count}명, 건너뜀: {self.plan.skip_count}명",
                "=" * 60,
            ]
            for i, item in enumerate(self.plan.items, start=1):
                lines.append(f"{i:>3}. {item.display()}")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            self.exported_path = path
            speak("계획을 텍스트 파일로 저장했습니다.")
            wx.MessageBox(f"저장되었습니다.\n{path}", "내보내기 완료", wx.OK | wx.ICON_INFORMATION)
        except OSError as e:
            wx.MessageBox(f"저장 실패: {e}", "오류", wx.OK | wx.ICON_ERROR)
