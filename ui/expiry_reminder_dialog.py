"""자료실 구독 만료 조기 알림 메일 다이얼로그 (v1.2.11).

`NudgeMailDialog` 와 같은 발송 흐름이지만, 대상 조회가 HTTP 없는 즉시 처리이고,
중복 방지 키가 '만료일'(period_to) 이라는 점이 다르다.

생성자 파라미터 `days_before` 로 7일 / 3일 두 변종을 만든다.
"""
from __future__ import annotations

import threading
from datetime import date
from typing import Optional

import wx

from core.expiry_reminder import (
    ExpiryTarget,
    REMINDER_DAYS_BEFORE,
    find_expiry_targets,
    template_for_kind,
)
from core.mail_sender import MailSender, SEND_MODE_INDIVIDUAL
from core.models import Member
from core.nudge_history import NudgeHistoryStore
from screen_reader import speak
from ui.item_text_ctrl import ItemTextCtrl
from ui.progress_dialog import ProgressTaskDialog


class ExpiryReminderDialog(wx.Dialog):
    """`days_before` 일 후 만료될 회원들에게 알림 메일을 보낸다.

    days_before 는 7 또는 3 (REMINDER_DAYS_BEFORE 의 키).
    """

    def __init__(
        self,
        parent,
        *,
        days_before: int,
        members: list[Member],
        payment_store,
        mail_sender: MailSender,
        history: NudgeHistoryStore,
        admin_user_id: str,
        log_writer=None,
    ) -> None:
        if days_before not in REMINDER_DAYS_BEFORE:
            raise ValueError(
                f"지원하지 않는 days_before: {days_before}. "
                f"지원값: {sorted(REMINDER_DAYS_BEFORE.keys())}"
            )
        self.days_before = days_before
        self.kind = REMINDER_DAYS_BEFORE[days_before]
        super().__init__(
            parent,
            title=f"자료실 구독 만료 알림 — {days_before}일 전",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.members = members
        self.payment_store = payment_store
        self.mail_sender = mail_sender
        self.history = history
        self.admin_user_id = admin_user_id
        self.log_writer = log_writer
        self.sent_count: int = 0
        self.failed_count: int = 0
        self._targets: list[ExpiryTarget] = []

        self._build_ui()
        self.SetMinSize(wx.Size(720, 500))
        self.Fit()
        self.Centre()
        wx.CallAfter(self._load_targets)

    # ---------- UI ----------

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.summary_label = wx.StaticText(panel, label="대상 회원을 조회 중...")
        sizer.Add(self.summary_label, 0, wx.ALL, 10)

        list_label = wx.StaticText(
            panel,
            label="대상 회원 목록(&L) — 스페이스로 체크 해제하면 발송에서 빠집니다:",
        )
        sizer.Add(list_label, 0, wx.LEFT | wx.RIGHT, 10)
        self.list_box = wx.CheckListBox(panel, choices=[], name="대상 회원 목록")
        sizer.Add(self.list_box, 1, wx.EXPAND | wx.ALL, 10)

        preview_label = wx.StaticText(panel, label="메일 본문 미리보기(&P):")
        sizer.Add(preview_label, 0, wx.LEFT | wx.RIGHT, 10)
        self.preview = ItemTextCtrl(
            panel,
            value="",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_DONTWRAP,
            name="메일 본문 미리보기",
        )
        sizer.Add(self.preview, 0, wx.EXPAND | wx.ALL, 10)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.send_btn = wx.Button(panel, wx.ID_OK, "발송(&S)")
        self.send_btn.Disable()
        self.check_all_btn = wx.Button(panel, wx.ID_ANY, "모두 체크(&A)")
        self.uncheck_all_btn = wx.Button(panel, wx.ID_ANY, "체크 해제(&U)")
        self.cancel_btn = wx.Button(panel, wx.ID_CANCEL, "닫기(&C)")
        for b in (self.send_btn, self.check_all_btn, self.uncheck_all_btn, self.cancel_btn):
            btn_sizer.Add(b, 1, wx.ALL | wx.EXPAND, 4)
        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 4)

        panel.SetSizer(sizer)
        self.list_box.Bind(wx.EVT_LISTBOX, self._on_select)
        self.send_btn.Bind(wx.EVT_BUTTON, self._on_send)
        self.check_all_btn.Bind(wx.EVT_BUTTON, self._on_check_all)
        self.uncheck_all_btn.Bind(wx.EVT_BUTTON, self._on_uncheck_all)

    def _load_targets(self) -> None:
        targets = find_expiry_targets(
            self.members,
            payment_store=self.payment_store,
            days_before=self.days_before,
            admin_user_id=self.admin_user_id,
            history=self.history,
        )
        self._targets = targets
        self.list_box.Set([f"[V] {t.display()}" for t in targets])
        for i in range(self.list_box.GetCount()):
            self.list_box.Check(i, True)
        label = f"만료 {self.days_before}일 전"
        if targets:
            self.summary_label.SetLabel(
                f"{label} 알림 대상 {len(targets)}명 "
                "(같은 만료일에 이미 보낸 회원은 자동 제외됨)"
            )
            self.send_btn.Enable()
            self.list_box.SetSelection(0)
            self._update_preview(0)
            speak(f"{label} 알림 대상 {len(targets)}명 조회 완료.")
        else:
            self.summary_label.SetLabel(f"{label} 알림 대상 회원이 없습니다.")
            self.preview.SetValue("(보낼 회원이 없습니다.)")
            speak(f"{label} 알림 대상 회원이 없습니다.")

    def _update_preview(self, idx: int) -> None:
        if not (0 <= idx < len(self._targets)):
            return
        t = self._targets[idx]
        tpl = template_for_kind(self.kind)
        subject, body = tpl(t.member, t.expiry_date)
        self.preview.SetValue(f"제목: {subject}\n\n{body}")

    def _on_select(self, event):
        idx = self.list_box.GetSelection()
        if idx >= 0:
            self._update_preview(idx)

    def _on_check_all(self, event=None) -> None:
        for i in range(self.list_box.GetCount()):
            self.list_box.Check(i, True)
        speak(f"전체 {self.list_box.GetCount()}명 체크")

    def _on_uncheck_all(self, event=None) -> None:
        for i in range(self.list_box.GetCount()):
            self.list_box.Check(i, False)
        speak("체크 해제 완료")

    def _checked_targets(self) -> list[ExpiryTarget]:
        return [
            self._targets[i]
            for i in range(self.list_box.GetCount())
            if self.list_box.IsChecked(i) and i < len(self._targets)
        ]

    # ---------- 발송 ----------

    def _on_send(self, event=None) -> None:
        if not self.mail_sender.enabled:
            wx.MessageBox(
                f"이 기능은 {self.mail_sender.SENDER_USER_ID} 아이디로 "
                "로그인했을 때에만 사용할 수 있습니다.",
                "rtgreen 전용", wx.OK | wx.ICON_WARNING,
            )
            return

        targets = self._checked_targets()
        if not targets:
            wx.MessageBox(
                "보낼 회원이 한 명도 체크돼 있지 않습니다.",
                "선택 필요", wx.OK | wx.ICON_INFORMATION,
            )
            return

        label = f"만료 {self.days_before}일 전 알림"
        confirm = wx.MessageBox(
            f"{len(targets)}명에게 '{label}' 메일을 개별 발송합니다.\n"
            f"(회원당 약 1초 소요. 같은 만료일에 대해서는 다시 보내지 않습니다.)\n\n"
            "계속하시겠습니까?",
            "발송 확인", wx.YES_NO | wx.ICON_QUESTION | wx.NO_DEFAULT,
        )
        if confirm != wx.YES:
            speak("발송을 취소했습니다.")
            return

        kind = self.kind
        sender = self.mail_sender
        history = self.history
        log_writer = self.log_writer
        tpl = template_for_kind(kind)
        lock = threading.Lock()

        def worker(progress_cb):
            ok = 0
            fail = 0
            sent_pairs: list[tuple[str, date]] = []
            total = len(targets)
            for i, t in enumerate(targets, start=1):
                progress_cb(i, total, f"{i}/{total} — {t.member.user_id} 발송 중")
                subject, body = tpl(t.member, t.expiry_date)
                try:
                    results = sender.send(
                        [t.member.user_id], subject, body,
                        mode=SEND_MODE_INDIVIDUAL,
                    )
                except Exception:
                    fail += 1
                    continue
                if not results:
                    fail += 1
                    continue
                r = results[0]
                if r.skipped:
                    fail += 1
                    continue
                if r.success:
                    ok += 1
                    sent_pairs.append((t.member.user_id, t.expiry_date))
                else:
                    fail += 1
            with lock:
                # 만료일(period_to) 을 그대로 'when' 으로 저장 — 같은 만료일에
                # 두 번 안 보내도록 was_sent_for 가 비교할 키.
                for uid, period_to in sent_pairs:
                    history.mark_sent(uid, kind, when=period_to)
                if log_writer is not None:
                    try:
                        log_writer.write_event(
                            f"expiry_reminder kind={kind} sent={ok} failed={fail} "
                            f"actor={sender.current_user_id}"
                        )
                    except Exception:
                        pass
            return (ok, fail)

        dlg = ProgressTaskDialog(
            self,
            title=f"{label} 메일 발송 중",
            task=worker,
            message=f"{len(targets)}명에게 발송 중...",
            can_cancel=False,
        )
        try:
            dlg.run_modal()
            err = dlg.error
            result = dlg.result
        finally:
            dlg.Destroy()

        if err is not None:
            wx.MessageBox(f"발송 중 오류: {err}", "오류", wx.OK | wx.ICON_ERROR)
            return
        if result is None:
            return

        ok, fail = result
        self.sent_count = ok
        self.failed_count = fail
        msg = f"발송 완료: 성공 {ok}건, 실패 {fail}건"
        speak(msg)
        wx.MessageBox(
            msg + (
                "\n\n같은 만료일에 대해서는 다시 보내지 않습니다."
                if ok > 0 else ""
            ),
            "발송 결과",
            wx.OK | wx.ICON_INFORMATION if fail == 0 else wx.OK | wx.ICON_WARNING,
        )
        self.EndModal(wx.ID_OK)
