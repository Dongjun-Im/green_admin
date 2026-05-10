"""수동 메일 발송 대화상자 (rtgreen 전용).

rtgreen 아이디로 로그인한 경우에만 사용 가능.
자동 발송이 실패했거나 임의 대상에게 직접 공지할 때 사용.

대상 선택:
  - 직접 입력 (쉼표 구분)
  - 최근 조정(탈퇴/강등)된 회원들
  - 최근 승급된 회원들
  - 등급별 전체 발송: 준회원/일반회원/우수회원/최우수회원/명예회원
  - 레벨 5~9 전체

첨부파일 (v1.0): 한 개 이상의 파일을 첨부해 모든 수신자에게 함께 발송.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import wx

from config import LEVEL_LABELS
from core.mail_sender import (
    MailSender,
    SEND_MODE_INDIVIDUAL,
    template_delete,
    template_demote,
    template_promote,
)
from core.models import AdjustmentReport, Member
from core.promotion_service import PromotionReport
from screen_reader import speak


class ManualMailDialog(wx.Dialog):
    def __init__(
        self,
        parent,
        mail_sender: MailSender,
        last_adjust_report: Optional[AdjustmentReport] = None,
        last_promo_report: Optional[PromotionReport] = None,
        members: Optional[list[Member]] = None,
    ):
        super().__init__(
            parent,
            title="수동 메일 발송",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.mail_sender = mail_sender
        self.last_adjust_report = last_adjust_report
        self.last_promo_report = last_promo_report
        self.members: list[Member] = list(members or [])
        self.last_result_lines: list[str] = []
        self.attachments: list[Path] = []

        self._build_ui()
        self.SetMinSize(wx.Size(760, 660))
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
        sizer.Add(
            wx.StaticText(panel, label="수신 대상(&T):"),
            0, wx.LEFT | wx.RIGHT, 8,
        )

        choices = self._build_group_choices()
        # majorDimension=1 + RA_SPECIFY_COLS → 한 줄에 1개씩 세로 배치.
        # 이래야 위/아래 방향키로 라디오 항목 사이를 이동할 수 있다.
        self.group_radio = wx.RadioBox(
            panel,
            choices=[c[0] for c in choices],
            majorDimension=1,
            style=wx.RA_SPECIFY_COLS,
            name="수신 대상",
        )
        self._group_data = choices  # [(label, recipients, subject, body), ...]
        if choices:
            self.group_radio.SetSelection(0)
        sizer.Add(self.group_radio, 0, wx.EXPAND | wx.ALL, 8)

        # 발송 방식 안내 (v0.5 — 항상 개별 발송)
        mode_notice = wx.StaticText(
            panel,
            label=(
                "발송 방식: 각 회원에게 개별 발송 (수신자에게 다른 수신자 ID 노출 안 됨).\n"
                "  · 회원 수만큼 시간이 걸립니다 — 진행률은 음성으로 안내됩니다."
            ),
        )
        sizer.Add(mode_notice, 0, wx.ALL, 8)

        # 수신인 편집창 (쉼표 구분)
        sizer.Add(
            wx.StaticText(panel, label="수신 아이디(쉼표 구분)(&R):"),
            0, wx.LEFT | wx.RIGHT, 8,
        )
        self.recipients_input = wx.TextCtrl(
            panel, value="",
            style=wx.TE_MULTILINE,
            size=(-1, 60),
            name="수신 아이디 편집창",
        )
        sizer.Add(self.recipients_input, 0, wx.EXPAND | wx.ALL, 8)

        sizer.Add(
            wx.StaticText(panel, label="제목(&S):"),
            0, wx.LEFT | wx.RIGHT, 8,
        )
        self.subject_input = wx.TextCtrl(panel, value="", name="제목 편집창")
        sizer.Add(self.subject_input, 0, wx.EXPAND | wx.ALL, 8)

        sizer.Add(
            wx.StaticText(panel, label="내용(&B):"),
            0, wx.LEFT | wx.RIGHT, 8,
        )
        self.body_input = wx.TextCtrl(
            panel, value="",
            style=wx.TE_MULTILINE,
            name="내용 편집창",
        )
        sizer.Add(self.body_input, 1, wx.EXPAND | wx.ALL, 8)

        # 첨부파일 영역 (v1.0 신규)
        sizer.Add(
            wx.StaticText(panel, label="첨부파일(&I):"),
            0, wx.LEFT | wx.RIGHT, 8,
        )
        self.attach_list = wx.ListBox(
            panel, choices=[], style=wx.LB_SINGLE,
            size=(-1, 80),
            name="첨부파일 목록",
        )
        sizer.Add(self.attach_list, 0, wx.EXPAND | wx.ALL, 8)

        attach_btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.attach_add_btn = wx.Button(panel, label="추가(&P)")
        self.attach_remove_btn = wx.Button(panel, label="제거(&V)")
        self.attach_clear_btn = wx.Button(panel, label="전체 제거(&Z)")
        attach_btn_sizer.Add(self.attach_add_btn, 0, wx.ALL, 5)
        attach_btn_sizer.Add(self.attach_remove_btn, 0, wx.ALL, 5)
        attach_btn_sizer.Add(self.attach_clear_btn, 0, wx.ALL, 5)
        sizer.Add(attach_btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 4)

        # 발송 / 취소
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.send_btn = wx.Button(panel, label="발송(&G)")
        self.cancel_btn = wx.Button(panel, wx.ID_CANCEL, "취소(&C)")
        btn_sizer.Add(self.send_btn, 0, wx.ALL, 5)
        btn_sizer.Add(self.cancel_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 8)

        panel.SetSizer(sizer)

        self.group_radio.Bind(wx.EVT_RADIOBOX, self._on_group_change)
        self.send_btn.Bind(wx.EVT_BUTTON, self._on_send)
        self.attach_add_btn.Bind(wx.EVT_BUTTON, self._on_attach_add)
        self.attach_remove_btn.Bind(wx.EVT_BUTTON, self._on_attach_remove)
        self.attach_clear_btn.Bind(wx.EVT_BUTTON, self._on_attach_clear)

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

        # 1) 직접 입력 (항상 첫 번째)
        items.append((
            "직접 입력 (수신 아이디를 직접 입력)",
            [],
            "[초록등대] 공지",
            "",
        ))

        # 2) 최근 조정 - 탈퇴
        if self.last_adjust_report and self.last_adjust_report.succeeded_delete:
            group = self.last_adjust_report.succeeded_delete
            subj, body = template_delete(group[0])
            items.append((
                f"최근 탈퇴 처리된 회원 ({len(group)}명)",
                [m.user_id for m in group],
                subj,
                body,
            ))

        # 3) 최근 조정 - 강등
        if self.last_adjust_report and self.last_adjust_report.succeeded_demote:
            group = self.last_adjust_report.succeeded_demote
            subj, body = template_demote(group[0], "현재 등급", "한 단계 낮은 등급")
            items.append((
                f"최근 강등된 회원 ({len(group)}명)",
                [m.user_id for m in group],
                subj,
                body,
            ))

        # 4) 최근 승급
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

        # 5) 등급별 전체 발송 (사이트 cl_level 매핑과 일치)
        # 4,5,6,7,8 5개 등급(준회원~명예회원) 을 항상 라디오에 표시 (0명도 포함).
        if self.members:
            for lv in (4, 5, 6, 7, 8):
                ids = [m.user_id for m in self.members if m.level == lv]
                label = LEVEL_LABELS.get(lv, f"레벨 {lv}")
                items.append((
                    f"{label}(레벨 {lv}) 전체 ({len(ids)}명)",
                    ids,
                    f"[초록등대] {label} 안내",
                    "",
                ))
            # 통합 — 가입 완료된 전체 회원 (4~8)
            ids_all = [m.user_id for m in self.members if 4 <= m.level <= 8]
            items.append((
                f"가입 완료된 전체 회원 ({len(ids_all)}명)",
                ids_all,
                "[초록등대] 전체 회원 공지",
                "",
            ))
        else:
            items.append((
                "등급별 발송 (회원 목록 미수집 — Ctrl+F 한 번 후 다시 여세요)",
                [],
                "",
                "",
            ))

        return items

    def _apply_group(self, idx: int) -> None:
        if not (0 <= idx < len(self._group_data)):
            return
        label, rec, subj, body = self._group_data[idx]
        self.recipients_input.SetValue(", ".join(rec))
        if subj:
            self.subject_input.SetValue(subj)
        if body:
            self.body_input.SetValue(body)

    def _on_group_change(self, event) -> None:
        self._apply_group(self.group_radio.GetSelection())

    # ----- 첨부 -----

    def _on_attach_add(self, event=None) -> None:
        with wx.FileDialog(
            self,
            "첨부할 파일을 선택하세요",
            wildcard="모든 파일 (*.*)|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE,
        ) as fd:
            if fd.ShowModal() != wx.ID_OK:
                return
            for raw in fd.GetPaths():
                p = Path(raw)
                if p.exists() and p not in self.attachments:
                    self.attachments.append(p)
        self._refresh_attach_list()
        speak(f"첨부파일 {len(self.attachments)}개")

    def _on_attach_remove(self, event=None) -> None:
        idx = self.attach_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.attachments):
            return
        removed = self.attachments.pop(idx)
        self._refresh_attach_list()
        speak(f"{removed.name} 제거")

    def _on_attach_clear(self, event=None) -> None:
        if not self.attachments:
            return
        self.attachments = []
        self._refresh_attach_list()
        speak("첨부파일 모두 제거")

    def _refresh_attach_list(self) -> None:
        self.attach_list.Set([
            f"{p.name}  ({_human_size(p)})"
            for p in self.attachments
        ])
        if self.attachments:
            self.attach_list.SetSelection(len(self.attachments) - 1)

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

        # 첨부 정보 — 확인 메시지에 포함
        attach_text = ""
        if self.attachments:
            names = ", ".join(p.name for p in self.attachments)
            total = sum(p.stat().st_size for p in self.attachments if p.exists())
            attach_text = (
                f"\n첨부 {len(self.attachments)}개 (총 {_format_bytes(total)}): {names}"
            )

        confirm = wx.MessageBox(
            f"{len(recipients)}명에게 메일을 개별 발송합니다.{attach_text}\n"
            f"계속하시겠습니까?\n"
            f"(회원당 약 1초 정도 소요. 이 작업은 되돌릴 수 없습니다.)",
            "발송 확인",
            wx.YES_NO | wx.ICON_QUESTION | wx.NO_DEFAULT,
        )
        if confirm != wx.YES:
            return

        speak(f"{len(recipients)}명에게 개별 발송을 시작합니다.")

        def _progress(current: int, total: int) -> None:
            step = max(1, total // 4)
            if current == 1 or current == total or current % step == 0:
                wx.CallAfter(speak, f"{current} / {total} 발송 중")

        results = self.mail_sender.send(
            recipients, subject, body,
            mode=SEND_MODE_INDIVIDUAL,
            progress_cb=_progress,
            attachments=list(self.attachments),
        )

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


def _human_size(p: Path) -> str:
    try:
        return _format_bytes(p.stat().st_size)
    except OSError:
        return "?"


def _format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"
