"""신규 가입자 알림 + 일괄/개별 승인 다이얼로그.

PaymentDialog 가 토스 거래내역 가져오기 직후 호출. DSM 자료실 그룹 멤버와
결제 활성 구독자를 비교해 "결제는 활성인데 DSM 그룹엔 없음" 회원이 있으면
이 다이얼로그를 띄운다.

승인 동작 = activate_subscriber_with_welcome_mail
    1) DSM 사용자 활성화 (없으면 생성, 임시 비밀번호 자동)
    2) 자료실 그룹 추가
    3) rtgreen 으로 로그인했으면 환영 메일까지 자동 발송

다이얼로그는 자체적으로 DsmSettings 를 로드하고 (이미 설정 되어 있어야 함),
승인 클릭 시 DsmClient 컨텍스트를 한 번만 열어 일괄 처리한다.
"""
from __future__ import annotations

import threading
from typing import Optional

import wx

from core.dsm_client import DsmAuthError, DsmClient
from core.dsm_config import DsmSettings, load_dsm_settings
from core.dsm_workflow import (
    NewSubscriberCandidate,
    activate_subscriber_with_welcome_mail,
)
from core.models import Member
from screen_reader import speak


class NewSubscriberDialog(wx.Dialog):
    def __init__(
        self,
        parent,
        *,
        candidates: list[NewSubscriberCandidate],
        sorisem_session=None,
        sorisem_current_user_id: str = "",
        sorisem_members: Optional[list[Member]] = None,
    ) -> None:
        super().__init__(
            parent,
            title="신규 가입자 알림 — DSM 활성화 대기",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
        )
        self.candidates = list(candidates)
        self.session = sorisem_session
        self.current_user_id = sorisem_current_user_id
        self.sorisem_members = list(sorisem_members) if sorisem_members else []
        self.settings: Optional[DsmSettings] = load_dsm_settings()
        self._build_ui()
        self.SetMinSize(wx.Size(720, 520))
        self.Fit()
        self.Centre()
        self._populate_list()
        wx.CallAfter(self._announce)

    # ---------- UI ----------

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        intro = wx.StaticText(
            panel,
            label=(
                "결제는 확인됐지만 아직 DSM 자료실 그룹에 추가되지 않은 회원입니다.\n"
                "승인하면 DSM 활성화, 자료실 그룹 추가, 환영 메일 발송까지 자동으로 진행합니다.\n"
                "(메일은 'rtgreen' 으로 로그인했을 때만 발송됩니다.)"
            ),
        )
        sizer.Add(intro, 0, wx.ALL, 10)

        sizer.Add(
            wx.StaticText(panel, label="신규 가입자 후보(&L):"),
            0, wx.LEFT | wx.RIGHT, 10,
        )
        self.list_ctrl = wx.ListCtrl(
            panel,
            style=wx.LC_REPORT | wx.LC_HRULES | wx.LC_VRULES,
            name="신규 가입자 후보",
        )
        self.list_ctrl.InsertColumn(0, "아이디", width=140)
        self.list_ctrl.InsertColumn(1, "이름 / 닉네임", width=180)
        self.list_ctrl.InsertColumn(2, "만료일", width=120)
        self.list_ctrl.InsertColumn(3, "개월", width=70)
        self.list_ctrl.InsertColumn(4, "상태", width=140)
        sizer.Add(self.list_ctrl, 1, wx.EXPAND | wx.ALL, 10)

        # 결과 영역
        sizer.Add(
            wx.StaticText(panel, label="처리 결과(&R):"),
            0, wx.LEFT | wx.RIGHT, 10,
        )
        self.result_text = wx.TextCtrl(
            panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY,
            name="처리 결과",
        )
        self.result_text.SetMinSize(wx.Size(-1, 140))
        sizer.Add(self.result_text, 0, wx.EXPAND | wx.ALL, 10)

        # 버튼
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.approve_selected_btn = wx.Button(
            panel, label="선택 회원 승인(&A)",
        )
        self.approve_all_btn = wx.Button(
            panel, label="전체 승인(&B)",
        )
        self.close_btn = wx.Button(panel, wx.ID_CLOSE, "닫기(&C)")
        self.approve_selected_btn.Bind(wx.EVT_BUTTON, self._on_approve_selected)
        self.approve_all_btn.Bind(wx.EVT_BUTTON, self._on_approve_all)
        self.close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        for b in (self.approve_selected_btn, self.approve_all_btn, self.close_btn):
            btn_sizer.Add(b, 0, wx.RIGHT, 8)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        panel.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CLOSE)

        # 설정이 비어 있으면 승인 비활성
        if self.settings is None or not self.settings.is_complete:
            self.approve_selected_btn.Disable()
            self.approve_all_btn.Disable()

    def _populate_list(self) -> None:
        self.list_ctrl.DeleteAllItems()
        for c in self.candidates:
            display_name = c.member.name or "(이름 없음)"
            if c.member.nickname:
                display_name = f"{display_name} / {c.member.nickname}"
            renew_label = "연장" if c.is_renewal else "신규"
            idx = self.list_ctrl.InsertItem(self.list_ctrl.GetItemCount(), c.user_id)
            self.list_ctrl.SetItem(idx, 1, display_name)
            self.list_ctrl.SetItem(idx, 2, c.period_to.isoformat())
            self.list_ctrl.SetItem(idx, 3, str(c.months))
            self.list_ctrl.SetItem(idx, 4, f"{renew_label} - 대기")

    # ---------- 이벤트 ----------

    def _selected_candidates(self) -> list[NewSubscriberCandidate]:
        out: list[NewSubscriberCandidate] = []
        idx = self.list_ctrl.GetFirstSelected()
        while idx >= 0:
            if 0 <= idx < len(self.candidates):
                out.append(self.candidates[idx])
            idx = self.list_ctrl.GetNextSelected(idx)
        return out

    def _on_approve_selected(self, _event=None) -> None:
        selected = self._selected_candidates()
        if not selected:
            wx.MessageBox(
                "먼저 목록에서 승인할 회원을 선택해 주세요.",
                "선택 필요", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        self._confirm_and_run(selected)

    def _on_approve_all(self, _event=None) -> None:
        if not self.candidates:
            return
        self._confirm_and_run(list(self.candidates))

    def _confirm_and_run(self, targets: list[NewSubscriberCandidate]) -> None:
        names = "\n".join(f"  - {c.user_id}" for c in targets)
        ans = wx.MessageBox(
            f"다음 {len(targets)}명을 DSM 활성화 + 자료실 그룹 추가 + 환영 메일까지 자동으로 진행합니까?\n\n"
            f"{names}",
            "승인 확인",
            wx.YES_NO | wx.ICON_QUESTION,
            self,
        )
        if ans != wx.YES:
            return
        if self.settings is None or not self.settings.is_complete:
            wx.MessageBox(
                "DSM 설정이 없습니다. 'DSM 설정' 메뉴에서 먼저 자격증명을 입력하세요.",
                "DSM 설정 필요", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        otp = self._maybe_ask_otp()
        if otp is None:
            return
        self.approve_selected_btn.Disable()
        self.approve_all_btn.Disable()
        self.result_text.SetValue("승인 진행 중...\n")
        speak(f"{len(targets)}명 승인을 시작합니다.")
        threading.Thread(
            target=self._run_in_thread, args=(targets, otp), daemon=True,
        ).start()

    def _run_in_thread(
        self, targets: list[NewSubscriberCandidate], otp: str,
    ) -> None:
        s = self.settings
        lines: list[str] = []

        def log(msg: str) -> None:
            lines.append(msg)
            wx.CallAfter(self.result_text.SetValue, "\n".join(lines))

        try:
            with DsmClient(s.url, verify_ssl=s.verify_ssl) as client:
                client.login(s.account, s.password, otp_code=otp)
                for c in targets:
                    try:
                        wf = activate_subscriber_with_welcome_mail(
                            dsm_client=client,
                            member=c.member,
                            group_name=s.group_name,
                            sorisem_session=self.session,
                            sorisem_current_user_id=self.current_user_id,
                            sorisem_members=self.sorisem_members,
                            period_to=c.period_to,
                            months=c.months,
                            is_renewal=c.is_renewal,
                        )
                    except DsmAuthError as e:
                        log(f"[실패] {c.user_id}: DSM 오류 - {e}")
                        continue
                    except Exception as e:
                        log(f"[실패] {c.user_id}: 예외 - {e}")
                        continue

                    if wf.activation.group_error:
                        head = "신규 생성됨" if wf.activation.created else "활성화됨"
                        head += " ([주의] 그룹추가 실패 - DSM 웹에서 직접 추가 필요)"
                    else:
                        head = (
                            "신규 생성 및 그룹추가" if wf.activation.created
                            else "활성화 및 그룹추가"
                        )
                    extra = ""
                    if wf.activation.initial_password:
                        extra = f" (임시PW: {wf.activation.initial_password})"
                    if wf.mail_attempted and wf.mail_sent:
                        mail_part = " + 환영메일 발송"
                    elif wf.mail_attempted:
                        mail_part = f" / 메일 실패: {wf.mail_message}"
                    elif wf.mail_skip_reason == "not_rtgreen":
                        mail_part = " (메일 생략: rtgreen 아님)"
                    elif wf.mail_skip_reason == "no_session":
                        mail_part = " (메일 생략: 세션 없음)"
                    elif wf.mail_skip_reason == "no_sorisem_match":
                        mail_part = " (메일 생략: 소리샘 회원 매칭 실패)"
                    else:
                        mail_part = ""
                    # 구글시트 폼 응답 시트의 '상태' 컬럼을 '활성' 으로 (best-effort).
                    # 단, 그룹 추가가 실패했으면 실제로 자료실 이용 불가라 시트는 건드리지 않음.
                    sheet_part = ""
                    if not wf.activation.group_error:
                        try:
                            from core.sheets_sync import push_form_status
                            if push_form_status(c.user_id, "활성") == "updated":
                                sheet_part = " + 시트 상태=활성"
                        except Exception:
                            pass
                    mark = "[성공]" if not wf.activation.group_error else "[일부]"
                    log(f"{mark} {c.user_id}: {head}{extra}{mail_part}{sheet_part}")
        except DsmAuthError as e:
            log(f"DSM 로그인 실패 — {e}")
            wx.CallAfter(self._run_done, success=False)
            return
        except Exception as e:
            log(f"예상치 못한 오류 — {e}")
            wx.CallAfter(self._run_done, success=False)
            return
        wx.CallAfter(self._run_done, success=True)

    def _run_done(self, *, success: bool) -> None:
        speak("승인 작업 완료." if success else "승인 작업 실패.")
        # 한 번 처리한 다음에는 동일 후보로 다시 누르지 않도록 비활성 유지가 안전.
        # 사용자가 닫고 다시 가져오기로 재검출하는 흐름.
        # 단, 닫기 버튼은 항상 활성.

    # ---------- OTP / 음성 ----------

    def _maybe_ask_otp(self) -> Optional[str]:
        if not self.settings or not self.settings.use_2fa:
            return ""
        with wx.TextEntryDialog(
            self,
            "DSM 2단계 인증 OTP 6자리를 입력하세요:",
            "OTP 코드 입력",
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return None
            otp = dlg.GetValue().strip()
        if not otp:
            wx.MessageBox(
                "OTP 코드가 비어 있습니다.",
                "입력 필요", wx.OK | wx.ICON_INFORMATION, self,
            )
            return None
        return otp

    def _announce(self) -> None:
        n = len(self.candidates)
        if self.settings is None or not self.settings.is_complete:
            speak(
                f"신규 가입자 알림. {n}명이 결제는 됐지만 DSM 자료실 그룹에 없습니다. "
                "DSM 설정이 비어 있어 승인 버튼이 비활성입니다."
            )
            return
        speak(
            f"신규 가입자 알림. {n}명이 결제는 됐지만 DSM 자료실 그룹에 없습니다. "
            "선택 회원 승인 또는 전체 승인 버튼으로 자동 활성화하실 수 있습니다."
        )
