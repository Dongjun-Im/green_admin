"""DSM 신규 사용자 생성 다이얼로그 (자료실 신청 폼 항목 확장).

운영자가 신청자 정보(이름·전화번호·이메일·요금제·희망아이디·비밀번호·동의여부)를
입력해 DSM 계정을 만들고 자료실 회원 그룹에 자동 추가한다. 생성 후:
  · store 가 있으면 form_applicants 테이블에 즉시 upsert (메인 매트릭스 반영)
  · 구글시트가 설정되어 있으면 '설문지 응답 시트1' 에 새 행을 append (best-effort)

생성 결과는 운영자에게 모달로 표시되어 회원에게 메일로 안내할 수 있도록.
스크린리더 친화: 모든 입력 위에 라벨 StaticText 를 컨트롤보다 먼저 생성, name= 부여.
"""
from __future__ import annotations

import sys
import threading
from datetime import datetime

import wx

from core.dsm_client import DsmAuthError, DsmClient
from core.dsm_config import DsmSettings
from core.dsm_service import (
    ActivationResult,
    activate_subscriber,
    generate_temp_password,
)
from core.models import FormApplicant
from core.sheets_sync import (
    NEW_USER_MEMO,
    GoogleAuthError,
    SheetsConfig,
    SheetsSyncClient,
    parse_plan_months,
)
from screen_reader import speak


# 요금제 콤보 항목 — config.SUBSCRIPTION_PRICING 과 일치 (3000/9000/12000/24000원).
PLAN_CHOICES = ["3000=1개월", "9000=3개월", "12000=6개월", "24000=12개월"]

# 자료실 이용 동의서 전문 — 다이얼로그에 읽기 전용으로 표시.
_AGREEMENT_TEXT = (
    "자료실 이용 동의\n"
    "아래 동의 사항을 읽으신 후 동의 여부를 선택해주세요.\n"
    "\n"
    "자료실 이용 동의 사항\n"
    "초록등대 자료실의 자료는 회원에게 제공되는 콘텐츠로, 다음 사항에 동의해주셔야 이용이 가능합니다.\n"
    "\n"
    "1. 자료실의 모든 자료는 무단으로 복제, 배포할 수 없습니다.\n"
    "\n"
    "2. 본인의 계정과 비밀번호를 타인과 공유하거나 양도할 수 없습니다.\n"
    "\n"
    "3. 자료실 콘텐츠를 외부 커뮤니티, SNS, 블로그 등에 무단으로 공유하거나 게시할 수 없습니다.\n"
    "\n"
    "4. 위 사항을 위반할 경우 사전 통보 없이 계정이 즉시 정지될 수 있으며, 환불은 불가합니다.\n"
    "\n"
    "본 동의 사항에 동의하지 않으시면 자료실 이용이 제한됩니다."
)


def _set_password_mask_to_asterisk(ctrl: wx.TextCtrl) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        EM_SETPASSWORDCHAR = 0x00CC
        ctypes.windll.user32.SendMessageW(
            ctrl.GetHandle(), EM_SETPASSWORDCHAR, ord("*"), 0,
        )
        ctrl.Refresh()
    except Exception:
        pass


