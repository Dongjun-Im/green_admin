"""안내 메일(nudge) 대상 미리보기 + 발송 다이얼로그 (v1.2.10).

두 종류 안내 메일을 같은 다이얼로그 클래스로 처리:
  · KIND_ACTIVITY_NUDGE   — green3 게시판에 6개월간 글 없는 회원
  · KIND_INACTIVE_WARNING — 1년 이상 사이트 미접속 회원

흐름:
  1) 다이얼로그 생성 시 대상 회원 조회 (ProgressTaskDialog 안에서 백그라운드).
     · 활동 안내는 회원별 HTTP 요청이 있어 시간이 걸림 — 진행률 표시.
     · 장기미접속은 로컬 last_login_date 만 보면 되므로 즉시.
  2) 결과를 ListBox 로 보여 줌. 사용자가 "선택 해제" 로 일부를 빼 갈 수 있음.
  3) "발송" 버튼 → 다시 ProgressTaskDialog 안에서 mail_sender 호출.
     성공한 회원은 NudgeHistoryStore 에 기록 → 다음번 30일 이내 자동 제외.
"""
from __future__ import annotations

import threading
from datetime import date
from typing import Optional

import wx

from core.activity_counter import ActivityCounter
from core.mail_sender import MailSender, SEND_MODE_INDIVIDUAL
from core.models import Member
from core.nudge_history import (
    KIND_ACTIVITY_NUDGE,
    KIND_INACTIVE_WARNING,
    NudgeHistoryStore,
)
from core.nudge_mail import (
    NudgeTarget,
    find_activity_nudge_targets,
    find_inactive_warning_targets,
    template_activity_nudge,
    template_inactive_warning,
)
from screen_reader import speak
from ui.item_text_ctrl import ItemTextCtrl
from ui.progress_dialog import ProgressTaskDialog


_TITLE = {
    KIND_ACTIVITY_NUDGE: "활동 안내 메일 (green3 6개월 글 없음)",
    KIND_INACTIVE_WARNING: "장기미접속 사전 경고 메일 (1년+ 미접속)",
}


