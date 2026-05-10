"""자료실 구독 만료 임박자에게 일괄 안내 메일 발송 다이얼로그.

활성 구독 중 N일 이내 만료자를 자동 추출해 미리보기 → 체크박스로 제외 가능 →
rtgreen 계정 한해 일괄 발송. core/payment_mail.template_subscription_expiring 사용.
"""
from __future__ import annotations

import threading
from datetime import date

import requests
import wx

from core.mail_sender import MailSender
from core.models import Member
from core.payment_mail import template_subscription_expiring
from core.payment_store import PaymentStore
from screen_reader import speak


_PRESET_DAYS = (7, 14, 30)


class PaymentMailDialog(wx.Dialog):
    def __init__(
        self,
        parent,
        *,
        store: PaymentStore,
        members: list[Member],
        session: requests.Session | None,
        current_user_id: str,
    ) -> None:
        super().__init__(
            parent,
            title="자료실 만료 임박자 메일 발송",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.store = store
        self.members_by_uid = {m.user_id: m for m in members}
        self.session = session
        self.current_user_id = current_user_id
        # 현재 추출된 (member, period_to, days_left) 후보. UI 갱신 시 새로 채움.
        self._candidates: list[tuple[Member, date, int]] = []

        self._build_ui()
        self.SetMinSize(wx.Size(620, 480))
        self.Fit()
        self.Centre()
        self._refresh_candidates()
        wx.CallAfter(self._announce)

    # ---------- UI ----------

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(
            wx.StaticText(panel, label="만료 임박 기준(&D):"),
            0, wx.LEFT | wx.RIGHT | wx.TOP, 10,
        )
        self.days_radio = wx.RadioBox(
            panel,
            choices=[f"{d}일 이내" for d in _PRESET_DAYS],
            majorDimension=1,
            style=wx.RA_SPECIFY_ROWS,
        )
        self.days_radio.SetSelection(0)  # 기본 7일
        self.days_radio.Bind(wx.EVT_RADIOBOX, lambda e: self._refresh_candidates())
        sizer.Add(self.days_radio, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # 대상 회원 — 체크리스트
        sizer.Add(
            wx.StaticText(panel, label="발송 대상 (체크 해제하면 제외):"),
            0, wx.TOP | wx.LEFT | wx.RIGHT, 10,
        )
        self.list_box = wx.CheckListBox(panel, choices=[])
        sizer.Add(self.list_box, 1, wx.EXPAND | wx.ALL, 10)

        # 진행 상태
        self.status_text = wx.StaticText(panel, label="")
        sizer.Add(self.status_text, 0, wx.LEFT | wx.RIGHT, 10)

        # 버튼
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.send_btn = wx.Button(panel, label="발송(&S)")
        self.cancel_btn = wx.Button(panel, wx.ID_CANCEL, "닫기")
        self.send_btn.Bind(wx.EVT_BUTTON, self._on_send)
        btn_sizer.Add(self.send_btn, 0, wx.RIGHT, 8)
        btn_sizer.Add(self.cancel_btn, 0)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        panel.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CANCEL)

    # ---------- 데이터 ----------

    def _selected_days(self) -> int:
        return _PRESET_DAYS[self.days_radio.GetSelection()]

    def _refresh_candidates(self) -> None:
        days = self._selected_days()
        today = date.today()
        candidates: list[tuple[Member, date, int]] = []

        # 회원별 가장 늦은 만료일을 latest_period_to 로 모음
        latest_by_uid: dict[str, date] = {}
        for s in self.store.all_subscriptions():
            cur = latest_by_uid.get(s.member_user_id)
            if cur is None or s.period_to > cur:
                latest_by_uid[s.member_user_id] = s.period_to

        for uid, latest in latest_by_uid.items():
            if latest < today:
                continue  # 이미 만료
            days_left = (latest - today).days
            if days_left > days:
                continue
            m = self.members_by_uid.get(uid)
            if m is None:
                # 회원 목록에 없는 user_id 는 메일 발송 대상에서 제외 (사이트 메시지 시스템 필요)
                continue
            candidates.append((m, latest, days_left))

        candidates.sort(key=lambda t: t[1])  # 만료 임박순
        self._candidates = candidates

        labels = [
            f"{m.user_id} / {m.name or ''} / {m.nickname or ''}  "
            f"→ {pt.isoformat()} (남은 {dl}일)"
            for m, pt, dl in candidates
        ]
        self.list_box.Set(labels)
        # 기본 모두 체크
        for i in range(len(labels)):
            self.list_box.Check(i, True)

        self.status_text.SetLabel(f"대상 후보: {len(candidates)}명")

    def _on_send(self, _event=None) -> None:
        if self.session is None:
            wx.MessageBox(
                "로그인 세션이 없어 메일을 발송할 수 없습니다.",
                "세션 없음", wx.OK | wx.ICON_WARNING, self,
            )
            return

        sender = MailSender(self.session, self.current_user_id)
        if not sender.enabled:
            wx.MessageBox(
                f"메일 발송은 '{MailSender.SENDER_USER_ID}' 계정으로 로그인했을 때만 가능합니다.\n"
                f"현재 계정: {self.current_user_id}",
                "발송 불가", wx.OK | wx.ICON_WARNING, self,
            )
            return

        # 체크된 항목만 발송 대상.
        chosen = [
            (m, pt, dl)
            for i, (m, pt, dl) in enumerate(self._candidates)
            if self.list_box.IsChecked(i)
        ]
        if not chosen:
            wx.MessageBox(
                "발송할 회원이 없습니다.",
                "안내", wx.OK | wx.ICON_INFORMATION, self,
            )
            return

        # 확인
        ans = wx.MessageBox(
            f"{len(chosen)}명에게 만료 안내 메일을 발송합니다.\n계속할까요?",
            "발송 확인",
            wx.YES_NO | wx.ICON_QUESTION,
            self,
        )
        if ans != wx.YES:
            return

        self.send_btn.Disable()
        self.status_text.SetLabel("발송 중...")
        speak(f"{len(chosen)}명에게 메일을 발송합니다.")
        threading.Thread(
            target=self._send_in_thread, args=(sender, chosen), daemon=True,
        ).start()

    def _send_in_thread(self, sender: MailSender, chosen: list) -> None:
        success = 0
        failed: list[tuple[str, str]] = []
        total = len(chosen)
        for idx, (m, pt, days_left) in enumerate(chosen, start=1):
            wx.CallAfter(self.status_text.SetLabel, f"발송 중 {idx}/{total}: {m.user_id}")
            subject, body = template_subscription_expiring(m, pt, days_left)
            results = sender.send(
                recipients=[m.user_id],
                subject=subject,
                body=body,
            )
            ok = any(r.success for r in results)
            if ok:
                success += 1
            else:
                msg = "; ".join(r.message for r in results if r.message)
                failed.append((m.user_id, msg or "사유 미상"))

        wx.CallAfter(self._on_done, success, failed)

    def _on_done(self, success: int, failed: list) -> None:
        msg = f"발송 완료: 성공 {success}명 / 실패 {len(failed)}명"
        if failed:
            msg += "\n\n실패 명단:"
            for uid, reason in failed[:10]:
                msg += f"\n  · {uid}: {reason}"
            if len(failed) > 10:
                msg += f"\n  · 외 {len(failed) - 10}건"
        self.status_text.SetLabel(f"성공 {success} / 실패 {len(failed)}")
        speak(f"발송 완료. 성공 {success}명, 실패 {len(failed)}명.")
        wx.MessageBox(msg, "발송 결과", wx.OK | wx.ICON_INFORMATION, self)
        self.send_btn.Enable()

    def _announce(self) -> None:
        speak(
            "만료 임박자 메일 발송. "
            "기본 7일 이내 만료자가 표시됩니다. "
            "대상에서 빼고 싶으면 체크박스 해제 후 발송 버튼."
        )
