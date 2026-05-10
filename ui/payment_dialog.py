"""자료실 구독비 관리 메인 다이얼로그.

상단:  KPI (활성 구독자, 이번 달 만료, 데이터 기준일)
가운데: 회원 × 최근 12개월 매트릭스 (ListCtrl REPORT)
하단:  토스 거래내역 가져오기 / 기타 입금 보기 / 새로고침 / 닫기

토스 import 흐름:
    1) 파일 선택 (wx.FileDialog)
    2) 비밀번호 입력 (wx.PasswordEntryDialog)
    3) parse_toss_xlsx 로 입금 행 추출 → PaymentStore.import_deposits
    4) PaymentMatcher.process — 자동 매칭, 실패 시 PaymentAliasDialog 콜백
    5) 결과 요약 모달 + 매트릭스 새로고침
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import wx

from config import SUBSCRIPTION_PRICING
from core import app_options
from core.models import Member
from core.payment_matcher import PaymentMatcher
from core.payment_matrix import (
    MonthHeader,
    cell_state,
    combined_status,
    month_range,
    payment_state_label,
    short_subscription_status,
    status_summary,
    subscription_period_label,
)
from core.payment_store import (
    PaymentStore,
    Subscription,
    Transaction,
    lookup_months,
)
from core.payment_html import default_html_path, write_payment_html
from core.payment_txt import default_txt_path, write_payment_txt
from core.payment_xlsx import default_xlsx_path, write_payment_xlsx
from core.sheets_sync import push_form_status
from core.toss_xlsx import TossPasswordError, parse_toss_xlsx
from screen_reader import speak
import threading

from core.dsm_client import DsmAuthError, DsmClient
from core.dsm_config import load_dsm_settings
from core.dsm_service import deactivate_subscriber
from core.dsm_workflow import (
    activate_subscriber_with_welcome_mail,
    delete_subscriber,
    detect_new_subscribers,
)
from ui.dsm_dialog import DsmDialog
from ui.dsm_setup_dialog import DsmSetupDialog
from ui.new_subscriber_dialog import NewSubscriberDialog
from ui.payment_alias_dialog import PaymentAliasDialog
from ui.payment_export_dialog import (
    FORMAT_HTML,
    FORMAT_TXT,
    FORMAT_XLSX,
    PaymentExportDialog,
)
from ui.payment_mail_dialog import PaymentMailDialog
from ui.sheets_sync_dialog import SheetsSyncDialog


_MATRIX_MONTHS = 12


def _format_sheet_status_note(result: str) -> str:
    """push_form_status() 결과 코드를 결과 메시지 한 줄로 변환."""
    if not result:
        return ""  # 시트 미설정 또는 인증 전 — 표시할 것 없음
    if result == "updated":
        return "\n  - 구글시트 '상태' 컬럼 갱신됨"
    if result == "not_found":
        return "\n  - 구글시트: 해당 회원의 폼 응답 행이 없어 상태 미갱신"
    if result.startswith("error:auth:"):
        return "\n  - 구글시트 상태 갱신 실패 (인증 필요 - 먼저 구글시트 동기화)"
    if result.startswith("error:"):
        return f"\n  - 구글시트 상태 갱신 실패: {result[len('error:'):]}"
    return ""


class PaymentDialog(wx.Dialog):
    def __init__(
        self,
        parent,
        *,
        members: list[Member],
        store: PaymentStore | None = None,
        session=None,
        current_user_id: str = "",
    ) -> None:
        super().__init__(
            parent,
            title="자료실 구독비 관리",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
        )
        self.all_members = members
        self.store = store or PaymentStore()
        self.session = session
        self.current_user_id = current_user_id
        self._matrix_rows: list[tuple[Member, list[Subscription]]] = []
        # DSM 자료실 그룹 멤버 user_id 집합. None = 아직 조회 안 됨.
        self._dsm_member_names: set[str] | None = None
        # DSM 사용자명(lower) → 이메일. None = 아직 조회 안 됨.
        self._dsm_emails: dict[str, str] | None = None
        self._build_ui()
        self.SetMinSize(wx.Size(900, 580))
        self.Fit()
        self.Centre()
        self._reload()
        wx.CallAfter(self._announce_open)
        # 자동 가져오기 옵션 — 기본 False. 켜져 있으면 메인 열린 직후 비동기로
        # DSM 멤버를 한 번 가져와 매트릭스에 반영.
        if bool(app_options.get("auto_fetch_dsm_on_open")):
            wx.CallAfter(self._fetch_dsm_members, silent=True)

    # ---------- UI ----------

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.kpi_text = wx.StaticText(panel, label="(데이터 없음)")
        sizer.Add(self.kpi_text, 0, wx.ALL, 10)

        sizer.Add(
            wx.StaticText(panel, label="회원 검색(&S) - 아이디, 이름, 닉네임:"),
            0, wx.LEFT | wx.RIGHT, 10,
        )
        self.search_input = wx.TextCtrl(panel, name="검색")
        self.search_input.Bind(wx.EVT_TEXT, lambda e: self._refresh_matrix())
        sizer.Add(self.search_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        self.matrix = wx.ListCtrl(
            panel,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_HRULES | wx.LC_VRULES,
            name="구독 매트릭스",
        )
        sizer.Add(self.matrix, 1, wx.EXPAND | wx.ALL, 10)
        self.matrix.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_row_selected)

        # 매트릭스에서 선택한 회원 단위 작업
        row_action_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.activate_member_btn = wx.Button(
            panel, label="선택 회원 DSM 활성화(&A)",
        )
        self.deactivate_member_btn = wx.Button(
            panel, label="선택 회원 DSM 비활성화(&X)",
        )
        self.delete_member_btn = wx.Button(
            panel, label="선택 회원 DSM 삭제(&Z)",
        )
        self.activate_member_btn.Bind(wx.EVT_BUTTON, self._on_dsm_activate_member)
        self.deactivate_member_btn.Bind(wx.EVT_BUTTON, self._on_dsm_deactivate_member)
        self.delete_member_btn.Bind(wx.EVT_BUTTON, self._on_dsm_delete_member)
        row_action_sizer.Add(self.activate_member_btn, 0, wx.RIGHT, 8)
        row_action_sizer.Add(self.deactivate_member_btn, 0, wx.RIGHT, 8)
        row_action_sizer.Add(self.delete_member_btn, 0)
        sizer.Add(row_action_sizer, 0, wx.LEFT | wx.RIGHT, 10)

        # 단가표 외 금액 입금도 구독으로 인정할 개월수 (0 = 인정 안 함, '기타'로 분류)
        opt_sizer = wx.BoxSizer(wx.HORIZONTAL)
        opt_sizer.Add(
            wx.StaticText(
                panel,
                label="단가표 외 입금 구독 인정 개월(&K) — 0이면 인정 안 함:",
            ),
            0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8,
        )
        self.other_months_spin = wx.SpinCtrl(
            panel, min=0, max=60,
            initial=int(app_options.get("other_amount_subscription_months") or 0),
            name="단가표 외 입금 구독 개월",
        )
        self.other_months_spin.Bind(wx.EVT_SPINCTRL, self._on_other_months_changed)
        opt_sizer.Add(self.other_months_spin, 0, wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(opt_sizer, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)

        # 버튼
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.import_btn = wx.Button(panel, label="토스 거래내역 가져오기(&I)")
        self.others_btn = wx.Button(panel, label="기타 입금 보기(&O)")
        self.sheets_btn = wx.Button(panel, label="구글시트 동기화(&G)")
        self.mail_btn = wx.Button(panel, label="만료 임박자 메일(&M)")
        self.dsm_btn = wx.Button(panel, label="DSM 설정(&D)")
        self.dsm_manage_btn = wx.Button(panel, label="DSM 관리(&N)")
        self.dsm_fetch_btn = wx.Button(panel, label="DSM 멤버 가져오기(&Y)")
        self.export_btn = wx.Button(panel, label="내보내기(&E)...")
        self.refresh_btn = wx.Button(panel, label="새로고침(&R)")
        self.close_btn = wx.Button(panel, wx.ID_CLOSE, "닫기")
        self.import_btn.Bind(wx.EVT_BUTTON, self._on_import)
        self.others_btn.Bind(wx.EVT_BUTTON, self._on_view_others)
        self.sheets_btn.Bind(wx.EVT_BUTTON, self._on_sheets_sync)
        self.mail_btn.Bind(wx.EVT_BUTTON, self._on_expire_mail)
        self.dsm_btn.Bind(wx.EVT_BUTTON, self._on_dsm_setup)
        self.dsm_manage_btn.Bind(wx.EVT_BUTTON, self._on_dsm_manage)
        self.dsm_fetch_btn.Bind(wx.EVT_BUTTON, lambda e: self._fetch_dsm_members())
        self.export_btn.Bind(wx.EVT_BUTTON, self._on_export)
        self.refresh_btn.Bind(wx.EVT_BUTTON, lambda e: self._reload())
        self.close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        for b in (
            self.import_btn, self.others_btn, self.sheets_btn,
            self.mail_btn, self.dsm_btn, self.dsm_manage_btn,
            self.dsm_fetch_btn,
            self.export_btn,
            self.refresh_btn, self.close_btn,
        ):
            btn_sizer.Add(b, 0, wx.RIGHT, 8)
        # 자동 가져오기 토글 — 옵션은 app_options.json 에 영구 저장.
        self.auto_dsm_cb = wx.CheckBox(
            panel, label="메인 열 때 DSM 자동(&U)",
            name="DSM 자동 가져오기 토글",
        )
        self.auto_dsm_cb.SetValue(bool(app_options.get("auto_fetch_dsm_on_open")))
        self.auto_dsm_cb.Bind(wx.EVT_CHECKBOX, self._on_toggle_auto_dsm)
        btn_sizer.Add(self.auto_dsm_cb, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        panel.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CLOSE)

    # ---------- 데이터 로딩 ----------

    # 매트릭스 고정 컬럼 수 (월별 12개 컬럼 앞)
    _FIXED_COLS = 6

    def _reload(self) -> None:
        # 컬럼 재구성 (오늘 기준 12개월)
        self.month_headers: list[MonthHeader] = month_range(date.today(), _MATRIX_MONTHS)

        self.matrix.ClearAll()
        self.matrix.InsertColumn(0, "아이디 / 이름 / 닉네임", width=220)
        self.matrix.InsertColumn(1, "구독 상태", width=130)
        self.matrix.InsertColumn(2, "구독 기간", width=170)
        self.matrix.InsertColumn(3, "DSM 이메일", width=170)
        self.matrix.InsertColumn(4, "입금 상태", width=110)
        self.matrix.InsertColumn(5, "DSM 정합", width=140)
        col = self._FIXED_COLS
        for h in self.month_headers:
            self.matrix.InsertColumn(col, h.label, width=58)
            col += 1

        # 행 집합 — 결제 구독 이력 ∪ DSM 자료실 그룹 멤버 ∪ 폼 신청자.
        all_subs = self.store.all_subscriptions()
        subs_by_uid: dict[str, list[Subscription]] = {}
        for s in all_subs:
            subs_by_uid.setdefault(s.member_user_id, []).append(s)

        members_by_uid = {m.user_id: m for m in self.all_members}

        # 폼 신청자 — member_user_id(희망아이디) 기준
        applicants = self.store.all_form_applicants()
        self._applicant_uids_lower: set[str] = {
            a.member_user_id.strip().lower() for a in applicants if a.member_user_id
        }
        applicants_by_uid_lower = {
            a.member_user_id.strip().lower(): a
            for a in applicants if a.member_user_id
        }
        dsm_lower_set: set[str] = set()
        if self._dsm_member_names is not None:
            dsm_lower_set = {(n or "").strip().lower() for n in self._dsm_member_names if n}
        dsm_email_keys: set[str] = set((self._dsm_emails or {}).keys())

        all_uids: set[str] = set(subs_by_uid.keys())

        def _resolve_uid(raw: str) -> str:
            """raw 와 lower 일치하는 회원 user_id 가 있으면 그걸로, 없으면 raw."""
            key = (raw or "").strip()
            if not key:
                return key
            return next(
                (m.user_id for m in self.all_members
                 if m.user_id.lower() == key.lower()),
                key,
            )

        if self._dsm_member_names is not None:
            for dn in self._dsm_member_names:
                if dn:
                    all_uids.add(_resolve_uid(dn))
        for a in applicants:
            if a.member_user_id:
                all_uids.add(_resolve_uid(a.member_user_id))

        rows: list[tuple[Member, list[Subscription]]] = []
        for uid in all_uids:
            subs = sorted(subs_by_uid.get(uid, []), key=lambda x: x.period_from)
            m = members_by_uid.get(uid)
            if m is None:
                # 소리샘 회원 목록엔 없는 user_id — 이름 채우기 우선순위:
                #   ① 폼 신청자 이름  ② DSM 사용자/이메일 정보  ③ '소리샘 회원 아님'
                uid_l = uid.strip().lower()
                fa = applicants_by_uid_lower.get(uid_l)
                if fa and (fa.name or "").strip():
                    fb_name = fa.name.strip()
                elif uid_l in dsm_lower_set or uid_l in dsm_email_keys:
                    em = (self._dsm_emails or {}).get(uid_l, "")
                    fb_name = f"(DSM 사용자{(' ' + em) if em else ''})"
                else:
                    fb_name = "(소리샘 회원 아님)"
                m = Member(user_id=uid, name=fb_name, nickname="")
            rows.append((m, subs))

        # 정렬 — 만료일 가까운 순. 구독 이력 없는 행은 뒤로.
        def sort_key(item):
            _, subs = item
            return max((s.period_to for s in subs), default=date.max)
        rows.sort(key=sort_key)
        self._matrix_rows = rows

        self._refresh_kpi()
        self._refresh_matrix()

    def _refresh_kpi(self) -> None:
        today = date.today()
        active = 0
        expiring_this_month = 0
        for _, subs in self._matrix_rows:
            latest = max((s.period_to for s in subs), default=None)
            if latest is None:
                continue
            if latest >= today:
                active += 1
            if latest.year == today.year and latest.month == today.month:
                expiring_this_month += 1
        # 신청했는데 아직 입금 안 됨 (폼 신청자 중 구독 레코드 없는 사람)
        pending = 0
        applicant_uids = getattr(self, "_applicant_uids_lower", set())
        if applicant_uids:
            paid_uids = {
                m.user_id.strip().lower()
                for m, subs in self._matrix_rows if subs
            }
            pending = len(applicant_uids - paid_uids)
        kpi = (
            f"활성 구독자: {active}명   "
            f"이번 달 만료: {expiring_this_month}명   "
        )
        if applicant_uids:
            kpi += f"결제 대기: {pending}명   "
        kpi += f"기준일: {today.isoformat()}"
        self.kpi_text.SetLabel(kpi)

    def _refresh_matrix(self) -> None:
        kw = self.search_input.GetValue().strip().lower()
        self.matrix.DeleteAllItems()
        today = date.today()
        dsm_lower: set[str] | None = None
        if self._dsm_member_names is not None:
            dsm_lower = {(n or "").strip().lower() for n in self._dsm_member_names if n}
        applicant_uids = getattr(self, "_applicant_uids_lower", set())
        emails = self._dsm_emails
        for member, subs in self._matrix_rows:
            if kw and not (
                kw in member.user_id.lower()
                or kw in (member.name or "").lower()
                or kw in (member.nickname or "").lower()
            ):
                continue
            uid_lower = member.user_id.strip().lower()
            is_applicant = uid_lower in applicant_uids
            row_label = f"{member.user_id} / {member.name}"
            if member.nickname:
                row_label += f" / {member.nickname}"
            idx = self.matrix.InsertItem(self.matrix.GetItemCount(), row_label)
            # 1: 구독 상태
            self.matrix.SetItem(
                idx, 1,
                short_subscription_status(subs, today, is_applicant=is_applicant),
            )
            # 2: 구독 기간
            self.matrix.SetItem(idx, 2, subscription_period_label(subs, today))
            # 3: DSM 이메일
            if emails is None:
                email_cell = "?"
            else:
                email_cell = emails.get(uid_lower, "—")
            self.matrix.SetItem(idx, 3, email_cell)
            # 4: 입금 상태
            self.matrix.SetItem(
                idx, 4,
                payment_state_label(has_subscription=bool(subs), is_applicant=is_applicant),
            )
            # 5: DSM·정합
            in_dsm: bool | None = None if dsm_lower is None else (uid_lower in dsm_lower)
            self.matrix.SetItem(
                idx, 5, combined_status(subs, today, in_dsm_group=in_dsm),
            )
            # 6~: 월별 셀
            for col_offset, h in enumerate(self.month_headers):
                self.matrix.SetItem(
                    idx, self._FIXED_COLS + col_offset, cell_state(subs, h),
                )

    # ---------- 이벤트 ----------

    def _on_row_selected(self, _event=None) -> None:
        idx = self.matrix.GetFirstSelected()
        if idx < 0:
            return
        # ListCtrl 의 GetItemText 로 첫 두 컬럼만 읽어 음성.
        speak(
            f"{self.matrix.GetItemText(idx, 0)}, "
            f"{self.matrix.GetItemText(idx, 1)}"
        )

    def _on_import(self, _event=None) -> None:
        with wx.FileDialog(
            self,
            "토스뱅크 거래내역 XLSX 선택",
            wildcard="Excel 파일 (*.xlsx)|*.xlsx",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = Path(dlg.GetPath())

        # 비밀번호 — 토스는 모든 거래내역 .xlsx 가 암호화돼 있다.
        with wx.PasswordEntryDialog(
            self,
            f"{path.name} 의 비밀번호:",
            "비밀번호 입력",
        ) as pwd_dlg:
            if pwd_dlg.ShowModal() != wx.ID_OK:
                return
            password = pwd_dlg.GetValue()

        # 파싱
        try:
            deposits = parse_toss_xlsx(path, password)
        except TossPasswordError as e:
            speak("비밀번호가 틀렸거나 파일을 열 수 없습니다.")
            wx.MessageBox(
                f"파일을 열 수 없습니다.\n{e}",
                "복호화 실패",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        except Exception as e:
            wx.MessageBox(f"파싱 오류: {e}", "오류", wx.OK | wx.ICON_ERROR, self)
            return

        added, skipped_dup = self.store.import_deposits(deposits)
        if added == 0 and skipped_dup == 0:
            speak("입금 거래가 없습니다.")
            wx.MessageBox(
                "파일에서 입금 거래를 찾지 못했습니다.",
                "안내",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return

        # 매칭 — 단가표 외 입금도 구독 인정 개월수가 설정돼 있으면 함께 적용.
        matcher = PaymentMatcher(
            self.store, self.all_members,
            other_amount_months=self._other_amount_months(),
        )
        summary = matcher.process(manual_callback=self._manual_match_cb)
        self._reload()

        msg = (
            f"입금 거래 {added}건 추가 (중복 {skipped_dup}건 무시)\n"
            f"자동 매칭: {summary.auto_matched_payers}명\n"
            f"수동 매칭: {summary.manual_matched_payers}명\n"
            f"건너뜀(매핑 없음): {summary.skipped_unmatched_payers}명\n"
            f"새 구독: {summary.new_subscriptions}건\n"
            f"기타 입금(단가표 외): {summary.other_amount_txns}건"
        )
        speak(f"가져오기 완료. 새 구독 {summary.new_subscriptions}건.")
        wx.MessageBox(msg, "가져오기 결과", wx.OK | wx.ICON_INFORMATION, self)

        # 신규 가입자 검출 — DSM 자료실 그룹과 비교해서 활성화 누락을 알린다.
        # DSM 설정이 없으면 조용히 스킵.
        wx.CallAfter(self._maybe_prompt_new_subscribers)

    # ---------- 매칭 헬퍼 (import + 기타 입금 재산정 공용) ----------

    def _manual_match_cb(self, payer_name, candidates, txns):
        """자동 매칭 실패 시 입금자명 → 회원 수동 매핑 다이얼로그."""
        with PaymentAliasDialog(
            self,
            payer_name=payer_name,
            candidates=candidates,
            unprocessed_txns=txns,
            all_members=self.all_members,
        ) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                return dlg.chosen_member
            return None

    def _other_amount_months(self) -> int:
        try:
            return int(self.other_months_spin.GetValue())
        except Exception:
            return int(app_options.get("other_amount_subscription_months") or 0)

    def _on_other_months_changed(self, _event=None) -> None:
        """단가표 외 입금 구독 개월 SpinCtrl 변경 — 옵션 저장 + 기존 입금 재산정 제안."""
        n = self._other_amount_months()
        app_options.set_value("other_amount_subscription_months", n)
        if n <= 0:
            speak("단가표 외 입금은 기타로 분류합니다 (구독 인정 안 함).")
            return
        speak(f"단가표 외 입금을 {n}개월 구독으로 인정하도록 설정했습니다.")
        # 이미 가져온 단가표 외 입금에도 적용할지 — 한 번 물어본다.
        others = [t for t in self.store.all_transactions() if lookup_months(t.amount) is None]
        unprocessed = [
            t for t in others if not self.store.subscription_exists_for_txn(t.id)
        ]
        if not unprocessed:
            return
        ans = wx.MessageBox(
            f"이미 가져온 '단가표 외' 입금 {len(unprocessed)}건도 {n}개월 구독으로 "
            f"지금 재산정할까요?\n(입금자-회원 매칭이 안 된 건은 다시 물어봅니다.)",
            "기타 입금 재산정", wx.YES_NO | wx.ICON_QUESTION, self,
        )
        if ans != wx.YES:
            return
        matcher = PaymentMatcher(
            self.store, self.all_members, other_amount_months=n,
        )
        summary = matcher.process(manual_callback=self._manual_match_cb)
        self._reload()
        speak(f"재산정 완료. 새 구독 {summary.new_subscriptions}건.")
        wx.MessageBox(
            f"단가표 외 입금 재산정 완료\n"
            f"  자동 매칭: {summary.auto_matched_payers}명\n"
            f"  수동 매칭: {summary.manual_matched_payers}명\n"
            f"  건너뜀: {summary.skipped_unmatched_payers}명\n"
            f"  새 구독: {summary.new_subscriptions}건\n"
            f"  여전히 기타로 남음: {summary.other_amount_txns}건",
            "재산정 결과", wx.OK | wx.ICON_INFORMATION, self,
        )

    def _on_export(self, _event=None) -> None:
        """선택한 형식들을 한 번에 backups/payments_YYYY-MM-DD.<ext> 로 저장.

        XLSX·HTML·TXT 중 하나 이상 선택. 단일 파일이면 '지금 열까요?', 여러
        개면 '백업 폴더 열까요?' 로 안내.
        """
        with PaymentExportDialog(self) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            formats = list(dlg.selected_formats)

        # 형식별 (라벨, 출력 함수, 기본 경로 함수)
        writers = {
            FORMAT_XLSX: ("엑셀", write_payment_xlsx, default_xlsx_path),
            FORMAT_HTML: ("HTML 리포트", write_payment_html, default_html_path),
            FORMAT_TXT: ("텍스트", write_payment_txt, default_txt_path),
        }
        saved: list[tuple[str, Path]] = []
        errors: list[str] = []
        for fmt in formats:
            label, writer, path_fn = writers[fmt]
            try:
                out = writer(path_fn(), self.store, self.all_members)
                saved.append((label, out))
            except Exception as e:
                errors.append(f"{label} 저장 실패: {e}")

        if not saved:
            speak("내보내기 실패.")
            wx.MessageBox(
                "\n".join(errors) or "저장된 파일이 없습니다.",
                "오류", wx.OK | wx.ICON_ERROR, self,
            )
            return

        # 음성 + 안내
        labels = ", ".join(lbl for lbl, _ in saved)
        speak(f"{labels} 저장했습니다.")
        if errors:
            wx.MessageBox(
                "일부 형식이 실패했습니다:\n" + "\n".join(errors),
                "일부 실패", wx.OK | wx.ICON_WARNING, self,
            )

        # 단일 → '지금 열까요?', 다중 → '백업 폴더 열까요?'
        if len(saved) == 1:
            self._prompt_open_file(saved[0][1])
        else:
            self._prompt_open_folder(saved)

    def _prompt_open_file(self, path: Path) -> None:
        """저장 후 '지금 열까요?' 모달 — main_frame 의 _render_and_save_report 패턴."""
        ans = wx.MessageBox(
            f"{path}\n\n지금 열까요?",
            "저장 완료",
            wx.YES_NO | wx.ICON_INFORMATION,
            self,
        )
        if ans != wx.YES:
            return
        try:
            import os
            import sys
            import subprocess
            if sys.platform == "win32":
                os.startfile(str(path))  # noqa: SIM115
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            pass

    def _prompt_open_folder(self, saved: list[tuple[str, Path]]) -> None:
        """다중 저장 시 — 파일 목록 표시 후 '백업 폴더 열까요?'."""
        listing = "\n".join(f"  - {lbl}: {p}" for lbl, p in saved)
        folder = saved[0][1].parent
        ans = wx.MessageBox(
            f"{len(saved)}개 형식이 저장되었습니다:\n{listing}\n\n"
            f"백업 폴더를 열까요?\n{folder}",
            "저장 완료",
            wx.YES_NO | wx.ICON_INFORMATION,
            self,
        )
        if ans != wx.YES:
            return
        try:
            import os
            import sys
            import subprocess
            if sys.platform == "win32":
                os.startfile(str(folder))  # noqa: SIM115
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception:
            pass

    def _on_sheets_sync(self, _event=None) -> None:
        """구글시트 양방향 동기화 다이얼로그 열기."""
        with SheetsSyncDialog(self, store=self.store, members=self.all_members) as dlg:
            dlg.ShowModal()
        # 시트에서 alias 가 들어왔을 수 있으니 매트릭스 재계산.
        # (구독 자체는 시트 단방향 push 라 변동 없음)
        self._reload()

    def _on_expire_mail(self, _event=None) -> None:
        """만료 임박자에게 안내 메일 일괄 발송."""
        with PaymentMailDialog(
            self,
            store=self.store,
            members=self.all_members,
            session=self.session,
            current_user_id=self.current_user_id,
        ) as dlg:
            dlg.ShowModal()

    def _on_dsm_setup(self, _event=None) -> None:
        """DSM(Synology) 연결 설정 다이얼로그."""
        with DsmSetupDialog(self) as dlg:
            dlg.ShowModal()

    def _on_dsm_manage(self, _event=None) -> None:
        """DSM 관리 — 진단 + 사용자 목록 + 정합성 체크 + 신규 사용자 생성."""
        with DsmDialog(
            self, store=self.store, members=self.all_members,
        ) as dlg:
            dlg.ShowModal()
        # 신규 사용자 생성으로 form_applicants 가 추가됐을 수 있으니 매트릭스 갱신.
        self._reload()

    # ---------- DSM 멤버 가져오기 (매트릭스 병합용) ----------

    def _on_toggle_auto_dsm(self, _event=None) -> None:
        """\"메인 열 때 DSM 자동\" 체크박스 변경 — app_options 영구 저장."""
        new_val = self.auto_dsm_cb.IsChecked()
        app_options.set_value("auto_fetch_dsm_on_open", bool(new_val))
        if new_val:
            speak("자료실 메인 열 때 DSM 자동 가져오기를 켰습니다.")
        else:
            speak("DSM 자동 가져오기를 껐습니다. 필요할 때 버튼으로 가져오세요.")

    def _fetch_dsm_members(self, *, silent: bool = False) -> None:
        """DSM 자료실 그룹 멤버 user_id 를 가져와 매트릭스에 반영.

        silent=True 면 진행 음성/메시지 없이 백그라운드 동작 (자동 가져오기용).
        설정이 없거나 OTP 가 필요한데 사용자가 취소하면 조용히 종료.
        """
        settings = load_dsm_settings()
        if settings is None or not settings.is_complete:
            if not silent:
                wx.MessageBox(
                    "DSM 설정이 없습니다. 'DSM 설정' 메뉴에서 먼저 자격증명을 입력하세요.",
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
        self.dsm_fetch_btn.Disable()
        if not silent:
            speak("DSM 자료실 그룹 멤버를 가져옵니다.")
        threading.Thread(
            target=self._fetch_dsm_members_in_thread,
            args=(settings, otp, silent),
            daemon=True,
        ).start()

    def _fetch_dsm_members_in_thread(self, settings, otp: str, silent: bool) -> None:
        diag_path: Path | None = None
        emails: dict[str, str] = {}
        try:
            with DsmClient(settings.url, verify_ssl=settings.verify_ssl) as client:
                client.login(settings.account, settings.password, otp_code=otp)
                members = client.list_group_members(settings.group_name)
                # 전체 사용자에서 이메일 맵 — 기본 additional 에 email 포함.
                try:
                    for u in client.list_users():
                        nm = (u.get("name") or "").strip().lower()
                        em = (u.get("email") or "").strip()
                        if nm and em:
                            emails[nm] = em
                except Exception:
                    emails = {}
                # 0명이면 진단 덤프를 한 번 더 (캡처 모드) 떠서 파일에 남김 —
                # 실제 응답을 보고 어느 변종이 빈 결과인지 운영자가 점검 가능.
                if not members and not silent:
                    diag = client.collect_group_member_diagnostics(
                        settings.group_name,
                    )
                    diag_path = self._write_dsm_diag(diag)
        except DsmAuthError as e:
            wx.CallAfter(self._fetch_dsm_failed, str(e), silent)
            return
        except Exception as e:
            wx.CallAfter(self._fetch_dsm_failed, f"예상치 못한 오류: {e}", silent)
            return
        names = {(m.get("name") or "").strip() for m in members if m.get("name")}
        wx.CallAfter(self._fetch_dsm_done, names, emails, silent, diag_path)

    def _write_dsm_diag(self, diag: dict) -> Path | None:
        """진단 결과를 data/dumps/dsm_group_diag_YYYYMMDD-HHMM.json 으로 저장."""
        try:
            from datetime import datetime as _dt
            import json as _json
            from config import DUMPS_DIR
            stamp = _dt.now().strftime("%Y%m%d-%H%M%S")
            out = Path(DUMPS_DIR) / f"dsm_group_diag_{stamp}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                _json.dumps(diag, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return out
        except Exception:
            return None

    def _fetch_dsm_done(
        self, names: set[str], emails: dict[str, str], silent: bool,
        diag_path: Path | None = None,
    ) -> None:
        self._dsm_member_names = names
        self._dsm_emails = emails if emails else {}
        self.dsm_fetch_btn.Enable()
        self._reload()
        if silent:
            return
        if names:
            speak(f"DSM 자료실 그룹 멤버 {len(names)}명을 매트릭스에 반영했습니다.")
            wx.MessageBox(
                f"DSM 자료실 그룹 멤버 {len(names)}명, 이메일 {len(emails)}건을 가져와 매트릭스에 반영했습니다.\n"
                "각 행의 'DSM 정합' / 'DSM 이메일' 컬럼으로 상태를 확인할 수 있습니다.",
                "가져오기 완료", wx.OK | wx.ICON_INFORMATION, self,
            )
            return

        # 0명일 때 — 진단 파일을 함께 안내.
        speak("DSM 그룹 멤버 0명. 진단 파일을 확인해 주세요.")
        diag_part = ""
        if diag_path is not None:
            diag_part = (
                f"\n\n진단 덤프가 다음 경로에 저장됐습니다:\n{diag_path}\n"
                "이 파일을 메모장으로 열어 확인하거나 운영진에게 공유하면\n"
                "어느 API 변종에서 실제 응답이 어떻게 오는지 알 수 있습니다."
            )
        ans = wx.MessageBox(
            "DSM 자료실 그룹에서 멤버를 0명 가져왔습니다.\n\n"
            "확인할 점:\n"
            "  - 'DSM 설정'의 '자료실 회원 그룹' 이름이 DSM의 실제 그룹 이름과 일치\n"
            "  - 로그인한 관리자 계정이 그 그룹의 사용자 목록 조회 권한 보유\n"
            "  - 'DSM 관리' - '진단 실행' 으로 그룹 존재 여부와 사용자 수를 점검\n\n"
            "그룹은 비어 있는데 결제 활성 회원만 있다면 매트릭스의 'DSM 정합' 컬럼에\n"
            "'활성화 누락' 으로 표시되어 즉시 보입니다."
            + diag_part
            + ("\n\n진단 파일을 지금 열어보시겠습니까?" if diag_path else ""),
            "가져오기 결과 — 0명",
            (wx.YES_NO if diag_path else wx.OK) | wx.ICON_WARNING, self,
        )
        if diag_path is not None and ans == wx.YES:
            try:
                import os
                import sys
                import subprocess
                if sys.platform == "win32":
                    os.startfile(str(diag_path))
                else:
                    subprocess.Popen(["xdg-open", str(diag_path)])
            except Exception:
                pass

    def _fetch_dsm_failed(self, message: str, silent: bool) -> None:
        self.dsm_fetch_btn.Enable()
        if silent:
            # 자동 가져오기 — 사용자 흐름 방해하지 않도록 상태바 안내만.
            return
        speak("DSM 멤버 가져오기 실패.")
        wx.MessageBox(message, "가져오기 실패", wx.OK | wx.ICON_ERROR, self)

    # ---------- 매트릭스 선택 회원의 DSM 활성/비활성 ----------

    def _selected_member(self):
        """매트릭스에서 선택된 회원 (Member) 또는 None.
        매트릭스 첫 컬럼이 'user_id / name / nickname' — user_id 로 회원 검색."""
        idx = self.matrix.GetFirstSelected()
        if idx < 0:
            return None
        row_label = self.matrix.GetItemText(idx, 0)
        user_id = row_label.split("/", 1)[0].strip()
        if not user_id:
            return None
        # all_members 에서 해당 user_id 찾기
        for m in self.all_members:
            if m.user_id == user_id:
                return m
        # 매트릭스에 있지만 회원 목록에 없는 케이스 (목록 외) — 임시 객체
        from core.models import Member
        return Member(user_id=user_id, name="", nickname="")

    def _on_dsm_activate_member(self, _event=None) -> None:
        member = self._selected_member()
        if member is None:
            wx.MessageBox(
                "먼저 매트릭스에서 회원을 선택해 주세요.",
                "선택 필요", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        ans = wx.MessageBox(
            f"'{member.user_id}' ({member.name}) 을 DSM 자료실 그룹에 활성화합니까?",
            "활성화 확인", wx.YES_NO | wx.ICON_QUESTION, self,
        )
        if ans != wx.YES:
            return
        self._run_member_dsm_action(member, action="activate")

    def _on_dsm_deactivate_member(self, _event=None) -> None:
        member = self._selected_member()
        if member is None:
            wx.MessageBox(
                "먼저 매트릭스에서 회원을 선택해 주세요.",
                "선택 필요", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        ans = wx.MessageBox(
            f"'{member.user_id}' ({member.name}) 을 DSM 에서 비활성화하고 그룹에서 제거합니까?",
            "비활성화 확인", wx.YES_NO | wx.ICON_QUESTION, self,
        )
        if ans != wx.YES:
            return
        self._run_member_dsm_action(member, action="deactivate")

    def _on_dsm_delete_member(self, _event=None) -> None:
        member = self._selected_member()
        if member is None:
            wx.MessageBox(
                "먼저 매트릭스에서 회원을 선택해 주세요.",
                "선택 필요", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        dlg = wx.RichMessageDialog(
            self,
            f"'{member.user_id}' ({member.name}) 을 DSM에서 영구 삭제하고\n"
            f"입금자명 매핑(alias)도 함께 정리합니다.\n\n"
            f"[주의] 이 작업은 되돌릴 수 없습니다. 계속할까요?",
            "사용자 삭제 확인",
            wx.YES_NO | wx.ICON_WARNING | wx.NO_DEFAULT,
        )
        dlg.ShowCheckBox(
            "이 회원의 구독, 신청 기록도 함께 삭제 - 매트릭스에서 완전히 제거 "
            "(체크 해제 시 구독, 거래 이력은 회계 자료로 보존)"
        )
        ret = dlg.ShowModal()
        purge_local = dlg.IsCheckBoxChecked()
        dlg.Destroy()
        if ret != wx.ID_YES:
            return
        self._run_member_dsm_action(member, action="delete", purge_local=purge_local)

    def _run_member_dsm_action(self, member, *, action: str, purge_local: bool = False) -> None:
        settings = load_dsm_settings()
        if settings is None or not settings.is_complete:
            wx.MessageBox(
                "DSM 설정이 없습니다. 'DSM 설정' 메뉴에서 먼저 자격증명을 입력하고 저장하세요.",
                "DSM 설정 필요", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        # 2FA 사용 시 OTP
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

        self.activate_member_btn.Disable()
        self.deactivate_member_btn.Disable()
        self.delete_member_btn.Disable()
        action_label = {
            "activate": "활성화",
            "deactivate": "비활성화",
            "delete": "삭제",
        }.get(action, action)
        speak(f"{member.user_id} {action_label} 진행 중.")
        threading.Thread(
            target=self._member_dsm_action_in_thread,
            args=(settings, member, action, otp, purge_local),
            daemon=True,
        ).start()

    def _member_dsm_action_in_thread(
        self, settings, member, action: str, otp: str, purge_local: bool = False,
    ) -> None:
        # 작업 후 _dsm_member_names 캐시에 적용할 동작: ("add"|"discard", user_id) | None
        cache_op: tuple[str, str] | None = None
        try:
            with DsmClient(settings.url, verify_ssl=settings.verify_ssl) as client:
                client.login(settings.account, settings.password, otp_code=otp)
                if action == "activate":
                    # 활성 구독에서 만료일·개월 추출 — 메일 본문에 사용.
                    period_to = None
                    months = 0
                    is_renewal = False
                    subs = self.store.subscriptions_for_member(member.user_id)
                    if subs:
                        active = [s for s in subs if s.period_to >= date.today()]
                        if active:
                            latest = max(active, key=lambda s: s.period_to)
                            period_to = latest.period_to
                            months = latest.months
                        is_renewal = len(subs) > 1
                    workflow = activate_subscriber_with_welcome_mail(
                        dsm_client=client,
                        member=member,
                        group_name=settings.group_name,
                        sorisem_session=self.session,
                        sorisem_current_user_id=self.current_user_id,
                        sorisem_members=self.all_members,
                        period_to=period_to,
                        months=months,
                        is_renewal=is_renewal,
                    )
                    result = workflow.activation
                    head = "신규 생성됨" if result.created else "기존 계정 활성화"
                    msg = f"{head}: {result.user_name}"
                    if result.in_group:
                        msg += "\n  - 자료실 그룹 추가: 완료"
                    elif result.group_error:
                        msg += (
                            "\n  - [주의] 자료실 그룹 추가 실패 - DSM 웹 관리자에서 직접 추가해 주세요\n"
                            f"      ({result.group_error})"
                        )
                    else:
                        msg += "\n  - 자료실 그룹 추가: 이미 멤버"
                    if result.initial_password:
                        msg += (
                            f"\n  - 임시 비밀번호: {result.initial_password}\n"
                            "    회원에게 이 비밀번호를 안내하고 첫 로그인 후 변경 권유."
                        )
                    # 메일 결과
                    if workflow.mail_attempted:
                        if workflow.mail_sent:
                            msg += "\n  - 환영 메일: 발송 완료"
                        else:
                            msg += f"\n  - 환영 메일: 실패 - {workflow.mail_message}"
                    elif workflow.mail_skip_reason == "not_rtgreen":
                        msg += (
                            "\n  - 환영 메일: 생략 ('rtgreen' 으로 로그인되지 않음)"
                        )
                    elif workflow.mail_skip_reason == "no_session":
                        msg += "\n  - 환영 메일: 생략 (세션 없음)"
                    elif workflow.mail_skip_reason == "no_sorisem_match":
                        msg += (
                            f"\n  - 환영 메일: 생략 (소리샘 회원 매칭 실패 - "
                            f"user_id '{member.user_id}' 가 회원 목록에 없음)"
                        )
                    cache_op = ("add", member.user_id) if result.in_group else None
                elif action == "delete":
                    del_result = delete_subscriber(
                        dsm_client=client,
                        member_user_id=member.user_id,
                        store=self.store,
                        purge_local=purge_local,
                    )
                    head = (
                        f"DSM 사용자 영구 삭제 완료: {del_result.user_name}"
                        if del_result.found_in_dsm
                        else f"'{del_result.user_name}' 은 DSM 에 없습니다 - 이미 정리된 상태"
                    )
                    msg = (
                        f"{head}\n"
                        f"  - 정리된 alias 매핑: {del_result.aliases_removed}건"
                    )
                    if purge_local:
                        msg += (
                            f"\n  - 삭제된 구독 기록: {del_result.subscriptions_removed}건"
                            f"\n  - 삭제된 폼 신청 기록: {del_result.form_record_removed}건"
                            f"\n  - 매트릭스에서 이 회원 행이 사라집니다 (거래 transactions 만 보존)"
                        )
                    else:
                        msg += "\n  - 구독, 거래, 폼 기록은 보존됨 - 매트릭스엔 '이전 회원' 등으로 계속 표시"
                    cache_op = ("discard", member.user_id)
                else:
                    result = deactivate_subscriber(
                        client, member.user_id, settings.group_name,
                    )
                    if not result.found:
                        msg = (
                            f"'{result.user_name}' 은 DSM 에 없습니다 - 이미 정리된 상태."
                        )
                    else:
                        msg = (
                            f"비활성화 완료: {result.user_name}\n"
                            f"  - 그룹 제거: "
                            f"{'예' if result.removed_from_group else '아니오 (이미 없음)'}"
                        )
                    cache_op = ("discard", member.user_id)
        except DsmAuthError as e:
            wx.CallAfter(self._member_action_failed, str(e))
            return
        except Exception as e:
            wx.CallAfter(self._member_action_failed, f"예상치 못한 오류: {e}")
            return

        # 구글시트 폼 응답 시트의 '상태' 컬럼 갱신 (best-effort, 인증 전이면 스킵)
        # 단, purge_local 로 매트릭스에서 완전히 제거하는 경우엔 시트 상태도 굳이 안 건드림.
        if not (action == "delete" and purge_local):
            msg += _format_sheet_status_note(
                push_form_status(member.user_id, "활성" if action == "activate" else "비활성")
            )
        wx.CallAfter(self._member_action_done, msg, cache_op)

    def _member_action_done(self, msg: str, cache_op=None) -> None:
        speak("작업 완료.")
        wx.MessageBox(msg, "DSM 작업 결과", wx.OK | wx.ICON_INFORMATION, self)
        self.activate_member_btn.Enable()
        self.deactivate_member_btn.Enable()
        self.delete_member_btn.Enable()
        # DSM 그룹 멤버 캐시 갱신 — DSM 멤버 가져오기 없이도 'DSM·정합' 컬럼이 맞게.
        if cache_op is not None and self._dsm_member_names is not None:
            op, uid = cache_op
            if op == "discard":
                u = uid.strip().lower()
                self._dsm_member_names = {
                    n for n in self._dsm_member_names if (n or "").strip().lower() != u
                }
            elif op == "add":
                self._dsm_member_names.add(uid)
        # 삭제·alias·구독 변경 후 매트릭스에 즉시 반영.
        self._reload()

    def _member_action_failed(self, message: str) -> None:
        speak("작업 실패.")
        wx.MessageBox(message, "DSM 작업 실패", wx.OK | wx.ICON_ERROR, self)
        self.activate_member_btn.Enable()
        self.deactivate_member_btn.Enable()
        self.delete_member_btn.Enable()

    # ---------- 토스 import 직후 신규 가입자 검출 ----------

    def _maybe_prompt_new_subscribers(self) -> None:
        """DSM 자료실 그룹과 비교해 활성화 누락(=신규 가입자) 회원을 알림.

        - DSM 설정 없음 → 조용히 종료
        - DSM 그룹 조회 실패 → 사유만 안내하고 종료
        - 후보 0명 → "신규 가입자 없음" 1회성 음성 안내 후 종료
        - 후보 ≥1명 → "확인하시겠습니까?" 확인 후 NewSubscriberDialog 오픈
        """
        settings = load_dsm_settings()
        if settings is None or not settings.is_complete:
            return  # DSM 설정 없으면 검출 자체를 스킵

        ans = wx.MessageBox(
            "토스 거래내역 가져오기가 끝났습니다.\n"
            "DSM 자료실 그룹과 비교해 활성화 누락된 신규 가입자가 있는지 확인하시겠습니까?\n"
            "(DSM 로그인이 필요합니다.)",
            "신규 가입자 확인",
            wx.YES_NO | wx.ICON_QUESTION,
            self,
        )
        if ans != wx.YES:
            return

        otp = ""
        if settings.use_2fa:
            with wx.TextEntryDialog(
                self,
                "DSM 2단계 인증 OTP 6자리를 입력하세요:",
                "OTP 코드 입력",
            ) as dlg:
                if dlg.ShowModal() != wx.ID_OK:
                    return
                otp = dlg.GetValue().strip()
            if not otp:
                return

        speak("DSM 자료실 그룹 확인 중.")
        threading.Thread(
            target=self._detect_new_subscribers_in_thread,
            args=(settings, otp),
            daemon=True,
        ).start()

    def _detect_new_subscribers_in_thread(self, settings, otp: str) -> None:
        try:
            with DsmClient(settings.url, verify_ssl=settings.verify_ssl) as client:
                client.login(settings.account, settings.password, otp_code=otp)
                members = client.list_group_members(settings.group_name)
        except DsmAuthError as e:
            wx.CallAfter(self._new_subscribers_failed, str(e))
            return
        except Exception as e:
            wx.CallAfter(self._new_subscribers_failed, f"예상치 못한 오류: {e}")
            return
        names = [m.get("name", "") for m in members]
        candidates = detect_new_subscribers(
            store=self.store,
            all_members=self.all_members,
            dsm_group_member_names=names,
        )
        wx.CallAfter(self._new_subscribers_done, candidates)

    def _new_subscribers_done(self, candidates: list) -> None:
        if not candidates:
            speak("신규 가입자가 없습니다.")
            wx.MessageBox(
                "DSM 자료실 그룹과 결제 활성 구독이 일치합니다 — 신규 가입자 없음.",
                "확인 결과", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        with NewSubscriberDialog(
            self,
            candidates=candidates,
            sorisem_session=self.session,
            sorisem_current_user_id=self.current_user_id,
            sorisem_members=self.all_members,
        ) as dlg:
            dlg.ShowModal()

    def _new_subscribers_failed(self, message: str) -> None:
        speak("DSM 그룹 조회 실패.")
        wx.MessageBox(
            f"DSM 자료실 그룹 조회 실패:\n{message}\n\n"
            "DSM 설정, 자격증명을 확인해 주세요.",
            "조회 실패", wx.OK | wx.ICON_ERROR, self,
        )

    def _on_view_others(self, _event=None) -> None:
        """단가표에 일치하지 않는 입금(1원 인증·찬조금 등) 별도 표시."""
        all_txns = self.store.all_transactions()
        others = [t for t in all_txns if lookup_months(t.amount) is None]
        if not others:
            wx.MessageBox(
                "단가표 외 입금이 없습니다.",
                "기타 입금",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        # 간단 표시 — 본격 다이얼로그는 후속 PR.
        lines = [f"{len(others)}건의 기타 입금:\n"]
        for t in sorted(others, key=lambda x: x.txn_at, reverse=True)[:50]:
            lines.append(
                f"  {t.txn_at.strftime('%Y-%m-%d %H:%M')}  "
                f"{t.payer_name:<10}  {t.amount:>10,}원"
            )
        if len(others) > 50:
            lines.append(f"  ... 외 {len(others) - 50}건")
        wx.MessageBox("\n".join(lines), "기타 입금", wx.OK | wx.ICON_INFORMATION, self)

    # ---------- 음성 ----------

    def _announce_open(self) -> None:
        pricing = ", ".join(f"{a:,}원={m}개월" for a, m in sorted(SUBSCRIPTION_PRICING.items()))
        speak(
            "자료실 구독비 관리 화면이 열렸습니다. "
            f"단가표: {pricing}. "
            "토스 거래내역 가져오기로 입금 데이터를 가져올 수 있습니다."
        )
