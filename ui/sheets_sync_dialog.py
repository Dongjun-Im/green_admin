"""구글시트 양방향 동기화 다이얼로그.

흐름:
    1. 시트 ID/URL 입력 (이전에 저장된 값 자동 로드)
    2. "동기화 시작" 클릭 → 별도 스레드에서 OAuth 인증 + sync 진행
       (첫 사용 시 브라우저가 떠 본인 구글 계정 인증 — 완료까지 다이얼로그 블로킹 안 함)
    3. 결과 요약 + 시트 링크 표시
"""
from __future__ import annotations

import threading
import webbrowser

import wx

from core.payment_store import PaymentStore
from core.sheets_sync import (
    GoogleAuthError,
    SheetsConfig,
    SyncSummary,
    normalize_spreadsheet_id,
    run_full_sync,
    sheet_url,
)
from screen_reader import speak


class SheetsSyncDialog(wx.Dialog):
    def __init__(self, parent, *, store: PaymentStore, members) -> None:
        super().__init__(
            parent,
            title="구글시트 동기화",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.store = store
        self.members = members
        self.config = SheetsConfig.load()
        self._build_ui()
        self.SetMinSize(wx.Size(640, 480))
        self.Fit()
        self.Centre()
        wx.CallAfter(self._announce_open)

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(
            wx.StaticText(
                panel,
                label=(
                    "구글 시트 주소(URL) 또는 시트 ID 입력(&I):\n"
                    "구글 스프레드시트 주소를 그대로 붙여넣으면 됩니다.\n"
                    "예) https://docs.google.com/spreadsheets/d/<여기가 시트 ID>/edit"
                ),
            ),
            0, wx.ALL, 10,
        )
        self.id_input = wx.TextCtrl(panel, value=self.config.spreadsheet_id,
                                    name="구글 시트 주소 또는 ID")
        self.id_input.Bind(wx.EVT_TEXT, self._on_id_changed)
        sizer.Add(self.id_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # 저장된(또는 지금 입력한) 시트의 전체 주소를 항상 보여 준다.
        sizer.Add(
            wx.StaticText(panel, label="저장된 시트 주소(&U):"),
            0, wx.LEFT | wx.RIGHT | wx.TOP, 10,
        )
        self.url_display = wx.TextCtrl(
            panel, value=sheet_url(self.config.spreadsheet_id),
            style=wx.TE_READONLY, name="저장된 시트 주소",
        )
        sizer.Add(self.url_display, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        sizer.Add(
            wx.StaticText(
                panel,
                label=(
                    "처음 동기화할 때만: 인터넷 브라우저가 열립니다. 본인 구글 계정으로 로그인해 주세요.\n"
                    "그 구글 계정이 이 시트를 볼 수 있어야 합니다 (시트 소유자이거나 공유받은 계정).\n"
                    "한 번 로그인하면 다음부터는 자동으로 됩니다."
                ),
            ),
            0, wx.ALL, 10,
        )

        # 동기화 정책 안내
        policy_text = (
            "시트에 다음 탭들이 자동으로 추가됩니다 (원래 있던 탭은 건드리지 않습니다):\n"
            "  - 초록앱_alias_매핑 : 입금자 이름과 소리샘 아이디 연결 (양쪽이 서로 반영)\n"
            "  - 초록앱_구독_매트릭스 : 회원별 구독 현황 (앱에서 시트로 매번 새로 씀)\n"
            "  - 초록앱_거래내역 : 가져온 입금 내역 (앱에서 시트로 매번 새로 씀)\n"
            "  - 설문지 응답 시트1 : 구글 폼으로 신청한 회원 (시트에서 앱으로 읽어옴)"
        )
        sizer.Add(
            wx.StaticText(panel, label=policy_text),
            0, wx.LEFT | wx.RIGHT, 10,
        )

        # 진행 상태 + 결과 영역
        self.status_text = wx.StaticText(panel, label="대기 중")
        sizer.Add(self.status_text, 0, wx.ALL, 10)

        # 버튼
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.sync_btn = wx.Button(panel, label="동기화 시작(&G)")
        self.open_btn = wx.Button(panel, label="이 주소로 시트 열기(&O)")
        self.copy_btn = wx.Button(panel, label="시트 주소 복사(&Y)")
        self.close_btn = wx.Button(panel, wx.ID_CLOSE, "닫기")
        self.sync_btn.Bind(wx.EVT_BUTTON, self._on_sync)
        self.open_btn.Bind(wx.EVT_BUTTON, self._on_open_sheet)
        self.copy_btn.Bind(wx.EVT_BUTTON, self._on_copy_url)
        self.close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        for b in (self.sync_btn, self.open_btn, self.copy_btn, self.close_btn):
            btn_sizer.Add(b, 0, wx.RIGHT, 8)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        panel.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CLOSE)
        self._refresh_url_display()

    # ---------- 시트 주소 ----------

    def _refresh_url_display(self) -> None:
        url = sheet_url(self.id_input.GetValue())
        self.url_display.SetValue(url or "(아직 시트 주소를 입력하지 않았습니다)")

    def _on_id_changed(self, _event=None) -> None:
        self._refresh_url_display()

    def _on_copy_url(self, _event=None) -> None:
        url = sheet_url(self.id_input.GetValue())
        if not url:
            wx.MessageBox("먼저 구글 시트 주소나 시트 ID 를 입력하세요.",
                          "입력 필요", wx.OK | wx.ICON_INFORMATION, self)
            return
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(url))
            finally:
                wx.TheClipboard.Close()
            speak("시트 주소를 복사했습니다.")
            self._set_status(f"시트 주소를 복사했습니다: {url}")
        else:
            wx.MessageBox(url, "시트 주소", wx.OK | wx.ICON_INFORMATION, self)

    # ---------- 이벤트 ----------

    def _on_sync(self, _event=None) -> None:
        sid = normalize_spreadsheet_id(self.id_input.GetValue())
        if not sid:
            wx.MessageBox(
                "시트 ID 또는 URL 을 입력하세요.",
                "입력 필요",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return

        # ID 저장 (인증 성공 여부와 무관하게 다음 시도에 자동 채움)
        self.config.spreadsheet_id = sid
        self.config.save()
        self.id_input.ChangeValue(sid)   # EVT_TEXT 재발생 없이 값만 정리
        self._refresh_url_display()

        self.sync_btn.Disable()
        self._set_status("인증 + 동기화 중... (브라우저가 열릴 수 있습니다)")
        speak("동기화를 시작합니다. 브라우저 창이 열리면 본인 구글 계정으로 로그인해 주세요.")

        # OAuth 의 run_local_server 가 블로킹이라 별도 스레드.
        threading.Thread(
            target=self._sync_in_thread, args=(sid,), daemon=True
        ).start()

    def _sync_in_thread(self, spreadsheet_id: str) -> None:
        try:
            summary = run_full_sync(self.store, self.members, spreadsheet_id)
        except GoogleAuthError as e:
            wx.CallAfter(self._on_sync_failed, str(e))
            return
        except Exception as e:
            wx.CallAfter(self._on_sync_failed, f"동기화 오류: {e}")
            return
        wx.CallAfter(self._on_sync_done, summary)

    def _on_sync_done(self, summary: SyncSummary) -> None:
        # 마지막 sync 시각 기록
        from datetime import datetime
        self.config.last_sync_at = datetime.now().isoformat(timespec="seconds")
        self.config.save()

        msg = (
            f"동기화 완료\n"
            f"  alias 시트로 보냄: {summary.aliases_pushed_to_sheet}건\n"
            f"  alias 앱으로 가져옴: {summary.aliases_pulled_to_sqlite}건\n"
            f"  구독 push: {summary.subscriptions_pushed}건\n"
            f"  거래내역 push: {summary.transactions_pushed}건\n"
            f"  폼 신청자 가져옴(설문지 응답 시트1): {summary.form_applicants_pulled}건"
        )
        self._set_status(msg)
        speak(
            f"동기화 완료. 매핑 변경 {summary.aliases_pulled_to_sqlite}건, "
            f"구독 {summary.subscriptions_pushed}건, "
            f"폼 신청자 {summary.form_applicants_pulled}건."
        )
        self.sync_btn.Enable()

    def _on_sync_failed(self, message: str) -> None:
        self._set_status(f"실패: {message}")
        speak("동기화 실패.")
        wx.MessageBox(message, "동기화 실패", wx.OK | wx.ICON_ERROR, self)
        self.sync_btn.Enable()

    def _on_open_sheet(self, _event=None) -> None:
        sid = normalize_spreadsheet_id(self.id_input.GetValue())
        if not sid:
            wx.MessageBox(
                "먼저 시트 ID 를 입력하세요.",
                "입력 필요",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        webbrowser.open(sheet_url(sid))

    def _set_status(self, msg: str) -> None:
        self.status_text.SetLabel(msg)
        self.Layout()

    def _announce_open(self) -> None:
        if self.config.spreadsheet_id:
            speak(
                "구글시트 동기화 화면. 저장된 시트 주소가 있습니다. "
                "주소를 바꾸려면 맨 위 입력칸에 새 구글 시트 주소를 붙여넣고, "
                "그대로 쓰려면 동기화 시작 버튼을 누르세요."
            )
        else:
            speak(
                "구글시트 동기화 화면. 맨 위 입력칸에 쓰실 구글 시트 주소를 붙여넣어 주세요. "
                "주소는 docs.google.com 으로 시작하는 스프레드시트 주소입니다."
            )