class NudgeMailDialog(wx.Dialog):
    """안내 메일 대상 미리보기 + 발송.

    kind: KIND_ACTIVITY_NUDGE 또는 KIND_INACTIVE_WARNING.
    """

    def __init__(
        self,
        parent,
        *,
        kind: str,
        members: list[Member],
        mail_sender: MailSender,
        history: NudgeHistoryStore,
        admin_user_id: str,
        activity_counter: Optional[ActivityCounter] = None,
        log_writer=None,
    ) -> None:
        super().__init__(
            parent,
            title=_TITLE.get(kind, "안내 메일"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.kind = kind
        self.members = members
        self.mail_sender = mail_sender
        self.history = history
        self.admin_user_id = admin_user_id
        self.activity_counter = activity_counter
        self.log_writer = log_writer
        # 발송 결과 — 호출자가 확인 가능.
        self.sent_count: int = 0
        self.failed_count: int = 0
        # 대상 목록 (조회 후 채워짐).
        self._targets: list[NudgeTarget] = []
        # 체크된 회원만 발송 — 기본은 전체 체크.

        self._build_ui()
        self.SetMinSize(wx.Size(720, 520))
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

        self.list_box = wx.CheckListBox(
            panel, choices=[], name="대상 회원 목록",
        )
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
        self.send_btn.Disable()  # 대상 조회 끝나야 활성화
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

    # ---------- 대상 조회 ----------

    def _load_targets(self) -> None:
        """다이얼로그가 뜨자마자 백그라운드 조회 시작."""
        if self.kind == KIND_ACTIVITY_NUDGE:
            self._load_activity_nudge_targets()
        else:
            self._load_inactive_warning_targets()

    def _load_activity_nudge_targets(self) -> None:
        """green3 글 조회는 HTTP 호출이라 ProgressTaskDialog 로 진행률 표시."""
        if self.activity_counter is None:
            wx.MessageBox(
                "ActivityCounter 가 없어 대상을 조회할 수 없습니다.",
                "오류", wx.OK | wx.ICON_ERROR,
            )
            self.EndModal(wx.ID_CANCEL)
            return

        members = self.members
        admin_uid = self.admin_user_id
        counter = self.activity_counter
        history = self.history

        def worker(progress_cb):
            return find_activity_nudge_targets(
                members,
                counter,
                admin_user_id=admin_uid,
                history=history,
                progress_cb=progress_cb,
            )

        dlg = ProgressTaskDialog(
            self,
            title="활동 안내 — 대상 조회 중",
            task=worker,
            message="green3 게시판 글 수 조회 중...",
            can_cancel=True,
        )
        try:
            dlg.run_modal()
            err = dlg.error
            result = dlg.result
        finally:
            dlg.Destroy()

        if err is not None:
            wx.MessageBox(
                f"대상 조회 실패: {err}", "오류", wx.OK | wx.ICON_ERROR,
            )
            self.EndModal(wx.ID_CANCEL)
            return
        self._set_targets(result or [])

    def _load_inactive_warning_targets(self) -> None:
        """장기미접속 조회는 로컬에서 끝나므로 즉시 처리."""
        targets = find_inactive_warning_targets(
            self.members,
            admin_user_id=self.admin_user_id,
            history=self.history,
        )
        self._set_targets(targets)

    def _set_targets(self, targets: list[NudgeTarget]) -> None:
        self._targets = targets
        self.list_box.Set([self._format_entry(t) for t in targets])
        for i in range(self.list_box.GetCount()):
            self.list_box.Check(i, True)
        kind_label = "활동 안내" if self.kind == KIND_ACTIVITY_NUDGE else "장기미접속 경고"
        if targets:
            self.summary_label.SetLabel(
                f"{kind_label} 대상 {len(targets)}명 (30일 이내 발송된 회원은 자동 제외됨)"
            )
            self.send_btn.Enable()
            self.list_box.SetSelection(0)
            self._update_preview(0)
            speak(f"{kind_label} 대상 {len(targets)}명 조회 완료.")
        else:
            self.summary_label.SetLabel(f"{kind_label} 대상 회원이 없습니다.")
            self.preview.SetValue("(보낼 회원이 없습니다.)")
            speak(f"{kind_label} 대상 회원이 없습니다.")

    def _format_entry(self, t: NudgeTarget) -> str:
        if self.kind == KIND_ACTIVITY_NUDGE:
            return f"[V] {t.display_activity()}"
        return f"[V] {t.display_warning()}"

    def _update_preview(self, idx: int) -> None:
        if not (0 <= idx < len(self._targets)):
            return
        m = self._targets[idx].member
        if self.kind == KIND_ACTIVITY_NUDGE:
            subject, body = template_activity_nudge(m)
        else:
            subject, body = template_inactive_warning(m)
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

    def _checked_targets(self) -> list[NudgeTarget]:
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
            speak("rtgreen 전용 기능입니다.")
            return

        targets = self._checked_targets()
        if not targets:
            wx.MessageBox(
                "보낼 회원이 한 명도 체크돼 있지 않습니다.",
                "선택 필요", wx.OK | wx.ICON_INFORMATION,
            )
            return

        kind_label = "활동 안내" if self.kind == KIND_ACTIVITY_NUDGE else "장기미접속 경고"
        confirm = wx.MessageBox(
            f"{len(targets)}명에게 '{kind_label}' 메일을 개별 발송합니다.\n"
            f"(회원당 약 1초 소요. 보낸 회원은 30일간 다시 보내지 않습니다.)\n\n"
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
        today = date.today()
        lock = threading.Lock()

        def worker(progress_cb):
            ok = 0
            fail = 0
            sent_uids: list[str] = []
            total = len(targets)
            for i, t in enumerate(targets, start=1):
                progress_cb(i, total, f"{i}/{total} — {t.member.user_id} 발송 중")
                m = t.member
                if kind == KIND_ACTIVITY_NUDGE:
                    subject, body = template_activity_nudge(m)
                else:
                    subject, body = template_inactive_warning(m)
                try:
                    results = sender.send(
                        [m.user_id], subject, body,
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
                    # rtgreen 아닌 계정 — 한 번만 표시되고 끝.
                    fail += 1
                    continue
                if r.success:
                    ok += 1
                    sent_uids.append(m.user_id)
                else:
                    fail += 1
            # 한 번에 disk write — lock 으로 안전하게.
            with lock:
                if sent_uids:
                    history.mark_sent_many(sent_uids, kind, when=today)
                if log_writer is not None:
                    try:
                        log_writer.write_event(
                            f"nudge_mail kind={kind} sent={ok} failed={fail} "
                            f"actor={sender.current_user_id}"
                        )
                    except Exception:
                        pass
            return (ok, fail)

        dlg = ProgressTaskDialog(
            self,
            title=f"{kind_label} 메일 발송 중",
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
            wx.MessageBox(
                f"발송 중 오류: {err}", "오류", wx.OK | wx.ICON_ERROR,
            )
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
                "\n\n다음 30일간 같은 회원에게는 다시 보내지 않습니다."
                if ok > 0 else ""
            ),
            "발송 결과",
            wx.OK | wx.ICON_INFORMATION if fail == 0 else wx.OK | wx.ICON_WARNING,
        )
        self.EndModal(wx.ID_OK)
