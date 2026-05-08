"""신규 가입자 승인 다이얼로그 (v0.5).

가입 신청·대기 등급의 회원을 한 명씩 보여주고
"승인 / 거부 / 미루기" 중 한 가지를 결정하게 한다.

사용 시 main_frame 에서 회원 목록과 admin/log_writer/undo_stack 을 받아
실제 사이트 처리까지 다이얼로그 안에서 수행한다.
"""
from __future__ import annotations

from datetime import date

import wx

from config import (
    APPROVE_TO_LEVEL,
    LEVEL_LABELS,
    REJECT_TO_LEVEL,
)
from core.mail_sender import template_welcome
from core.member_admin import MemberAdminAdapter
from core.models import AdjustmentItem, Member
from core.pending_members import PendingMember, PendingSeenStore
from screen_reader import speak


class PendingMemberDialog(wx.Dialog):
    """신규 가입자 승인 처리 다이얼로그.

    동작:
      · 한 회원씩 정보를 표시 → "승인 / 거부 / 미루기 / 끝내기" 선택
      · 승인 → 등급을 APPROVE_TO_LEVEL(준회원) 로 변경, 안내 메시지 발송 가능
      · 거부 → 등급을 REJECT_TO_LEVEL(거부) 로 변경
      · 미루기 → seen 으로 마킹 (다음 실행 시 기본 목록에서 제외)
      · 끝내기 → 다이얼로그 종료
    """

    def __init__(
        self,
        parent,
        pendings: list[PendingMember],
        session,
        admin_user_id: str,
        log_writer=None,
        undo_stack=None,
        seen_store=None,
        level_history=None,
        mail_sender=None,
    ):
        super().__init__(
            parent,
            title=f"신규 가입자 승인 — {len(pendings)}명",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.pendings = list(pendings)
        self.session = session
        self.admin_user_id = (admin_user_id or "").lower()
        self.log_writer = log_writer
        self.undo_stack = undo_stack
        self.seen_store: PendingSeenStore = seen_store or PendingSeenStore()
        self.level_history = level_history
        self.mail_sender = mail_sender
        # 승인 시 환영 메일 자동 발송 — 발송 결과를 보고용으로 누적
        self.welcome_mails_sent: int = 0
        self.welcome_mails_failed: int = 0
        self.welcome_mails_skipped: int = 0
        self.idx = 0

        self.approved: list[Member] = []
        self.rejected: list[Member] = []
        self.deferred: list[Member] = []

        self._build_ui()
        self.SetMinSize(wx.Size(560, 420))
        self.Fit()
        self.Centre()
        wx.CallAfter(self._show_current)

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.header = wx.StaticText(panel, label="")
        sizer.Add(self.header, 0, wx.ALL, 10)

        self.detail = wx.TextCtrl(
            panel, value="",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_DONTWRAP,
            name="가입자 상세",
        )
        sizer.Add(self.detail, 1, wx.EXPAND | wx.ALL, 10)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.approve_btn = wx.Button(panel, wx.ID_ANY, "승인(&A)")
        self.reject_btn = wx.Button(panel, wx.ID_ANY, "거부(&R)")
        self.defer_btn = wx.Button(panel, wx.ID_ANY, "미루기(&S)")
        self.close_btn = wx.Button(panel, wx.ID_CLOSE, "끝내기(&C)")
        btn_sizer.Add(self.approve_btn, 0, wx.ALL, 5)
        btn_sizer.Add(self.reject_btn, 0, wx.ALL, 5)
        btn_sizer.Add(self.defer_btn, 0, wx.ALL, 5)
        btn_sizer.Add(self.close_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 8)

        panel.SetSizer(sizer)
        self.approve_btn.Bind(wx.EVT_BUTTON, self._on_approve)
        self.reject_btn.Bind(wx.EVT_BUTTON, self._on_reject)
        self.defer_btn.Bind(wx.EVT_BUTTON, self._on_defer)
        self.close_btn.Bind(wx.EVT_BUTTON, lambda e: self._finish())
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

    # ---------- 화면 ----------

    def _show_current(self) -> None:
        if self.idx >= len(self.pendings):
            self._finish()
            return
        pm = self.pendings[self.idx]
        m = pm.member
        nick = m.nickname or m.name or m.user_id
        # v1.0.2: LEVEL_LABELS 우선 (사용자 확정 매핑)
        level_label = LEVEL_LABELS.get(m.level, f"레벨 {m.level}")
        join = m.join_date.isoformat() if m.join_date else "알 수 없음"
        days = pm.days_since_join
        days_text = f"{days}일 전" if days is not None else "알 수 없음"
        seen = "이전에 본 적 있음 (미룸)" if pm.seen_before else "처음 알림"

        self.header.SetLabel(
            f"{self.idx + 1} / {len(self.pendings)} — {m.user_id} ({nick})"
        )
        lines = [
            f"아이디: {m.user_id}",
            f"이름: {m.name}",
            f"닉네임: {m.nickname}",
            f"현재 등급: {level_label} (레벨 {m.level})",
            f"가입일: {join} ({days_text})",
            f"상태: {seen}",
            "",
            "[A] 승인 → 준회원으로 등급 변경",
            "[R] 거부 → 거부 등급으로 등급 변경",
            "[S] 미루기 → 이번에는 건너뛰고 다음에 다시 알림",
            "[C] 끝내기 → 남은 회원 그대로 두고 닫기",
        ]
        self.detail.SetValue("\n".join(lines))

        speak(
            f"{self.idx + 1}번째 신규 가입자. {nick}, "
            f"가입 {days_text}. 승인, 거부, 미루기 중 골라주세요."
        )
        self.approve_btn.SetFocus()

    # ---------- 액션 ----------

    def _on_approve(self, event=None) -> None:
        pm = self.pendings[self.idx]
        m = pm.member
        nick = m.nickname or m.name or m.user_id
        confirm = wx.MessageBox(
            f"{m.user_id} ({nick}) 회원을 "
            f"{LEVEL_LABELS.get(APPROVE_TO_LEVEL, '준회원')}로 승인합니다.\n"
            f"이 작업은 사이트에 즉시 반영됩니다. 계속하시겠습니까?",
            "승인 확인",
            wx.YES_NO | wx.ICON_QUESTION,
        )
        if confirm != wx.YES:
            return
        self._apply_change(m, APPROVE_TO_LEVEL, label="승인")

    def _on_reject(self, event=None) -> None:
        pm = self.pendings[self.idx]
        m = pm.member
        nick = m.nickname or m.name or m.user_id
        confirm = wx.MessageBox(
            f"{m.user_id} ({nick}) 회원을 거부 등급으로 처리합니다.\n"
            f"이 작업은 사이트에 즉시 반영되며 되돌리려면 Ctrl+Z 또는 수동 처리가 필요합니다.\n"
            f"계속하시겠습니까?",
            "거부 확인",
            wx.YES_NO | wx.ICON_WARNING | wx.NO_DEFAULT,
        )
        if confirm != wx.YES:
            return
        self._apply_change(m, REJECT_TO_LEVEL, label="거부")

    def _on_defer(self, event=None) -> None:
        pm = self.pendings[self.idx]
        # seen 마킹 — 다음 실행 시 기본 알림 목록에서 제외
        try:
            self.seen_store.mark_seen(pm.member.user_id)
        except Exception:
            pass
        self.deferred.append(pm.member)
        speak("이 회원은 미루기 처리했습니다.")
        self.idx += 1
        self._show_current()

    def _apply_change(self, member: Member, new_level: int, label: str) -> None:
        from_level = member.level
        admin = MemberAdminAdapter(self.session, dry_run=False)
        result = admin.change_level(member, new_level)
        if not result.success:
            speak(f"{label} 실패: {result.message}")
            wx.MessageBox(
                f"처리 실패: {result.message}\n"
                f"응답 일부: {result.response_snippet[:200]}",
                "오류",
                wx.OK | wx.ICON_ERROR,
            )
            return

        # 모델 갱신
        member.level = new_level
        member.level_label = LEVEL_LABELS.get(new_level, str(new_level))

        # 로그
        if self.log_writer is not None:
            try:
                audit = AdjustmentItem(
                    member=member,
                    action="demote" if new_level < from_level else "demote",
                    from_level=from_level,
                    to_level=new_level,
                    reason=f"가입 {label} (관리자 {self.admin_user_id})",
                )
                self.log_writer.write_action(audit, result)
            except Exception:
                pass

        # Undo 스택
        if self.undo_stack is not None:
            try:
                from core.undo_stack import UndoItem
                self.undo_stack.push(
                    label=f"가입 {label} ({member.user_id})",
                    items=[UndoItem(
                        user_id=member.user_id,
                        nickname=member.nickname,
                        from_level=from_level,
                        to_level=new_level,
                    )],
                )
            except Exception:
                pass

        # seen 마킹 (이미 처리되었으므로 다시 안 뜨도록)
        try:
            self.seen_store.mark_seen(member.user_id)
        except Exception:
            pass

        # v1.0: 영구 등급 이력 기록
        if self.level_history is not None:
            try:
                self.level_history.record(
                    user_id=member.user_id,
                    nickname=member.nickname,
                    from_level=from_level,
                    to_level=new_level,
                    source="approve" if label == "승인" else "reject",
                    reason=f"가입 {label}",
                    actor=self.admin_user_id,
                )
            except Exception:
                pass

        if label == "승인":
            self.approved.append(member)
            # v1.0+: 승인 시 환영 메일 자동 발송 (rtgreen 으로 로그인된 경우)
            self._send_welcome_mail(member)
            speak(f"{member.user_id} 회원을 승인했습니다.")
        else:
            self.rejected.append(member)
            speak(f"{member.user_id} 회원을 거부 처리했습니다.")

        self.idx += 1
        self._show_current()

    def _send_welcome_mail(self, member: Member) -> None:
        """승인된 신규 회원에게 환영 메일을 즉시 발송한다.

        rtgreen 외 계정으로 로그인 중이면 mail_sender.enabled == False 라
        조용히 skip. 로그에는 기록.
        """
        if self.mail_sender is None:
            self.welcome_mails_skipped += 1
            return
        if not self.mail_sender.enabled:
            self.welcome_mails_skipped += 1
            return
        try:
            subject, body = template_welcome(member)
            results = self.mail_sender.send([member.user_id], subject, body)
            ok = any(r.success for r in results)
            if ok:
                self.welcome_mails_sent += 1
                # 로그에 기록 (write_event 사용)
                if self.log_writer is not None:
                    try:
                        self.log_writer.write_event(
                            f"welcome_mail user={member.user_id}"
                        )
                    except Exception:
                        pass
            else:
                self.welcome_mails_failed += 1
                if self.log_writer is not None:
                    try:
                        self.log_writer.write_event(
                            f"welcome_mail_failed user={member.user_id}"
                        )
                    except Exception:
                        pass
        except Exception:
            self.welcome_mails_failed += 1

    def _finish(self) -> None:
        n_a, n_r, n_d = len(self.approved), len(self.rejected), len(self.deferred)
        msg = f"신규 가입자 처리 종료. 승인 {n_a}명, 거부 {n_r}명, 미루기 {n_d}명."
        if n_a > 0:
            sent = self.welcome_mails_sent
            failed = self.welcome_mails_failed
            skipped = self.welcome_mails_skipped
            if sent + failed + skipped > 0:
                if skipped == n_a:
                    msg += " 환영 메일은 rtgreen 계정이 아니라 발송 안 됨."
                else:
                    parts = []
                    if sent:
                        parts.append(f"환영 메일 {sent}명 발송")
                    if failed:
                        parts.append(f"실패 {failed}명")
                    if skipped:
                        parts.append(f"건너뜀 {skipped}명")
                    if parts:
                        msg += " " + ", ".join(parts) + "."
        speak(msg)
        self.EndModal(wx.ID_OK)

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        code = event.GetKeyCode()
        if code == wx.WXK_ESCAPE:
            self._finish()
            return
        event.Skip()
