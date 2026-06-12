"""초록등대 회원관리 메인 프레임."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
from datetime import date
from pathlib import Path

import requests
import wx

from config import (
    ADMIN_MEMBER_URL,
    APP_NAME,
    APP_VERSION,
    BACKUPS_DIR,
    DATA_DIR,
    DUMPS_DIR,
    LOGS_DIR,
)
from core import app_options
from core.activity_counter import ActivityCounter
from core.backup_retention import DEFAULT_RETENTION_MONTHS, archive_old_backups
from core.backup_service import BackupService
from core.crawler import MemberCrawler
from core.dashboard_summary import build_dashboard_lines
from core.html_report import default_report_path, write_report
from core.keybindings import (
    KEYBINDINGS_FILE,
    build_accelerator_entries,
    load_user_bindings,
    write_template,
)
from core.level_adjustment import LevelAdjustmentService
from core.level_history import LevelHistoryStore
from core.log_writer import OperationLogWriter
from core.mail_sender import (
    MailSender,
    template_delete,
    template_demote,
    template_promote,
)
from core.member_admin import MemberAdminAdapter
from core.member_parser import EmptyParseError, MemberListParser
from core.nudge_history import NudgeHistoryStore
from core.site_diagnostics import diagnose_admin_member_html
from core.update_check import check_for_updates, download_release_asset
from core.models import AdjustmentPlan, AdjustmentReport, BackupResult
from core.mvp_service import MvpReport, MvpService, write_mvp_report
from core.pending_members import PendingSeenStore, find_pending
from core.promotion_service import PromotionPlan, PromotionReport, PromotionService
from core.schedule_tracker import ScheduleTracker
from core.undo_stack import UndoEntry, UndoItem, UndoStack
from core.withdrawn_blocklist import WithdrawnBlocklist
from screen_reader import cancel_speech, speak
from ui.backup_diff_dialog import BackupDiffDialog
from ui.confirm_dialog import ConfirmAdjustmentDialog
from ui.confirm_promotion_dialog import ConfirmPromotionDialog
from ui.level_history_dialog import LevelHistoryDialog
from ui.help_dialog import HelpDialog, show_about
from ui.item_text_ctrl import ItemTextCtrl
from ui.manual_dialog import ManualDialog
from ui.log_viewer_dialog import LogViewerDialog
from ui.mail_dialog import ManualMailDialog
from ui.mvp_dialog import MvpDialog
from ui.board_dialog import BoardAdminDialog
from ui.scheduled_notice_dialog import ScheduledNoticeDialog
from ui.expiry_reminder_dialog import ExpiryReminderDialog
from ui.nas_log_dialog import NasLogDialog
from ui.scheduler_dialog import SchedulerDialog
from ui.nudge_dialog import NudgeMailDialog
from ui.payment_dialog import PaymentDialog
from ui.pending_member_dialog import PendingMemberDialog
from ui.progress_dialog import ProgressTaskDialog
from ui.promotion_imminent_dialog import PromotionImminentDialog
from ui.search_dialog import MemberSearchDialog
from ui.stats_dialog import StatsDialog


# Inno Setup 무인(silent) 설치 플래그 — 자동 업데이트 흐름에서 사용 (v1.2.9).
# 각 플래그 의미:
#   /SP-              초반 "Are you sure you want to install?" 안내 화면 생략
#   /VERYSILENT       설치 마법사 UI 완전히 숨김 (진행률 창도 안 뜸)
#   /SUPPRESSMSGBOXES 확인 메시지박스 자동 '예' 처리
#   /NORESTART        Windows 재부팅 절대 안 함 (사용자 작업 보호)
#   /CLOSEAPPLICATIONS    실행 중인 앱이 파일 잠금 시 자동 종료
#   /RESTARTAPPLICATIONS  설치 끝나면 닫혔던 앱 자동 재시작
# installer.iss 의 [Run] 항목에서 skipifsilent 플래그를 빼 두어, /VERYSILENT
# 모드에서도 새 EXE 가 [Run] 으로 자동 실행되도록 했음.
SILENT_INSTALL_FLAGS: tuple[str, ...] = (
    "/SP-",
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/CLOSEAPPLICATIONS",
    "/RESTARTAPPLICATIONS",
)


# 메뉴 ID
ID_BACKUP_NOW = wx.NewIdRef()
ID_ADJUST_PREVIEW = wx.NewIdRef()
ID_ADJUST_APPLY = wx.NewIdRef()
ID_PROMOTE_NOW = wx.NewIdRef()
ID_SEARCH = wx.NewIdRef()
ID_MANUAL_MAIL = wx.NewIdRef()
ID_CHECK_DUE = wx.NewIdRef()
ID_LAST_INFO = wx.NewIdRef()
ID_OPEN_BACKUP = wx.NewIdRef()
ID_OPEN_LOG = wx.NewIdRef()
ID_LOGOUT = wx.NewIdRef()
ID_DUMP = wx.NewIdRef()
ID_HELP_KEYS = wx.NewIdRef()
ID_MANUAL = wx.NewIdRef()
ID_STATS = wx.NewIdRef()
ID_BACKUP_DIFF = wx.NewIdRef()
ID_LOG_VIEWER = wx.NewIdRef()
ID_UNDO_LAST = wx.NewIdRef()
ID_ARCHIVE_OLD_BACKUPS = wx.NewIdRef()
ID_PROMOTION_IMMINENT = wx.NewIdRef()
ID_HTML_REPORT = wx.NewIdRef()
ID_KEYBINDINGS_OPEN = wx.NewIdRef()
ID_MVP_NOW = wx.NewIdRef()
ID_PENDING_MEMBERS = wx.NewIdRef()
ID_PENDING_RESET_SEEN = wx.NewIdRef()
ID_LEVEL_HISTORY = wx.NewIdRef()
ID_SITE_DIAGNOSE = wx.NewIdRef()
ID_CHECK_UPDATE = wx.NewIdRef()
ID_PAYMENTS = wx.NewIdRef()
ID_TOGGLE_AUTO_ADJUST = wx.NewIdRef()
ID_BOARD_ADMIN = wx.NewIdRef()
ID_NAS_LOG = wx.NewIdRef()
# v1.2.10: 안내 메일(nudge) — green3 6개월 글 없음 / 1년+ 미접속 경고.
ID_NUDGE_ACTIVITY = wx.NewIdRef()
ID_NUDGE_INACTIVE_WARN = wx.NewIdRef()
# v1.2.11: 자료실 구독 만료 조기 알림 — 7일/3일 전.
ID_EXPIRY_REMIND_7 = wx.NewIdRef()
ID_EXPIRY_REMIND_3 = wx.NewIdRef()
# v1.3.1: 자동 스케줄러 관리 GUI.
ID_SCHEDULER_SETUP = wx.NewIdRef()
# v1.4: 예약 공지 목록 관리.
ID_SCHEDULED_NOTICES = wx.NewIdRef()


class MainFrame(wx.Frame):
    def __init__(
        self,
        session: requests.Session,
        admin_user_id: str,
    ):
        super().__init__(
            None,
            title=f"{APP_NAME} v{APP_VERSION}",
            size=(820, 560),
        )

        self.session = session
        self.admin_user_id = admin_user_id
        self.parser = MemberListParser()
        self.crawler = MemberCrawler(session, ADMIN_MEMBER_URL, parser=self.parser)
        self.tracker = ScheduleTracker()
        self.tracker.load()
        self.log_writer = OperationLogWriter()
        self.mail_sender = MailSender(session, admin_user_id)

        self._busy = False
        self._last_plan: AdjustmentPlan | None = None
        self._last_promo_plan: PromotionPlan | None = None
        self._cached_members: list | None = None
        self._last_adjust_report: AdjustmentReport | None = None
        self._last_promo_report: PromotionReport | None = None
        self.undo_stack = UndoStack()
        self.pending_seen = PendingSeenStore()
        self.level_history = LevelHistoryStore()
        # 장기미접속으로 '탈퇴' 처리된 회원 명단 — 재가입 시 승인 자동 차단
        self.inactivity_blocklist = WithdrawnBlocklist()
        # v1.2.10: 안내 메일(nudge) 발송 이력 — 30일 이내 중복 발송 방지용.
        self.nudge_history = NudgeHistoryStore(
            Path(DATA_DIR) / "nudge_history.json"
        )

        self._build_menu()
        self._build_ui()
        self._build_status_bar()

        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self.Bind(wx.EVT_CLOSE, self._on_close)

        self.Centre()

    # ---------- UI ----------

    def _build_menu(self) -> None:
        bar = wx.MenuBar()

        file_menu = wx.Menu()
        file_menu.Append(ID_LAST_INFO, "마지막 작업 정보(&I)\tCtrl+I")
        file_menu.AppendSeparator()
        file_menu.Append(ID_OPEN_BACKUP, "백업 폴더 열기(&O)\tCtrl+O")
        file_menu.Append(ID_OPEN_LOG, "로그 폴더 열기(&G)")
        file_menu.Append(ID_LOG_VIEWER, "작업 로그 뷰어(&V)...\tCtrl+Shift+L")
        file_menu.Append(ID_LEVEL_HISTORY, "등급 변경 이력(&Y)...\tCtrl+Shift+Y")
        file_menu.Append(ID_BACKUP_DIFF, "백업 비교(&D)...\tCtrl+Shift+D")
        file_menu.Append(ID_ARCHIVE_OLD_BACKUPS, "오래된 백업 정리(&A)...")
        file_menu.AppendSeparator()
        file_menu.Append(ID_LOGOUT, "로그아웃(&L)\tCtrl+L")
        file_menu.Append(wx.ID_EXIT, "프로그램 종료(&X)\tAlt+F4")
        bar.Append(file_menu, "파일(&F)")

        task_menu = wx.Menu()
        task_menu.Append(ID_SEARCH, "회원 검색(&F)\tCtrl+F")
        task_menu.Append(ID_STATS, "회원 통계(&S)\tCtrl+T")
        task_menu.Append(ID_PENDING_MEMBERS, "신규 가입자 승인(&E)...")
        task_menu.Append(ID_PROMOTION_IMMINENT, "승급 임박 회원 분석(&Q)...")
        task_menu.Append(ID_MVP_NOW, "MVP TOP 10 분석(&V)...")
        task_menu.AppendSeparator()
        task_menu.Append(ID_BACKUP_NOW, "우수회원 백업 실행(&B)\tCtrl+B")
        task_menu.Append(
            ID_PROMOTE_NOW,
            "게시물 기반 자동 승급(&U)\tCtrl+U",
        )
        task_menu.Append(ID_HTML_REPORT, "HTML 리포트 만들기(&H)...")
        task_menu.AppendSeparator()
        task_menu.Append(ID_ADJUST_PREVIEW, "장기미접속 조정 — 미리보기(&P)\tCtrl+R")
        task_menu.Append(
            ID_ADJUST_APPLY,
            "장기미접속 조정 — 실제 적용(&A)\tCtrl+Shift+R",
        )
        # 체크 항목 — 시작 시 도래해 있으면 자동 실행할지 사용자가 선택.
        self.auto_adjust_item = task_menu.Append(
            ID_TOGGLE_AUTO_ADJUST,
            "장기미접속 조정 자동 실행(&O)",
            "체크 시 앱 시작 직후 도래한 경우 자동으로 미리보기를 실행합니다",
            kind=wx.ITEM_CHECK,
        )
        self.auto_adjust_item.Check(bool(app_options.get("auto_run_adjustment")))
        task_menu.AppendSeparator()
        task_menu.Append(
            ID_MANUAL_MAIL,
            "수동 메일 발송 (rtgreen 전용)(&M)\tCtrl+M",
        )
        task_menu.Append(
            ID_NUDGE_ACTIVITY,
            "활동 안내 메일 (green3 6개월 글 없음)(&V)...",
        )
        task_menu.Append(
            ID_NUDGE_INACTIVE_WARN,
            "장기미접속 사전 경고 메일 (1년+ 미접속)(&E)...",
        )
        task_menu.Append(
            ID_EXPIRY_REMIND_7,
            "자료실 구독 만료 알림 — 7일 전(&7)...",
        )
        task_menu.Append(
            ID_EXPIRY_REMIND_3,
            "자료실 구독 만료 알림 — 3일 전(&3)...",
        )
        task_menu.Append(
            ID_SCHEDULER_SETUP,
            "자동 스케줄러 관리(&Y)...",
        )
        task_menu.Append(ID_PAYMENTS, "자료실 구독비 관리(&P)...\tCtrl+P")
        task_menu.Append(ID_NAS_LOG, "자료실 접속 로그(&L)...")
        task_menu.Append(ID_BOARD_ADMIN, "게시판 관리 / 공지 작성(&W)...")
        task_menu.Append(ID_SCHEDULED_NOTICES, "예약 공지 목록(&Q)...")
        task_menu.AppendSeparator()
        task_menu.Append(ID_UNDO_LAST, "마지막 작업 되돌리기(&Z)\tCtrl+Z")
        task_menu.AppendSeparator()
        task_menu.Append(ID_CHECK_DUE, "지금 작업 가능 여부 확인(&C)\tCtrl+D")
        bar.Append(task_menu, "작업(&T)")

        dev_menu = wx.Menu()
        dev_menu.Append(ID_DUMP, "관리자 페이지 HTML 덤프(&H)")
        dev_menu.Append(ID_SITE_DIAGNOSE, "사이트 구조 진단(&D)")
        dev_menu.Append(ID_PENDING_RESET_SEEN, "신규 가입자 '본 적 있음' 기록 초기화")
        bar.Append(dev_menu, "개발자(&D)")

        help_menu = wx.Menu()
        help_menu.Append(ID_MANUAL, "사용 설명서(&M)\tShift+F1")
        help_menu.Append(ID_HELP_KEYS, "단축키 안내(&K)\tCtrl+K")
        help_menu.Append(wx.ID_ABOUT, "프로그램 정보(&A)\tF1")
        help_menu.AppendSeparator()
        help_menu.Append(ID_KEYBINDINGS_OPEN, "단축키 설정 파일(&B)...")
        help_menu.Append(ID_CHECK_UPDATE, "업데이트 확인(&U)")
        bar.Append(help_menu, "도움말(&H)")

        self.SetMenuBar(bar)

        self.Bind(wx.EVT_MENU, self.on_backup_now, id=ID_BACKUP_NOW)
        self.Bind(wx.EVT_MENU, self.on_promote_now, id=ID_PROMOTE_NOW)
        self.Bind(wx.EVT_MENU, self.on_search, id=ID_SEARCH)
        self.Bind(wx.EVT_MENU, self.on_manual_mail, id=ID_MANUAL_MAIL)
        self.Bind(wx.EVT_MENU, self.on_adjust_preview, id=ID_ADJUST_PREVIEW)
        self.Bind(wx.EVT_MENU, self.on_adjust_apply, id=ID_ADJUST_APPLY)
        self.Bind(wx.EVT_MENU, self.on_check_due, id=ID_CHECK_DUE)
        self.Bind(wx.EVT_MENU, self.on_last_info, id=ID_LAST_INFO)
        self.Bind(wx.EVT_MENU, self.on_open_backup, id=ID_OPEN_BACKUP)
        self.Bind(wx.EVT_MENU, self.on_open_log, id=ID_OPEN_LOG)
        self.Bind(wx.EVT_MENU, self.on_logout, id=ID_LOGOUT)
        self.Bind(wx.EVT_MENU, self.on_dump, id=ID_DUMP)
        self.Bind(wx.EVT_MENU, self.on_about, id=wx.ID_ABOUT)
        self.Bind(wx.EVT_MENU, self.on_help_keys, id=ID_HELP_KEYS)
        self.Bind(wx.EVT_MENU, self.on_manual, id=ID_MANUAL)
        self.Bind(wx.EVT_MENU, self.on_stats, id=ID_STATS)
        self.Bind(wx.EVT_MENU, self.on_backup_diff, id=ID_BACKUP_DIFF)
        self.Bind(wx.EVT_MENU, self.on_log_viewer, id=ID_LOG_VIEWER)
        self.Bind(wx.EVT_MENU, self.on_undo_last, id=ID_UNDO_LAST)
        self.Bind(wx.EVT_MENU, self.on_archive_old_backups, id=ID_ARCHIVE_OLD_BACKUPS)
        self.Bind(wx.EVT_MENU, self.on_promotion_imminent, id=ID_PROMOTION_IMMINENT)
        self.Bind(wx.EVT_MENU, self.on_html_report, id=ID_HTML_REPORT)
        self.Bind(wx.EVT_MENU, self.on_keybindings_open, id=ID_KEYBINDINGS_OPEN)
        self.Bind(wx.EVT_MENU, self.on_mvp_now, id=ID_MVP_NOW)
        self.Bind(wx.EVT_MENU, self.on_pending_members, id=ID_PENDING_MEMBERS)
        self.Bind(wx.EVT_MENU, self.on_pending_reset_seen, id=ID_PENDING_RESET_SEEN)
        self.Bind(wx.EVT_MENU, self.on_level_history, id=ID_LEVEL_HISTORY)
        self.Bind(wx.EVT_MENU, self.on_site_diagnose, id=ID_SITE_DIAGNOSE)
        self.Bind(wx.EVT_MENU, self.on_check_update, id=ID_CHECK_UPDATE)
        self.Bind(wx.EVT_MENU, self.on_payments, id=ID_PAYMENTS)
        self.Bind(wx.EVT_MENU, self.on_nas_log, id=ID_NAS_LOG)
        self.Bind(wx.EVT_MENU, self.on_board_admin, id=ID_BOARD_ADMIN)
        self.Bind(wx.EVT_MENU, self.on_scheduled_notices, id=ID_SCHEDULED_NOTICES)
        self.Bind(wx.EVT_MENU, self.on_nudge_activity, id=ID_NUDGE_ACTIVITY)
        self.Bind(wx.EVT_MENU, self.on_nudge_inactive_warn, id=ID_NUDGE_INACTIVE_WARN)
        self.Bind(wx.EVT_MENU, self.on_expiry_remind_7, id=ID_EXPIRY_REMIND_7)
        self.Bind(wx.EVT_MENU, self.on_expiry_remind_3, id=ID_EXPIRY_REMIND_3)
        self.Bind(wx.EVT_MENU, self.on_scheduler_setup, id=ID_SCHEDULER_SETUP)
        self.Bind(wx.EVT_MENU, self.on_toggle_auto_adjust, id=ID_TOGGLE_AUTO_ADJUST)
        self.Bind(wx.EVT_MENU, self._on_close, id=wx.ID_EXIT)

        # 사용자 정의 단축키 (data/keybindings.json) 적용
        self._apply_user_keybindings()

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        title = wx.StaticText(
            panel,
            label=f"{APP_NAME}  —  {self.admin_user_id} 님 (동호회관리자)",
        )
        font = title.GetFont()
        font.SetPointSize(font.GetPointSize() + 2)
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        title.SetFont(font)
        sizer.Add(title, 0, wx.ALL, 10)

        # v1.2.11: 운영자 대시보드 — 오늘 처리할 일이 한눈에 보이도록.
        # 빈 줄로 시작했다가 _refresh_dashboard() 가 채움.
        dashboard_label = wx.StaticText(panel, label="오늘 해야 할 일(&O):")
        sizer.Add(dashboard_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        self.dashboard_text = ItemTextCtrl(
            panel,
            value="",
            style=wx.TE_READONLY | wx.TE_MULTILINE,
            name="오늘 해야 할 일",
        )
        sizer.Add(self.dashboard_text, 0, wx.EXPAND | wx.ALL, 10)

        info_label = wx.StaticText(panel, label="현재 상태(&S):")
        sizer.Add(info_label, 0, wx.LEFT | wx.RIGHT, 10)

        self.status_text = ItemTextCtrl(
            panel,
            value="",
            style=wx.TE_READONLY | wx.TE_MULTILINE,
            name="현재 상태",
        )
        sizer.Add(self.status_text, 0, wx.EXPAND | wx.ALL, 10)

        log_label = wx.StaticText(panel, label="작업 결과(&R):")
        sizer.Add(log_label, 0, wx.LEFT | wx.RIGHT, 10)

        self.result_text = ItemTextCtrl(
            panel,
            value="",
            style=wx.TE_READONLY | wx.TE_MULTILINE,
            name="작업 결과",
        )
        sizer.Add(self.result_text, 1, wx.EXPAND | wx.ALL, 10)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        backup_btn = wx.Button(panel, ID_BACKUP_NOW, "우수회원 백업(&B)")
        adjust_btn = wx.Button(panel, ID_ADJUST_PREVIEW, "장기미접속 조정(&P)")
        info_btn = wx.Button(panel, ID_LAST_INFO, "마지막 작업 정보(&I)")
        btn_sizer.Add(backup_btn, 0, wx.ALL, 5)
        btn_sizer.Add(adjust_btn, 0, wx.ALL, 5)
        btn_sizer.Add(info_btn, 0, wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 10)

        # v1.2.11: wx.Button 은 EVT_BUTTON 이벤트를 발생시키므로 _build_menu 의
        # EVT_MENU 바인딩만으로는 클릭이 안 잡힘 (메뉴/단축키만 동작했음).
        # 같은 핸들러를 EVT_BUTTON 으로도 바인딩 — 메뉴와 버튼 모두 작동.
        self.Bind(wx.EVT_BUTTON, self.on_backup_now, id=ID_BACKUP_NOW)
        self.Bind(wx.EVT_BUTTON, self.on_adjust_preview, id=ID_ADJUST_PREVIEW)
        self.Bind(wx.EVT_BUTTON, self.on_last_info, id=ID_LAST_INFO)

        panel.SetSizer(sizer)
        self._refresh_status()

    def _build_status_bar(self) -> None:
        self.CreateStatusBar(2)
        self.SetStatusWidths([-3, -1])
        self.SetStatusText("준비됨", 0)
        self.SetStatusText("", 1)

    def _refresh_status(self) -> None:
        last_b = self.tracker.last_backup_date()
        last_a = self.tracker.last_adjustment_date()
        last_m = self.tracker.last_mvp_date()
        next_b = self.tracker.next_backup_date()
        next_a = self.tracker.next_adjustment_date()
        next_m = self.tracker.next_mvp_date()
        b_due = (
            "지금 가능"
            if self.tracker.is_backup_due()
            else f"{next_b.isoformat()} ({self.tracker.days_until_backup()}일 후)"
        )
        a_due = (
            "지금 가능"
            if self.tracker.is_adjustment_due()
            else f"{next_a.isoformat()} ({self.tracker.days_until_adjustment()}일 후)"
        )
        m_due = (
            "지금 가능"
            if self.tracker.is_mvp_due()
            else f"{next_m.isoformat()} ({self.tracker.days_until_mvp()}일 후)"
        )
        text = (
            f"마지막 백업: {last_b or '기록 없음'}   다음 백업: {b_due}\n"
            f"마지막 조정: {last_a or '기록 없음'}   다음 조정: {a_due}\n"
            f"마지막 MVP:  {last_m or '기록 없음'}   다음 MVP:  {m_due}"
        )
        self.status_text.SetValue(text)
        self._refresh_dashboard()

    def _set_cached_members(self, members) -> None:
        """회원 목록 캐시 갱신 + 대시보드 즉시 갱신 (v1.3.3).

        대시보드의 '신규 가입 대기' / '장기미접속 후보' 가 캐시에 의존하므로,
        캐시가 바뀌면 대시보드도 같이 갱신해야 '?' 가 남지 않는다. 워커 스레드
        에서 호출될 수 있어 wx.CallAfter 로 메인 스레드에서 갱신.
        """
        self._cached_members = members
        wx.CallAfter(self._refresh_dashboard)

    def _refresh_dashboard(self) -> None:
        """대시보드 '오늘 해야 할 일' 위젯을 갱신 (v1.2.11).

        회원 캐시·가입 대기 카운트는 가능한 만큼만 쓰고, 없으면 'N/A' 로 표시.
        주기적 갱신은 안 함 — _refresh_status 가 호출될 때마다 같이 갱신.
        """
        widget = getattr(self, "dashboard_text", None)
        if widget is None:
            return
        members = self._cached_members
        pending_count: int | None = None
        if members is not None:
            try:
                from core.pending_members import find_pending
                pending = find_pending(
                    members, seen_store=self.pending_seen,
                    blocklist=self.inactivity_blocklist,
                )
                pending_count = len(pending)
            except Exception:
                pending_count = None
        lines = build_dashboard_lines(
            tracker=self.tracker,
            members=members,
            pending_count=pending_count,
            admin_user_id=self.admin_user_id,
        )
        widget.SetValue("\n".join(lines))

    # ---------- 키보드 ----------

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        code = event.GetKeyCode()
        if code == wx.WXK_ESCAPE:
            cancel_speech()
            return
        event.Skip()

    def _on_close(self, event) -> None:
        try:
            cancel_speech()
        except Exception:
            pass
        self.Destroy()

    # ---------- 작업 핸들러 ----------

    def on_backup_now(self, event=None) -> None:
        if self._busy:
            speak("다른 작업이 진행 중입니다.")
            return
        self._run_in_thread(self._do_backup_with_promotion, label="우수회원 백업+승급")

    def _do_backup_with_promotion(self) -> None:
        """백업 → 우수회원 자동 승급 (게시물 50건 이상) 일괄 처리."""
        speak("우수회원 백업과 자동 승급을 시작합니다.")
        wx.CallAfter(self.SetStatusText, "회원 목록 수집 중...", 0)
        try:
            members = self.crawler.fetch_all_members(progress_cb=self._page_progress_cb)
        except EmptyParseError as e:
            wx.CallAfter(self._report_error, str(e))
            return
        except Exception as e:
            wx.CallAfter(self._report_error, f"수집 실패: {e}")
            return

        # 1) 백업 (수집된 members 기반으로 직접 처리하기 위해 BackupService 호출은 그대로 사용)
        try:
            backup_result = self._run_backup_with_members(members)
        except Exception as e:
            wx.CallAfter(self._report_error, f"백업 실패: {e}")
            return
        self.tracker.mark_backup_done(count=backup_result.member_count)
        self.log_writer.write_event(
            f"backup count={backup_result.member_count} txt={backup_result.txt_path.name}"
        )

        # 2) 자동 승급 (게시물 50건 이상 → 우수회원)
        try:
            promo_report = self._run_promotion_with_members(members)
        except Exception as e:
            wx.CallAfter(self._report_error, f"승급 실패: {e}")
            return

        wx.CallAfter(self._after_backup_and_promotion, backup_result, promo_report)

    def _run_backup_with_members(self, members):
        """BackupService 의 run() 을 이미 수집한 members 로 처리."""
        from datetime import date as _date
        from pathlib import Path
        from config import BACKUPS_DIR

        service = BackupService(self.crawler)
        outstanding = [m for m in members if m.level in service.OUTSTANDING_LEVELS]
        outstanding.sort(
            key=lambda m: (
                -m.level,
                -(m.last_login_date.toordinal() if m.last_login_date else 0),
            )
        )
        today = _date.today()
        day_dir = Path(BACKUPS_DIR) / today.isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)
        txt_path = day_dir / f"outstanding_members_{today.isoformat()}.txt"
        xlsx_path = day_dir / f"outstanding_members_{today.isoformat()}.xlsx"
        service._write_txt(outstanding, txt_path)
        service._write_xlsx(outstanding, xlsx_path)

        breakdown: dict[int, int] = {}
        for m in outstanding:
            breakdown[m.level] = breakdown.get(m.level, 0) + 1

        return BackupResult(
            txt_path=txt_path,
            xlsx_path=xlsx_path,
            member_count=len(outstanding),
            level_breakdown=breakdown,
        )

    def _run_promotion_with_members(self, members) -> PromotionReport:
        wx.CallAfter(self.SetStatusText, "자동 승급 분석 중...", 0)
        admin = MemberAdminAdapter(self.session, dry_run=False)
        service = PromotionService(
            self.crawler,
            admin,
            admin_user_id=self.admin_user_id,
            log_writer=self.log_writer,
        )
        plan = service.build_plan(members=members, progress_cb=self._page_progress_cb)
        if not plan.items:
            return PromotionReport(dry_run=False)
        return service.apply_plan(plan, progress_cb=self._item_progress_cb)

    def _send_promotion_mails(self, promo: PromotionReport) -> list[str]:
        if not self.mail_sender.enabled:
            return [f"메일 발송 건너뜀 (로그인 아이디 {self.admin_user_id!r}, rtgreen 전용 기능)"]
        if not promo.succeeded:
            return []
        from config import LEVEL_LABELS
        lines: list[str] = []
        # 목표 등급별로 그룹화해서 메일 한 번씩
        by_target: dict[int, list] = {}
        for item in promo.succeeded:
            by_target.setdefault(item.to_level, []).append(item)
        for to_level, group in sorted(by_target.items()):
            from_label = LEVEL_LABELS.get(group[0].from_level, f"레벨 {group[0].from_level}")
            to_label = LEVEL_LABELS.get(to_level, f"레벨 {to_level}")
            # 샘플 사용자로 템플릿 생성 (내용 동일)
            sample_member = group[0].member
            avg_count = sum(it.post_count for it in group) // max(1, len(group))
            subject, body = template_promote(sample_member, from_label, to_label, avg_count)
            recipients = [it.member.user_id for it in group]
            results = self.mail_sender.send(recipients, subject, body)
            lines.append(f"승급 축하 메일 ({to_label}): {len(recipients)}명")
            for r in results:
                lines.append(f"  {'OK' if r.success else 'FAIL'}  {r.message}")
        return lines

    def _after_backup_and_promotion(
        self, backup: BackupResult, promo: PromotionReport
    ) -> None:
        self._last_promo_report = promo
        breakdown = ", ".join(
            f"레벨{lv} {cnt}명" for lv, cnt in sorted(backup.level_breakdown.items(), reverse=True)
        )
        lines = [
            f"우수회원 {backup.member_count}명 백업 완료. ({breakdown})",
            f"엑셀: {backup.xlsx_path.name}",
            f"저장 폴더: {backup.txt_path.parent}",
            "",
            promo.speak_summary,
        ]
        if promo.failed:
            lines.append("승급 실패 내역:")
            for m, msg in promo.failed[:10]:
                lines.append(f"  - {m.user_id}: {msg}")
        mail_lines = self._send_promotion_mails(promo)
        if mail_lines:
            lines.append("")
            lines.extend(mail_lines)
        self.result_text.SetValue("\n".join(lines))
        self._refresh_status()
        self.SetStatusText("백업·승급 완료", 0)
        speak(
            f"우수회원 {backup.member_count}명 백업 완료. "
            f"{len(promo.succeeded)}명을 우수회원으로 승급했습니다."
        )

    # 단순 수동 백업 (승급 없이) - 내부적으로는 동일 경로 사용
    def _do_backup(self) -> None:
        self._do_backup_with_promotion()

    # ---------- 수동 메일 발송 (rtgreen 전용) ----------

    def on_manual_mail(self, event=None) -> None:
        if not self.mail_sender.enabled:
            speak("rtgreen 아이디로 로그인한 경우에만 사용할 수 있습니다.")
            wx.MessageBox(
                f"수동 메일 발송은 '{self.mail_sender.SENDER_USER_ID}' 아이디로 로그인한 경우에만 "
                f"사용할 수 있습니다.\n현재 로그인 아이디: {self.admin_user_id}\n\n"
                f"'Ctrl+L' 로 로그아웃한 뒤 rtgreen 아이디로 다시 로그인해 주세요.",
                "rtgreen 전용 기능",
                wx.OK | wx.ICON_WARNING,
            )
            return
        dlg = ManualMailDialog(
            self,
            mail_sender=self.mail_sender,
            last_adjust_report=self._last_adjust_report,
            last_promo_report=self._last_promo_report,
            members=self._cached_members or [],
        )
        dlg.ShowModal()
        dlg.Destroy()

    # ---------- 안내 메일 (v1.2.10) ----------

    def _open_nudge_dialog(self, kind: str) -> None:
        """공통 진입점 — green3 활동 안내 + 장기미접속 경고 둘 다 같은 다이얼로그.

        회원 목록 캐시가 없으면 먼저 가져온 뒤 진행. ActivityCounter 는
        활동 안내 메일에서만 필요하지만 두 경우 모두 같이 넘김 — 검색 다이얼로그
        와 마찬가지 패턴.
        """
        if not self.mail_sender.enabled:
            speak("rtgreen 아이디로 로그인한 경우에만 사용할 수 있습니다.")
            wx.MessageBox(
                f"안내 메일 발송은 '{self.mail_sender.SENDER_USER_ID}' 아이디로 "
                "로그인한 경우에만 사용할 수 있습니다.\n"
                f"현재 로그인 아이디: {self.admin_user_id}",
                "rtgreen 전용 기능", wx.OK | wx.ICON_WARNING,
            )
            return
        members = self._cached_members
        if not members:
            wx.MessageBox(
                "회원 목록이 비어 있습니다. 먼저 'Ctrl+F' 회원 검색을 한 번 "
                "열어 회원 목록을 불러온 뒤 다시 시도해 주세요.",
                "회원 목록 필요", wx.OK | wx.ICON_INFORMATION,
            )
            return
        dlg = NudgeMailDialog(
            self,
            kind=kind,
            members=members,
            mail_sender=self.mail_sender,
            history=self.nudge_history,
            admin_user_id=self.admin_user_id,
            activity_counter=ActivityCounter(self.session),
            log_writer=self.log_writer,
        )
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def on_nudge_activity(self, event=None) -> None:
        from core.nudge_history import KIND_ACTIVITY_NUDGE
        self._open_nudge_dialog(KIND_ACTIVITY_NUDGE)

    def on_nudge_inactive_warn(self, event=None) -> None:
        from core.nudge_history import KIND_INACTIVE_WARNING
        self._open_nudge_dialog(KIND_INACTIVE_WARNING)

    # ---------- 자료실 구독 만료 알림 (v1.2.11) ----------

    def _open_expiry_reminder(self, days_before: int) -> None:
        """7일 / 3일 전 만료 알림 메일 발송 공통 진입점."""
        if not self.mail_sender.enabled:
            speak("rtgreen 아이디로 로그인한 경우에만 사용할 수 있습니다.")
            wx.MessageBox(
                f"이 기능은 '{self.mail_sender.SENDER_USER_ID}' 아이디로 "
                "로그인한 경우에만 사용할 수 있습니다.",
                "rtgreen 전용 기능", wx.OK | wx.ICON_WARNING,
            )
            return
        members = self._cached_members
        if not members:
            wx.MessageBox(
                "회원 목록이 비어 있습니다. 먼저 'Ctrl+F' 회원 검색을 한 번 "
                "열어 회원 목록을 불러온 뒤 다시 시도해 주세요.",
                "회원 목록 필요", wx.OK | wx.ICON_INFORMATION,
            )
            return
        # PaymentStore 는 lazy import — payment_dialog 가 처음 열릴 때 init 되는
        # 인프라와 동일 DB 파일을 공유하므로 여기서 새 인스턴스를 만들어도 안전.
        from core.payment_store import PaymentStore
        store = PaymentStore()
        dlg = ExpiryReminderDialog(
            self,
            days_before=days_before,
            members=members,
            payment_store=store,
            mail_sender=self.mail_sender,
            history=self.nudge_history,
            admin_user_id=self.admin_user_id,
            log_writer=self.log_writer,
        )
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def on_expiry_remind_7(self, event=None) -> None:
        self._open_expiry_reminder(7)

    def on_expiry_remind_3(self, event=None) -> None:
        self._open_expiry_reminder(3)

    # ---------- 자동 스케줄러 관리 (v1.3.1) ----------

    def on_scheduler_setup(self, event=None) -> None:
        """Windows 작업 스케줄러 등록/해제 GUI 를 연다."""
        dlg = SchedulerDialog(self)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    # ---------- 회원 검색 ----------

    def on_search(self, event=None) -> None:
        if self._busy:
            speak("다른 작업이 진행 중입니다.")
            return
        if self._cached_members:
            self._show_search_dialog(self._cached_members)
            return
        self._run_in_thread(self._do_search_fetch, label="회원 검색용 수집")

    def _do_search_fetch(self) -> None:
        speak("검색을 위해 회원 목록을 불러옵니다.")
        wx.CallAfter(self.SetStatusText, "회원 목록 수집 중...", 0)
        try:
            members = self.crawler.fetch_all_members(progress_cb=self._page_progress_cb)
        except EmptyParseError as e:
            wx.CallAfter(self._report_error, str(e))
            return
        except Exception as e:
            wx.CallAfter(self._report_error, f"수집 실패: {e}")
            return
        self._set_cached_members(members)
        wx.CallAfter(self._show_search_dialog, members)

    def _show_search_dialog(self, members) -> None:
        self.SetStatusText(f"회원 {len(members)}명 수집 완료", 0)
        dlg = MemberSearchDialog(
            self,
            members,
            session=self.session,
            admin_user_id=self.admin_user_id,
            log_writer=self.log_writer,
            undo_stack=self.undo_stack,
            level_history=self.level_history,
            blocklist=self.inactivity_blocklist,
        )
        dlg.ShowModal()
        if dlg.changed_count > 0:
            self.SetStatusText(
                f"등급 {dlg.changed_count}건 수동 변경됨", 0
            )
            self.result_text.SetValue(
                f"회원 검색에서 {dlg.changed_count}건의 등급이 수동 변경되었습니다."
            )
        dlg.Destroy()
        # 검색 다이얼로그에서 등급 변경·탈퇴 처리 등으로 회원 상태가 바뀔 수 있으니
        # 대시보드(가입 대기 / 장기미접속 후보) 도 같이 갱신.
        self._refresh_dashboard()

    def on_promote_now(self, event=None) -> None:
        """게시물 기반 자동 승급 — 미리보기 단계로 진입.

        v0.4 부터는 장기미접속 조정과 동일하게 2단계 워크플로:
        1) 회원 수집 + 게시물 수 카운트 + dry-run plan 생성
        2) 미리보기 다이얼로그 → 사용자 승인 → 실제 적용
        """
        if self._busy:
            speak("다른 작업이 진행 중입니다.")
            return
        self._run_in_thread(self._do_promotion_preview, label="자동 승급 미리보기")

    def _do_promotion_preview(self) -> None:
        speak("회원 목록을 수집한 뒤 '우리들의 이야기' 게시물 수를 분석합니다.")
        wx.CallAfter(self.SetStatusText, "회원 수집 중...", 0)
        try:
            members = self.crawler.fetch_all_members(progress_cb=self._page_progress_cb)
        except EmptyParseError as e:
            wx.CallAfter(self._report_error, str(e))
            return
        except Exception as e:
            wx.CallAfter(self._report_error, f"수집 실패: {e}")
            return
        self._set_cached_members(members)

        # dry-run plan 생성 (사이트에는 아직 아무 변경 없음)
        try:
            admin = MemberAdminAdapter(self.session, dry_run=True)
            service = PromotionService(
                self.crawler,
                admin,
                admin_user_id=self.admin_user_id,
                log_writer=self.log_writer,
            )
            plan = service.build_plan(
                members=members,
                progress_cb=self._page_progress_cb,
            )
        except Exception as e:
            wx.CallAfter(self._report_error, f"승급 분석 실패: {e}")
            return

        self._last_promo_plan = plan
        wx.CallAfter(self._show_promotion_preview, plan)

    def _show_promotion_preview(self, plan: PromotionPlan) -> None:
        self.SetStatusText("자동 승급 미리보기 준비됨", 0)
        if not plan.items:
            speak("승급 대상이 없습니다.")
            wx.MessageBox(
                f"전체 {plan.total_scanned}명 중 승급 대상이 0건입니다.",
                "자동 승급 미리보기",
                wx.OK | wx.ICON_INFORMATION,
            )
            return

        dlg = ConfirmPromotionDialog(self, plan)
        result = dlg.ShowModal()
        dlg.Destroy()
        if result != wx.ID_OK:
            speak("자동 승급을 취소했습니다.")
            return

        # 최종 확인 한 번 더
        confirm = wx.MessageBox(
            f"정말로 {len(plan.items)}명을 승급하시겠습니까?\n\n"
            f"이 작업은 사이트에 즉시 반영됩니다.\n"
            f"적용 후 Ctrl+Z 로 되돌릴 수 있습니다.",
            "최종 확인",
            wx.YES_NO | wx.ICON_WARNING | wx.NO_DEFAULT,
        )
        if confirm != wx.YES:
            speak("적용을 취소했습니다.")
            return

        self._run_in_thread(
            lambda: self._do_apply_promotion_plan(plan),
            label="자동 승급 적용",
        )

    def _do_apply_promotion_plan(self, plan: PromotionPlan) -> None:
        speak("자동 승급을 실제 적용합니다.")
        wx.CallAfter(self.SetStatusText, "자동 승급 적용 중...", 0)
        admin = MemberAdminAdapter(self.session, dry_run=False)
        service = PromotionService(
            self.crawler,
            admin,
            admin_user_id=self.admin_user_id,
            log_writer=self.log_writer,
        )
        try:
            report = service.apply_plan(plan, progress_cb=self._item_progress_cb)
        except Exception as e:
            wx.CallAfter(self._report_error, f"적용 실패: {e}")
            return

        # Undo 스택에 push
        if report.succeeded:
            self.undo_stack.push(
                label=f"자동 승급 ({len(report.succeeded)}명)",
                items=[
                    UndoItem(
                        user_id=it.member.user_id,
                        nickname=it.member.nickname,
                        from_level=it.from_level,
                        to_level=it.to_level,
                    )
                    for it in report.succeeded
                ],
            )
            # v1.0: 영구 등급 이력 기록
            try:
                self.level_history.record_batch(
                    [
                        {
                            "user_id": it.member.user_id,
                            "nickname": it.member.nickname,
                            "from_level": it.from_level,
                            "to_level": it.to_level,
                            "reason": (
                                f"활동점수 {it.score:.1f} "
                                f"(글 {it.post_count}/댓글 {it.comment_count})"
                            ),
                        }
                        for it in report.succeeded
                    ],
                    source="auto_promote",
                    actor=self.admin_user_id,
                )
            except Exception:
                pass

        wx.CallAfter(self._after_promotion_only, report)

    def _after_promotion_only(self, report: PromotionReport) -> None:
        self._last_promo_report = report
        lines = [report.speak_summary]
        if report.succeeded:
            lines.append("승급 회원:")
            for it in report.succeeded[:30]:
                lines.append(f"  - {it.member.user_id} {it.member.nickname} / 게시물 {it.post_count}건 → 레벨 {it.to_level}")
        if report.failed:
            lines.append("실패 내역:")
            for m, msg in report.failed[:10]:
                lines.append(f"  - {m.user_id}: {msg}")
        # 메일 발송
        mail_lines = self._send_promotion_mails(report)
        if mail_lines:
            lines.append("")
            lines.extend(mail_lines)
        self.result_text.SetValue("\n".join(lines))
        self.SetStatusText("승급 완료", 0)
        speak(report.speak_summary)

    def on_adjust_preview(self, event=None) -> None:
        if self._busy:
            speak("다른 작업이 진행 중입니다.")
            return
        self._do_build_plan()

    def _do_build_plan(self) -> None:
        """진행 다이얼로그 + 비프 안내가 포함된 장기미접속 미리보기 생성."""
        if self._busy:
            return
        self._busy = True
        speak("회원 목록을 불러와 조정 대상을 분석합니다.")
        self.SetStatusText("조정 미리보기 생성 중...", 0)

        admin = MemberAdminAdapter(self.session, dry_run=True)
        service = LevelAdjustmentService(
            self.crawler,
            admin,
            admin_user_id=self.admin_user_id,
            log_writer=self.log_writer,
            blocklist=self.inactivity_blocklist,
            activity_counter=ActivityCounter(self.session),
        )

        def worker(progress_cb):
            return service.build_plan(
                progress_cb=lambda c, t: progress_cb(
                    c, t, f"회원 페이지 수집 {c}/{t}",
                ),
                activity_progress_cb=lambda c, t: progress_cb(
                    c, t, f"green3 활동 점검 {c}/{t}",
                ),
            )

        dlg = ProgressTaskDialog(
            self, title="장기미접속 미리보기",
            task=worker,
            message="회원 목록 수집 중...",
        )
        try:
            dlg.run_modal()
        finally:
            dlg.Destroy()
            self._busy = False
            self.SetStatusText("준비됨", 0)

        if dlg.error is not None:
            if isinstance(dlg.error, EmptyParseError):
                self._report_error(str(dlg.error))
            else:
                self._report_error(f"미리보기 실패: {dlg.error}")
            return

        plan = dlg.result
        self._last_plan = plan
        self._show_plan_dialog(plan)

    def _show_plan_dialog(self, plan: AdjustmentPlan) -> None:
        self.SetStatusText("조정 미리보기 준비됨", 0)
        actionable_n = len(plan.actionable)
        if actionable_n == 0:
            speak("조정할 회원이 없습니다.")
            wx.MessageBox(
                f"전체 {plan.total_scanned}명 중 조정 대상이 0건입니다.",
                "조정 미리보기",
                wx.OK | wx.ICON_INFORMATION,
            )
            return

        dlg = ConfirmAdjustmentDialog(self, plan)
        result = dlg.ShowModal()
        dlg.Destroy()

        if result == wx.ID_OK:
            self._confirm_and_apply(plan)
        else:
            speak("조정을 취소했습니다.")

    def _confirm_and_apply(self, plan: AdjustmentPlan) -> None:
        actionable_n = len(plan.actionable)
        confirm = wx.MessageBox(
            f"정말로 {actionable_n}명을 처리하시겠습니까?\n"
            f"(등급 강등 {plan.demote_count}명, 탈퇴 {plan.delete_count}명)\n\n"
            f"이 작업은 되돌릴 수 없습니다.",
            "최종 확인",
            wx.YES_NO | wx.ICON_WARNING | wx.NO_DEFAULT,
        )
        if confirm != wx.YES:
            speak("적용을 취소했습니다.")
            return

        self._run_in_thread(
            lambda: self._do_apply_plan(plan),
            label="조정 적용",
        )

    def _do_apply_plan(self, plan: AdjustmentPlan) -> None:
        speak("조정을 실제 적용합니다.")
        wx.CallAfter(self.SetStatusText, "조정 적용 중...", 0)
        admin = MemberAdminAdapter(self.session, dry_run=False)
        service = LevelAdjustmentService(
            self.crawler,
            admin,
            admin_user_id=self.admin_user_id,
            log_writer=self.log_writer,
            blocklist=self.inactivity_blocklist,
            activity_counter=ActivityCounter(self.session),
        )
        try:
            report = service.apply_plan(plan, progress_cb=self._item_progress_cb)
        except Exception as e:
            wx.CallAfter(self._report_error, f"적용 실패: {e}")
            return

        if not report.failed:
            self.tracker.mark_adjustment_done(
                demoted=len(report.succeeded_demote),
                deleted=len(report.succeeded_delete),
            )

        # Undo 스택에 성공한 변경분만 push
        undo_items: list[UndoItem] = []
        for m in report.succeeded_demote:
            # plan 에서 to_level 을 다시 조회
            plan_item = next(
                (i for i in plan.items if i.member.user_id == m.user_id),
                None,
            )
            if plan_item and plan_item.to_level is not None:
                undo_items.append(UndoItem(
                    user_id=m.user_id, nickname=m.nickname,
                    from_level=plan_item.from_level, to_level=plan_item.to_level,
                ))
        for m in report.succeeded_delete:
            plan_item = next(
                (i for i in plan.items if i.member.user_id == m.user_id),
                None,
            )
            if plan_item and plan_item.to_level is not None:
                undo_items.append(UndoItem(
                    user_id=m.user_id, nickname=m.nickname,
                    from_level=plan_item.from_level, to_level=plan_item.to_level,
                ))
        if undo_items:
            self.undo_stack.push(
                label=f"장기미접속 조정 ({len(undo_items)}명)",
                items=undo_items,
            )
            # v1.0: 영구 등급 이력 기록
            try:
                self.level_history.record_batch(
                    [
                        {
                            "user_id": it.user_id,
                            "nickname": it.nickname,
                            "from_level": it.from_level,
                            "to_level": it.to_level,
                            "reason": "6개월 이상 미접속",
                        }
                        for it in undo_items
                    ],
                    source="level_adjust",
                    actor=self.admin_user_id,
                )
            except Exception:
                pass

        wx.CallAfter(self._after_apply, report)

    def _after_apply(self, report: AdjustmentReport) -> None:
        self._last_adjust_report = report
        lines = [report.speak_summary]
        if report.failed:
            lines.append("실패 내역:")
            for m, msg in report.failed[:20]:
                lines.append(f"  - {m.user_id}: {msg}")
        # 메일 발송 (rtgreen 전용)
        mail_lines = self._send_adjustment_mails(report)
        if mail_lines:
            lines.append("")
            lines.extend(mail_lines)
        self.result_text.SetValue("\n".join(lines))
        self._refresh_status()
        self.SetStatusText("조정 완료", 0)
        speak(report.speak_summary)

    def _send_adjustment_mails(self, report: AdjustmentReport) -> list[str]:
        """조정 대상자에게 안내 메일 발송 (rtgreen 전용).

        반환: 결과 로그 라인 (화면 표시용). 비활성화면 안내만 남김.
        """
        if not self.mail_sender.enabled:
            return [f"메일 발송 건너뜀 (로그인 아이디 {self.admin_user_id!r}, rtgreen 전용 기능)"]

        lines: list[str] = []
        # 강등 일괄 메일 (등급 변경 전 라벨은 member.level_label, 새 라벨은 level_transitions)
        if report.succeeded_demote:
            from config import LEVEL_LABELS, LEVEL_TRANSITIONS
            # 회원별로 from/to 다를 수 있어 템플릿 반복. 다만 내용이 거의 같으니 하나의 메일로 보낼 수 있음.
            # 간단화: 모든 강등 대상에게 공통 제목/본문. 개별 맞춤은 복잡도 대비 이득 낮음.
            subject, body = template_demote(
                report.succeeded_demote[0],
                "현재 등급",
                "한 단계 낮은 등급",
            )
            # 수신인 ID 리스트
            recipients = [m.user_id for m in report.succeeded_demote]
            results = self.mail_sender.send(recipients, subject, body)
            lines.append(f"강등 안내 메일: {len(recipients)}명")
            for r in results:
                lines.append(f"  {'OK' if r.success else 'FAIL'}  {r.message}")

        if report.succeeded_delete:
            subject, body = template_delete(report.succeeded_delete[0])
            recipients = [m.user_id for m in report.succeeded_delete]
            results = self.mail_sender.send(recipients, subject, body)
            lines.append(f"탈퇴 안내 메일: {len(recipients)}명")
            for r in results:
                lines.append(f"  {'OK' if r.success else 'FAIL'}  {r.message}")

        return lines

    def on_adjust_apply(self, event=None) -> None:
        # 직접 단축키로 들어오면 바로 미리보기부터 시작
        self.on_adjust_preview(event)

    def on_check_due(self, event=None) -> None:
        b = "가능" if self.tracker.is_backup_due() else f"{self.tracker.days_until_backup()}일 후"
        a = "가능" if self.tracker.is_adjustment_due() else f"{self.tracker.days_until_adjustment()}일 후"
        msg = f"우수회원 백업: {b}. 장기미접속 조정: {a}."
        speak(msg)
        self.SetStatusText(msg, 0)

    def on_toggle_auto_adjust(self, event=None) -> None:
        """장기미접속 조정 자동 실행 옵션 토글."""
        new_val = self.auto_adjust_item.IsChecked()
        app_options.set_value("auto_run_adjustment", bool(new_val))
        if new_val:
            speak(
                "장기미접속 조정 자동 실행을 켰습니다. "
                "다음 앱 시작 시 도래해 있으면 자동으로 미리보기가 시작됩니다."
            )
        else:
            speak(
                "장기미접속 조정 자동 실행을 껐습니다. "
                "도래 여부는 시작 시 음성 안내만 하고, "
                "직접 컨트롤 R 또는 작업 메뉴로 시작해 주세요."
            )

    def on_last_info(self, event=None) -> None:
        last_b = self.tracker.last_backup_date()
        last_a = self.tracker.last_adjustment_date()
        msg = (
            f"마지막 백업일은 {last_b or '없음'}, "
            f"마지막 조정일은 {last_a or '없음'} 입니다."
        )
        speak(msg)
        self._refresh_status()

    def on_open_backup(self, event=None) -> None:
        self._open_folder(BACKUPS_DIR)

    def on_open_log(self, event=None) -> None:
        self._open_folder(LOGS_DIR)

    def _open_folder(self, path: str) -> None:
        try:
            os.makedirs(path, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(path)  # noqa: SIM115
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            wx.MessageBox(f"폴더를 열 수 없습니다: {e}", "오류", wx.OK | wx.ICON_ERROR)

    def on_logout(self, event=None) -> None:
        confirm = wx.MessageBox(
            "로그아웃하면 저장된 자격 증명이 삭제되고 프로그램이 종료됩니다. 계속하시겠습니까?",
            "로그아웃",
            wx.YES_NO | wx.ICON_QUESTION,
        )
        if confirm != wx.YES:
            return
        try:
            from green_auth.credentials import delete_credentials
            delete_credentials()
        except Exception:
            pass
        try:
            from green_auth.config import LOGOUT_URL
            self.session.get(LOGOUT_URL, timeout=10)
        except Exception:
            pass
        speak("로그아웃되었습니다.")
        self.Close()

    def on_dump(self, event=None) -> None:
        if self._busy:
            return
        self._run_in_thread(self._do_dump, label="HTML 덤프")

    def _do_dump(self) -> None:
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved = []
        try:
            for page in (1, 2, 3):
                sep = "&" if "?" in ADMIN_MEMBER_URL else "?"
                url = ADMIN_MEMBER_URL if page == 1 else f"{ADMIN_MEMBER_URL}{sep}page={page}"
                resp = self.session.get(url, timeout=20)
                if not resp.ok:
                    break
                path = os.path.join(DUMPS_DIR, f"{ts}_page{page}.html")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(resp.text)
                saved.append(path)
        except Exception as e:
            wx.CallAfter(self._report_error, f"덤프 실패: {e}")
            return
        msg = f"{len(saved)}개 페이지를 덤프했습니다."
        wx.CallAfter(self.result_text.SetValue, msg + "\n" + "\n".join(saved))
        wx.CallAfter(speak, msg)

    def on_about(self, event=None) -> None:
        show_about(self)

    def on_help_keys(self, event=None) -> None:
        dlg = HelpDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def on_manual(self, event=None) -> None:
        dlg = ManualDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    # ---------- v0.4 신규: 통계 / 백업 비교 / 로그 뷰어 ----------

    def on_stats(self, event=None) -> None:
        dlg = StatsDialog(self, members=self._cached_members or [])
        dlg.ShowModal()
        dlg.Destroy()

    def on_payments(self, event=None) -> None:
        """자료실 구독비 관리 화면 — 토스 거래내역 import + 회원×월 매트릭스."""
        members = self._cached_members
        if not members:
            speak(
                "회원 목록이 비어있어 자료실 구독비 화면을 열 수 없습니다. "
                "Ctrl+F 로 회원 검색을 한 번 실행해 회원을 불러온 뒤 다시 시도해 주세요."
            )
            wx.MessageBox(
                "회원 목록이 비어있어 매핑·매트릭스를 만들 수 없습니다.\n"
                "먼저 Ctrl+F 로 회원 검색을 한 번 실행해 회원 목록을 불러와 주세요.",
                "회원 목록 필요",
                wx.OK | wx.ICON_INFORMATION,
            )
            return
        dlg = PaymentDialog(
            self,
            members=members,
            session=self.session,
            current_user_id=self.admin_user_id,
        )
        dlg.ShowModal()
        dlg.Destroy()

    def on_nas_log(self, event=None) -> None:
        """자료실(NAS) 접속 로그 — DSM Log Center 의 파일 전송 + 로그인 로그를
        회원·시간·동작 기준으로 조회·내보내기."""
        dlg = NasLogDialog(self, members=self._cached_members or [])
        dlg.ShowModal()
        dlg.Destroy()

    def on_board_admin(self, event=None) -> None:
        """게시판 관리 / 공지 작성 — 소리샘 게시판 설정 폼 + 단일/일괄 공지."""
        dlg = BoardAdminDialog(
            self, session=self.session, admin_user_id=self.admin_user_id,
        )
        dlg.ShowModal()
        dlg.Destroy()

    def on_scheduled_notices(self, event=None) -> None:
        """예약 공지 목록 — 예약한 공지 확인·취소·삭제."""
        dlg = ScheduledNoticeDialog(self)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def on_backup_diff(self, event=None) -> None:
        dlg = BackupDiffDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def on_log_viewer(self, event=None) -> None:
        dlg = LogViewerDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def on_level_history(self, event=None) -> None:
        dlg = LevelHistoryDialog(self, store=self.level_history)
        dlg.ShowModal()
        dlg.Destroy()

    # ---------- 업데이트 확인 (v1.0) ----------

    def on_check_update(self, event=None) -> None:
        """수동: 캐시 무시하고 강제 확인."""
        self._run_in_thread(
            lambda: self._do_check_update(force=True, silent_when_uptodate=False),
            label="업데이트 확인",
        )

    def check_update_in_background(self) -> None:
        """시작 시: 24시간 캐시 사용, 최신이면 조용히 넘어감."""
        self._run_in_thread(
            lambda: self._do_check_update(force=False, silent_when_uptodate=True),
            label="업데이트 자동 확인",
        )

    def _do_check_update(self, force: bool, silent_when_uptodate: bool) -> None:
        info = check_for_updates(force=force)
        if info is None:
            if silent_when_uptodate:
                return
            wx.CallAfter(
                wx.MessageBox,
                "현재 최신 버전입니다.",
                "업데이트 확인",
                wx.OK | wx.ICON_INFORMATION,
            )
            return
        wx.CallAfter(self._show_update_info, info)

    def _show_update_info(self, info) -> None:
        speak(info.speak_summary())
        body_lines = [
            f"새 버전 {info.latest} 가 사용 가능합니다.",
            f"현재 버전: {info.current}",
            "",
        ]
        if info.name:
            body_lines.append(info.name)
            body_lines.append("")
        if info.body:
            body_lines.append(info.body)
            body_lines.append("")

        has_asset = bool(info.download_url)
        if has_asset:
            kind = "설치관리자" if info.is_installer else "포터블 ZIP"
            body_lines.append(
                f"바로 받기를 누르면 {kind}({info.asset_name}) 을 자동으로 받습니다."
            )
            body_lines.append("어떻게 하시겠습니까?")
            body = "\n".join(body_lines)
            dlg = wx.MessageDialog(
                self, body, "새 버전 알림",
                wx.YES_NO | wx.CANCEL | wx.ICON_INFORMATION,
            )
            try:
                dlg.SetYesNoCancelLabels(
                    "지금 받기(&D)", "릴리스 페이지(&O)", "닫기(&C)",
                )
            except Exception:
                # 이 wx 버전에서 라벨 변경이 막혀 있어도 동작 자체엔 영향 없음.
                pass
            ans = dlg.ShowModal()
            dlg.Destroy()
            if ans == wx.ID_YES:
                self.on_install_update(info)
                return
            if ans == wx.ID_NO:
                self._open_release_page(info.release_url)
            return

        # 자산이 없으면 기존 흐름 — 릴리스 페이지 열기 / 닫기.
        body_lines.append(f"릴리스 페이지를 지금 여시겠습니까?\n{info.release_url}")
        body = "\n".join(body_lines)
        ans = wx.MessageBox(
            body, "새 버전 알림",
            wx.YES_NO | wx.ICON_INFORMATION,
        )
        if ans == wx.YES and info.release_url:
            self._open_release_page(info.release_url)

    def _open_release_page(self, url: str) -> None:
        if not url:
            return
        try:
            if sys.platform == "win32":
                os.startfile(url)  # noqa: SIM115
            else:
                subprocess.Popen(["xdg-open", url])
        except Exception:
            pass

    def on_install_update(self, info) -> None:
        """릴리스 자산을 받아 (가능하면) 설치관리자를 실행한다."""
        if not info.download_url or not info.asset_name:
            wx.MessageBox(
                "이번 릴리스에는 직접 받을 수 있는 파일이 없습니다.\n"
                "릴리스 페이지에서 직접 받아 주세요.",
                "업데이트", wx.OK | wx.ICON_INFORMATION,
            )
            self._open_release_page(info.release_url)
            return

        dest = Path(tempfile.gettempdir()) / info.asset_name
        # 이전 .part 잔존물 정리는 download_release_asset 내부에서 함.

        def worker(progress_cb):
            return download_release_asset(
                info.download_url,
                dest,
                progress_cb=progress_cb,
                fallback_total=info.asset_size,
            )

        dlg = ProgressTaskDialog(
            self,
            title=f"새 버전 {info.latest} 받는 중",
            task=worker,
            message="설치관리자를 받고 있습니다..." if info.is_installer
                    else "포터블 ZIP 을 받고 있습니다...",
            can_cancel=False,
        )
        try:
            dlg.run_modal()
            err = dlg.error
            downloaded = dlg.result
        finally:
            dlg.Destroy()

        if err is not None:
            wx.MessageBox(
                f"다운로드에 실패했습니다.\n{err}",
                "업데이트", wx.OK | wx.ICON_ERROR,
            )
            return
        if downloaded is None:
            return

        downloaded = Path(downloaded)
        if info.is_installer:
            prompt = (
                "받기가 끝났습니다.\n"
                f"파일: {downloaded.name}\n\n"
                "지금 설치하고 프로그램을 재시작하시겠습니까?\n"
                "(설치는 클릭 없이 자동으로 진행되고, 끝나면 새 버전이 자동으로 켜집니다.\n"
                "나중에 누르시면 받은 파일 경로만 알려 드리고 프로그램은 그대로 둡니다.)"
            )
            ans = wx.MessageBox(
                prompt, "설치", wx.YES_NO | wx.ICON_QUESTION,
            )
            if ans == wx.YES:
                try:
                    if sys.platform == "win32":
                        # 본 EXE 와 부모-자식 관계를 끊어 둬야 본 EXE 가 종료돼도
                        # 설치관리자가 계속 동작.
                        DETACHED_FLAGS = (
                            getattr(subprocess, "DETACHED_PROCESS", 0)
                            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                        )
                        subprocess.Popen(
                            [str(downloaded), *SILENT_INSTALL_FLAGS],
                            creationflags=DETACHED_FLAGS,
                            close_fds=True,
                        )
                    else:
                        subprocess.Popen([str(downloaded)])
                except Exception as e:
                    wx.MessageBox(
                        f"설치관리자 실행에 실패했습니다.\n{e}\n경로: {downloaded}",
                        "업데이트", wx.OK | wx.ICON_ERROR,
                    )
                    return
                # 설치관리자가 본 EXE 를 덮어쓰려면 본 앱이 종료돼 있어야 한다.
                # 약간 늦춰 종료해서 setup.exe 가 충분히 떠 있도록.
                wx.CallLater(500, self.Close)
            else:
                wx.MessageBox(
                    f"받은 파일: {downloaded}\n원하실 때 직접 실행해 주세요.",
                    "업데이트", wx.OK | wx.ICON_INFORMATION,
                )
        else:
            # 포터블 ZIP — 자동 설치 안 함, 폴더만 열어 줌.
            try:
                if sys.platform == "win32":
                    os.startfile(str(downloaded.parent))  # noqa: SIM115
            except Exception:
                pass
            wx.MessageBox(
                "포터블 ZIP 을 받았습니다.\n"
                f"경로: {downloaded}\n\n"
                "압축을 풀고 그 안의 초록등대회원관리.exe 를 실행해 주세요.",
                "업데이트", wx.OK | wx.ICON_INFORMATION,
            )

    # ---------- 사이트 구조 진단 (v1.0) ----------

    def on_site_diagnose(self, event=None) -> None:
        if self._busy:
            speak("다른 작업이 진행 중입니다.")
            return
        self._run_in_thread(self._do_site_diagnose, label="사이트 진단")

    def _do_site_diagnose(self) -> None:
        wx.CallAfter(self.SetStatusText, "사이트 진단 중...", 0)
        try:
            resp = self.session.get(ADMIN_MEMBER_URL, timeout=20)
            html = resp.text or ""
        except Exception as e:
            wx.CallAfter(self._report_error, f"진단 실패 (네트워크): {e}")
            return
        report = diagnose_admin_member_html(html)
        wx.CallAfter(self._show_site_diagnose, report)

    def _show_site_diagnose(self, report) -> None:
        text = report.text()
        self.result_text.SetValue(text)
        speak(text.split("\n", 1)[0])
        if report.severity == "error":
            wx.MessageBox(text, "사이트 진단 — 문제 발견",
                          wx.OK | wx.ICON_ERROR)
        elif report.severity == "warning":
            wx.MessageBox(text, "사이트 진단 — 주의",
                          wx.OK | wx.ICON_WARNING)
        else:
            wx.MessageBox(text, "사이트 진단", wx.OK | wx.ICON_INFORMATION)

    # ---------- Undo ----------

    def on_undo_last(self, event=None) -> None:
        if self._busy:
            speak("다른 작업이 진행 중입니다.")
            return

        entry = self.undo_stack.peek()
        if entry is None or not entry.items:
            speak("되돌릴 작업이 없습니다.")
            wx.MessageBox(
                "되돌릴 작업이 없습니다.",
                "되돌리기",
                wx.OK | wx.ICON_INFORMATION,
            )
            return

        # 자기 자신이 포함되었는지 확인 (안전 점검)
        admin_lower = (self.admin_user_id or "").lower()
        for it in entry.items:
            if it.user_id.lower() == admin_lower:
                wx.MessageBox(
                    "되돌릴 항목에 본인 계정이 포함되어 있어 거부합니다.",
                    "되돌리기 거부",
                    wx.OK | wx.ICON_ERROR,
                )
                return

        from datetime import datetime as _dt
        try:
            ts = _dt.fromisoformat(entry.timestamp).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            ts = entry.timestamp

        confirm = wx.MessageBox(
            f"가장 최근 작업을 되돌립니다.\n\n"
            f"  · 시각: {ts}\n"
            f"  · 작업: {entry.label}\n"
            f"  · 대상: {len(entry.items)}명\n\n"
            f"각 회원의 등급을 작업 직전 값으로 일괄 복구합니다.\n"
            f"계속하시겠습니까?",
            "마지막 작업 되돌리기",
            wx.YES_NO | wx.ICON_WARNING | wx.NO_DEFAULT,
        )
        if confirm != wx.YES:
            speak("되돌리기를 취소했습니다.")
            return

        self._run_in_thread(
            lambda: self._do_undo(entry),
            label="Undo 적용",
        )

    def _do_undo(self, entry: UndoEntry) -> None:
        speak("되돌리기를 적용합니다.")
        wx.CallAfter(self.SetStatusText, "되돌리기 적용 중...", 0)
        admin = MemberAdminAdapter(self.session, dry_run=False)
        # to_level → from_level 로 복구 (즉, 현재 등급에서 이전 등급으로)
        level_map = {it.user_id: it.from_level for it in entry.items}
        result = admin.bulk_apply(
            level_map,
            action_label=f"되돌리기 ({len(level_map)}명)",
        )

        # 로그 — 각 항목을 inverse 액션으로 기록
        if self.log_writer is not None:
            try:
                from core.models import AdjustmentItem
                from core.models import Member as _Member
                for it in entry.items:
                    pseudo = _Member(
                        user_id=it.user_id,
                        nickname=it.nickname,
                        level=it.to_level,
                        level_label="",
                    )
                    audit = AdjustmentItem(
                        member=pseudo,
                        action="demote",  # 표시용
                        from_level=it.to_level,
                        to_level=it.from_level,
                        reason=f"되돌리기 ({entry.label})",
                    )
                    self.log_writer.write_action(audit, result)
            except Exception:
                pass

        # 성공 시에만 스택에서 제거
        if result.success:
            self.undo_stack.pop()
            # v1.0: 영구 이력에는 inverse 항목 (현재→이전등급) 기록
            try:
                self.level_history.record_batch(
                    [
                        {
                            "user_id": it.user_id,
                            "nickname": it.nickname,
                            "from_level": it.to_level,    # 직전 작업이 to_level 로 바꿔놓은 상태
                            "to_level": it.from_level,    # 되돌리기로 from_level 로 복구
                            "reason": f"되돌리기 ({entry.label})",
                        }
                        for it in entry.items
                    ],
                    source="undo",
                    actor=self.admin_user_id,
                )
            except Exception:
                pass

        # 캐시된 회원 객체 등급도 갱신
        if self._cached_members:
            id_to_from = {it.user_id: it.from_level for it in entry.items}
            for m in self._cached_members:
                if m.user_id in id_to_from:
                    m.level = id_to_from[m.user_id]
                    from config import LEVEL_LABELS as _LL
                    m.level_label = _LL.get(m.level, str(m.level))

        wx.CallAfter(self._after_undo, entry, result)

    def _after_undo(self, entry: UndoEntry, result) -> None:
        if result.success:
            msg = f"되돌리기 완료: {entry.label} ({len(entry.items)}명)"
        else:
            msg = f"되돌리기 실패: {result.message}"
        self.result_text.SetValue(msg)
        self.SetStatusText(msg, 0)
        speak(msg)

    # ---------- 신규 가입자 승인 (v0.5) ----------

    def on_pending_members(self, event=None) -> None:
        """수동 호출. 캐시가 있으면 즉시, 없으면 새로 수집."""
        if self._busy:
            speak("다른 작업이 진행 중입니다.")
            return
        if self._cached_members:
            self._show_pending_dialog(
                self._cached_members, only_unseen=False
            )
            return
        self._run_in_thread(
            lambda: self._do_fetch_then_pending(only_unseen=False),
            label="신규 가입자 수집",
        )

    def on_pending_reset_seen(self, event=None) -> None:
        confirm = wx.MessageBox(
            "신규 가입자 '본 적 있음' 기록을 모두 초기화합니다.\n"
            "다음 실행 시 모든 미승인 회원이 다시 알림됩니다.\n"
            "계속하시겠습니까?",
            "기록 초기화",
            wx.YES_NO | wx.ICON_QUESTION,
        )
        if confirm != wx.YES:
            return
        try:
            self.pending_seen.clear()
        except Exception as e:
            wx.MessageBox(f"초기화 실패: {e}", "오류", wx.OK | wx.ICON_ERROR)
            return
        speak("신규 가입자 기록을 초기화했습니다.")

    def _do_fetch_then_pending(self, only_unseen: bool) -> None:
        """회원 목록 수집 후 가입자 다이얼로그 트리거 (백그라운드 스레드용)."""
        speak("회원 목록을 불러옵니다.")
        wx.CallAfter(self.SetStatusText, "회원 목록 수집 중...", 0)
        try:
            members = self.crawler.fetch_all_members(progress_cb=self._page_progress_cb)
        except EmptyParseError as e:
            wx.CallAfter(self._report_error, str(e))
            return
        except Exception as e:
            wx.CallAfter(self._report_error, f"수집 실패: {e}")
            return
        self._set_cached_members(members)
        wx.CallAfter(self._show_pending_dialog, members, only_unseen)

    def _show_pending_dialog(self, members, only_unseen: bool) -> None:
        """다이얼로그 모달 표시 (메인 스레드에서 호출)."""
        pendings = find_pending(
            members,
            seen_store=self.pending_seen,
            only_unseen=only_unseen,
            blocklist=self.inactivity_blocklist,
        )
        if not pendings:
            speak("처리할 신규 가입자가 없습니다.")
            self.SetStatusText("신규 가입자 없음", 0)
            return

        # 알림 박스로 한 번 알리기 — 자동 트리거 흐름에서도 음성으로 안내됨
        ans = wx.MessageBox(
            f"신규 가입 신청·대기 회원이 {len(pendings)}명 있습니다.\n"
            f"지금 한 명씩 승인 또는 거부 처리하시겠습니까?\n\n"
            f"(아니오 → 메인으로 복귀, 작업 메뉴 → '신규 가입자 승인' 으로 언제든 다시 처리 가능)",
            "신규 가입자 알림",
            wx.YES_NO | wx.ICON_QUESTION,
        )
        if ans != wx.YES:
            speak("신규 가입자 처리를 미뤘습니다.")
            return

        dlg = PendingMemberDialog(
            self,
            pendings=pendings,
            session=self.session,
            admin_user_id=self.admin_user_id,
            log_writer=self.log_writer,
            undo_stack=self.undo_stack,
            seen_store=self.pending_seen,
            level_history=self.level_history,
            mail_sender=self.mail_sender,
            blocklist=self.inactivity_blocklist,
        )
        dlg.ShowModal()
        # 결과 보고
        n_a = len(dlg.approved)
        n_r = len(dlg.rejected)
        n_d = len(dlg.deferred)
        msg = (
            f"신규 가입자 처리 결과: 승인 {n_a}명 / 거부 {n_r}명 / 미루기 {n_d}명"
        )
        self.result_text.SetValue(msg)
        self.SetStatusText(msg, 0)
        dlg.Destroy()

    def check_pending_on_startup(self) -> None:
        """프로그램 시작 직후 자동 호출. 본 적 없는 신규 가입자만 알림."""
        if self._busy:
            return
        # 캐시가 있으면 즉시 사용 (보통 시작 시점엔 비어있음)
        if self._cached_members:
            self._show_pending_dialog(self._cached_members, only_unseen=True)
            return
        self._run_in_thread(
            lambda: self._do_fetch_then_pending(only_unseen=True),
            label="신규 가입자 자동 확인",
        )

    # ---------- MVP TOP 10 ----------

    def on_mvp_now(self, event=None) -> None:
        if self._busy:
            speak("다른 작업이 진행 중입니다.")
            return
        self._do_mvp_analysis()

    def _do_mvp_analysis(self) -> None:
        """진행 다이얼로그 + 비프 안내가 포함된 MVP TOP N 분석.

        2단계: (1) 회원 페이지 수집 → (2) 회원별 활동점수 산정. 두 단계 모두
        같은 ProgressTaskDialog 게이지에 진행률을 보고하고, 진행에 따라 음높이가
        상승하는 짧은 비프가 함께 재생된다.
        """
        if self._busy:
            return
        self._busy = True
        speak(
            "회원 목록을 수집한 뒤 우리들의 이야기와 질문게시판의 "
            "글·댓글을 합산합니다. 일반회원 이상 대상이라 시간이 걸릴 수 있습니다."
        )
        self.SetStatusText("MVP 분석 중...", 0)

        crawler = self.crawler
        admin_user_id = self.admin_user_id

        def worker(progress_cb):
            members = crawler.fetch_all_members(
                progress_cb=lambda c, t: progress_cb(
                    c, t, f"회원 페이지 수집 {c}/{t}",
                ),
            )
            service = MvpService(crawler, admin_user_id=admin_user_id)
            report = service.run(
                members=members,
                progress_cb=lambda c, t: progress_cb(
                    c, t, f"활동점수 산정 {c}/{t}",
                ),
            )
            try:
                saved = write_mvp_report(report)
            except Exception:
                saved = None
            return (members, report, saved)

        dlg = ProgressTaskDialog(
            self, title="MVP TOP 분석",
            task=worker,
            message="MVP 분석 시작 — 회원 페이지 수집 중...",
        )
        try:
            dlg.run_modal()
        finally:
            dlg.Destroy()
            self._busy = False
            self.SetStatusText("준비됨", 0)

        if dlg.error is not None:
            if isinstance(dlg.error, EmptyParseError):
                self._report_error(str(dlg.error))
            else:
                self._report_error(f"MVP 분석 실패: {dlg.error}")
            return

        members, report, saved = dlg.result
        self._set_cached_members(members)
        if report.items:
            self.tracker.mark_mvp_done(top_n=len(report.items), quarter=report.quarter)
            self.log_writer.write_event(
                f"mvp quarter={report.quarter} top_n={len(report.items)}"
            )
        self._after_mvp(report, saved)

    def _after_mvp(self, report: MvpReport, saved_path) -> None:
        self.SetStatusText(f"MVP 분석 완료: {report.quarter}", 0)
        lines = [report.speak_summary()]
        if saved_path is not None:
            lines.append(f"저장: {saved_path}")
        if report.items:
            lines.append("")
            for it in report.items:
                lines.append(it.display())
        self.result_text.SetValue("\n".join(lines))
        self._refresh_status()
        speak(report.speak_summary())

        dlg = MvpDialog(self, report)
        dlg.ShowModal()
        dlg.Destroy()

    # ---------- 승급 임박 회원 ----------

    def on_promotion_imminent(self, event=None) -> None:
        if self._busy:
            speak("다른 작업이 진행 중입니다.")
            return
        dlg = PromotionImminentDialog(self, self.crawler)
        dlg.ShowModal()
        dlg.Destroy()

    # ---------- HTML 리포트 ----------

    def on_html_report(self, event=None) -> None:
        if not self._cached_members:
            ans = wx.MessageBox(
                "회원 데이터가 아직 캐시되지 않았습니다.\n"
                "지금 회원 목록을 새로 수집할까요?",
                "HTML 리포트",
                wx.YES_NO | wx.ICON_QUESTION,
            )
            if ans != wx.YES:
                return
            self._run_in_thread(self._do_fetch_then_report, label="HTML 리포트")
            return
        self._render_and_save_report(self._cached_members)

    def _do_fetch_then_report(self) -> None:
        speak("회원 목록을 수집합니다.")
        wx.CallAfter(self.SetStatusText, "회원 수집 중...", 0)
        try:
            members = self.crawler.fetch_all_members(progress_cb=self._page_progress_cb)
        except Exception as e:
            wx.CallAfter(self._report_error, f"수집 실패: {e}")
            return
        self._set_cached_members(members)
        wx.CallAfter(self._render_and_save_report, members)

    def _render_and_save_report(self, members) -> None:
        try:
            path = write_report(default_report_path(), members=members)
        except Exception as e:
            wx.MessageBox(f"리포트 저장 실패: {e}", "오류", wx.OK | wx.ICON_ERROR)
            return
        msg = f"HTML 리포트 저장됨: {path}"
        self.result_text.SetValue(msg)
        speak("HTML 리포트를 저장했습니다.")
        # 사용자에게 열어볼지 묻기
        ans = wx.MessageBox(
            f"{path}\n\n지금 브라우저로 열까요?",
            "HTML 리포트",
            wx.YES_NO | wx.ICON_INFORMATION,
        )
        if ans == wx.YES:
            try:
                if sys.platform == "win32":
                    os.startfile(str(path))  # noqa: SIM115
                else:
                    subprocess.Popen(["xdg-open", str(path)])
            except Exception:
                pass

    # ---------- 단축키 설정 ----------

    def on_keybindings_open(self, event=None) -> None:
        try:
            if not KEYBINDINGS_FILE.exists():
                write_template(KEYBINDINGS_FILE)
                wx.MessageBox(
                    f"기본값으로 단축키 설정 템플릿을 만들었습니다.\n"
                    f"{KEYBINDINGS_FILE}\n\n"
                    f"파일을 메모장에서 열어 편집하고 저장한 뒤\n"
                    f"프로그램을 재시작하면 새 단축키가 적용됩니다.",
                    "단축키 설정",
                    wx.OK | wx.ICON_INFORMATION,
                )
            if sys.platform == "win32":
                os.startfile(str(KEYBINDINGS_FILE))  # noqa: SIM115
            else:
                subprocess.Popen(["xdg-open", str(KEYBINDINGS_FILE)])
        except Exception as e:
            wx.MessageBox(
                f"파일을 열 수 없습니다: {e}",
                "오류",
                wx.OK | wx.ICON_ERROR,
            )

    def _apply_user_keybindings(self) -> None:
        """data/keybindings.json 의 사용자 정의 단축키를 AcceleratorTable 로 추가."""
        bindings = load_user_bindings()
        if not bindings:
            return
        action_to_id: dict[str, int] = {
            "search":         int(ID_SEARCH),
            "stats":          int(ID_STATS),
            "backup":         int(ID_BACKUP_NOW),
            "promote":        int(ID_PROMOTE_NOW),
            "adjust_preview": int(ID_ADJUST_PREVIEW),
            "adjust_apply":   int(ID_ADJUST_APPLY),
            "manual_mail":    int(ID_MANUAL_MAIL),
            "check_due":      int(ID_CHECK_DUE),
            "last_info":      int(ID_LAST_INFO),
            "open_backup":    int(ID_OPEN_BACKUP),
            "logout":         int(ID_LOGOUT),
            "log_viewer":     int(ID_LOG_VIEWER),
            "backup_diff":    int(ID_BACKUP_DIFF),
            "undo_last":      int(ID_UNDO_LAST),
            "help_keys":      int(ID_HELP_KEYS),
            "manual":         int(ID_MANUAL),
            "promotion_imminent": int(ID_PROMOTION_IMMINENT),
            "html_report":    int(ID_HTML_REPORT),
            "nas_log":        int(ID_NAS_LOG),
        }
        try:
            entries = build_accelerator_entries(bindings, action_to_id)
            if entries:
                self.SetAcceleratorTable(wx.AcceleratorTable(entries))
        except Exception:
            pass

    # ---------- 백업 보관 정책 ----------

    def on_archive_old_backups(self, event=None) -> None:
        if self._busy:
            speak("다른 작업이 진행 중입니다.")
            return
        from core.backup_retention import find_old_backup_dirs
        targets = find_old_backup_dirs(months=DEFAULT_RETENTION_MONTHS)
        if not targets:
            speak(f"{DEFAULT_RETENTION_MONTHS}개월 이상 된 백업이 없습니다.")
            wx.MessageBox(
                f"{DEFAULT_RETENTION_MONTHS}개월보다 오래된 백업 폴더가 없습니다.",
                "오래된 백업 정리",
                wx.OK | wx.ICON_INFORMATION,
            )
            return

        sample = "\n".join(f"  · {p.name}" for p in targets[:10])
        more = f"\n  ... 외 {len(targets) - 10}개" if len(targets) > 10 else ""
        confirm = wx.MessageBox(
            f"{len(targets)}개의 오래된 백업 폴더를 zip 으로 압축한 뒤\n"
            f"원본 폴더를 삭제합니다 (zip 자체는 backups/archives/ 에 보존).\n\n"
            f"대상:\n{sample}{more}\n\n"
            f"계속하시겠습니까?",
            "오래된 백업 정리",
            wx.YES_NO | wx.ICON_WARNING | wx.NO_DEFAULT,
        )
        if confirm != wx.YES:
            speak("정리를 취소했습니다.")
            return

        self._run_in_thread(self._do_archive_old_backups, label="백업 압축")

    def _do_archive_old_backups(self) -> None:
        speak("오래된 백업 폴더 압축을 시작합니다.")
        wx.CallAfter(self.SetStatusText, "백업 압축 중...", 0)
        try:
            result = archive_old_backups(months=DEFAULT_RETENTION_MONTHS)
        except Exception as e:
            wx.CallAfter(self._report_error, f"백업 압축 실패: {e}")
            return

        lines = [f"오래된 백업 정리 결과: {result.summary}"]
        if result.archived:
            lines.append("")
            lines.append("[압축됨]")
            for name in result.archived:
                lines.append(f"  · {name}.zip")
        if result.errors:
            lines.append("")
            lines.append("[실패]")
            for name, msg in result.errors:
                lines.append(f"  · {name}: {msg}")
        if result.archive_dir:
            lines.append("")
            lines.append(f"보관 위치: {result.archive_dir}")

        wx.CallAfter(self.result_text.SetValue, "\n".join(lines))
        wx.CallAfter(self.SetStatusText, result.summary, 0)
        wx.CallAfter(speak, result.summary)

    # ---------- 자동 스케줄 ----------

    def run_scheduled_tasks_if_due(self) -> None:
        # v1.0: 업데이트 확인은 항상 백그라운드로
        self.check_update_in_background()
        # v1.2: NAS 접속 로그도 시작 시 한 번 백그라운드 수집 (silent — 실패해도 다이얼로그 X)
        self._maybe_fetch_nas_log_in_bg()

        backup_due = self.tracker.is_backup_due()
        adjust_due = self.tracker.is_adjustment_due()
        mvp_due = self.tracker.is_mvp_due()
        # 장기미접속 조정 자동 실행 옵션 — 기본 False (사용자 직접 실행).
        auto_run_adjust = bool(app_options.get("auto_run_adjustment"))

        if not backup_due and not adjust_due and not mvp_due:
            self.on_check_due()
            # 정기 작업이 없을 때도 신규 가입자 확인은 항상 실행
            wx.CallAfter(self.check_pending_on_startup)
            return

        if backup_due:
            speak("3개월 주기 백업을 자동 시작합니다.")
            self._run_in_thread(self._do_backup_then_maybe_adjust, label="자동 백업")
        elif adjust_due and auto_run_adjust:
            speak("6개월 주기 장기미접속 조정 미리보기를 자동 생성합니다.")
            self.on_adjust_preview()
        elif adjust_due:
            # 자동 실행 꺼짐 — 안내만 하고 사용자 트리거 대기.
            speak(
                "6개월 주기 장기미접속 조정이 도래해 있습니다. "
                "자동 실행이 꺼져 있으니 컨트롤 R 또는 작업 메뉴에서 시작해 주세요."
            )
            self.SetStatusText(
                "장기미접속 조정 도래 — Ctrl+R 로 시작 가능", 0,
            )
            wx.CallAfter(self.check_pending_on_startup)
        elif mvp_due:
            speak("3개월 주기 MVP 분석을 자동 시작합니다.")
            wx.CallAfter(self._do_mvp_analysis)

    def _do_backup_then_maybe_adjust(self) -> None:
        self._do_backup()
        # 조정이 도래해 있고 자동 실행 옵션이 켜져 있으면 백업 끝난 뒤 미리보기 자동 트리거.
        if (
            self.tracker.is_adjustment_due()
            and bool(app_options.get("auto_run_adjustment"))
        ):
            wx.CallAfter(self._auto_trigger_adjust)
        elif self.tracker.is_adjustment_due():
            wx.CallAfter(
                speak,
                "백업이 끝났습니다. 장기미접속 조정도 도래해 있으나 "
                "자동 실행이 꺼져 있어 시작하지 않습니다. "
                "컨트롤 R 또는 작업 메뉴에서 시작해 주세요.",
            )
        elif self.tracker.is_mvp_due():
            wx.CallAfter(self._auto_trigger_mvp)

    def _auto_trigger_adjust(self) -> None:
        speak("이어서 6개월 주기 장기미접속 조정 미리보기를 시작합니다.")
        self.on_adjust_preview()

    def _auto_trigger_mvp(self) -> None:
        speak("이어서 3개월 주기 MVP 분석을 시작합니다.")
        wx.CallAfter(self._do_mvp_analysis)

    def trigger_pending_check_if_due(self) -> None:
        """긴 자동 작업이 끝난 뒤 또는 시작 직후에 신규 가입자 알림."""
        wx.CallAfter(self.check_pending_on_startup)

    # ---------- 헬퍼 ----------

    def _run_in_thread(self, func, label: str) -> None:
        def worker():
            self._busy = True
            try:
                func()
            finally:
                self._busy = False
                wx.CallAfter(self.SetStatusText, "준비됨", 0)
        t = threading.Thread(target=worker, name=label, daemon=True)
        t.start()

    # ---------- NAS 접속 로그 — 시작 시 백그라운드 수집 ----------

    def _maybe_fetch_nas_log_in_bg(self) -> None:
        """앱 시작 시 한 번 DSM 에서 새 로그를 받아와 SQLite 에 저장.

        조건이 안 맞으면(설정 없음 / 옵션 꺼짐 / 2FA 활성 / 사용자 미설정 등)
        조용히 건너뛴다. 어떤 예외도 대화상자로 띄우지 않음 — 사용자가 다이얼로그
        를 열면 그때 상태 줄과 store.last_status() 에 사유가 나타난다.
        """
        try:
            from core import app_options as _opts
            if not bool(_opts.get("auto_fetch_nas_log_on_start", True)):
                return
        except Exception:
            return  # 옵션 모듈 자체에 문제가 있으면 스킵

        try:
            from core.dsm_config import load_dsm_settings
            from core.nas_log_store import NasLogStore
            settings = load_dsm_settings()
        except Exception:
            return
        if settings is None or not settings.is_complete:
            try:
                NasLogStore().set_last_status(False, "DSM 설정이 없습니다")
            except Exception:
                pass
            return
        if settings.use_2fa:
            # 시작 시 OTP 다이얼로그가 떠 버리면 스크린리더 사용자가 매우 혼란.
            # 자동 수집은 건너뛰고, 사용자가 다이얼로그를 열어 직접 새로고침하게 둔다.
            try:
                NasLogStore().set_last_status(
                    False, "2단계 인증이 활성화되어 자동 수집을 건너뜁니다 — '지금 새로고침'을 눌러 주세요",
                )
            except Exception:
                pass
            return

        threading.Thread(
            target=self._fetch_nas_log_silent, args=(settings,),
            name="nas_log_bg_fetch", daemon=True,
        ).start()

    def _fetch_nas_log_silent(self, settings) -> None:
        # 절대로 UI 다이얼로그를 띄우지 않는다 — 실패는 store.set_last_status 에만 남김.
        try:
            from core.dsm_client import DsmAuthError, DsmClient
            from core.nas_log_service import fetch_and_store_logs
            from core.nas_log_store import NasLogStore
            from config import DUMPS_DIR
            store = NasLogStore()
            with DsmClient(settings.url, verify_ssl=settings.verify_ssl) as client:
                client.login(settings.account, settings.password)
                fetch_and_store_logs(
                    client, store,
                    dsm_group_name=settings.group_name or None,
                    dump_dir=str(DUMPS_DIR),
                )
        except Exception as e:  # noqa: BLE001 — 어떤 예외도 조용히
            try:
                NasLogStore().set_last_status(False, f"자동 수집 실패: {e}")
            except Exception:
                pass

    def _page_progress_cb(self, current: int, total: int) -> None:
        if current % 5 == 0:
            wx.CallAfter(self.SetStatusText, f"페이지 {current} 처리 중", 1)

    def _item_progress_cb(self, current: int, total: int) -> None:
        wx.CallAfter(self.SetStatusText, f"{current}/{total} 적용 중", 1)
        if total > 0 and current % max(1, total // 5) == 0:
            wx.CallAfter(speak, f"{current} / {total} 진행 중")

    def _report_error(self, msg: str) -> None:
        # v1.0: 회원 목록 분석 실패면 사이트 구조 자동 진단 정보를 덧붙인다.
        diag_text = ""
        if "회원 목록을 분석" in msg or "수집 실패" in msg:
            try:
                resp = self.session.get(ADMIN_MEMBER_URL, timeout=15)
                report = diagnose_admin_member_html(resp.text or "")
                if report.findings:
                    diag_text = "\n\n" + report.text()
            except Exception:
                pass
        full = msg + diag_text
        self.result_text.SetValue(full)
        self.SetStatusText("오류", 0)
        speak(msg)
        wx.MessageBox(full, "오류", wx.OK | wx.ICON_ERROR)