class DsmCreateUserDialog(wx.Dialog):
    def __init__(
        self,
        parent,
        *,
        settings: DsmSettings,
        store=None,
        members=None,
    ) -> None:
        super().__init__(
            parent,
            title="DSM 신규 사용자 만들기",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.settings = settings
        self.store = store
        self.members = members
        self.last_result: ActivationResult | None = None
        self._build_ui()
        self.SetMinSize(wx.Size(580, 640))
        self.Fit()
        self.Centre()
        wx.CallAfter(self._announce)

    # ---------- UI ----------

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(
            wx.StaticText(panel, label=(
                "자료실 신청 정보를 입력해 DSM 계정을 만들고 자료실 회원 그룹에 추가합니다.\n"
                f"그룹: {self.settings.group_name}\n"
                "생성 후 구글시트 '설문지 응답 시트1' 에 동일 내용이 새 행으로 추가됩니다."
            )),
            0, wx.ALL, 10,
        )

        def labeled(label_text: str, ctrl_factory):
            sizer.Add(
                wx.StaticText(panel, label=label_text),
                0, wx.LEFT | wx.RIGHT | wx.TOP, 10,
            )
            ctrl = ctrl_factory()
            sizer.Add(ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
            return ctrl

        # 이름
        self.name_input = labeled(
            "이름(&M):", lambda: wx.TextCtrl(panel, name="이름"),
        )
        # 전화번호
        self.phone_input = labeled(
            "전화번호(&T):", lambda: wx.TextCtrl(panel, name="전화번호"),
        )
        # 이메일
        self.email_input = labeled(
            "이메일(&E):", lambda: wx.TextCtrl(panel, name="이메일"),
        )
        # 요금제 (콤보)
        self.plan_choice = labeled(
            "요금제(&L):",
            lambda: wx.Choice(panel, choices=PLAN_CHOICES, name="요금제"),
        )
        self.plan_choice.SetSelection(0)
        # 희망아이디 (= DSM/소리샘 user_id)
        self.userid_input = labeled(
            "희망아이디(&I) - DSM/소리샘 user_id:",
            lambda: wx.TextCtrl(panel, name="희망아이디"),
        )

        # 비밀번호 + 자동 생성 + 표시
        sizer.Add(
            wx.StaticText(panel, label="비밀번호(&P):"),
            0, wx.LEFT | wx.RIGHT | wx.TOP, 10,
        )
        pw_row = wx.BoxSizer(wx.HORIZONTAL)
        self.password_input = wx.TextCtrl(
            panel, style=wx.TE_PASSWORD, name="비밀번호",
        )
        _set_password_mask_to_asterisk(self.password_input)
        self.show_pw_chk = wx.CheckBox(panel, label="표시(&S)")
        self.show_pw_chk.Bind(wx.EVT_CHECKBOX, self._on_toggle_show_pw)
        self.gen_pw_btn = wx.Button(panel, label="자동 생성(&G)")
        self.gen_pw_btn.Bind(wx.EVT_BUTTON, self._on_generate_password)
        pw_row.Add(self.password_input, 1, wx.EXPAND | wx.RIGHT, 8)
        pw_row.Add(self.show_pw_chk, 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 8)
        pw_row.Add(self.gen_pw_btn, 0, wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(pw_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # 비밀번호 확인
        sizer.Add(
            wx.StaticText(panel, label="비밀번호 확인(&C):"),
            0, wx.LEFT | wx.RIGHT | wx.TOP, 10,
        )
        self.password2_input = wx.TextCtrl(
            panel, style=wx.TE_PASSWORD, name="비밀번호 확인",
        )
        _set_password_mask_to_asterisk(self.password2_input)
        sizer.Add(self.password2_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # 동의 영역
        sizer.Add(
            wx.StaticText(panel, label="자료실 이용 동의 안내(&V):"),
            0, wx.LEFT | wx.RIGHT | wx.TOP, 10,
        )
        self.agreement_text = wx.TextCtrl(
            panel,
            value=_AGREEMENT_TEXT,
            style=wx.TE_MULTILINE | wx.TE_READONLY,
            name="자료실 이용 동의 안내",
        )
        self.agreement_text.SetMinSize(wx.Size(-1, 160))
        sizer.Add(self.agreement_text, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        self.agree_cb = wx.CheckBox(
            panel, label="위 동의 사항에 모두 동의합니다(&A)", name="동의 체크",
        )
        sizer.Add(self.agree_cb, 0, wx.ALL, 10)

        # 진행/결과
        self.status_text = wx.StaticText(panel, label="대기 중")
        sizer.Add(self.status_text, 0, wx.ALL, 10)

        # 버튼
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.create_btn = wx.Button(panel, label="만들기(&K)")
        self.cancel_btn = wx.Button(panel, wx.ID_CANCEL, "닫기")
        self.create_btn.Bind(wx.EVT_BUTTON, self._on_create)
        btn_sizer.Add(self.create_btn, 0, wx.RIGHT, 8)
        btn_sizer.Add(self.cancel_btn, 0)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        panel.SetSizer(sizer)
        sizer.SetSizeHints(self)
        self.SetEscapeId(wx.ID_CANCEL)
        self.SetAffirmativeId(wx.ID_OK)
        self.name_input.SetFocus()

    # ---------- 이벤트 ----------

    def _selected_plan_raw(self) -> str:
        idx = self.plan_choice.GetSelection()
        if 0 <= idx < len(PLAN_CHOICES):
            return PLAN_CHOICES[idx]
        return ""

    def _on_generate_password(self, _event=None) -> None:
        pw = generate_temp_password()
        self.password_input.SetValue(pw)
        self.password2_input.SetValue(pw)
        speak("임시 비밀번호 자동 생성됨. 비밀번호 확인도 같이 채워졌습니다.")

    def _on_toggle_show_pw(self, _event=None) -> None:
        if self.show_pw_chk.GetValue():
            wx.MessageBox(
                f"현재 입력된 비밀번호:\n\n{self.password_input.GetValue()}",
                "비밀번호 표시",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            self.show_pw_chk.SetValue(False)

    def _on_create(self, _event=None) -> None:
        name = self.name_input.GetValue().strip()
        phone = self.phone_input.GetValue().strip()
        email = self.email_input.GetValue().strip()
        plan_raw = self._selected_plan_raw()
        userid = self.userid_input.GetValue().strip()
        pw = self.password_input.GetValue()
        pw2 = self.password2_input.GetValue()

        if not name:
            wx.MessageBox("이름을 입력하세요.", "입력 필요",
                          wx.OK | wx.ICON_INFORMATION, self)
            self.name_input.SetFocus()
            return
        if not userid:
            wx.MessageBox("희망아이디(user_id)를 입력하세요.", "입력 필요",
                          wx.OK | wx.ICON_INFORMATION, self)
            self.userid_input.SetFocus()
            return
        if not pw:
            wx.MessageBox("비밀번호를 입력하거나 '자동 생성' 버튼을 눌러 주세요.",
                          "입력 필요", wx.OK | wx.ICON_INFORMATION, self)
            self.password_input.SetFocus()
            return
        if pw != pw2:
            wx.MessageBox("비밀번호와 비밀번호 확인이 일치하지 않습니다.",
                          "입력 확인", wx.OK | wx.ICON_WARNING, self)
            self.password2_input.SetFocus()
            return
        if not self.agree_cb.IsChecked():
            wx.MessageBox(
                "자료실 이용 동의 사항에 동의해야 계정을 만들 수 있습니다.\n"
                "'위 동의 사항에 모두 동의합니다' 체크박스를 선택해 주세요.",
                "동의 필요", wx.OK | wx.ICON_INFORMATION, self,
            )
            self.agree_cb.SetFocus()
            return

        otp = self._maybe_ask_otp()
        if otp is None:
            return

        self.create_btn.Disable()
        self._set_status("DSM 에 사용자 생성 중...")
        speak("DSM 에 사용자 생성 시작.")
        threading.Thread(
            target=self._create_in_thread,
            args=(name, phone, email, plan_raw, userid, pw, otp),
            daemon=True,
        ).start()

    def _create_in_thread(
        self, name: str, phone: str, email: str, plan_raw: str,
        userid: str, pw: str, otp: str,
    ) -> None:
        s = self.settings
        try:
            with DsmClient(s.url, verify_ssl=s.verify_ssl) as client:
                client.login(s.account, s.password, otp_code=otp)
                result = activate_subscriber(
                    client, userid, s.group_name,
                    initial_password=pw,
                    email=email, description=name,
                )
        except DsmAuthError as e:
            wx.CallAfter(self._failed, str(e))
            return
        except Exception as e:
            wx.CallAfter(self._failed, f"예상치 못한 오류: {e}")
            return

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1) 로컬 store 에 신청자 upsert — 메인 매트릭스 즉시 반영
        store_note = ""
        if self.store is not None:
            try:
                self.store.upsert_form_applicants([FormApplicant(
                    member_user_id=userid,
                    applied_at=now_str,
                    name=name,
                    phone=phone,
                    email=email,
                    plan_raw=plan_raw,
                    plan_months=parse_plan_months(plan_raw),
                    agreed=True,
                )])
                store_note = "  앱 신청자 목록에 반영됨"
            except Exception as e:
                store_note = f"  앱 신청자 목록 반영 실패: {e}"

        # 2) 구글시트 '설문지 응답 시트1' 에 행 추가 (best-effort)
        sheet_note = self._append_to_sheet(
            now_str, name, phone, email, plan_raw, userid, pw,
        )

        wx.CallAfter(self._succeeded, result, pw, store_note, sheet_note)

    def _append_to_sheet(
        self, now_str: str, name: str, phone: str, email: str,
        plan_raw: str, userid: str, pw: str,
    ) -> str:
        """설문지 응답 시트1 에 한 행 추가. 실패해도 사용자 생성은 유지 — 메시지만."""
        try:
            cfg = SheetsConfig.load()
        except Exception:
            cfg = None
        sid = (cfg.spreadsheet_id if cfg else "") or ""
        if not sid:
            return "  구글시트 미설정 - 시트 행 추가 생략"
        try:
            client = SheetsSyncClient(sid)
            # A~Q (17개): 9개 폼 필드 + 시작일/만료일/발송플래그 4개(빈칸) + 상태=활성 + 메모
            title = client.append_form_response([
                now_str, name, phone, email, plan_raw, userid, pw, pw, "동의함",
                "", "", "", "", "", "", "활성", NEW_USER_MEMO,
            ])
            return f"  구글시트 '{title}' 에 새 행 추가됨 (상태=활성, 메모='{NEW_USER_MEMO}')"
        except GoogleAuthError as e:
            return f"  구글시트 행 추가 실패 (인증): {e}"
        except Exception as e:
            return f"  구글시트 행 추가 실패: {e}"

    def _succeeded(
        self, result: ActivationResult, password_used: str,
        store_note: str, sheet_note: str,
    ) -> None:
        self.last_result = result
        head = "신규 생성됨" if result.created else "기존 계정 활성화"
        if result.in_group:
            group_line = "  자료실 그룹 : 추가됨"
        elif result.group_error:
            group_line = (
                "  자료실 그룹 : [주의] 추가 실패 - DSM 웹 관리자에서 직접 추가해 주세요\n"
                f"                ({result.group_error})"
            )
        else:
            group_line = "  자료실 그룹 : 이미 멤버"
        extra_lines = []
        if store_note:
            extra_lines.append(store_note)
        if sheet_note:
            extra_lines.append(sheet_note)
        extra = ("\n" + "\n".join(extra_lines)) if extra_lines else ""
        msg = (
            f"{head}\n\n"
            f"  사용자 이름 : {result.user_name}\n"
            f"{group_line}\n"
            f"  비밀번호    : {password_used}\n"
            f"{extra}\n\n"
            f"이 비밀번호를 회원에게 안내해 주세요. "
            f"첫 로그인 후 회원이 직접 변경하도록 권합니다."
        )
        self._set_status(f"{head}: {result.user_name}")
        speak(f"DSM 사용자 {result.user_name} {head}.")
        wx.MessageBox(msg, "생성 결과", wx.OK | wx.ICON_INFORMATION, self)
        self.EndModal(wx.ID_OK)

    def _failed(self, message: str) -> None:
        self._set_status(f"실패: {message}")
        speak("DSM 사용자 생성 실패.")
        wx.MessageBox(message, "생성 실패", wx.OK | wx.ICON_ERROR, self)
        self.create_btn.Enable()

    def _maybe_ask_otp(self) -> str | None:
        if not self.settings.use_2fa:
            return ""
        with wx.TextEntryDialog(
            self,
            "DSM 2단계 인증 OTP 6자리:",
            "OTP 코드 입력",
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return None
            otp = dlg.GetValue().strip()
        if not otp:
            return None
        return otp

    def _set_status(self, msg: str) -> None:
        self.status_text.SetLabel(msg)
        self.Layout()

    def _announce(self) -> None:
        speak(
            f"DSM 신규 사용자 생성 화면. "
            f"자료실 그룹 {self.settings.group_name} 에 추가됩니다. "
            "이름, 전화번호, 이메일, 요금제, 희망아이디, 비밀번호를 입력하고 "
            "동의 사항을 확인한 뒤 동의 체크박스를 선택하세요."
        )
