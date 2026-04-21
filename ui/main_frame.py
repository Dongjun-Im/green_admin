"""초록등대 회원관리 메인 프레임."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import date

import requests
import wx

from config import (
    ADMIN_MEMBER_URL,
    APP_NAME,
    APP_VERSION,
    BACKUPS_DIR,
    DUMPS_DIR,
    LOGS_DIR,
)
from core.backup_service import BackupService
from core.crawler import MemberCrawler
from core.level_adjustment import LevelAdjustmentService
from core.log_writer import OperationLogWriter
from core.mail_sender import (
    MailSender,
    template_delete,
    template_demote,
    template_promote,
)
from core.member_admin import MemberAdminAdapter
from core.member_parser import EmptyParseError, MemberListParser
from core.models import AdjustmentPlan, AdjustmentReport, BackupResult
from core.promotion_service import PromotionPlan, PromotionReport, PromotionService
from core.schedule_tracker import ScheduleTracker
from screen_reader import cancel_speech, speak
from ui.confirm_dialog import ConfirmAdjustmentDialog
from ui.help_dialog import HelpDialog, show_about
from ui.item_text_ctrl import ItemTextCtrl
from ui.mail_dialog import ManualMailDialog
from ui.search_dialog import MemberSearchDialog


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
        self._cached_members: list | None = None
        self._last_adjust_report: AdjustmentReport | None = None
        self._last_promo_report: PromotionReport | None = None

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
        file_menu.AppendSeparator()
        file_menu.Append(ID_LOGOUT, "로그아웃(&L)\tCtrl+L")
        file_menu.Append(wx.ID_EXIT, "프로그램 종료(&X)\tAlt+F4")
        bar.Append(file_menu, "파일(&F)")

        task_menu = wx.Menu()
        task_menu.Append(ID_SEARCH, "회원 검색(&F)\tCtrl+F")
        task_menu.AppendSeparator()
        task_menu.Append(ID_BACKUP_NOW, "우수회원 백업 실행(&B)\tCtrl+B")
        task_menu.Append(
            ID_PROMOTE_NOW,
            "게시물 기반 자동 승급(&U)\tCtrl+U",
        )
        task_menu.AppendSeparator()
        task_menu.Append(ID_ADJUST_PREVIEW, "장기미접속 조정 — 미리보기(&P)\tCtrl+R")
        task_menu.Append(
            ID_ADJUST_APPLY,
            "장기미접속 조정 — 실제 적용(&A)\tCtrl+Shift+R",
        )
        task_menu.AppendSeparator()
        task_menu.Append(
            ID_MANUAL_MAIL,
            "수동 메일 발송 (rtgreen 전용)(&M)\tCtrl+M",
        )
        task_menu.AppendSeparator()
        task_menu.Append(ID_CHECK_DUE, "지금 작업 가능 여부 확인(&C)\tCtrl+D")
        bar.Append(task_menu, "작업(&T)")

        dev_menu = wx.Menu()
        dev_menu.Append(ID_DUMP, "관리자 페이지 HTML 덤프(&H)")
        bar.Append(dev_menu, "개발자(&D)")

        help_menu = wx.Menu()
        help_menu.Append(wx.ID_ABOUT, "프로그램 정보(&A)\tF1")
        help_menu.Append(ID_HELP_KEYS, "단축키 안내(&K)\tCtrl+K")
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
        self.Bind(wx.EVT_MENU, self._on_close, id=wx.ID_EXIT)

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
        next_b = self.tracker.next_backup_date()
        next_a = self.tracker.next_adjustment_date()
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
        text = (
            f"마지막 백업: {last_b or '기록 없음'}   다음 백업: {b_due}\n"
            f"마지막 조정: {last_a or '기록 없음'}   다음 조정: {a_due}"
        )
        self.status_text.SetValue(text)

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
        )
        dlg.ShowModal()
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
        self._cached_members = members
        wx.CallAfter(self._show_search_dialog, members)

    def _show_search_dialog(self, members) -> None:
        self.SetStatusText(f"회원 {len(members)}명 수집 완료", 0)
        dlg = MemberSearchDialog(self, members)
        dlg.ShowModal()
        dlg.Destroy()

    def on_promote_now(self, event=None) -> None:
        if self._busy:
            speak("다른 작업이 진행 중입니다.")
            return
        confirm = wx.MessageBox(
            "게시물 50건 이상인 일반회원을 우수회원으로 승급합니다.\n"
            "승급은 사이트에 즉시 반영되며 되돌릴 수 없습니다.\n"
            "계속하시겠습니까?",
            "우수회원 자동 승급",
            wx.YES_NO | wx.ICON_QUESTION,
        )
        if confirm != wx.YES:
            return
        self._run_in_thread(self._do_promote_only, label="우수회원 승급")

    def _do_promote_only(self) -> None:
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
        self._cached_members = members
        try:
            report = self._run_promotion_with_members(members)
        except Exception as e:
            wx.CallAfter(self._report_error, f"승급 실패: {e}")
            return
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
        self._run_in_thread(self._do_build_plan, label="조정 미리보기")

    def _do_build_plan(self) -> None:
        speak("회원 목록을 불러와 조정 대상을 분석합니다.")
        wx.CallAfter(self.SetStatusText, "조정 미리보기 생성 중...", 0)
        try:
            admin = MemberAdminAdapter(self.session, dry_run=True)
            service = LevelAdjustmentService(
                self.crawler,
                admin,
                admin_user_id=self.admin_user_id,
                log_writer=self.log_writer,
            )
            plan = service.build_plan(progress_cb=self._page_progress_cb)
        except EmptyParseError as e:
            wx.CallAfter(self._report_error, str(e))
            return
        except Exception as e:
            wx.CallAfter(self._report_error, f"미리보기 실패: {e}")
            return

        self._last_plan = plan
        wx.CallAfter(self._show_plan_dialog, plan)

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

    # ---------- 자동 스케줄 ----------

    def run_scheduled_tasks_if_due(self) -> None:
        backup_due = self.tracker.is_backup_due()
        adjust_due = self.tracker.is_adjustment_due()

        if not backup_due and not adjust_due:
            self.on_check_due()
            return

        if backup_due:
            speak("3개월 주기 백업을 자동 시작합니다.")
            self._run_in_thread(self._do_backup_then_maybe_adjust, label="자동 백업")
        elif adjust_due:
            speak("6개월 주기 장기미접속 조정 미리보기를 자동 생성합니다.")
            self.on_adjust_preview()

    def _do_backup_then_maybe_adjust(self) -> None:
        self._do_backup()
        # 조정이 도래해 있으면 백업 끝난 뒤 미리보기 자동 트리거
        if self.tracker.is_adjustment_due():
            wx.CallAfter(self._auto_trigger_adjust)

    def _auto_trigger_adjust(self) -> None:
        speak("이어서 6개월 주기 장기미접속 조정 미리보기를 시작합니다.")
        self.on_adjust_preview()

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

    def _page_progress_cb(self, current: int, total: int) -> None:
        if current % 5 == 0:
            wx.CallAfter(self.SetStatusText, f"페이지 {current} 처리 중", 1)

    def _item_progress_cb(self, current: int, total: int) -> None:
        wx.CallAfter(self.SetStatusText, f"{current}/{total} 적용 중", 1)
        if total > 0 and current % max(1, total // 5) == 0:
            wx.CallAfter(speak, f"{current} / {total} 진행 중")

    def _report_error(self, msg: str) -> None:
        self.result_text.SetValue(msg)
        self.SetStatusText("오류", 0)
        speak(msg)
        wx.MessageBox(msg, "오류", wx.OK | wx.ICON_ERROR)
