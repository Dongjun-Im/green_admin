"""회원 검색 대화상자 — 이름/아이디/닉네임 부분 일치 검색.

회원을 선택해 등급을 직접 변경할 수도 있다 (Ctrl+G 또는 "등급 변경" 버튼).
등급 변경은 단건 처리이며 사이트에 즉시 반영된다.

v1.0.2 신규:
  · 등급 필터 (Ctrl+1) — 레벨 5~9 + 전체 + 기타로 한 번에 좁히기
  · 다중 선택 일괄 변경 (Ctrl+B) — 체크된 회원을 한 번의 폼 POST 로 일괄 변경
"""
from __future__ import annotations

from typing import Optional

import requests
import wx

from config import LEVEL_LABELS, SELECTABLE_LEVELS, WITHDRAW_LEVEL
from core.admin_flags import AdminFlagsStore
from core.member_admin import MemberAdminAdapter
from core.member_notes import MemberNotesStore
from core.models import Member
from screen_reader import speak
from ui.item_text_ctrl import ItemTextCtrl
from ui.level_change_dialog import LevelChangeDialog
from ui.member_note_dialog import MemberNoteDialog


# 등급 필터 — 사이트 cl_level 매핑 (4=준,5=일반,6=우수,7=최우수,8=명예).
# 0~3 (손님/탈퇴/거부/대기) 과 9 (동호회관리자) 는 "기타" 로 통합.
FILTER_LEVELS: tuple[int, ...] = (4, 5, 6, 7, 8)


