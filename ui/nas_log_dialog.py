"""자료실(NAS) 접속 로그 다이얼로그.

상단: 기간/회원/동작/카테고리 필터 + 적용/새로고침/내보내기/진단 버튼.
하단: 결과 ListCtrl (REPORT) — 시간/회원/IP/프로토콜/동작/카테고리/파일.
상태 줄: 마지막 수집 결과·항목 수.

- 초기에는 SQLite 에 이미 쌓인 로그(최근 30일)만 빠르게 보여 준다.
- '지금 새로고침' 을 누르면 DSM 에서 새 로그를 추가 수집(별도 스레드, 2FA 시 OTP 대화상자).
- 응답 원본 저장(진단) 은 빌드 차이로 비어 나올 때 fallback 보강용 JSON 을 떠 저장.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import wx

from core.dsm_client import DsmAuthError, DsmClient
from core.dsm_config import load_dsm_settings
from core.nas_log_service import (
    ACTION_GROUPS,
    ACTION_LABELS,
    enrich_with_members,
    fetch_and_store_logs,
    save_diagnostic_dump,
)
from core.nas_log_store import NasLogFilter, NasLogStore
from core.nas_log_html import default_html_path, write_nas_log_html
from core.nas_log_txt import default_txt_path, write_nas_log_txt
from core.nas_log_xlsx import default_xlsx_path, write_nas_log_xlsx
from screen_reader import speak
from ui.nas_log_export_dialog import (
    FORMAT_HTML,
    FORMAT_TXT,
    FORMAT_XLSX,
    NasLogExportDialog,
)
from ui.progress_dialog import ProgressTaskDialog


class NasLogDialog(wx.Dialog):
    def __init__(self, parent, *, members=None):
        super().__init__(
            parent,
            title="자료실 접속 로그",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
        )
        self.members = list(members or [])
        self.store = NasLogStore()
        # DSM 자료실 그룹 멤버 — 메타에 캐시된 마지막 값 로드 (회원 매칭 2순위).
        self._dsm_group_members: list[str] = self.store.dsm_group_members()
        self._build_ui()
        self.SetMinSize(wx.Size(960, 600))
        self.Fit()
        self.Centre()
        self._apply_filter()
        wx.CallAfter(self._announce_open)

    # ---------- UI ----------

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        # ----- 필터 영역 -----
        outer.Add(
            wx.StaticText(panel, label="필터 — 기간(시작/종료), 회원 ID/이름/닉네임, 동작, 카테고리(&F):"),
            0, wx.LEFT | wx.RIGHT | wx.TOP, 8,
        )
        row1 = wx.BoxSizer(wx.HORIZONTAL)
        row1.Add(wx.StaticText(panel, label="시작 날짜:"),
                 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 4)
        self.start_dp = wx.adv.DatePickerCtrl(
            panel, style=wx.adv.DP_DROPDOWN, name="시작 날짜",
        )
        # 기본: 30일 전
        default_start = date.today() - timedelta(days=30)
        self.start_dp.SetValue(_wxdate(default_start))
        row1.Add(self.start_dp, 0, wx.RIGHT, 8)

        row1.Add(wx.StaticText(panel, label="종료 날짜:"),
                 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 4)
        self.end_dp = wx.adv.DatePickerCtrl(
            panel, style=wx.adv.DP_DROPDOWN, name="종료 날짜",
        )
        self.end_dp.SetValue(_wxdate(date.today()))
        row1.Add(self.end_dp, 0, wx.RIGHT, 8)

        row1.Add(wx.StaticText(panel, label="회원 (아이디/이름/닉네임):"),
                 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 4)
        self.user_input = wx.TextCtrl(panel, name="회원 ID 또는 이름 또는 닉네임 부분일치")
        self.user_input.SetMinSize(wx.Size(180, -1))
        row1.Add(self.user_input, 0, wx.RIGHT, 8)
        outer.Add(row1, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        row2 = wx.BoxSizer(wx.HORIZONTAL)
        row2.Add(wx.StaticText(panel, label="동작:"),
                 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 4)
        self._action_groups = list(ACTION_GROUPS)
        self.action_choice = wx.Choice(
            panel, choices=[label for label, _ in self._action_groups],
            name="동작 종류 필터",
        )
        self.action_choice.SetSelection(0)
        row2.Add(self.action_choice, 0, wx.RIGHT, 8)

        row2.Add(wx.StaticText(panel, label="카테고리(폴더 첫 단계):"),
                 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 4)
        self.category_input = wx.TextCtrl(panel, name="카테고리 부분일치")
        self.category_input.SetMinSize(wx.Size(180, -1))
        row2.Add(self.category_input, 0, wx.RIGHT, 8)

        self.apply_btn = wx.Button(panel, label="필터 적용(&F)")
        self.apply_btn.Bind(wx.EVT_BUTTON, lambda e: self._apply_filter())
        row2.Add(self.apply_btn, 0, wx.RIGHT, 8)
        outer.Add(row2, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 4)

        # ----- 액션 버튼 -----
        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.refresh_btn = wx.Button(panel, label="지금 새로고침(&R)")
        self.refresh_btn.Bind(wx.EVT_BUTTON, self._on_refresh)
        btns.Add(self.refresh_btn, 0, wx.RIGHT, 8)

        self.export_btn = wx.Button(panel, label="내보내기(&E)...")
        self.export_btn.Bind(wx.EVT_BUTTON, self._on_export)
        btns.Add(self.export_btn, 0, wx.RIGHT, 8)

        self.diag_btn = wx.Button(panel, label="진단: 응답 원본 저장(&V)")
        self.diag_btn.Bind(wx.EVT_BUTTON, self._on_diag)
        btns.Add(self.diag_btn, 0, wx.RIGHT, 8)
        outer.Add(btns, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)

        # ----- 상태 줄 -----
        self.status_text = wx.StaticText(panel, label="대기 중")
        outer.Add(self.status_text, 0, wx.ALL, 8)

        # ----- 결과 목록 -----
        outer.Add(
            wx.StaticText(panel, label="접속 로그(&L):"),
            0, wx.LEFT | wx.RIGHT, 8,
        )
        self.list_ctrl = wx.ListCtrl(
            panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL,
            name="접속 로그 목록",
        )
        self._build_columns()
        outer.Add(self.list_ctrl, 1, wx.EXPAND | wx.ALL, 8)

        # ----- 닫기 버튼 -----
        close_btn = wx.Button(panel, wx.ID_CLOSE, "닫기(&C)")
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        outer.Add(close_btn, 0, wx.ALIGN_RIGHT | wx.ALL, 8)

        panel.SetSizer(outer)
        self.SetEscapeId(wx.ID_CLOSE)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

    def _build_columns(self) -> None:
        cols = [
            ("시간", 150),
            ("회원", 200),
            ("IP", 130),
            ("프로토콜", 100),
            ("동작", 80),
            ("카테고리", 140),
            ("파일", 220),
            ("전체 경로", 240),
            ("원문(진단)", 320),  # DSM 응답 본문 — 동작 파싱 보강용
        ]
        for i, (title, w) in enumerate(cols):
            self.list_ctrl.AppendColumn(title, width=w)

    # ---------- 필터 + 표시 ----------

    def _current_filter(self) -> NasLogFilter:
        """SQL 단계 필터. 회원 검색어는 SQL 에서 빼고 (이름/닉네임은 회원 매칭 후에만
        알 수 있으므로 enrich 한 뒤 Python 에서 필터)."""
        s_wx = self.start_dp.GetValue()
        e_wx = self.end_dp.GetValue()
        start_d = _pydate(s_wx) if s_wx.IsValid() else None
        end_d = _pydate(e_wx) if e_wx.IsValid() else None
        action_idx = self.action_choice.GetSelection()
        actions: list[str] = []
        if 0 <= action_idx < len(self._action_groups):
            _label, action_list = self._action_groups[action_idx]
            actions = list(action_list)
        return NasLogFilter(
            start_date=start_d,
            end_date=end_d,
            dsm_user_id_like="",   # 이름/닉네임 매치까지 가능하도록 SQL 에선 비움
            action_in=actions,
            category_like=self.category_input.GetValue().strip(),
            limit=5000,
        )

    def _apply_filter(self) -> None:
        flt = self._current_filter()
        entries = self.store.entries(flt)
        # 소리샘 회원(있으면) → DSM 자료실 그룹(있으면) → 미등록 순으로 매칭.
        rows = enrich_with_members(
            entries, self.members,
            dsm_group_member_ids=self._dsm_group_members,
        )
        # 회원 검색어 — DSM 아이디 / 소리샘 user_id / 이름 / 닉네임 부분 일치.
        user_q = self.user_input.GetValue().strip().lower()
        if user_q:
            rows = [r for r in rows if _row_matches_user_query(r, user_q)]
        self.list_ctrl.DeleteAllItems()
        for r in rows:
            e = r.entry
            idx = self.list_ctrl.InsertItem(self.list_ctrl.GetItemCount(),
                                            e.logged_at.replace("T", " "))
            self.list_ctrl.SetItem(idx, 1, r.display_name)
            self.list_ctrl.SetItem(idx, 2, e.ip)
            self.list_ctrl.SetItem(idx, 3, e.protocol)
            self.list_ctrl.SetItem(idx, 4, ACTION_LABELS.get(e.action, e.action))
            self.list_ctrl.SetItem(idx, 5, e.category)
            self.list_ctrl.SetItem(idx, 6, e.file_name)
            self.list_ctrl.SetItem(idx, 7, e.file_path)
            # 원문 — 진단용. 너무 길면 자름.
            raw = (e.raw_message or "").replace("\n", " ").replace("\r", " ")
            self.list_ctrl.SetItem(idx, 8, raw[:240])
        self._refresh_status_line(extra=f"표시 {len(rows)}건")
        speak(f"접속 로그 {len(rows)}건 표시.")

    def _refresh_status_line(self, *, extra: str = "") -> None:
        total = self.store.count()
        ok, msg, at = self.store.last_status()
        when = at.replace("T", " ") if at else "-"
        flag = "OK" if ok else "(인증 필요 또는 미수집)"
        bits = [f"전체 저장: {total}건", f"마지막 수집: {when} {flag}"]
        # 회원 매칭 자원 가용성 표시 — 매칭이 안 될 때 원인 파악에 도움.
        if self.members:
            bits.append(f"소리샘 회원: {len(self.members)}명")
        else:
            bits.append("소리샘 회원: 없음 (Ctrl+F 로 한 번 회원 검색을 실행하면 이름이 보입니다)")
        bits.append(f"자료실 그룹: {len(self._dsm_group_members)}명")
        if msg:
            bits.append(f"메시지: {msg}")
        if extra:
            bits.append(extra)
        self.status_text.SetLabel(" / ".join(bits))
        self.Layout()

    # ---------- 새로고침 (DSM 호출) ----------

    def _on_refresh(self, _event=None) -> None:
        settings = load_dsm_settings()
        if settings is None or not settings.is_complete:
            wx.MessageBox(
                "DSM 설정이 없습니다. 자료실 구독비 관리 → 'DSM 설정' 에서 먼저 자격을 입력하세요.",
                "DSM 설정 필요", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        otp = ""
        if settings.use_2fa:
            with wx.TextEntryDialog(
                self, "DSM 2단계 인증 OTP 6자리:", "OTP 코드 입력",
            ) as dlg:
                if dlg.ShowModal() != wx.ID_OK:
                    return
                otp = dlg.GetValue().strip()
            if not otp:
                return
        speak("DSM 에서 접속 로그를 가져옵니다. 진행 비프음이 함께 들립니다.")

        # 진행률 + 상승 비프음 다이얼로그 (ProgressTaskDialog 가 워커 스레드 관리).
        store = self.store

        def worker(progress_cb):
            from config import DUMPS_DIR
            with DsmClient(settings.url, verify_ssl=settings.verify_ssl) as client:
                client.login(settings.account, settings.password, otp_code=otp)
                return fetch_and_store_logs(
                    client, store,
                    dsm_group_name=settings.group_name or None,
                    progress_cb=progress_cb,
                    dump_dir=str(DUMPS_DIR),
                )

        self.refresh_btn.Disable()
        self.diag_btn.Disable()
        dlg = ProgressTaskDialog(
            self,
            title="DSM 로그 수집",
            task=worker,
            message="DSM 에 접속해 로그를 가져오는 중입니다...",
            can_cancel=False,
        )
        try:
            dlg.run_modal()
        finally:
            dlg.Destroy()
        self.refresh_btn.Enable()
        self.diag_btn.Enable()

        if dlg.error is not None:
            msg = str(dlg.error)
            self.store.set_last_status(False, msg)
            speak(f"새로고침 실패. {msg}")
            wx.MessageBox(msg, "새로고침 실패", wx.OK | wx.ICON_WARNING, self)
            return

        result = dlg.result
        # 새로 받은 DSM 자료실 그룹 멤버를 매칭 인덱스에 반영
        if getattr(result, "dsm_group_member_ids", None):
            self._dsm_group_members = list(result.dsm_group_member_ids)
        if result and result.ok:
            speak(f"새로고침 완료. {result.message}")
            # 파일 전송 로그가 0건이면 DSM 설정 안내 — 운영자가 가장 자주 빠뜨리는 부분.
            if getattr(result, "file_transfer_seems_disabled", False) \
                    and getattr(result, "connection_count", 0) > 0:
                wx.MessageBox(
                    "파일 동작 로그가 0건입니다. (로그인/로그아웃만 들어왔습니다.)\n\n"
                    "DSM 의 '로그 센터' 는 패키지 센터로 설치되는 별도 패키지입니다. "
                    "다음 절차로 기록 대상을 켜 주세요:\n\n"
                    "  1. DSM 웹 관리자 → 메인 메뉴(왼쪽 위) → '로그 센터' 열기\n"
                    "     (로그 센터가 없으면 패키지 센터에서 'Log Center' 설치)\n"
                    "  2. 왼쪽의 '로그 수신' 또는 '설정' 메뉴\n"
                    "  3. '로그 형식별 설정' 또는 '로그 종류' 에서\n"
                    "     - WebDAV 로그\n"
                    "     - SMB 로그 (Windows 파일 서비스)\n"
                    "     - File Station 로그\n"
                    "     - AFP / FTP / NFS (필요한 경우)\n"
                    "     항목을 모두 활성화\n"
                    "  4. 적용 → 회원이 다운로드/삭제 등을 한 뒤 다시 '지금 새로고침'\n\n"
                    "이미 다 켜져 있는데도 0건이 계속 나오면, '진단: 응답 원본 저장' 으로 "
                    "파일을 만들어 개발자에게 보내 주세요 — 그러면 DSM 빌드의 응답 형식을 "
                    "파일에서 보고 정확한 logtype 으로 가져오도록 맞춰 드립니다.",
                    "파일 동작 로그가 비어 있습니다",
                    wx.OK | wx.ICON_WARNING, self,
                )
            elif result.other_count and result.other_sample_path:
                # '기타' 가 많이 나오면 파서 보강 요청.
                wx.MessageBox(
                    f"새로고침 완료 — {result.message}\n\n"
                    f"'기타'로 분류된 항목이 {result.other_count}건 있습니다. "
                    f"파서 보강을 위해 샘플을 저장했습니다:\n{result.other_sample_path}\n\n"
                    "이 파일을 개발자에게 보내 주시면 동작 분류를 정확히 맞출 수 있습니다.",
                    "수집 완료 (보강 필요)", wx.OK | wx.ICON_INFORMATION, self,
                )
        else:
            speak("새로고침 실패")
        self._apply_filter()

    # ---------- 진단 ----------

    def _on_diag(self, _event=None) -> None:
        settings = load_dsm_settings()
        if settings is None or not settings.is_complete:
            wx.MessageBox(
                "DSM 설정이 없습니다. 'DSM 설정' 에서 먼저 자격을 입력하세요.",
                "DSM 설정 필요", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        otp = ""
        if settings.use_2fa:
            with wx.TextEntryDialog(
                self, "DSM 2단계 인증 OTP 6자리:", "OTP 코드 입력",
            ) as dlg:
                if dlg.ShowModal() != wx.ID_OK:
                    return
                otp = dlg.GetValue().strip()
        self.refresh_btn.Disable()
        self.diag_btn.Disable()
        speak("진단용 응답 원본을 저장합니다.")
        threading.Thread(
            target=self._diag_in_thread, args=(settings, otp), daemon=True,
        ).start()

    def _diag_in_thread(self, settings, otp: str) -> None:
        from config import DUMPS_DIR
        path = ""
        err = ""
        try:
            with DsmClient(settings.url, verify_ssl=settings.verify_ssl) as client:
                client.login(settings.account, settings.password, otp_code=otp)
                path = save_diagnostic_dump(client, DUMPS_DIR)
        except DsmAuthError as e:
            err = str(e)
        except Exception as e:
            err = f"예상치 못한 오류: {e}"
        wx.CallAfter(self._diag_done, path, err)

    def _diag_done(self, path: str, err: str) -> None:
        self.refresh_btn.Enable()
        self.diag_btn.Enable()
        if err:
            speak("진단 저장 실패.")
            wx.MessageBox(err, "진단 실패", wx.OK | wx.ICON_ERROR, self)
            return
        if not path:
            wx.MessageBox(
                "진단 파일을 저장하지 못했습니다 (쓰기 권한 또는 경로 문제).",
                "진단 실패", wx.OK | wx.ICON_ERROR, self,
            )
            return
        speak("진단 파일 저장 완료.")
        wx.MessageBox(
            f"진단 파일을 저장했습니다.\n\n경로:\n{path}\n\n"
            "이 파일을 개발자에게 보내 주시면 DSM 응답 구조에 맞게 파서를 보강할 수 있습니다.",
            "진단 저장 완료", wx.OK | wx.ICON_INFORMATION, self,
        )

    # ---------- 내보내기 ----------

    def _on_export(self, _event=None) -> None:
        with NasLogExportDialog(self) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            formats = dlg.selected_formats
        flt = self._current_filter()
        saved: list[Path] = []
        try:
            if FORMAT_TXT in formats:
                saved.append(write_nas_log_txt(default_txt_path(), self.store, self.members, flt=flt))
            if FORMAT_XLSX in formats:
                saved.append(write_nas_log_xlsx(default_xlsx_path(), self.store, self.members, flt=flt))
            if FORMAT_HTML in formats:
                saved.append(write_nas_log_html(default_html_path(), self.store, self.members, flt=flt))
        except Exception as e:
            wx.MessageBox(
                f"내보내기 중 오류: {e}",
                "내보내기 실패", wx.OK | wx.ICON_ERROR, self,
            )
            return
        if not saved:
            return
        listing = "\n".join(str(p) for p in saved)
        speak(f"{len(saved)}개 파일로 저장했습니다.")
        wx.MessageBox(
            f"저장 완료 — {len(saved)}개 파일\n\n{listing}",
            "내보내기 완료", wx.OK | wx.ICON_INFORMATION, self,
        )

    # ---------- 공통 ----------

    def _announce_open(self) -> None:
        total = self.store.count()
        ok, msg, at = self.store.last_status()
        when = at.replace("T", " ") if at else "(아직 없음)"
        flag = "" if ok else " — 인증 필요 또는 미수집"
        speak(
            f"자료실 접속 로그 화면. 전체 저장 {total}건, 마지막 수집 {when}{flag}. "
            "기간과 회원 ID 등으로 필터한 뒤 '필터 적용' 을 누르세요. "
            "'지금 새로고침' 으로 DSM 에서 최신 로그를 가져올 수 있습니다."
        )
        self._refresh_status_line()

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CLOSE)
            return
        event.Skip()


# ---------- 헬퍼 ----------

def _row_matches_user_query(row, q_lower: str) -> bool:
    """회원 검색어가 DSM 아이디·소리샘 user_id·이름·닉네임 중 어디에라도 부분 일치하면 True."""
    uid = (row.entry.dsm_user_id or "").lower()
    if q_lower in uid:
        return True
    m = row.member
    if m:
        for field in (m.user_id, m.name, m.nickname):
            if field and q_lower in str(field).lower():
                return True
    return False


def _wxdate(d: date) -> wx.DateTime:
    return wx.DateTime.FromDMY(d.day, d.month - 1, d.year)


def _pydate(d: wx.DateTime) -> date:
    return date(d.GetYear(), d.GetMonth() + 1, d.GetDay())


# wx.adv 는 namespace import 필요 — 모듈 끝에 명시.
import wx.adv  # noqa: E402  pylint: disable=wrong-import-position
