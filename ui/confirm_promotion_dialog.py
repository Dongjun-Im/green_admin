"""자동 승급 미리보기 → 사용자 확인 대화상자.

장기미접속 조정과 동일한 2 단계 워크플로 (드라이런 → 적용) 를
자동 승급에도 적용한다.
"""
from __future__ import annotations

import wx

from config import LEVEL_LABELS
from core.promotion_service import PromotionItem, PromotionPlan
from screen_reader import speak
from ui.item_text_ctrl import ItemTextCtrl


class ConfirmPromotionDialog(wx.Dialog):
    """드라이런 결과를 항목별로 보여주고 실제 적용 여부를 묻는다.

    종료값:
      wx.ID_OK      → 사용자 승인
      wx.ID_CANCEL  → 취소
    """

    def __init__(self, parent, plan: PromotionPlan):
        super().__init__(
            parent,
            title="자동 승급 미리보기",
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

        # 목표 등급별 카운트
        by_target: dict[int, int] = {}
        for it in self.plan.items:
            by_target[it.to_level] = by_target.get(it.to_level, 0) + 1
        breakdown = ", ".join(
            f"{LEVEL_LABELS.get(lv, str(lv))} {cnt}명"
            for lv, cnt in sorted(by_target.items(), reverse=True)
        )
        summary = (
            f"총 {len(self.plan.items)}명 승급 예정. "
            f"({breakdown}) "
            f"전체 스캔 {self.plan.total_scanned}명, "
            f"게시물 카운트 완료 {self.plan.total_counted}명."
        )
        self.summary_label = wx.StaticText(panel, label=summary)
        sizer.Add(self.summary_label, 0, wx.ALL, 10)

        sizer.Add(
            wx.StaticText(panel, label="승급 대상 목록(&L):"),
            0, wx.LEFT | wx.RIGHT, 10,
        )

        self.list_box = wx.ListBox(
            panel,
            choices=[item.display() for item in self.plan.items],
            style=wx.LB_SINGLE,
            name="승급 대상 목록",
        )
        if self.plan.items:
            self.list_box.SetSelection(0)
        sizer.Add(self.list_box, 1, wx.EXPAND | wx.ALL, 10)

        self.detail = ItemTextCtrl(
            panel, value="",
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

        if not self.plan.items:
            self.apply_btn.Disable()

        self.cancel_btn.SetFocus()

    def _announce_initial(self) -> None:
        speak(
            self.summary_label.GetLabel()
            + " 위 아래 화살표로 항목을 확인하세요."
        )

    def _on_select(self, event):
        idx = self.list_box.GetSelection()
        if 0 <= idx < len(self.plan.items):
            it = self.plan.items[idx]
            self.detail.SetValue(self._format_detail(it))
            speak(it.display())

    def _format_detail(self, it: PromotionItem) -> str:
        m = it.member
        from_label = LEVEL_LABELS.get(it.from_level, f"레벨 {it.from_level}")
        to_label = LEVEL_LABELS.get(it.to_level, f"레벨 {it.to_level}")
        return (
            f"아이디: {m.user_id}\n"
            f"이름: {m.name}\n"
            f"닉네임: {m.nickname}\n"
            f"현재 등급: {from_label} (레벨 {it.from_level})\n"
            f"승급 후: {to_label} (레벨 {it.to_level})\n"
            f"게시물 수: {it.post_count}건"
        )

    def _on_export(self, event):
        with wx.FileDialog(
            self,
            "승급 계획을 TXT로 저장",
            wildcard="텍스트 파일 (*.txt)|*.txt",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
            defaultFile="promotion_plan.txt",
        ) as fd:
            if fd.ShowModal() != wx.ID_OK:
                return
            path = fd.GetPath()
        try:
            lines = [
                f"자동 승급 미리보기",
                f"전체 스캔: {self.plan.total_scanned}명, "
                f"게시물 카운트 완료: {self.plan.total_counted}명",
                f"승급 예정: {len(self.plan.items)}명",
                "=" * 60,
            ]
            for i, it in enumerate(self.plan.items, start=1):
                lines.append(f"{i:>3}. {it.display()}")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            self.exported_path = path
            speak("계획을 텍스트 파일로 저장했습니다.")
            wx.MessageBox(
                f"저장되었습니다.\n{path}",
                "내보내기 완료",
                wx.OK | wx.ICON_INFORMATION,
            )
        except OSError as e:
            wx.MessageBox(f"저장 실패: {e}", "오류", wx.OK | wx.ICON_ERROR)