class MemberSearchDialog(wx.Dialog):
    def __init__(
        self,
        parent,
        members: list[Member],
        session: Optional[requests.Session] = None,
        admin_user_id: str = "",
        log_writer=None,
        undo_stack=None,
        level_history=None,
        blocklist=None,
    ):
        super().__init__(
            parent,
            title="회원 검색",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.all_members = members
        self.filtered: list[Member] = list(members)
        self.session = session
        self.admin_user_id = (admin_user_id or "").lower()
        self.log_writer = log_writer
        self.undo_stack = undo_stack
        self.level_history = level_history
        # 장기미접속 탈퇴자 재가입 차단 명단 (core.withdrawn_blocklist.WithdrawnBlocklist)
        self.blocklist = blocklist
        self.notes_store = MemberNotesStore()
        # 보유한 회원 ID 들의 메모를 한 번에 캐시 (검색 결과 렌더 빠르게)
        self._notes_cache = self.notes_store.get_many(
            [m.user_id for m in self.all_members]
        )
        # v1.0.4: 수동 동호회관리자 표시 — 사이트가 explicit cl_admin 필드를 안 쓰면
        # 자동 감지로는 운영진을 못 잡을 수 있어 수동 마킹을 영구 저장한다.
        # 회원 목록에 적용해 자동/수동 모두 is_admin=True 로 통일.
        self.admin_flags = AdminFlagsStore()
        manual_ids = self.admin_flags.all_ids()
        for m in self.all_members:
            if m.user_id in manual_ids:
                m.is_admin = True
        # 등급 변경된 회원 수 (호출자에게 캐시 갱신 신호)
        self.changed_count: int = 0
        # 등급 필터 — None = 전체, "기타" = 5~9 외, 정수 = 해당 레벨만
        self._level_filter: Optional[int | str] = None

        self._build_ui()
        # v1.0.5: 7개 버튼이 가로로 들어가 잘리는 일이 없도록 최소 폭 확대.
        self.SetMinSize(wx.Size(820, 620))
        self.Fit()
        self.Centre()
        wx.CallAfter(self._announce_initial)

    # ---------- UI ----------

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        label = wx.StaticText(panel, label="검색어(&S) — 아이디, 이름, 닉네임 부분 일치:")
        sizer.Add(label, 0, wx.ALL, 8)

        self.search_input = wx.TextCtrl(
            panel,
            value="",
            style=wx.TE_PROCESS_ENTER,
            name="검색어",
        )
        sizer.Add(self.search_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        # ---- 등급 필터 (v1.0.2) ----
        filter_sizer = wx.BoxSizer(wx.HORIZONTAL)
        filter_sizer.Add(
            wx.StaticText(panel, label="등급 필터(&F):"),
            0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 8,
        )
        self._filter_keys, filter_choices = self._build_filter_choices()
        self.filter_choice = wx.Choice(panel, choices=filter_choices, name="등급 필터")
        self.filter_choice.SetSelection(0)
        filter_sizer.Add(self.filter_choice, 1, wx.ALL | wx.EXPAND, 4)
        sizer.Add(filter_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)

        # v1.0.6: "일괄 변경" 버튼이 화면 하단에서 안 눌린다는 보고가 반복돼,
        # 다이얼로그 *상단* (필터 바로 아래) 에 큰 보조 버튼을 두 개 더 둔다.
        # 동일한 _on_bulk_change 핸들러를 호출 — 어느 버튼을 눌러도 같은 동작.
        top_action_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.top_bulk_btn = wx.Button(
            panel, wx.ID_ANY, ">> 현재 목록 일괄 등급 변경 (F2) <<"
        )
        self.top_bulk_btn.SetToolTip(
            "체크된 회원이 있으면 그들만, 없으면 현재 등급 필터 전체에 적용. "
            "단축키: F2 / Ctrl+B / Alt+B"
        )
        top_action_sizer.Add(self.top_bulk_btn, 1, wx.ALL | wx.EXPAND, 4)
        sizer.Add(top_action_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)

        list_label = wx.StaticText(
            panel,
            label="결과 목록(&L) — 스페이스로 체크, ↑/↓ 이동:",
        )
        sizer.Add(list_label, 0, wx.ALL, 8)

        # CheckListBox 로 다중 선택 (스크린리더 친화적: Space 토글)
        self.list_box = wx.CheckListBox(
            panel,
            choices=[self._format_entry(m) for m in self.all_members],
            name="회원 목록",
        )
        if self.all_members:
            self.list_box.SetSelection(0)
        sizer.Add(self.list_box, 1, wx.EXPAND | wx.ALL, 8)

        detail_label = wx.StaticText(panel, label="선택한 회원 상세(&D):")
        sizer.Add(detail_label, 0, wx.LEFT | wx.RIGHT, 8)

        self.detail = ItemTextCtrl(
            panel,
            value="",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_DONTWRAP,
            name="회원 상세",
        )
        sizer.Add(self.detail, 0, wx.EXPAND | wx.ALL, 8)

        # v1.0.5: 버튼이 한 줄에 7개 들어가면 다이얼로그 폭을 초과해 일부가
        # 잘리는 문제가 있어, 주요/보조 두 줄로 분리. 또한 WrapSizer 로 만들어
        # 폭이 좁아져도 자동으로 다음 줄로 넘어가게 한다.
        self.change_btn = wx.Button(panel, wx.ID_ANY, "등급 변경(&G)")
        # 일괄 등급 변경은 핵심 액션이라 더 잘 보이도록 라벨에 강조 표시.
        self.bulk_btn = wx.Button(
            panel, wx.ID_ANY, ">> 일괄 등급 변경(&B) <<"
        )
        # 선택한 회원을 사이트 등급 '탈퇴'(WITHDRAW_LEVEL) 로 내리는 전용 버튼.
        self.withdraw_btn = wx.Button(panel, wx.ID_ANY, "탈퇴 처리(&W)")
        self.withdraw_btn.SetToolTip(
            "선택한 회원을 사이트 등급 '탈퇴' 로 내립니다. 확인창에서 "
            "재가입 승인 차단 여부를 고를 수 있습니다. 단축키: Alt+W"
        )
        # v1.0.6: 버튼은 항상 활성. 세션이 없으면 클릭 시 안내만 띄움 — 비활성화
        # 상태가 "버튼이 안 눌린다" 라는 사용자 혼동을 만든 사례가 있어 변경.
        self.check_all_btn = wx.Button(panel, wx.ID_ANY, "모두 체크(&A)")
        self.uncheck_all_btn = wx.Button(panel, wx.ID_ANY, "체크 해제(&U)")
        self.admin_toggle_btn = wx.Button(panel, wx.ID_ANY, "관리자 표시 토글(&M)")
        self.note_btn = wx.Button(panel, wx.ID_ANY, "메모 / 태그(&N)")
        close_btn = wx.Button(panel, wx.ID_CLOSE, "닫기(&C)")

        # v1.0.6: WrapSizer 가 일부 환경에서 버튼을 화면 밖으로 미루는 사례가
        # 있어, 단순 BoxSizer 두 줄 + EXPAND 로 대체.
        primary_sizer = wx.BoxSizer(wx.HORIZONTAL)
        for b in (self.bulk_btn, self.change_btn, self.withdraw_btn, self.note_btn, close_btn):
            primary_sizer.Add(b, 1, wx.ALL | wx.EXPAND, 4)
        sizer.Add(primary_sizer, 0, wx.EXPAND | wx.ALL, 4)

        secondary_sizer = wx.BoxSizer(wx.HORIZONTAL)
        for b in (self.check_all_btn, self.uncheck_all_btn, self.admin_toggle_btn):
            secondary_sizer.Add(b, 1, wx.ALL | wx.EXPAND, 4)
        sizer.Add(secondary_sizer, 0, wx.EXPAND | wx.ALL, 4)

        panel.SetSizer(sizer)

        self.search_input.Bind(wx.EVT_TEXT, self._on_search_change)
        self.search_input.Bind(wx.EVT_TEXT_ENTER, self._on_enter)
        self.filter_choice.Bind(wx.EVT_CHOICE, self._on_filter_change)
        self.list_box.Bind(wx.EVT_LISTBOX, self._on_select)
        self.list_box.Bind(wx.EVT_LISTBOX_DCLICK, self._on_change_level)
        self.list_box.Bind(wx.EVT_CHECKLISTBOX, self._on_check_toggle)
        self.change_btn.Bind(wx.EVT_BUTTON, self._on_change_level)
        self.bulk_btn.Bind(wx.EVT_BUTTON, self._on_bulk_change)
        self.withdraw_btn.Bind(wx.EVT_BUTTON, self._on_withdraw)
        # 상단 버튼도 동일 핸들러
        self.top_bulk_btn.Bind(wx.EVT_BUTTON, self._on_bulk_change)
        self.check_all_btn.Bind(wx.EVT_BUTTON, self._on_check_all)
        self.uncheck_all_btn.Bind(wx.EVT_BUTTON, self._on_uncheck_all)
        self.admin_toggle_btn.Bind(wx.EVT_BUTTON, self._on_admin_toggle)
        self.note_btn.Bind(wx.EVT_BUTTON, self._on_note)
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

        self.search_input.SetFocus()

        # v1.0.5: 일괄 등급 변경 버튼이 어떤 환경에서 안 눌린다는 사용자 피드백.
        # 포커스 위치/EVT_CHAR_HOOK 가로채기에 영향받지 않도록 명시적 액셀러레이터
        # 테이블을 다이얼로그에 부착 — Ctrl+B / F2 / Alt+B 모두 일괄 변경 발사.
        bulk_id = self.bulk_btn.GetId()
        change_id = self.change_btn.GetId()
        admin_id = self.admin_toggle_btn.GetId()
        check_all_id = self.check_all_btn.GetId()
        uncheck_id = self.uncheck_all_btn.GetId()
        withdraw_id = self.withdraw_btn.GetId()
        accels = [
            (wx.ACCEL_CTRL, ord("B"), bulk_id),
            (wx.ACCEL_NORMAL, wx.WXK_F2, bulk_id),
            (wx.ACCEL_CTRL, ord("G"), change_id),
            (wx.ACCEL_CTRL, ord("M"), admin_id),
            (wx.ACCEL_ALT, ord("B"), bulk_id),
            (wx.ACCEL_ALT, ord("A"), check_all_id),
            (wx.ACCEL_ALT, ord("U"), uncheck_id),
            (wx.ACCEL_ALT, ord("M"), admin_id),
            (wx.ACCEL_ALT, ord("W"), withdraw_id),
        ]
        self.SetAcceleratorTable(wx.AcceleratorTable(accels))

    def _announce_initial(self) -> None:
        admin_cnt = sum(1 for m in self.all_members if m.is_admin)
        msg = f"회원 검색. 총 {len(self.all_members)}명"
        if admin_cnt:
            msg += f", 그 중 동호회관리자 {admin_cnt}명"
        msg += (
            ". 등급 필터는 알트 F, 스페이스로 체크 토글, "
            "알트 A 는 현재 목록 전체 체크. "
            "알트 B 는 일괄 등급 변경 — 체크된 회원이 없으면 현재 필터 전체에 적용."
        )
        speak(msg)
        if self.all_members:
            self._update_detail(0)

    # ---------- 필터 / 표시 ----------

    def _build_filter_choices(self) -> tuple[list, list[str]]:
        """[(키, 라벨)] — 키는:
        · None        : 전체
        · 정수(5~9)   : 해당 레벨 (관리자 제외)
        · "관리자"    : is_admin=True 만
        · "기타"      : 5~9 외 + 관리자도 아닌 회원

        v1.0.4: "동호회관리자" / "기타" 필터는 0명이어도 항상 노출 — 관리자
        그룹이 보이지 않는다는 사용자 피드백 반영. 인원수는 라벨로 함께 표시.
        """
        keys: list = [None]
        labels: list[str] = [f"전체 ({len(self.all_members)}명)"]
        # 5~9 — 관리자는 별도 그룹으로 빼서 명예회원과 섞이지 않게.
        for lv in FILTER_LEVELS:
            cnt = sum(
                1 for m in self.all_members
                if m.level == lv and not m.is_admin
            )
            keys.append(lv)
            labels.append(f"{LEVEL_LABELS.get(lv, str(lv))} (레벨 {lv}) — {cnt}명")
        # 동호회관리자 — 0명이어도 항상 표시
        admin_cnt = sum(1 for m in self.all_members if m.is_admin)
        keys.append("관리자")
        labels.append(f"동호회관리자 — {admin_cnt}명")
        # 기타 — 0명이어도 항상 표시
        other_cnt = sum(
            1 for m in self.all_members
            if m.level not in FILTER_LEVELS and not m.is_admin
        )
        keys.append("기타")
        labels.append(f"기타 (가입단계 등) — {other_cnt}명")
        return keys, labels

    def _format_entry(self, m: Member, idx: Optional[int] = None) -> str:
        # 체크 마커 — 시각/스크린리더 모두 즉시 알 수 있도록 텍스트로 직접 표기.
        check_mark = "[ ]"
        list_box = getattr(self, "list_box", None)
        if list_box is not None and idx is not None and idx < list_box.GetCount():
            try:
                check_mark = "[V]" if list_box.IsChecked(idx) else "[ ]"
            except Exception:
                check_mark = "[ ]"

        # v1.0.2: 동호회관리자(is_admin=True) 는 cl_level 과 무관하게
        # "동호회관리자" 로 표시해 명예회원과 즉시 구분.
        if m.is_admin:
            level_label = "동호회관리자"
            level_part = f"{level_label}(레벨 {m.level})"
        else:
            level_label = LEVEL_LABELS.get(m.level, f"레벨 {m.level}")
            level_part = f"{level_label}(레벨 {m.level})"

        note = self._notes_cache.get(m.user_id)
        marker = " ★" if note and note.has_content else ""
        return (
            f"{check_mark} {m.user_id} / {m.name} / {m.nickname} / "
            f"{level_part}{marker}"
        )

    def _apply_filters(self) -> None:
        """검색어 + 등급 필터를 동시에 적용해 self.filtered 와 list_box 를 갱신."""
        query = self.search_input.GetValue().strip().lower()
        result: list[Member] = []
        for m in self.all_members:
            # 등급 필터
            f = self._level_filter
            if f is None:
                pass
            elif f == "관리자":
                if not m.is_admin:
                    continue
            elif f == "기타":
                if m.is_admin or m.level in FILTER_LEVELS:
                    continue
            else:
                # 정수 레벨 — 관리자는 별도 그룹이므로 제외
                if m.is_admin or m.level != f:
                    continue
            # 검색어
            if query and not (
                query in m.user_id.lower()
                or query in (m.name or "").lower()
                or query in (m.nickname or "").lower()
            ):
                continue
            result.append(m)

        self.filtered = result
        self.list_box.Set([self._format_entry(m, i) for i, m in enumerate(result)])
        if result:
            self.list_box.SetSelection(0)
            self._update_detail(0)
        else:
            self.detail.SetValue("(일치하는 회원 없음)")
            speak("검색 결과 없음")

    def _on_search_change(self, event):
        self._apply_filters()
        # 결과 개수 발화
        wx.CallLater(200, lambda: speak(f"{len(self.filtered)}명"))

    def _on_filter_change(self, event):
        idx = self.filter_choice.GetSelection()
        if 0 <= idx < len(self._filter_keys):
            self._level_filter = self._filter_keys[idx]
        else:
            self._level_filter = None
        self._apply_filters()
        label = self.filter_choice.GetString(idx) if idx >= 0 else "전체"
        speak(f"필터: {label}. {len(self.filtered)}명")

    def _on_enter(self, event):
        if self.filtered:
            self.list_box.SetFocus()

    def _on_select(self, event):
        idx = self.list_box.GetSelection()
        if idx >= 0:
            self._update_detail(idx)

    def _on_check_toggle(self, event):
        # 체크박스 토글 시 항목 텍스트의 [V]/[ ] 마커도 즉시 갱신.
        idx = event.GetSelection()
        if 0 <= idx < len(self.filtered):
            m = self.filtered[idx]
            self.list_box.SetString(idx, self._format_entry(m, idx))
            checked = self.list_box.IsChecked(idx)
            state = "체크" if checked else "체크 해제"
            speak(f"{m.user_id} {state}. 현재 {self._checked_count()}명 선택")

    def _checked_count(self) -> int:
        return sum(
            1 for i in range(self.list_box.GetCount()) if self.list_box.IsChecked(i)
        )

    def _checked_members(self) -> list[Member]:
        return [
            self.filtered[i]
            for i in range(self.list_box.GetCount())
            if self.list_box.IsChecked(i) and i < len(self.filtered)
        ]

    def _update_detail(self, idx: int) -> None:
        if not (0 <= idx < len(self.filtered)):
            return
        m = self.filtered[idx]
        last = m.last_login_date.isoformat() if m.last_login_date else "알 수 없음"
        join = m.join_date.isoformat() if m.join_date else "알 수 없음"
        if m.is_admin:
            level_text = f"동호회관리자 (cl_level={m.level})"
        else:
            level_text = (
                f"{LEVEL_LABELS.get(m.level, f'레벨 {m.level}')} (레벨 {m.level})"
            )
        lines = [
            f"아이디: {m.user_id}",
            f"이름: {m.name}",
            f"닉네임: {m.nickname}",
            f"등급: {level_text}",
            f"마지막 접속일: {last}",
            f"가입일: {join}",
            f"접속수: {m.login_count if m.login_count is not None else '알 수 없음'}",
        ]
        note = self._notes_cache.get(m.user_id)
        if note and note.has_content:
            if note.tags:
                lines.append(f"태그: {', '.join(note.tags)}")
            if note.note:
                lines.append(f"메모: {note.note}")
        self.detail.SetValue("\n".join(lines))
        speak(self._format_entry(m, idx))

    def _refresh_filter_counts(self) -> None:
        """등급 변경 후 필터 드롭다운의 인원수 라벨도 갱신."""
        sel_key = self._level_filter
        keys, labels = self._build_filter_choices()
        self._filter_keys = keys
        self.filter_choice.Set(labels)
        try:
            new_idx = keys.index(sel_key)
        except ValueError:
            new_idx = 0
            self._level_filter = None
        self.filter_choice.SetSelection(new_idx)

    # ---------- 키보드 ----------

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        code = event.GetKeyCode()
        mods = event.GetModifiers()
        if code == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CLOSE)
            return
        # Ctrl+G : 단건 등급 변경
        if (mods & wx.MOD_CONTROL) and code in (ord("G"), ord("g")):
            self._on_change_level(event)
            return
        # Ctrl+B : 선택 일괄 변경
        if (mods & wx.MOD_CONTROL) and code in (ord("B"), ord("b")):
            self._on_bulk_change(event)
            return
        # Ctrl+N : 메모/태그
        if (mods & wx.MOD_CONTROL) and code in (ord("N"), ord("n")):
            self._on_note(event)
            return
        # Ctrl+1 : 필터에 포커스
        if (mods & wx.MOD_CONTROL) and code == ord("1"):
            self.filter_choice.SetFocus()
            return
        # Ctrl+Shift+A : 현재 목록 전체 체크 (Alt+A 보조 단축키)
        if (mods & wx.MOD_CONTROL) and (mods & wx.MOD_SHIFT) and code in (ord("A"), ord("a")):
            self._on_check_all()
            return
        # Ctrl+Shift+U : 체크 전체 해제
        if (mods & wx.MOD_CONTROL) and (mods & wx.MOD_SHIFT) and code in (ord("U"), ord("u")):
            self._on_uncheck_all()
            return
        # Space : 목록에 포커스가 있으면 현재 항목 체크 토글 (CheckListBox 이벤트가
        # 누락되는 환경 대비 명시적 핸들러).
        if code == wx.WXK_SPACE and self.list_box.HasFocus():
            idx = self.list_box.GetSelection()
            if 0 <= idx < self.list_box.GetCount():
                self.list_box.Check(idx, not self.list_box.IsChecked(idx))
                # 마커 텍스트 + 음성 안내
                if idx < len(self.filtered):
                    m = self.filtered[idx]
                    self.list_box.SetString(idx, self._format_entry(m, idx))
                    state = "체크" if self.list_box.IsChecked(idx) else "체크 해제"
                    speak(
                        f"{m.user_id} {state}. 현재 {self._checked_count()}명 선택"
                    )
                return
        event.Skip()

    # ---------- 체크 보조 ----------

    def _on_check_all(self, event=None) -> None:
        for i in range(self.list_box.GetCount()):
            self.list_box.Check(i, True)
        # 마커 텍스트도 함께 갱신
        self._refresh_all_entries()
        speak(f"현재 목록 {self.list_box.GetCount()}명 모두 체크")

    def _on_uncheck_all(self, event=None) -> None:
        for i in range(self.list_box.GetCount()):
            self.list_box.Check(i, False)
        self._refresh_all_entries()
        speak("체크 해제 완료")

    def _refresh_all_entries(self) -> None:
        """필터링된 모든 항목의 텍스트(체크 마커 포함) 를 갱신."""
        for i, m in enumerate(self.filtered):
            self.list_box.SetString(i, self._format_entry(m, i))

    def _modal_msg(
        self, message: str, caption: str, icon: int = wx.ICON_INFORMATION
    ) -> None:
        """parent 를 self 로 명시한 정보용 모달.

        wx.MessageBox 를 부모 없이 호출하면 일부 환경에서 다이얼로그 뒤에
        숨거나 포커스를 못 받는 사례가 있어 헬퍼로 래핑.
        """
        dlg = wx.MessageDialog(self, message, caption, wx.OK | icon)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_admin_toggle(self, event=None) -> None:
        """체크된 회원의 동호회관리자 표시를 토글한다.

        혼합(일부 admin, 일부 아님) 일 때는 모두 admin 으로 표시 (마킹 우선).
        사이트(소리샘) 에 cl_level=10 으로 등급 변경을 POST 하고, 성공 시에만
        admin_flags.json 과 메모리 상태를 갱신한다. 해제 시에는 표시 직전 등급으로
        복원한다 (admin_flags 에 prev_level 로 저장된 값 사용, 없으면 6=일반회원).
        """
        if self.session is None:
            speak("로그인 세션이 없어 동호회관리자 표시를 변경할 수 없습니다.")
            wx.MessageBox(
                "로그인 세션이 없어 사이트에 반영할 수 없습니다.\n"
                "한 번 로그아웃 후 다시 로그인하면 해결됩니다.",
                "세션 없음",
                wx.OK | wx.ICON_WARNING,
            )
            return

        members = self._checked_members()
        if not members:
            speak("체크된 회원이 없습니다. 스페이스로 체크해 주세요.")
            wx.MessageBox(
                "동호회관리자 표시를 토글할 회원을 먼저 체크해 주세요.\n"
                "스페이스로 체크하거나 알트 A 로 현재 목록 전체 체크할 수 있습니다.",
                "선택 필요",
                wx.OK | wx.ICON_INFORMATION,
            )
            return

        # 본인 계정 보호: 자기 자신을 잘못 강등시켜 권한을 잃지 않도록.
        if any(m.user_id.lower() == self.admin_user_id for m in members):
            speak("본인 계정은 안전을 위해 이 화면에서 동호회관리자 표시를 변경할 수 없습니다.")
            wx.MessageBox(
                "본인 계정의 동호회관리자 표시는 이 화면에서 변경할 수 없습니다.\n"
                "관리자 권한 상실을 막기 위한 안전 장치입니다.",
                "변경 불가",
                wx.OK | wx.ICON_INFORMATION,
            )
            return

        all_admin = all(m.is_admin for m in members)
        admin_adapter = MemberAdminAdapter(self.session, dry_run=False)
        ADMIN_LEVEL = 9   # 사이트 cl_level select 의 동호회관리자 옵션 값
        DEFAULT_RESTORE_LEVEL = 5  # 일반회원 — prev_level 정보가 없을 때

        if all_admin:
            # 해제: 각자 직전 등급으로 복원
            level_map: dict[str, int] = {}
            restore_levels: dict[str, int] = {}
            for m in members:
                prev = self.admin_flags.get_prev_level(m.user_id)
                target = prev if prev is not None else DEFAULT_RESTORE_LEVEL
                level_map[m.user_id] = target
                restore_levels[m.user_id] = target
            action = "해제"
            action_label = f"동호회관리자 해제 → 직전 등급 복원 ({len(members)}명)"
        else:
            # 표시: 모두 레벨 10 으로
            level_map = {m.user_id: ADMIN_LEVEL for m in members}
            restore_levels = {}
            action = "표시"
            action_label = f"동호회관리자 표시 → 레벨 10 ({len(members)}명)"

        result = admin_adapter.bulk_apply(level_map, action_label=action_label)
        if not result.success:
            speak("사이트에 반영하지 못했습니다.")
            self._modal_msg(
                f"사이트 반영 실패: {result.message}\n"
                f"응답 일부: {result.response_snippet[:200] if result.response_snippet else '(없음)'}\n"
                "로컬 표시는 변경되지 않았습니다.",
                "오류",
                wx.ICON_ERROR,
            )
            return

        # 사이트 POST 성공 — 로컬 상태 갱신
        if all_admin:
            for m in members:
                target = restore_levels[m.user_id]
                m.is_admin = False
                m.level = target
                m.level_label = LEVEL_LABELS.get(target, str(target))
                self.admin_flags.unmark(m.user_id)
            speak(f"{len(members)}명 동호회관리자 표시 해제, 직전 등급으로 복원")
        else:
            for m in members:
                # 표시 직전 등급을 admin_flags 에 저장 — 해제 시 이 값으로 복원.
                self.admin_flags.mark(m.user_id, prev_level=m.level)
                m.is_admin = True
                m.level = ADMIN_LEVEL
                m.level_label = LEVEL_LABELS.get(ADMIN_LEVEL, str(ADMIN_LEVEL))
            speak(f"{len(members)}명 동호회관리자 표시, 사이트 레벨 10 반영")

        # 화면/필터 갱신 — 표시가 바뀌면 그룹 이동도 즉시 반영
        self._refresh_filter_counts()
        self._apply_filters()

        wx.MessageBox(
            f"{len(members)}명을 동호회관리자로 {action}하고 사이트에 반영했습니다.\n"
            f"data/admin_flags.json 에 저장되어 다음 새로고침에도 유지됩니다.",
            "동호회관리자 표시 토글",
            wx.OK | wx.ICON_INFORMATION,
        )

    # ---------- 메모/태그 ----------

    def _on_note(self, event=None) -> None:
        idx = self.list_box.GetSelection()
        if idx < 0 or idx >= len(self.filtered):
            speak("회원을 먼저 선택해 주세요.")
            return
        member = self.filtered[idx]
        dlg = MemberNoteDialog(self, member, self.notes_store)
        dlg.ShowModal()
        if dlg.changed:
            updated = self.notes_store.get(member.user_id)
            if updated is None:
                self._notes_cache.pop(member.user_id, None)
            else:
                self._notes_cache[member.user_id] = updated
            self.list_box.SetString(idx, self._format_entry(member, idx))
            self._update_detail(idx)
        dlg.Destroy()

    # ---------- 단건 등급 변경 ----------

    def _on_change_level(self, event=None) -> None:
        if self.session is None:
            speak("로그인 세션이 없어 등급 변경을 진행할 수 없습니다.")
            wx.MessageBox(
                "로그인 세션이 없어 등급 변경을 진행할 수 없습니다.\n"
                "한 번 로그아웃 후 다시 로그인하면 해결됩니다.",
                "세션 없음",
                wx.OK | wx.ICON_WARNING,
            )
            return
        idx = self.list_box.GetSelection()
        if idx < 0 or idx >= len(self.filtered):
            speak("회원을 먼저 선택해 주세요.")
            return
        member = self.filtered[idx]

        if member.user_id.lower() == self.admin_user_id:
            speak("본인의 등급은 안전을 위해 이 화면에서 변경할 수 없습니다.")
            wx.MessageBox(
                "본인 계정의 등급은 이 화면에서 변경할 수 없습니다.\n"
                "관리자 권한 상실을 막기 위한 안전 장치입니다.",
                "변경 불가",
                wx.OK | wx.ICON_INFORMATION,
            )
            return

        dlg = LevelChangeDialog(self, member)
        result = dlg.ShowModal()
        new_level = dlg.new_level
        dlg.Destroy()
        if result != wx.ID_OK or new_level is None:
            return

        self._apply_level_change(member, new_level)

    def _apply_level_change(
        self,
        member: Member,
        new_level: int,
        *,
        source: str = "manual",
        reason: str = "검색에서 수동 변경",
        undo_label: str | None = None,
        action_word: str | None = None,
    ) -> bool:
        """선택 회원의 등급을 사이트에 즉시 반영. 성공 시 True.

        source/reason 은 등급 이력·로그에 남길 출처/사유 문구.
        action_word 가 주어지면 음성·로그의 '강등/승급' 대신 그 단어를 쓴다 (예: "탈퇴 처리").
        """
        from_level = member.level
        admin = MemberAdminAdapter(self.session, dry_run=False)
        result = admin.change_level(member, new_level)

        def _dump_lines() -> str:
            d = getattr(result, "debug", None) or {}
            if not d:
                return ""
            return "\n\n[진단용 저장 파일]\n" + "\n".join(f"· {k}: {v}" for k, v in d.items())

        if not result.success:
            speak("등급 변경에 실패했습니다.")
            self._modal_msg(
                f"등급 변경 실패: {result.message}\n"
                f"응답 일부: {result.response_snippet[:200]}"
                f"{_dump_lines()}",
                "오류",
                wx.ICON_ERROR,
            )
            return False
        if getattr(result, "verified", None) is None and _dump_lines():
            # 변경 요청은 보냈으나 재조회로 반영 여부를 확인 못 함 — 사용자에게 경고.
            speak("변경 요청은 보냈지만 사이트 반영 여부를 확인하지 못했습니다.")
            self._modal_msg(
                f"{result.message}\n\n웹 브라우저에서 해당 회원 등급을 직접 확인해 주세요."
                f"{_dump_lines()}",
                "확인 필요",
                wx.ICON_WARNING,
            )

        # 사이트 폼의 실제 옵션 값과 라벨로 모델을 갱신해야 웹·앱 표시가 일치한다.
        # (LEVEL_LABELS 가 사이트 스킨의 cl_level 매핑과 다른 경우 대비)
        site_level = getattr(result, "effective_levels", {}).get(member.user_id)
        site_label = getattr(result, "effective_labels", {}).get(member.user_id)
        if isinstance(site_level, int):
            member.level = site_level
        else:
            member.level = new_level
        if site_label:
            member.level_label = site_label
        else:
            member.level_label = LEVEL_LABELS.get(member.level, str(member.level))
        # v1.0.3: 등급을 변경했으면 동호회관리자 그룹에서도 빠져야 한다.
        # 메모리 플래그 + 영구 저장(admin_flags.json) 모두 클리어.
        if member.is_admin:
            member.is_admin = False
            self.admin_flags.unmark(member.user_id)
        self.changed_count += 1

        # to_level 은 사이트가 실제로 받아들인 값을 우선 사용 — undo/이력의 일관성.
        actual_to_level = member.level if isinstance(member.level, int) else new_level

        if self.undo_stack is not None:
            try:
                from core.undo_stack import UndoItem
                self.undo_stack.push(
                    label=undo_label or f"수동 등급 변경 ({member.user_id})",
                    items=[UndoItem(
                        user_id=member.user_id,
                        nickname=member.nickname,
                        from_level=from_level,
                        to_level=actual_to_level,
                    )],
                )
            except Exception:
                pass

        if self.level_history is not None:
            try:
                self.level_history.record(
                    user_id=member.user_id,
                    nickname=member.nickname,
                    from_level=from_level,
                    to_level=actual_to_level,
                    source=source,
                    reason=reason,
                    actor=self.admin_user_id,
                )
            except Exception:
                pass

        if self.log_writer is not None:
            try:
                from core.models import AdjustmentItem
                if action_word:
                    direction = action_word
                else:
                    direction = "수동 강등" if new_level < from_level else "수동 승급"
                item = AdjustmentItem(
                    member=member,
                    action="demote",
                    from_level=from_level,
                    to_level=new_level,
                    reason=f"{direction} (관리자 {self.admin_user_id})",
                )
                self.log_writer.write_action(item, result)
            except Exception:
                pass

        new_label = LEVEL_LABELS.get(new_level, str(new_level))
        # v1.0.3: 단건 변경 후에도 현재 필터에서 벗어나면 목록에서 사라져야 함.
        self._refresh_filter_counts()
        self._apply_filters()
        if action_word:
            speak(f"{member.user_id} 회원을 {action_word} 했습니다.")
        else:
            speak(
                f"{member.user_id} 회원을 "
                f"{LEVEL_LABELS.get(from_level, from_level)} 에서 "
                f"{new_label} 로 변경했습니다."
            )
        return True

    # ---------- 탈퇴 처리 (단건 + 일괄) ----------

    def _on_withdraw(self, event=None) -> None:
        """선택한 회원(또는 체크한 여러 회원)을 사이트 등급 '탈퇴' 로 처리.

        · 체크박스로 표시된 회원이 한 명 이상이면 그 회원 전원을 한 번에 처리(일괄).
        · 체크된 회원이 없으면 현재 강조된(↑/↓로 선택한) 회원 한 명만 처리(단건).
        · 확인창에서 '재가입 승인 차단' 체크박스를 켜면 처리된 회원들이 모두 장기
          미접속 탈퇴자 명단에 들어가, 같은 아이디로 재가입 신청해도 승인이 막힌다.
        """
        speak("탈퇴 처리 버튼")
        if self.session is None:
            speak("로그인 세션이 없어 탈퇴 처리를 진행할 수 없습니다.")
            wx.MessageBox(
                "로그인 세션이 없어 탈퇴 처리를 진행할 수 없습니다.\n"
                "한 번 로그아웃 후 다시 로그인하면 해결됩니다.",
                "세션 없음", wx.OK | wx.ICON_WARNING,
            )
            return

        # 1) 처리 대상 수집 — 체크된 회원 우선, 없으면 현재 강조된 한 명.
        checked = self._checked_members()
        if checked:
            members: list[Member] = list(checked)
            mode = "bulk"
        else:
            idx = self.list_box.GetSelection()
            if idx < 0 or idx >= len(self.filtered):
                speak("탈퇴 처리할 회원을 먼저 선택하거나 스페이스로 체크해 주세요.")
                wx.MessageBox(
                    "목록에서 회원을 ↑/↓ 로 강조하거나 스페이스로 체크한 뒤 다시 누르세요.",
                    "선택 필요", wx.OK | wx.ICON_INFORMATION, self,
                )
                return
            members = [self.filtered[idx]]
            mode = "single"

        # 2) 본인 계정·이미 탈퇴 상태인 회원 제외.
        admin_id = self.admin_user_id

        def _is_withdrawn(m: Member) -> bool:
            # 매핑이 어긋난 스킨에서도 안전하도록 라벨 텍스트와 정수 둘 다 확인.
            label = (getattr(m, "level_label", "") or "").strip()
            return "탈퇴" in label or m.level == WITHDRAW_LEVEL

        skipped_self = [m for m in members if m.user_id.lower() == admin_id]
        already = [m for m in members if m.user_id.lower() != admin_id and _is_withdrawn(m)]
        targets = [
            m for m in members
            if m.user_id.lower() != admin_id and not _is_withdrawn(m)
        ]

        if not targets:
            speak("탈퇴 처리할 회원이 없습니다.")
            parts = []
            if skipped_self:
                parts.append(f"본인 계정 {len(skipped_self)}명 제외")
            if already:
                parts.append(f"이미 탈퇴 상태 {len(already)}명 제외")
            tail = " · ".join(parts) if parts else "선택된 회원이 없습니다."
            wx.MessageBox(
                f"탈퇴 처리할 회원이 없습니다.\n({tail})",
                "처리 불필요", wx.OK | wx.ICON_INFORMATION, self,
            )
            return

        # 3) 확인창 — 재가입 차단 체크박스 포함.
        wd_label = LEVEL_LABELS.get(WITHDRAW_LEVEL, "탈퇴")
        if mode == "single":
            m0 = targets[0]
            nick = m0.nickname or m0.name or m0.user_id
            cur_label = (getattr(m0, "level_label", "") or
                         LEVEL_LABELS.get(m0.level, str(m0.level)))
            msg = (
                f"{m0.user_id} ({nick}) 회원을\n"
                f"{cur_label} → {wd_label} 로 처리합니다.\n\n"
                "이 작업은 사이트에 즉시 반영됩니다. 계속하시겠습니까?"
            )
            title = "탈퇴 처리 최종 확인"
        else:
            sample = ", ".join(m.user_id for m in targets[:5])
            if len(targets) > 5:
                sample += f", … 외 {len(targets) - 5}명"
            extra_lines = []
            if skipped_self:
                extra_lines.append(f"(본인 계정 {len(skipped_self)}명 제외)")
            if already:
                extra_lines.append(f"(이미 탈퇴 상태 {len(already)}명 제외)")
            extra = ("\n" + "\n".join(extra_lines)) if extra_lines else ""
            msg = (
                f"체크된 {len(targets)}명을 일괄 '{wd_label}' 처리합니다.\n"
                f"대상: {sample}{extra}\n\n"
                "이 작업은 사이트에 즉시 반영되며 되돌리려면 Ctrl+Z 또는 수동 처리가 필요합니다.\n"
                "계속하시겠습니까?"
            )
            title = f"일괄 탈퇴 처리 최종 확인 ({len(targets)}명)"

        dlg = wx.RichMessageDialog(
            self, msg, title,
            wx.YES_NO | wx.ICON_WARNING | wx.NO_DEFAULT,
        )
        dlg.SetYesNoLabels("예 (탈퇴 처리)", "아니오 (취소)")
        block_label = (
            "이 회원의 재가입 승인을 막기 (장기미접속 탈퇴자 명단에 추가)"
            if mode == "single"
            else f"이 {len(targets)}명의 재가입 승인을 막기 (장기미접속 탈퇴자 명단에 추가)"
        )
        dlg.ShowCheckBox(block_label, checked=False)
        speak(
            f"{len(targets)}명 탈퇴 처리 확인창입니다. 재가입 차단 체크박스를 확인한 뒤 예를 누르세요."
            if mode == "bulk"
            else "탈퇴 처리 확인창입니다. 재가입 차단 체크박스를 확인한 뒤 예를 누르세요."
        )
        ans = dlg.ShowModal()
        block_rejoin = dlg.IsCheckBoxChecked()
        dlg.Destroy()
        if ans != wx.ID_YES:
            speak("탈퇴 처리를 취소했습니다.")
            return

        # 4) 실행 — 단건은 _apply_level_change, 일괄은 전용 핸들러.
        if mode == "single":
            m0 = targets[0]
            ok = self._apply_level_change(
                m0, WITHDRAW_LEVEL,
                source="manual_withdraw",
                reason="수동 탈퇴 처리" + (" + 재가입 차단" if block_rejoin else ""),
                undo_label=f"수동 탈퇴 처리 ({m0.user_id})",
                action_word="탈퇴 처리",
            )
            if not ok:
                return
            if block_rejoin:
                self._add_to_blocklist([m0], note="수동 탈퇴 처리")
            return

        # 일괄 처리
        self._apply_bulk_withdraw(targets, block_rejoin=block_rejoin)

    def _apply_bulk_withdraw(self, targets: list[Member], block_rejoin: bool) -> None:
        """체크된 여러 회원을 한 번의 POST 로 일괄 탈퇴 처리."""
        speak(f"{len(targets)}명 탈퇴 처리 중")
        admin = MemberAdminAdapter(self.session, dry_run=False)
        level_map = {m.user_id: WITHDRAW_LEVEL for m in targets}
        result = admin.bulk_apply(
            level_map,
            action_label=f"일괄 탈퇴 처리 ({len(targets)}명)",
        )

        def _dump_lines() -> str:
            d = getattr(result, "debug", None) or {}
            if not d:
                return ""
            return "\n\n[진단용 저장 파일]\n" + "\n".join(f"· {k}: {v}" for k, v in d.items())

        if not result.success:
            speak("일괄 탈퇴 처리에 실패했습니다.")
            self._modal_msg(
                f"일괄 탈퇴 처리 실패: {result.message}\n"
                f"응답 일부: {result.response_snippet[:200]}"
                f"{_dump_lines()}",
                "오류", wx.ICON_ERROR,
            )
            return

        if getattr(result, "verified", None) is None and _dump_lines():
            speak("일괄 변경 요청은 보냈지만 사이트 반영 여부를 확인하지 못했습니다.")
            self._modal_msg(
                f"{result.message}\n\n웹 브라우저에서 회원 등급을 직접 확인해 주세요."
                f"{_dump_lines()}",
                "확인 필요", wx.ICON_WARNING,
            )

        # 모델 갱신 — 사이트가 실제로 받아들인 옵션 값/라벨을 우선 사용.
        eff_levels = getattr(result, "effective_levels", {}) or {}
        eff_labels = getattr(result, "effective_labels", {}) or {}
        from_levels: dict[str, int] = {}
        to_levels: dict[str, int] = {}
        unmark_ids: list[str] = []
        for m in targets:
            from_levels[m.user_id] = m.level
            actual_level = eff_levels.get(m.user_id, WITHDRAW_LEVEL)
            actual_label = eff_labels.get(m.user_id) or LEVEL_LABELS.get(actual_level, "탈퇴")
            m.level = actual_level if isinstance(actual_level, int) else WITHDRAW_LEVEL
            m.level_label = actual_label
            to_levels[m.user_id] = m.level
            if m.is_admin:
                m.is_admin = False
                unmark_ids.append(m.user_id)
        if unmark_ids:
            self.admin_flags.unmark_many(unmark_ids)
        self.changed_count += len(targets)

        # Undo 스택 (한 묶음으로)
        if self.undo_stack is not None:
            try:
                from core.undo_stack import UndoItem
                self.undo_stack.push(
                    label=f"일괄 탈퇴 처리 ({len(targets)}명)",
                    items=[
                        UndoItem(
                            user_id=m.user_id,
                            nickname=m.nickname,
                            from_level=from_levels[m.user_id],
                            to_level=to_levels[m.user_id],
                        )
                        for m in targets
                    ],
                )
            except Exception:
                pass

        # 영구 이력 (배치)
        if self.level_history is not None:
            try:
                self.level_history.record_batch(
                    [
                        {
                            "user_id": m.user_id,
                            "nickname": m.nickname,
                            "from_level": from_levels[m.user_id],
                            "to_level": to_levels[m.user_id],
                            "reason": "검색에서 일괄 탈퇴 처리"
                                      + (" + 재가입 차단" if block_rejoin else ""),
                        }
                        for m in targets
                    ],
                    source="manual_withdraw_bulk",
                    actor=self.admin_user_id,
                )
            except Exception:
                pass

        # 작업 로그
        if self.log_writer is not None:
            try:
                from core.models import AdjustmentItem
                for m in targets:
                    item = AdjustmentItem(
                        member=m,
                        action="delete",
                        from_level=from_levels[m.user_id],
                        to_level=to_levels[m.user_id],
                        reason=f"수동 탈퇴 처리 일괄 (관리자 {self.admin_user_id})",
                    )
                    self.log_writer.write_action(item, result)
            except Exception:
                pass

        # 재가입 차단 명단 반영
        if block_rejoin:
            self._add_to_blocklist(targets, note="수동 탈퇴 처리 (일괄)")

        # 화면 갱신
        self._refresh_filter_counts()
        self._apply_filters()
        speak(f"일괄 탈퇴 처리 완료: {len(targets)}명.")
        self._modal_msg(
            f"{len(targets)}명을 탈퇴 처리했습니다."
            + (f"\n재가입 차단 명단에도 추가했습니다." if block_rejoin else ""),
            "일괄 탈퇴 처리 완료", wx.ICON_INFORMATION,
        )

    def _add_to_blocklist(self, members: list[Member], note: str = "수동 탈퇴 처리") -> None:
        """탈퇴 처리된 회원들을 재가입 차단 명단에 추가하고 음성/로그 기록."""
        if self.blocklist is None:
            speak("재가입 차단 명단 기능이 비활성화돼 있어 추가하지 못했습니다.")
            return
        added = 0
        failed: list[str] = []
        for m in members:
            try:
                self.blocklist.add(m.user_id, nickname=m.nickname, reason=note)
                added += 1
            except Exception:
                failed.append(m.user_id)
            if self.log_writer is not None:
                try:
                    self.log_writer.write_event(
                        f"manual_withdraw_block user={m.user_id} "
                        f"actor={self.admin_user_id}"
                    )
                except Exception:
                    pass
        if added and not failed:
            speak(f"재가입 차단 명단에 {added}명 추가했습니다.")
        elif added and failed:
            speak(f"재가입 차단 명단에 {added}명 추가, {len(failed)}명 실패.")
            wx.MessageBox(
                f"재가입 차단 명단 추가 결과: {added}명 성공, {len(failed)}명 실패.\n"
                f"실패: {', '.join(failed[:10])}",
                "주의", wx.OK | wx.ICON_WARNING, self,
            )
        else:
            speak("재가입 차단 명단 추가에 실패했습니다.")
            wx.MessageBox(
                "재가입 차단 명단에 추가하지 못했습니다.",
                "주의", wx.OK | wx.ICON_WARNING, self,
            )

    # ---------- 다중 선택 일괄 변경 (v1.0.2) ----------

    def _on_bulk_change(self, event=None) -> None:
        # v1.0.6: 클릭/단축키 진입 즉시 피드백.
        speak("일괄 변경 버튼")
        if self.session is None:
            speak("로그인 세션이 없어 등급 변경을 진행할 수 없습니다.")
            self._modal_msg(
                "로그인 세션이 없어 등급 변경을 진행할 수 없습니다.\n"
                "한 번 로그아웃 후 다시 로그인하면 해결됩니다.",
                "세션 없음",
                wx.ICON_WARNING,
            )
            return

        # v1.0.7: YES/NO 중간 확인 없이 곧장 새 등급 선택창으로.
        # 기존엔 wx.MessageBox 가 부모 미지정으로 다이얼로그 뒤에 숨거나
        # 포커스를 못 받는 환경에서 흐름이 멈추는 사례가 있었음.
        members = self._checked_members()
        if not members:
            members = list(self.filtered)
        if not members:
            speak("변경할 회원이 없습니다.")
            self._modal_msg(
                "현재 목록이 비어 있어 일괄 변경할 회원이 없습니다.\n"
                "등급 필터를 바꾸거나 검색어를 비워 주세요.",
                "변경 불가",
                wx.ICON_INFORMATION,
            )
            return

        # 본인 계정 제외 (소리 없이 — 안전 장치)
        admin_id = self.admin_user_id
        skipped_self = [m for m in members if m.user_id.lower() == admin_id]
        members = [m for m in members if m.user_id.lower() != admin_id]

        if not members:
            speak("일괄 변경할 회원이 없습니다.")
            self._modal_msg(
                "본인 계정만 포함되어 있어 변경할 수 없습니다.",
                "변경 불가",
                wx.ICON_INFORMATION,
            )
            return

        # 새 등급 선택 — wx.SingleChoiceDialog 로 부모를 명시.
        choices = [
            f"레벨 {lv} - {LEVEL_LABELS.get(lv, str(lv))}"
            for lv in SELECTABLE_LEVELS
        ]
        speak(f"{len(members)}명에 적용할 새 등급을 선택하세요.")
        choice_dlg = wx.SingleChoiceDialog(
            self,
            f"{len(members)}명을 어느 등급으로 변경할까요?",
            "일괄 등급 변경",
            choices,
        )
        choice_dlg.SetSelection(0)
        if choice_dlg.ShowModal() != wx.ID_OK:
            choice_dlg.Destroy()
            speak("일괄 변경을 취소했습니다.")
            return
        sel = choice_dlg.GetSelection()
        choice_dlg.Destroy()
        if not (0 <= sel < len(SELECTABLE_LEVELS)):
            return
        new_level = SELECTABLE_LEVELS[sel]

        # 이미 그 등급인 회원은 제외 (서버 부하 절감)
        targets = [m for m in members if m.level != new_level]
        same_count = len(members) - len(targets)
        if not targets:
            speak("모두 이미 그 등급입니다.")
            self._modal_msg(
                "선택한 회원이 모두 이미 해당 등급입니다. 변경할 항목이 없습니다.",
                "변경 없음",
                wx.ICON_INFORMATION,
            )
            return

        # 최종 확인 (1단계만, parent=self 명시)
        new_label = LEVEL_LABELS.get(new_level, str(new_level))
        sample = ", ".join(m.user_id for m in targets[:5])
        if len(targets) > 5:
            sample += f", … 외 {len(targets) - 5}명"
        msg_parts = [
            f"{len(targets)}명을 {new_label} (레벨 {new_level}) 로 일괄 변경합니다.",
            f"대상: {sample}",
        ]
        if same_count:
            msg_parts.append(f"(이미 같은 등급 {same_count}명 제외)")
        if skipped_self:
            msg_parts.append(f"(본인 계정 {len(skipped_self)}명 제외)")
        msg_parts.append("\n이 작업은 사이트에 즉시 반영됩니다. 계속하시겠습니까?")

        speak(f"{len(targets)}명을 {new_label} 로 변경, 확인창에서 예를 누르세요.")
        confirm_dlg = wx.MessageDialog(
            self,
            "\n".join(msg_parts),
            "일괄 등급 변경 최종 확인",
            wx.YES_NO | wx.ICON_WARNING,
        )
        confirm_dlg.SetYesNoLabels("예 (변경)", "아니오 (취소)")
        confirm = confirm_dlg.ShowModal()
        confirm_dlg.Destroy()
        if confirm != wx.ID_YES:
            speak("일괄 변경을 취소했습니다.")
            return

        speak(f"{len(targets)}명 일괄 변경 중")

        admin = MemberAdminAdapter(self.session, dry_run=False)
        level_map = {m.user_id: new_level for m in targets}
        result = admin.bulk_apply(
            level_map,
            action_label=f"검색 다이얼로그 일괄 변경 → 레벨 {new_level}",
        )

        if not result.success:
            speak("일괄 변경에 실패했습니다.")
            self._modal_msg(
                f"일괄 변경 실패: {result.message}\n"
                f"응답 일부: {result.response_snippet[:200]}",
                "오류",
                wx.ICON_ERROR,
            )
            return

        # 캐시 객체 갱신
        from_levels: dict[str, int] = {}
        unmark_ids: list[str] = []
        for m in targets:
            from_levels[m.user_id] = m.level
            m.level = new_level
            m.level_label = LEVEL_LABELS.get(new_level, str(new_level))
            # v1.0.3: 동호회관리자 플래그도 클리어 — 일괄 변경 후 변경된 회원은
            # 즉시 동호회관리자 그룹에서 빠지고 새 레벨 그룹으로 이동한다.
            if m.is_admin:
                m.is_admin = False
                unmark_ids.append(m.user_id)
        if unmark_ids:
            self.admin_flags.unmark_many(unmark_ids)
        self.changed_count += len(targets)

        # Undo 스택 (한 묶음으로)
        if self.undo_stack is not None:
            try:
                from core.undo_stack import UndoItem
                self.undo_stack.push(
                    label=f"일괄 등급 변경 ({len(targets)}명 → 레벨 {new_level})",
                    items=[
                        UndoItem(
                            user_id=m.user_id,
                            nickname=m.nickname,
                            from_level=from_levels[m.user_id],
                            to_level=new_level,
                        )
                        for m in targets
                    ],
                )
            except Exception:
                pass

        # 영구 이력
        if self.level_history is not None:
            try:
                self.level_history.record_batch(
                    [
                        {
                            "user_id": m.user_id,
                            "nickname": m.nickname,
                            "from_level": from_levels[m.user_id],
                            "to_level": new_level,
                            "reason": "검색 다이얼로그에서 일괄 변경",
                        }
                        for m in targets
                    ],
                    source="manual_bulk",
                    actor=self.admin_user_id,
                )
            except Exception:
                pass

        # 로그
        if self.log_writer is not None:
            try:
                from core.models import AdjustmentItem
                for m in targets:
                    direction = (
                        "수동 강등"
                        if new_level < from_levels[m.user_id]
                        else "수동 승급"
                    )
                    item = AdjustmentItem(
                        member=m,
                        action="demote",
                        from_level=from_levels[m.user_id],
                        to_level=new_level,
                        reason=f"{direction} 일괄 (관리자 {self.admin_user_id})",
                    )
                    self.log_writer.write_action(item, result)
            except Exception:
                pass

        # 화면 갱신 — 변경된 회원은 현재 필터 그룹에서 빠지고 새 레벨 그룹으로
        # 옮겨가야 하므로 _apply_filters 로 목록 자체를 다시 만든다.
        self._refresh_filter_counts()
        self._apply_filters()

        speak(
            f"일괄 변경 완료: {len(targets)}명을 {new_label} 로 변경했습니다."
        )
        self._modal_msg(
            f"{len(targets)}명을 {new_label} 로 변경했습니다.",
            "일괄 변경 완료",
            wx.ICON_INFORMATION,
        )
