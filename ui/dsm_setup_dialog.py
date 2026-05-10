"""DSM(Synology) 연결 설정 다이얼로그.

사용자가 DSM URL·관리자 ID/비밀번호·자료실 그룹 이름·2FA 등을 입력하고
실제 NAS 와 통신해 자격증명이 동작하는지 검증한 뒤 암호화 저장.

흐름:
    1. 저장된 설정 자동 로드 (있으면 필드 채움)
    2. "연결 테스트" → 별도 스레드에서 DsmClient.test_connection + login → 결과 표시
    3. "저장" → 입력값을 data/dsm_credentials.ini 에 Fernet 암호화 저장
       (테스트 성공 후 누르길 권장 — 강제는 하지 않음)
"""
from __future__ import annotations

import sys
import threading

import wx


def _set_password_mask_to_asterisk(ctrl: wx.TextCtrl) -> None:
    """Windows 기본 마스크 문자(●)는 NVDA 가 한글로 '짭' 비슷하게 읽어 거슬림.
    EM_SETPASSWORDCHAR 메시지로 별표(*)로 바꿔 '별표' 로 자연스럽게 읽히게.
    Windows 외 플랫폼에선 조용히 무시."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        EM_SETPASSWORDCHAR = 0x00CC
        hwnd = ctrl.GetHandle()
        ctypes.windll.user32.SendMessageW(hwnd, EM_SETPASSWORDCHAR, ord("*"), 0)
        ctrl.Refresh()
    except Exception:
        pass

from core.dsm_client import DsmAuthError, DsmClient
from core.dsm_config import (
    DsmSettings,
    delete_dsm_settings,
    load_dsm_settings,
    save_dsm_settings,
)
from screen_reader import speak


_DEFAULT_URL = "https://dsm.kbugreenlight.net"
_DEFAULT_GROUP = "자료실 회원"


class DsmSetupDialog(wx.Dialog):
    def __init__(self, parent) -> None:
        super().__init__(
            parent,
            title="DSM 연결 설정",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.existing = load_dsm_settings()
        self._build_ui()
        self._populate_from_settings(self.existing)
        self.SetMinSize(wx.Size(560, 540))
        self.Fit()
        self.Centre()
        wx.CallAfter(self._announce)

    # ---------- UI ----------

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        intro = wx.StaticText(panel, label=(
            "DSM(Synology NAS) 관리 자동화에 필요한 정보를 입력하세요.\n"
            "비밀번호는 이 PC 머신 키로 암호화되어 data/dsm_credentials.ini 에 저장됩니다.\n"
            "저장 전 '연결 테스트' 로 자격증명이 통하는지 확인하시는 걸 권장합니다."
        ))
        sizer.Add(intro, 0, wx.ALL, 10)

        # 핵심 — 위젯 생성 순서가 곧 스크린리더의 "직전 라벨" 매칭 순서.
        # 반드시 StaticText 를 먼저 만들고, 그 다음에 TextCtrl 을 만들어야
        # NVDA 등이 Tab 이동 시 정확히 그 라벨을 읽어준다.

        # DSM URL
        sizer.Add(
            wx.StaticText(panel, label="DSM URL(&U):"),
            0, wx.LEFT | wx.RIGHT | wx.TOP, 10,
        )
        self.url_input = wx.TextCtrl(panel, value=_DEFAULT_URL, name="DSM URL")
        sizer.Add(self.url_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # 관리자 아이디
        sizer.Add(
            wx.StaticText(panel, label="관리자 아이디(&I):"),
            0, wx.LEFT | wx.RIGHT | wx.TOP, 10,
        )
        self.account_input = wx.TextCtrl(panel, name="관리자 아이디")
        sizer.Add(self.account_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # 관리자 비밀번호
        sizer.Add(
            wx.StaticText(panel, label="관리자 비밀번호(&P):"),
            0, wx.LEFT | wx.RIGHT | wx.TOP, 10,
        )
        self.password_input = wx.TextCtrl(
            panel, style=wx.TE_PASSWORD, name="관리자 비밀번호",
        )
        _set_password_mask_to_asterisk(self.password_input)
        sizer.Add(self.password_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # 자료실 회원 그룹 이름
        sizer.Add(
            wx.StaticText(panel, label="자료실 회원 그룹 이름(&G):"),
            0, wx.LEFT | wx.RIGHT | wx.TOP, 10,
        )
        self.group_input = wx.TextCtrl(
            panel, value=_DEFAULT_GROUP, name="자료실 회원 그룹",
        )
        sizer.Add(self.group_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # 2단계 인증 체크박스 — 라벨이 컨트롤 자체에 붙음.
        self.use_2fa_chk = wx.CheckBox(
            panel, label="2단계 인증 사용(&2)", name="2단계 인증 사용",
        )
        self.use_2fa_chk.Bind(wx.EVT_CHECKBOX, self._on_2fa_toggle)
        sizer.Add(self.use_2fa_chk, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)

        # OTP 코드 (2FA 사용 시만 활성)
        sizer.Add(
            wx.StaticText(
                panel,
                label="OTP 코드 (2단계 인증 사용 시 연결 테스트용 6자리):",
            ),
            0, wx.LEFT | wx.RIGHT | wx.TOP, 10,
        )
        self.otp_input = wx.TextCtrl(panel, name="OTP 코드")
        self.otp_input.Enable(False)
        sizer.Add(self.otp_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # SSL 인증서 검증 체크박스
        self.verify_ssl_chk = wx.CheckBox(
            panel,
            label="SSL 인증서 검증 (자가 서명 인증서이면 끄기)",
            name="SSL 인증서 검증",
        )
        self.verify_ssl_chk.SetValue(True)
        sizer.Add(self.verify_ssl_chk, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)

        # 진행/결과 표시
        self.status_text = wx.StaticText(panel, label="대기 중")
        sizer.Add(self.status_text, 0, wx.ALL, 10)

        # 버튼
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.test_btn = wx.Button(panel, label="연결 테스트(&T)")
        self.save_btn = wx.Button(panel, label="저장(&S)")
        self.delete_btn = wx.Button(panel, label="저장된 설정 삭제")
        self.close_btn = wx.Button(panel, wx.ID_CLOSE, "닫기")
        self.test_btn.Bind(wx.EVT_BUTTON, self._on_test)
        self.save_btn.Bind(wx.EVT_BUTTON, self._on_save)
        self.delete_btn.Bind(wx.EVT_BUTTON, self._on_delete)
        self.close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        for b in (self.test_btn, self.save_btn, self.delete_btn, self.close_btn):
            btn_sizer.Add(b, 0, wx.RIGHT, 8)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        panel.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CLOSE)

    # ---------- 기존 값 로드 ----------

    def _populate_from_settings(self, s: DsmSettings | None) -> None:
        if s is None:
            return
        if s.url:
            self.url_input.SetValue(s.url)
        if s.account:
            self.account_input.SetValue(s.account)
        if s.password:
            self.password_input.SetValue(s.password)
        if s.group_name:
            self.group_input.SetValue(s.group_name)
        self.use_2fa_chk.SetValue(s.use_2fa)
        self.verify_ssl_chk.SetValue(s.verify_ssl)
        self.otp_input.Enable(s.use_2fa)
        self.status_text.SetLabel(
            f"저장된 설정을 불러왔습니다 (URL: {s.url})"
        )

    # ---------- 입력 → DsmSettings ----------

    def _read_inputs(self) -> DsmSettings:
        return DsmSettings(
            url=self.url_input.GetValue().strip(),
            account=self.account_input.GetValue().strip(),
            password=self.password_input.GetValue(),
            group_name=self.group_input.GetValue().strip(),
            use_2fa=self.use_2fa_chk.GetValue(),
            verify_ssl=self.verify_ssl_chk.GetValue(),
        )

    # ---------- 이벤트 ----------

    def _on_2fa_toggle(self, _event=None) -> None:
        self.otp_input.Enable(self.use_2fa_chk.GetValue())

    def _on_test(self, _event=None) -> None:
        s = self._read_inputs()
        if not s.is_complete:
            wx.MessageBox(
                "URL · 관리자 ID · 비밀번호 · 그룹 이름을 모두 입력해 주세요.",
                "입력 부족", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        otp = self.otp_input.GetValue().strip() if s.use_2fa else ""
        if s.use_2fa and not otp:
            wx.MessageBox(
                "2단계 인증을 사용한다면 OTP 코드도 입력해야 테스트 가능합니다.\n"
                "(저장만 할 거면 OTP 없이 저장 → 실제 사용 시점에 OTP 입력)",
                "OTP 필요", wx.OK | wx.ICON_INFORMATION, self,
            )
            return

        self.test_btn.Disable()
        self._set_status("연결 테스트 중...")
        speak("DSM 연결 테스트를 시작합니다.")
        threading.Thread(
            target=self._test_in_thread, args=(s, otp), daemon=True,
        ).start()

    def _test_in_thread(self, s: DsmSettings, otp: str) -> None:
        try:
            with DsmClient(s.url, verify_ssl=s.verify_ssl) as client:
                info = client.test_connection()
                client.login(s.account, s.password, otp_code=otp)
        except DsmAuthError as e:
            wx.CallAfter(self._test_failed, str(e))
            return
        except Exception as e:
            wx.CallAfter(self._test_failed, f"예상치 못한 오류: {e}")
            return
        wx.CallAfter(self._test_succeeded, info)

    def _test_succeeded(self, info) -> None:
        self._set_status(
            f"연결 성공. DSM Auth API 버전: "
            f"{info.auth_min_version}~{info.auth_max_version}"
        )
        speak("DSM 연결 성공.")
        wx.MessageBox(
            "DSM 연결·로그인이 정상 동작합니다.\n저장 버튼을 눌러 자격증명을 보관하세요.",
            "테스트 성공", wx.OK | wx.ICON_INFORMATION, self,
        )
        self.test_btn.Enable()

    def _test_failed(self, message: str) -> None:
        self._set_status(f"실패: {message}")
        speak("DSM 연결 실패.")
        wx.MessageBox(message, "테스트 실패", wx.OK | wx.ICON_ERROR, self)
        self.test_btn.Enable()

    def _on_save(self, _event=None) -> None:
        s = self._read_inputs()
        if not s.is_complete:
            wx.MessageBox(
                "URL · 관리자 ID · 비밀번호 · 그룹 이름을 모두 입력해 주세요.",
                "입력 부족", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        try:
            save_dsm_settings(s)
        except OSError as e:
            wx.MessageBox(f"저장 실패: {e}", "오류", wx.OK | wx.ICON_ERROR, self)
            return
        self._set_status("저장 완료 (data/dsm_credentials.ini)")
        speak("DSM 설정을 저장했습니다.")
        wx.MessageBox(
            "DSM 설정을 저장했습니다.\n비밀번호는 머신 고유 키로 암호화됩니다.",
            "저장 완료", wx.OK | wx.ICON_INFORMATION, self,
        )

    def _on_delete(self, _event=None) -> None:
        ans = wx.MessageBox(
            "저장된 DSM 설정을 삭제할까요?\n"
            "다음에 사용하려면 다시 입력·테스트가 필요합니다.",
            "삭제 확인", wx.YES_NO | wx.ICON_QUESTION, self,
        )
        if ans != wx.YES:
            return
        delete_dsm_settings()
        self._set_status("저장된 설정을 삭제했습니다.")
        speak("DSM 설정을 삭제했습니다.")

    def _set_status(self, msg: str) -> None:
        self.status_text.SetLabel(msg)
        self.Layout()

    def _announce(self) -> None:
        if self.existing is not None and self.existing.is_complete:
            speak("DSM 연결 설정 화면. 저장된 설정이 있습니다. 연결 테스트 버튼을 누를 수 있습니다.")
        else:
            speak("DSM 연결 설정 화면. URL 과 관리자 자격증명을 입력해 주세요.")
