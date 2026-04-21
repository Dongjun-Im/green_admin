"""수동 메일 발송 대화상자 (rtgreen 전용).

rtgreen 아이디로 로그인한 경우에만 사용 가능.
자동 발송이 실패했거나 임의 대상에게 직접 공지할 때 사용.

대상 선택:
  - 최근 조정(탈퇴)된 회원들
  - 최근 조정(강등)된 회원들
  - 최근 승급된 회원들
  - 직접 입력 (쉼표 구분)

제목/내용은 선택에 따라 기본 템플릿이 채워지며, 사용자가 수정 가능.
"""
from __future__ import annotations

from typing import Optional

import wx

from config import LEVEL_LABELS
from core.mail_sender import (
    MailSender,
    SEND_MODE_BULK,
    SEND_MODE_INDIVIDUAL,
    template_delete,
    template_demote,
    template_promote,
)
from core.models import AdjustmentReport
from core.promotion_service import PromotionReport
from screen_reader import speak
from ui.item_text_ctrl import ItemTextCtrl


class ManualMailDialog(wx.Dialog):
    def __init__(
        self,
        parent,
        mail_sender: MailSender,
        last_adjust_report: Optional[AdjustmentReport] = None,
        last_promo_report: Optional[PromotionReport] = None,
    ):
        super().__init__(
            parent,
            title="수동 메일 발송",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.mail_sender = mail_sender
        self.last_adjust_report = last_adjust_report
        self.last_promo_report = last_promo_report
        self.last_result_lines: list[str] = []

        self._build_ui()
        self.SetMinSize(wx.Size(720, 560))
        self.Fit()
        self.Centre()
        wx.CallAfter(self._announce_initial)

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        notice = wx.StaticText(
            panel,
            label="⚠ 수동 메일 발송은 rtgreen 아이디로 로그인한 경우에만 동작합니다.",
        )
        sizer.Add(notice, 0, wx.ALL, 8)

        # 수신 그룹 선택 (라디오)
        group_label = wx.StaticText(panel, label="수신 대상(&T):")
        sizer.Add(group_label, 0, wx.LEFT | wx.RIGHT, 8)

        choices = self._build_group_choices()
        self.group_radio = wx.RadioBox(
            panel,
            choices=[c[0] for c in choices],
            majorDimension=1,
            style=wx.RA_SPECIFY_COLS,
            name="수신 대상",
        )
        self._group_data = choices   # [(label, recipients, subject, body), ...]
        if choices:
            self.group_radio.SetSelection(0)
        sizer.Add(self.group_radio, 0, wx.EXPAND | wx.ALL, 8)

        # 발송 방식 라디오
        mode_label = wx.StaticText(panel, label="발송 방식(&O):")
        sizer.Add(mode_label, 0, wx.LEFT | wx.RIGHT, 8)
        self.mode_radio = wx.RadioBox(
            panel,
            choices=[
                "모두에게 한 번에 발송 (빠름, 수신자가 서로의 아이디를 볼 수 있음)",
                "각 회원에게 개별 발송 (느림, 프라이버시 보호)",
            ],
            majorDimension=1,
            style=wx.RA_SPECIFY_COLS,
            name="발송 방식",
        )
        self.mode_radio.SetSelection(0)  # 기본: 일괄
        sizer.Add(self.mode_radio, 0, wx.EXPAND | wx.ALL, 8)

        # 수신인 편집창 (쉼표 구분)
        rec_label = wx.StaticText(panel, label="수신 아이디(쉼표 구분)(&R):")
        sizer.Add(rec_label, 0, wx.LEFT | wx.RIGHT, 8)
        self.recipients_input = wx.TextCtrl(
            panel,
            value="",
            style=wx.TE_MULTILINE,
            size=(-1, 60),
            name="수신 아이디 편집창",
        )
        sizer.Add(self.recipients_input, 0, wx.EXPAND | wx.ALL, 8)

        subj_label = wx.StaticText(panel, label="제목(&S):")
        sizer.Add(subj_label, 0, wx.LEFT | wx.RIGHT, 8)
        self.subject_input = wx.TextCtrl(panel, value="", name="제목 편집창")
        sizer.Add(self.subject_input, 0, wx.EXPAND | wx.ALL, 8)

        body_label = wx.StaticText(panel, label="내용(&B):")
        sizer.Add(body_label, 0, wx.LEFT | wx.RIGHT, 8)
        self.body_input = wx.TextCtrl(
            panel,
            value="",
            style=wx.TE_MULTILINE,
            name="내용 편집창",
        )
        sizer.Add(self.body_input, 1, wx.EXPAND | wx.ALL, 8)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.send_btn = wx.Button(panel, label="발송(&G)")
        self.cancel_btn = wx.Button(panel, wx.ID_CANCEL, "취소(&C)")
        btn_sizer.Add(self.send_btn, 0, wx.ALL, 5)
        btn_sizer.Add(self.cancel_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 8)

        panel.SetSizer(sizer)

        self.group_radio.Bind(wx.EVT_RADIOBOX, self._on_group_change)
        self.send_btn.Bind(wx.EVT_BUTTON, self._on_send)

        # 초기값 채우기
        self._apply_group(0)
        self.subject_input.SetFocus()

    def _announce_initial(self) -> None:
        if not self.mail_sender.enabled:
            speak("경고. rtgreen 아이디로 로그인하지 않아 메일이 실제로 발송되지 않습니다.")
        else:
            speak("수동 메일 발송. 수신 대상과 내용을 확인한 뒤 발송 버튼을 누르세요.")

    # ----- 그룹 선택 -----

    def _build_group_choices(self) -> list[tuple[str, list[str], str, str]]:
        """(표시 라벨, 수신 아이디 리스트, 제목, 본문) 의 목록."""
        items: list[tuple[str, list[str], str, str]] = []

        # 1) 최근 조정 - 탈퇴
        if self.last_adjust_report and self.last_adjust_report.succeeded_delete:
            group = self.last_adjust_report.succeeded_delete
            subj, body = template_delete(group[0])
            items.append((
                f"최근 탈퇴 처리된 회원 ({len(group)}명)",
                [m.user_id for m in group],
                subj,
                body,
            ))

        # 2) 최근 조정 - 강등
        if self.last_adjust_report and self.last_adjust_report.succeeded_demote:
            group = self.last_adjust_report.succeeded_demote
            subj, body = template_demote(group[0], "현재 등급", "한 단계 낮은 등급")
            items.append((
                f"최근 강등된 회원 ({len(group)}명)",
                [m.user_id for m in group],
                subj,
                body,
            ))

        # 3) 최근 승급
        if self.last_promo_report and self.last_promo_report.succeeded:
            group = self.last_promo_report.succeeded
            sample = group[0]
            from_label = LEVEL_LABELS.get(sample.from_level, f"레벨 {sample.from_level}")
            to_label = LEVEL_LABELS.get(sample.to_level, f"레벨 {sample.to_level}")
            avg = sum(it.post_count for it in group) // max(1, len(group))
            subj, body = template_promote(sample.member, from_label, to_label, avg)
            items.append((
                f"최근 승급된 회원 ({len(group)}명)",
                [it.member.user_id for it in group],
                subj,
                body,
            ))

        # 4) 항상 사용 가능: 직접 입력
        items.append((
            "직접 입력 (수신 아이디를 직접 입력)",
            [],
            "[초록등대] 공지",
            "",
        ))
        return items

    def _apply_group(self, idx: int) -> None:
        if not (0 <= idx < len(self._group_data)):
            return
        label, rec, subj, body = self._group_data[idx]
        self.recipients_input.SetValue(", ".join(rec))
        self.subject_input.SetValue(subj)
        self.body_input.SetValue(body)

    def _on_group_change(self, event) -> None:
        self._apply_group(self.group_radio.GetSelection())

    # ----- 발송 -----

    def _on_send(self, event) -> None:
        if not self.mail_sender.enabled:
            wx.MessageBox(
                f"이 기능은 {self.mail_sender.SENDER_USER_ID} 아이디로 로그인했을 때에만 사용할 수 있습니다.\n"
                f"현재 로그인된 아이디: {self.mail_sender.current_user_id}",
                "rtgreen 전용 기능",
                wx.OK | wx.ICON_WARNING,
            )
            speak("rtgreen 전용 기능입니다. 발송이 취소되었습니다.")
            return

        raw = self.recipients_input.GetValue().replace("\n", ",")
        recipients = [r.strip() for r in raw.split(",") if r.strip()]
        subject = self.subject_input.GetValue().strip()
        body = self.body_input.GetValue()

        if not recipients:
            wx.MessageBox("수신 아이디를 한 명 이상 입력해 주세요.", "입력 오류", wx.OK | wx.ICON_WARNING)
            return
        if not subject:
            wx.MessageBox("제목을 입력해 주세요.", "입력 오류", wx.OK | wx.ICON_WARNING)
            return
        if not body.strip():
            wx.MessageBox("내용을 입력해 주세요.", "입력 오류", wx.OK | wx.ICON_WARNING)
            return

        mode_idx = self.mode_radio.GetSelection()
        mode = SEND_MODE_INDIVIDUAL if mode_idx == 1 else SEND_MODE_BULK
        mode_label = "개별 발송" if mode == SEND_MODE_INDIVIDUAL else "일괄 발송"

        confirm = wx.MessageBox(
            f"{len(recipients)}명에게 메일을 발송합니다. ({mode_label})\n"
            f"계속하시겠습니까?\n"
            f"(이 작업은 되돌릴 수 없습니다)",
            "발송 확인",
            wx.YES_NO | wx.ICON_QUESTION | wx.NO_DEFAULT,
        )
        if confirm != wx.YES:
            return

        speak(f"{len(recipients)}명에게 {mode_label}을 시작합니다.")
        results = self.mail_sender.send(recipients, subject, body, mode=mode)

        ok_count = sum(1 for r in results if r.success)
        fail_count = sum(1 for r in results if not r.success and not r.skipped)
        skipped = sum(1 for r in results if r.skipped)

        summary = f"발송 완료: 성공 {ok_count}건, 실패 {fail_count}건"
        if skipped:
            summary += f", 건너뜀 {skipped}건"

        self.last_result_lines = [summary]
        for r in results:
            tag = "OK" if r.success else ("SKIP" if r.skipped else "FAIL")
            self.last_result_lines.append(
                f"  [{tag}] {len(r.recipients)}명: {r.message}"
            )

        wx.MessageBox(
            "\n".join(self.last_result_lines),
            "발송 결과",
            wx.OK | wx.ICON_INFORMATION if fail_count == 0 else wx.OK | wx.ICON_WARNING,
        )
        speak(summary)
        self.EndModal(wx.ID_OK)
