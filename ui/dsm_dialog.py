"""DSM(Synology) 관리 메인 다이얼로그.

상단:  연결 상태 + "진단 실행" 버튼
중간:  진단 결과 텍스트 (NAS 연결 / DSM 로그인 / 비밀번호 정책 / 그룹 / 사용자 수)
하단:  DSM 사용자 목록 (ListBox)

설정이 안 되어 있으면 즉시 "DSM 설정 다이얼로그를 먼저 실행하세요" 안내.
모든 NAS 호출은 별도 스레드에서 진행 — UI 멈춤 방지.
"""
from __future__ import annotations

import threading
from typing import Any

import wx

from datetime import date

from core.dsm_client import DsmAuthError, DsmClient
from core.dsm_config import DsmSettings, load_dsm_settings
from core.dsm_service import (
    DeactivationResult,
    activate_subscriber,
    compute_sync_diff,
    deactivate_subscriber,
)
from core.dsm_workflow import delete_subscriber
from screen_reader import speak
from ui.dsm_create_user_dialog import DsmCreateUserDialog


class DsmDialog(wx.Dialog):
    def __init__(self, parent, *, store=None, members=None) -> None:
        super().__init__(
            parent,
            title="DSM 관리 — 진단 / 사용자 목록",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
        )
        self.settings: DsmSettings | None = load_dsm_settings()
        # 정합성 체크용 — store(PaymentStore) 와 members 가 모두 있을 때만 활성.
        self.store = store
        self.members = list(members) if members else []
        self._build_ui()
        self.SetMinSize(wx.Size(720, 600))
        self.Fit()
        self.Centre()
        self._update_status_summary()
        wx.CallAfter(self._announce)

    # ---------- UI ----------

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # 상단 — 현재 설정 요약
        self.summary_text = wx.StaticText(panel, label="...")
        sizer.Add(self.summary_text, 0, wx.ALL, 10)

        # 진단 버튼 + 사용자 목록 새로고침 버튼
        top_btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.diag_btn = wx.Button(panel, label="진단 실행(&D)")
        self.users_btn = wx.Button(panel, label="사용자 목록 새로고침(&L)")
        self.diag_btn.Bind(wx.EVT_BUTTON, self._on_run_diagnostics)
        self.users_btn.Bind(wx.EVT_BUTTON, self._on_refresh_users)
        top_btn_sizer.Add(self.diag_btn, 0, wx.RIGHT, 8)
        top_btn_sizer.Add(self.users_btn, 0)
        sizer.Add(top_btn_sizer, 0, wx.LEFT | wx.RIGHT, 10)

        # 진단 결과 — 라벨 먼저, 그 다음 출력 (스크린리더 순서 보존)
        sizer.Add(
            wx.StaticText(panel, label="진단 결과(&R):"),
            0, wx.LEFT | wx.RIGHT | wx.TOP, 10,
        )
        self.result_text = wx.TextCtrl(
            panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY,
            name="진단 결과",
        )
        self.result_text.SetMinSize(wx.Size(-1, 200))
        sizer.Add(self.result_text, 1, wx.EXPAND | wx.ALL, 10)

        # 사용자 목록
        sizer.Add(
            wx.StaticText(panel, label="DSM 사용자 목록(&U):"),
            0, wx.LEFT | wx.RIGHT | wx.TOP, 10,
        )
        self.users_list = wx.ListBox(
            panel, choices=[], name="DSM 사용자 목록",
        )
        self.users_list.SetMinSize(wx.Size(-1, 180))
        sizer.Add(self.users_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # 사용자 작업 버튼들
        action_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.create_user_btn = wx.Button(panel, label="신규 사용자 만들기(&C)")
        self.activate_btn = wx.Button(panel, label="선택 사용자 활성화(&A)")
        self.deactivate_btn = wx.Button(panel, label="선택 사용자 비활성화(&X)")
        self.delete_btn = wx.Button(panel, label="선택 사용자 삭제(&Z)")
        self.sync_btn = wx.Button(panel, label="결제 DSM 정합성 체크(&S)")
        self.create_user_btn.Bind(wx.EVT_BUTTON, self._on_create_user)
        self.activate_btn.Bind(wx.EVT_BUTTON, self._on_activate_selected)
        self.deactivate_btn.Bind(wx.EVT_BUTTON, self._on_deactivate_selected)
        self.delete_btn.Bind(wx.EVT_BUTTON, self._on_delete_selected)
        self.sync_btn.Bind(wx.EVT_BUTTON, self._on_sync_check)
        for b in (
            self.create_user_btn, self.activate_btn,
            self.deactivate_btn, self.delete_btn, self.sync_btn,
        ):
            action_sizer.Add(b, 0, wx.RIGHT, 8)
        sizer.Add(action_sizer, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)

        # 닫기
        close_btn = wx.Button(panel, wx.ID_CLOSE, "닫기")
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        sizer.Add(close_btn, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        panel.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CLOSE)

    # ---------- 상태 ----------

    def _update_status_summary(self) -> None:
        if self.settings is None or not self.settings.is_complete:
            self.summary_text.SetLabel(
                "DSM 설정이 없습니다. 먼저 '자료실 구독비 관리 - DSM 설정' 메뉴에서\n"
                "URL, 관리자 자격증명, 그룹을 입력하고 '저장' 한 뒤 다시 여세요."
            )
            self.diag_btn.Disable()
            self.users_btn.Disable()
            self.create_user_btn.Disable()
            self.activate_btn.Disable()
            self.deactivate_btn.Disable()
            self.delete_btn.Disable()
            self.sync_btn.Disable()
        else:
            self.summary_text.SetLabel(
                f"DSM URL : {self.settings.url}\n"
                f"관리자 ID : {self.settings.account}\n"
                f"자료실 회원 그룹 : {self.settings.group_name}\n"
                f"2단계 인증 : {'사용' if self.settings.use_2fa else '미사용'} | "
                f"SSL 검증 : {'켜짐' if self.settings.verify_ssl else '꺼짐'}"
            )
            self.diag_btn.Enable()
            self.users_btn.Enable()
            self.create_user_btn.Enable()
            self.activate_btn.Enable()
            self.deactivate_btn.Enable()
            self.delete_btn.Enable()
            # 정합성 체크는 store 가 있어야 의미 있음 (PaymentDialog 에서 열렸을 때만)
            if self.store is not None:
                self.sync_btn.Enable()
            else:
                self.sync_btn.Disable()

    # ---------- 진단 ----------

    def _on_run_diagnostics(self, _event=None) -> None:
        if self.settings is None or not self.settings.is_complete:
            return
        otp = self._maybe_ask_otp()
        if otp is None:
            return
        self.diag_btn.Disable()
        self.users_btn.Disable()
        self.result_text.SetValue("진단 실행 중...\n")
        speak("DSM 진단을 시작합니다.")
        threading.Thread(
            target=self._diag_in_thread, args=(otp,), daemon=True,
        ).start()

    def _diag_in_thread(self, otp: str) -> None:
        s = self.settings
        lines: list[str] = []

        def append(text: str) -> None:
            lines.append(text)
            wx.CallAfter(self.result_text.SetValue, "\n".join(lines))

        try:
            with DsmClient(s.url, verify_ssl=s.verify_ssl) as client:
                # 1) NAS 연결
                try:
                    info = client.test_connection()
                    append(
                        f"[1/5] NAS 연결 성공 — "
                        f"Auth API 버전 {info.auth_min_version}~{info.auth_max_version}"
                    )
                except DsmAuthError as e:
                    append(f"[1/5] NAS 연결 실패 — {e}")
                    wx.CallAfter(self._diag_done, success=False)
                    return

                # 2) DSM 로그인
                try:
                    client.login(s.account, s.password, otp_code=otp)
                    append("[2/5] DSM 로그인 성공")
                except DsmAuthError as e:
                    append(f"[2/5] DSM 로그인 실패 — {e}")
                    wx.CallAfter(self._diag_done, success=False)
                    return

                # 3) 비밀번호 정책
                try:
                    policy = client.get_password_policy()
                    summary = _format_password_policy(policy)
                    append(f"[3/5] 비밀번호 정책: {summary}")
                except DsmAuthError as e:
                    append(f"[3/5] 비밀번호 정책 조회 실패 — {e}")

                # 4) 자료실 회원 그룹 존재 확인
                try:
                    groups = client.list_groups()
                    names = {g.get("name", "") for g in groups}
                    if s.group_name in names:
                        append(f"[4/5] 그룹 '{s.group_name}' 존재 확인")
                    else:
                        sample = ", ".join(sorted(names))[:200]
                        append(
                            f"[4/5] 그룹 '{s.group_name}' 을 찾을 수 없음 — "
                            f"DSM 의 실제 그룹 이름과 일치하는지 확인 필요. "
                            f"현재 DSM 그룹 일부: {sample}"
                        )
                except DsmAuthError as e:
                    append(f"[4/5] 그룹 조회 실패 — {e}")

                # 5) 사용자 수
                try:
                    users = client.list_users()
                    active = sum(
                        1 for u in users if u.get("expired") == "normal"
                    )
                    append(
                        f"[5/5] DSM 사용자 {len(users)}명 "
                        f"(활성 {active}명 / 비활성과 만료 {len(users) - active}명)"
                    )
                except DsmAuthError as e:
                    append(f"[5/5] 사용자 목록 조회 실패 — {e}")
        except Exception as e:
            append(f"예상치 못한 오류: {e}")
            wx.CallAfter(self._diag_done, success=False)
            return

        wx.CallAfter(self._diag_done, success=True)

    def _diag_done(self, success: bool) -> None:
        self.diag_btn.Enable()
        self.users_btn.Enable()
        if success:
            speak("진단 완료.")
        else:
            speak("진단 실패. 결과 영역에서 사유를 확인하세요.")

    # ---------- 사용자 목록 ----------

    def _on_refresh_users(self, _event=None) -> None:
        if self.settings is None or not self.settings.is_complete:
            return
        otp = self._maybe_ask_otp()
        if otp is None:
            return
        self.users_btn.Disable()
        self.diag_btn.Disable()
        self.users_list.Set(["불러오는 중..."])
        speak("DSM 사용자 목록을 불러옵니다.")
        threading.Thread(
            target=self._load_users_in_thread, args=(otp,), daemon=True,
        ).start()

    def _load_users_in_thread(self, otp: str) -> None:
        s = self.settings
        try:
            with DsmClient(s.url, verify_ssl=s.verify_ssl) as client:
                client.login(s.account, s.password, otp_code=otp)
                users = client.list_users()
        except DsmAuthError as e:
            wx.CallAfter(self._users_failed, str(e))
            return
        except Exception as e:
            wx.CallAfter(self._users_failed, f"예상치 못한 오류: {e}")
            return
        wx.CallAfter(self._users_loaded, users)

    def _users_loaded(self, users: list[dict[str, Any]]) -> None:
        # 정렬 — 이름순
        users.sort(key=lambda u: u.get("name", ""))
        labels: list[str] = []
        for u in users:
            name = u.get("name", "")
            expired = u.get("expired", "normal")
            email = u.get("email", "") or ""
            status = _format_expired(expired)
            labels.append(
                f"{name}  ({status})" + (f"  {email}" if email else "")
            )
        if not labels:
            labels = ["(사용자 없음)"]
        self.users_list.Set(labels)
        speak(f"사용자 {len(users)}명 불러옴.")
        self.users_btn.Enable()
        self.diag_btn.Enable()

    def _users_failed(self, message: str) -> None:
        self.users_list.Set([f"실패: {message}"])
        speak("사용자 목록 불러오기 실패.")
        wx.MessageBox(message, "조회 실패", wx.OK | wx.ICON_ERROR, self)
        self.users_btn.Enable()
        self.diag_btn.Enable()

    # ---------- 신규 생성 / 활성·비활성 ----------

    def _on_create_user(self, _event=None) -> None:
        if self.settings is None or not self.settings.is_complete:
            return
        with DsmCreateUserDialog(
            self, settings=self.settings,
            store=self.store, members=self.members,
        ) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                # 생성됐으면 목록 새로고침
                self._on_refresh_users()

    def _selected_user_name(self) -> str | None:
        idx = self.users_list.GetSelection()
        if idx < 0:
            return None
        text = self.users_list.GetString(idx)
        if not text or text.startswith("("):
            return None
        # 형식: "name  (status)  email" — 첫 '  (' 앞이 이름.
        return text.split("  (", 1)[0].strip()

    def _on_activate_selected(self, _event=None) -> None:
        name = self._selected_user_name()
        if not name:
            wx.MessageBox(
                "먼저 사용자 목록에서 회원을 선택해 주세요.\n"
                "(목록이 비어있다면 '사용자 목록 새로고침' 먼저)",
                "선택 필요", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        ans = wx.MessageBox(
            f"'{name}' 을 활성화하고 자료실 회원 그룹에 추가합니까?",
            "활성화 확인", wx.YES_NO | wx.ICON_QUESTION, self,
        )
        if ans != wx.YES:
            return
        self._run_user_action(name, action="activate")

    def _on_deactivate_selected(self, _event=None) -> None:
        name = self._selected_user_name()
        if not name:
            wx.MessageBox(
                "먼저 사용자 목록에서 회원을 선택해 주세요.",
                "선택 필요", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        ans = wx.MessageBox(
            f"'{name}' 을 비활성화하고 자료실 회원 그룹에서 제거합니까?",
            "비활성화 확인", wx.YES_NO | wx.ICON_QUESTION, self,
        )
        if ans != wx.YES:
            return
        self._run_user_action(name, action="deactivate")

    def _on_delete_selected(self, _event=None) -> None:
        name = self._selected_user_name()
        if not name:
            wx.MessageBox(
                "먼저 사용자 목록에서 회원을 선택해 주세요.",
                "선택 필요", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        msg_lines = [
            f"'{name}' 을 DSM 에서 영구 삭제합니다.",
            "",
            "[주의] 이 작업은 되돌릴 수 없습니다.",
        ]
        if self.store is not None:
            msg_lines.append(
                "결제 DB 의 입금자명 매핑(alias) 도 함께 정리됩니다.\n"
                "(구독, 거래 이력은 회계 자료로 보존)"
            )
        msg_lines.append("\n계속할까요?")
        ans = wx.MessageBox(
            "\n".join(msg_lines),
            "사용자 삭제 확인",
            wx.YES_NO | wx.ICON_WARNING | wx.NO_DEFAULT, self,
        )
        if ans != wx.YES:
            return
        self._run_user_action(name, action="delete")

    def _run_user_action(self, name: str, *, action: str) -> None:
        otp = self._maybe_ask_otp()
        if otp is None:
            return
        self.create_user_btn.Disable()
        self.activate_btn.Disable()
        self.deactivate_btn.Disable()
        self.delete_btn.Disable()
        self.diag_btn.Disable()
        self.users_btn.Disable()
        action_label = {
            "activate": "활성화",
            "deactivate": "비활성화",
            "delete": "삭제",
        }.get(action, action)
        speak(f"{name} {action_label} 진행 중.")
        threading.Thread(
            target=self._user_action_in_thread,
            args=(name, action, otp),
            daemon=True,
        ).start()

    def _user_action_in_thread(self, name: str, action: str, otp: str) -> None:
        s = self.settings
        try:
            with DsmClient(s.url, verify_ssl=s.verify_ssl) as client:
                client.login(s.account, s.password, otp_code=otp)
                if action == "activate":
                    result = activate_subscriber(client, name, s.group_name)
                    summary = (
                        f"활성화 완료: {result.user_name}\n"
                        f"  - 신규 생성: {'예' if result.created else '아니오 (기존 계정)'}"
                    )
                    if result.in_group:
                        summary += "\n  - 자료실 그룹 추가: 완료"
                    elif result.group_error:
                        summary += (
                            "\n  - [주의] 자료실 그룹 추가 실패 - DSM 웹 관리자에서 직접 추가해 주세요\n"
                            f"      ({result.group_error})"
                        )
                    else:
                        summary += "\n  - 자료실 그룹 추가: 이미 멤버"
                    if result.initial_password:
                        summary += (
                            f"\n  - 임시 비밀번호: {result.initial_password}\n"
                            "    회원에게 이 비밀번호를 안내해 주세요."
                        )
                elif action == "delete":
                    del_result = delete_subscriber(
                        dsm_client=client,
                        member_user_id=name,
                        store=self.store,
                    )
                    if not del_result.found_in_dsm:
                        summary = (
                            f"'{del_result.user_name}' 은 이미 DSM 에 없습니다.\n"
                            f"  - 정리된 alias 매핑: {del_result.aliases_removed}건"
                        )
                    else:
                        summary = (
                            f"DSM 사용자 영구 삭제 완료: {del_result.user_name}\n"
                            f"  - 정리된 alias 매핑: {del_result.aliases_removed}건\n"
                            f"  - 구독, 거래 이력은 보존 (회계 자료)"
                        )
                else:
                    deact: DeactivationResult = deactivate_subscriber(
                        client, name, s.group_name,
                    )
                    if not deact.found:
                        summary = (
                            f"'{deact.user_name}' 은 이미 DSM 에 없습니다 (정리된 상태)."
                        )
                    else:
                        summary = (
                            f"비활성화 완료: {deact.user_name}\n"
                            f"  - 비활성: {'예' if deact.disabled else '아니오'}\n"
                            f"  - 그룹 제거: {'예' if deact.removed_from_group else '아니오 (이미 없음)'}"
                        )
        except DsmAuthError as e:
            wx.CallAfter(self._user_action_failed, str(e))
            return
        except Exception as e:
            wx.CallAfter(self._user_action_failed, f"예상치 못한 오류: {e}")
            return

        # 구글시트 폼 응답 시트의 '상태' 컬럼 갱신 (best-effort)
        from core.sheets_sync import push_form_status
        r = push_form_status(name, "활성" if action == "activate" else "비활성")
        if r == "updated":
            summary += "\n  - 구글시트 '상태' 컬럼 갱신됨"
        elif r == "not_found":
            summary += "\n  - 구글시트: 해당 회원의 폼 응답 행이 없어 상태 미갱신"
        elif r.startswith("error:auth:"):
            summary += "\n  - 구글시트 상태 갱신 실패 (인증 필요 - 먼저 구글시트 동기화)"
        elif r.startswith("error:"):
            summary += f"\n  - 구글시트 상태 갱신 실패: {r[len('error:'):]}"
        wx.CallAfter(self._user_action_done, summary)

    def _user_action_done(self, summary: str) -> None:
        speak("작업 완료.")
        wx.MessageBox(summary, "작업 결과", wx.OK | wx.ICON_INFORMATION, self)
        self._enable_action_buttons()
        # 목록 자동 새로고침
        self._on_refresh_users()

    def _user_action_failed(self, message: str) -> None:
        speak("작업 실패.")
        wx.MessageBox(message, "작업 실패", wx.OK | wx.ICON_ERROR, self)
        self._enable_action_buttons()

    def _enable_action_buttons(self) -> None:
        self.create_user_btn.Enable()
        self.activate_btn.Enable()
        self.deactivate_btn.Enable()
        self.delete_btn.Enable()
        self.diag_btn.Enable()
        self.users_btn.Enable()
        if self.store is not None:
            self.sync_btn.Enable()

    # ---------- 정합성 체크 ----------

    def _on_sync_check(self, _event=None) -> None:
        if self.settings is None or not self.settings.is_complete:
            return
        if self.store is None:
            wx.MessageBox(
                "결제 데이터에 접근할 수 없어 정합성 체크가 불가합니다.",
                "안내", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        otp = self._maybe_ask_otp()
        if otp is None:
            return
        self._enable_action_buttons()  # 일관성 위해 미리 호출 패턴 유지
        self.create_user_btn.Disable()
        self.activate_btn.Disable()
        self.deactivate_btn.Disable()
        self.diag_btn.Disable()
        self.users_btn.Disable()
        self.sync_btn.Disable()
        self.result_text.SetValue("정합성 체크 진행 중...\n")
        speak("결제와 DSM 정합성 체크 시작.")
        threading.Thread(
            target=self._sync_check_in_thread, args=(otp,), daemon=True,
        ).start()

    def _sync_check_in_thread(self, otp: str) -> None:
        s = self.settings
        try:
            with DsmClient(s.url, verify_ssl=s.verify_ssl) as client:
                client.login(s.account, s.password, otp_code=otp)
                all_users = client.list_users()
                group_members = client.list_group_members(s.group_name)
        except DsmAuthError as e:
            wx.CallAfter(self._sync_check_failed, str(e))
            return
        except Exception as e:
            wx.CallAfter(self._sync_check_failed, f"예상치 못한 오류: {e}")
            return

        # 결제 활성 구독자 user_id 수집 (오늘 기준 만료 안 된 사람)
        today = date.today()
        active_uids: set[str] = set()
        latest_to: dict[str, date] = {}
        for sub in self.store.all_subscriptions():
            cur = latest_to.get(sub.member_user_id)
            if cur is None or sub.period_to > cur:
                latest_to[sub.member_user_id] = sub.period_to
        for uid, pt in latest_to.items():
            if pt >= today:
                active_uids.add(uid)

        group_names = [m.get("name", "") for m in group_members]
        diff = compute_sync_diff(
            dsm_group_members=group_names,
            active_payment_user_ids=active_uids,
            dsm_total_users=len(all_users),
        )
        wx.CallAfter(self._sync_check_done, diff)

    def _sync_check_done(self, diff) -> None:
        if not diff.is_safe_to_compute:
            self.result_text.SetValue(
                "정합성 체크 중단 — DSM 사용자 수가 0 으로 응답되었습니다.\n"
                "API 응답 이상 가능성 — 잠시 후 재시도해 주세요."
            )
            speak("정합성 체크 중단. DSM 응답 이상.")
            self._enable_action_buttons()
            return

        lines = [
            f"DSM 전체 사용자 : {diff.dsm_total_users}명",
            f"DSM 자료실 그룹 : {len(diff.dsm_only) + len(diff.consistent)}명",
            f"결제 활성 구독 : {len(diff.payment_only) + len(diff.consistent)}명",
            "",
            f"양쪽 일치 : {len(diff.consistent)}명",
        ]
        if diff.dsm_only:
            lines.append("")
            lines.append(
                f"[주의] DSM 그룹엔 있지만 결제 활성 아님 - {len(diff.dsm_only)}명 (정리 후보):"
            )
            for n in diff.dsm_only:
                lines.append(f"   - {n}")
        if diff.payment_only:
            lines.append("")
            lines.append(
                f"[주의] 결제 활성인데 DSM 그룹엔 없음 - {len(diff.payment_only)}명 (활성화 누락):"
            )
            for n in diff.payment_only:
                lines.append(f"   - {n}")
        if not diff.dsm_only and not diff.payment_only:
            lines.append("")
            lines.append("[성공] 어긋남 없음 - 결제와 DSM 그룹이 완벽히 일치합니다.")

        self.result_text.SetValue("\n".join(lines))
        speak(
            f"정합성 체크 완료. 일치 {len(diff.consistent)}, "
            f"DSM만 {len(diff.dsm_only)}, 결제만 {len(diff.payment_only)}."
        )
        self._enable_action_buttons()

    def _sync_check_failed(self, message: str) -> None:
        self.result_text.SetValue(f"정합성 체크 실패:\n{message}")
        speak("정합성 체크 실패.")
        wx.MessageBox(message, "체크 실패", wx.OK | wx.ICON_ERROR, self)
        self._enable_action_buttons()

    # ---------- 2FA OTP 처리 ----------

    def _maybe_ask_otp(self) -> str | None:
        """2FA 사용 설정이면 사용자에게 OTP 코드를 묻고 반환.
        취소 시 None — 호출자는 작업 중단."""
        if not self.settings or not self.settings.use_2fa:
            return ""  # 2FA 미사용
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
        if self.settings is None or not self.settings.is_complete:
            speak(
                "DSM 관리 화면. 설정이 비어 있습니다. "
                "먼저 DSM 설정 메뉴에서 자격증명을 입력해 주세요."
            )
        else:
            speak(
                "DSM 관리 화면. "
                "진단 실행 버튼으로 NAS 연결과 자격증명, 그룹을 점검할 수 있고, "
                "사용자 목록 새로고침 버튼으로 DSM 사용자 목록을 불러올 수 있습니다."
            )


# ---------- 포매팅 헬퍼 ----------

def _format_password_policy(policy: dict[str, Any]) -> str:
    bits: list[str] = []
    ml = policy.get("min_length")
    if ml:
        bits.append(f"최소 길이 {ml}")
    if policy.get("mixed_case"):
        bits.append("대소문자 혼용")
    if policy.get("included_special"):
        bits.append("특수문자 필수")
    if policy.get("included_numeric"):
        bits.append("숫자 필수")
    if policy.get("exclude_username"):
        bits.append("사용자명 제외")
    if not bits:
        return "(특별한 제약 없음)"
    return ", ".join(bits)


def _format_expired(value: Any) -> str:
    """DSM 의 expired 필드를 사람이 읽기 쉬운 한글로."""
    if value == "normal":
        return "활성"
    if value == "now":
        return "비활성"
    if isinstance(value, str) and value:
        return f"만료 {value}"
    return str(value)
