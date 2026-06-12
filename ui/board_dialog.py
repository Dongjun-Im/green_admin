"""소리샘 게시판 관리 + 공지 작성 + 게시물 관리 다이얼로그.

맨 위 '화면 선택' 라디오 버튼으로 세 화면을 전환한다 (wx.Notebook 대신 라디오 +
wx.Simplebook — 일부 스크린리더가 노트북 페이지탭 개수를 잘못 읽는 문제 회피):
  1) 게시판 설정 — bo_table 을 넣고 '불러오기' 하면 그 게시판의 관리 폼 필드들이
     라벨+입력칸으로 동적으로 표시된다. 고쳐서 '저장' 하면 그 폼의 action 으로 POST.
  2) 공지 작성 — 단일 공지(게시판 한 곳) / 일괄 공지(여러 게시판). 그누보드 글쓰기
     폼의 '공지글로 등록' 체크를 켜서 글을 만든다. 일괄 공지 대상 게시판 목록은
     스페이스 키로 선택/해제.
  3) 게시물 관리 — 게시판 아이디(바로가기 코드)를 넣고 '게시물 목록 불러오기' 하면
     글 목록이 뜬다. 스페이스 키로 글을 골라 '선택 복사 / 선택 이동 / 선택 삭제'.

스크린리더 친화: 모든 입력 위에 라벨 StaticText 를 컨트롤보다 먼저 두고 name= 부여.
NAS·사이트 호출은 별도 스레드. 일괄 공지는 진행 다이얼로그(ProgressTaskDialog) 사용.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta

import wx
from wx.lib.scrolledpanel import ScrolledPanel

from core.board_admin import (
    KNOWN_BOARDS,
    BoardAdminError,
    BoardForm,
    FormField,
    PostItem,
    delete_posts,
    fetch_board_form,
    fetch_board_list_html,
    fetch_post_list,
    move_posts,
    post_notice_to_boards,
    submit_board_form,
    write_post,
)
from core.scheduled_notice import ScheduledNotice, ScheduledNoticeStore
from screen_reader import speak
from ui.progress_dialog import ProgressTaskDialog


def _split_board_ids(text: str) -> list[str]:
    """콤마/공백/세미콜론 구분 문자열 → bo_table 리스트 (중복 제거, 순서 보존)."""
    out: list[str] = []
    for tok in re.split(r"[,\s;]+", text or ""):
        t = tok.strip()
        if t and t not in out:
            out.append(t)
    return out


class BoardAdminDialog(wx.Dialog):
    def __init__(self, parent, *, session, admin_user_id: str = "") -> None:
        super().__init__(
            parent,
            title="게시판 관리 / 공지 작성",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
        )
        self.session = session
        self.admin_user_id = admin_user_id
        self._board_form: BoardForm | None = None
        # name -> (FormField, wx 컨트롤)  — 저장 시 값 읽어오는 용도
        self._field_widgets: dict[str, tuple[FormField, wx.Window]] = {}
        # 게시물 관리 화면 상태
        self._post_items: list[PostItem] = []
        self._post_list_form: dict[str, str] = {}
        self._post_list_action_url: str = ""
        self._build_ui()
        self.SetMinSize(wx.Size(780, 700))
        self.Fit()
        self.Centre()
        wx.CallAfter(self._announce)

    # ---------- UI ----------

    _PAGE_NAMES = ("게시판 설정", "공지 작성", "게시물 관리")

    def _build_ui(self) -> None:
        # wx.Notebook 의 페이지탭을 일부 스크린리더가 "2/4" 처럼 잘못 세는 문제가 있어
        # 라디오 버튼('화면 선택') + wx.Simplebook(눈에 보이는 탭 없음) 조합으로 대체.
        sizer = wx.BoxSizer(wx.VERTICAL)
        # majorDimension=1 + RA_SPECIFY_COLS → 한 줄에 1개씩 세로 배치.
        # 이래야 위/아래 방향키로 라디오 항목 사이를 순서대로 이동한다.
        # (RA_SPECIFY_ROWS 는 '1 행 = 가로' 라 화살표가 첫 항목으로 튄다 — mail_dialog 참고.)
        self.page_radio = wx.RadioBox(
            self, label="화면 선택(&P)", choices=list(self._PAGE_NAMES),
            majorDimension=1, style=wx.RA_SPECIFY_COLS, name="화면 선택",
        )
        self.page_radio.Bind(wx.EVT_RADIOBOX, self._on_page_changed)
        sizer.Add(self.page_radio, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

        self.book = wx.Simplebook(self, name="게시판 관리 본문")
        self.book.AddPage(self._build_settings_page(self.book), "게시판 설정")
        self.book.AddPage(self._build_notice_page(self.book), "공지 작성")
        self.book.AddPage(self._build_post_mgmt_page(self.book), "게시물 관리")
        self.book.SetSelection(0)
        sizer.Add(self.book, 1, wx.EXPAND | wx.ALL, 8)

        close_btn = wx.Button(self, wx.ID_CLOSE, "닫기(&C)")
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        sizer.Add(close_btn, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CLOSE)

    def _on_page_changed(self, _event=None) -> None:
        idx = self.page_radio.GetSelection()
        if 0 <= idx < self.book.GetPageCount():
            self.book.SetSelection(idx)
        name = self._PAGE_NAMES[idx] if 0 <= idx < len(self._PAGE_NAMES) else "화면"
        speak(f"{name} 화면")

    # ----- 화면 1: 게시판 설정 -----

    def _build_settings_page(self, parent) -> wx.Window:
        page = wx.Panel(parent)
        sz = wx.BoxSizer(wx.VERTICAL)

        sz.Add(
            wx.StaticText(page, label="게시판 아이디(&I) - 예: green3, green9:"),
            0, wx.LEFT | wx.RIGHT | wx.TOP, 8,
        )
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.bo_input = wx.TextCtrl(
            page, value=(KNOWN_BOARDS[0][0] if KNOWN_BOARDS else ""),
            name="게시판 아이디",
        )
        self.load_btn = wx.Button(page, label="설정 불러오기(&L)")
        self.load_btn.Bind(wx.EVT_BUTTON, self._on_load_form)
        row.Add(self.bo_input, 1, wx.EXPAND | wx.RIGHT, 8)
        row.Add(self.load_btn, 0)
        sz.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        known = ", ".join(f"{b}({n})" for b, n in KNOWN_BOARDS) or "(없음)"
        sz.Add(
            wx.StaticText(page, label=f"알려진 게시판: {known}"),
            0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8,
        )

        sz.Add(
            wx.StaticText(page, label="게시판 설정 항목(&O):"),
            0, wx.LEFT | wx.RIGHT, 8,
        )
        self.form_panel = ScrolledPanel(page, style=wx.VSCROLL | wx.BORDER_SUNKEN)
        self.form_sizer = wx.BoxSizer(wx.VERTICAL)
        self.form_panel.SetSizer(self.form_sizer)
        self.form_panel.SetupScrolling(scroll_x=False)
        self.form_panel.SetMinSize(wx.Size(-1, 360))
        sz.Add(self.form_panel, 1, wx.EXPAND | wx.ALL, 8)

        self.save_btn = wx.Button(page, label="게시판 설정 저장(&S)")
        self.save_btn.Bind(wx.EVT_BUTTON, self._on_save_form)
        self.save_btn.Disable()
        sz.Add(self.save_btn, 0, wx.ALIGN_RIGHT | wx.ALL, 8)

        page.SetSizer(sz)
        return page

    # ----- 화면 2: 공지 작성 -----

    def _build_notice_page(self, parent) -> wx.Window:
        page = wx.Panel(parent)
        sz = wx.BoxSizer(wx.VERTICAL)

        # 단일 공지
        sz.Add(wx.StaticText(page, label="[ 단일 공지 - 게시판 한 곳 ]"),
               0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        sz.Add(wx.StaticText(page, label="대상 게시판 아이디(&G):"),
               0, wx.LEFT | wx.RIGHT, 8)
        self.single_bo_input = wx.TextCtrl(
            page, value=(KNOWN_BOARDS[0][0] if KNOWN_BOARDS else ""),
            name="단일 공지 게시판 아이디",
        )
        sz.Add(self.single_bo_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        # 일괄 공지
        sz.Add(wx.StaticText(page, label="[ 일괄 공지 - 여러 게시판 ]"),
               0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        sz.Add(wx.StaticText(page, label="대상 게시판 선택(&L):"),
               0, wx.LEFT | wx.RIGHT, 8)
        self.boards_check = wx.CheckListBox(
            page,
            choices=[self._board_check_label(b, n, False) for b, n in KNOWN_BOARDS],
            name="대상 게시판 목록 (스페이스 키로 선택/해제)",
        )
        self.boards_check.SetMinSize(wx.Size(-1, 100))
        self.boards_check.Bind(wx.EVT_KEY_DOWN, self._on_boards_key)
        self.boards_check.Bind(wx.EVT_SET_FOCUS, self._on_boards_focus)
        self.boards_check.Bind(wx.EVT_CHECKLISTBOX, self._on_boards_clicked)
        sz.Add(self.boards_check, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        sz.Add(wx.StaticText(page, label="추가 게시판 아이디 (콤마/공백 구분)(&E):"),
               0, wx.LEFT | wx.RIGHT, 8)
        self.extra_boards_input = wx.TextCtrl(page, name="추가 게시판 아이디")
        sz.Add(self.extra_boards_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        # 공통 — 제목/본문/옵션
        sz.Add(wx.StaticText(page, label="제목(&T):"),
               0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.notice_subject = wx.TextCtrl(page, name="공지 제목")
        sz.Add(self.notice_subject, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        sz.Add(wx.StaticText(page, label="본문(&N):"),
               0, wx.LEFT | wx.RIGHT, 8)
        self.notice_content = wx.TextCtrl(page, style=wx.TE_MULTILINE, name="공지 본문")
        self.notice_content.SetMinSize(wx.Size(-1, 150))
        sz.Add(self.notice_content, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        opt_row = wx.BoxSizer(wx.HORIZONTAL)
        self.as_notice_cb = wx.CheckBox(page, label="공지글로 등록(&O)", name="공지로 등록")
        self.as_notice_cb.SetValue(True)
        self.html_cb = wx.CheckBox(page, label="HTML 본문(&H)", name="HTML 본문")
        opt_row.Add(self.as_notice_cb, 0, wx.RIGHT, 16)
        opt_row.Add(self.html_cb, 0)
        sz.Add(opt_row, 0, wx.ALL, 8)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.single_btn = wx.Button(page, label="단일 공지 작성(&W)")
        self.bulk_btn = wx.Button(page, label="일괄 공지 작성(&B)")
        self.single_btn.Bind(wx.EVT_BUTTON, self._on_single_notice)
        self.bulk_btn.Bind(wx.EVT_BUTTON, self._on_bulk_notice)
        btn_row.Add(self.single_btn, 0, wx.RIGHT, 8)
        btn_row.Add(self.bulk_btn, 0)
        sz.Add(btn_row, 0, wx.ALIGN_RIGHT | wx.ALL, 8)

        # 예약 발송 — 정해진 날짜·시각에 자동으로 올린다 (단일 게시판 + 일괄 대상 합쳐서).
        # 스크린리더가 읽도록 날짜·시각은 adv 피커 대신 일반 TextCtrl(+ 명확한 name).
        default_dt = datetime.now() + timedelta(hours=1)
        sz.Add(wx.StaticText(page, label="[ 예약 발송 - 정해진 시각에 자동 게시 ]"),
               0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        sched_row = wx.BoxSizer(wx.HORIZONTAL)
        sched_row.Add(wx.StaticText(page, label="예약 날짜 (2026-06-15 형식):"),
                      0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 4)
        self.sched_date = wx.TextCtrl(
            page, value=default_dt.strftime("%Y-%m-%d"),
            name="예약 날짜, 예: 2026-06-15",
        )
        self.sched_date.SetMinSize(wx.Size(120, -1))
        sched_row.Add(self.sched_date, 0, wx.RIGHT, 8)
        sched_row.Add(wx.StaticText(page, label="예약 시각 (24시간제 14:30):"),
                      0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 4)
        self.sched_time = wx.TextCtrl(
            page, value=default_dt.strftime("%H:%M"),
            name="예약 시각, 24시간제, 예: 14:30",
        )
        self.sched_time.SetMinSize(wx.Size(80, -1))
        sched_row.Add(self.sched_time, 0, wx.RIGHT, 8)
        self.schedule_btn = wx.Button(page, label="예약 발송(&Y)")
        self.schedule_btn.Bind(wx.EVT_BUTTON, self._on_schedule_notice)
        sched_row.Add(self.schedule_btn, 0)
        sz.Add(sched_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        sz.Add(wx.StaticText(
            page,
            label="예약하면 약 10분마다 도는 자동 작업이 정해진 시각 이후 올려 줍니다. "
                  "그 시각에 PC 가 켜져 있어야 합니다. '작업 - 예약 공지 목록' 에서 확인·취소.",
        ), 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        sz.Add(wx.StaticText(page, label="작성 결과(&R):"),
               0, wx.LEFT | wx.RIGHT, 8)
        self.notice_result = wx.TextCtrl(
            page, style=wx.TE_MULTILINE | wx.TE_READONLY, name="작성 결과",
        )
        self.notice_result.SetMinSize(wx.Size(-1, 100))
        sz.Add(self.notice_result, 0, wx.EXPAND | wx.ALL, 8)

        page.SetSizer(sz)
        return page

    # ----- 화면 3: 게시물 관리 (복사/이동/삭제) -----

    def _build_post_mgmt_page(self, parent) -> wx.Window:
        page = wx.Panel(parent)
        sz = wx.BoxSizer(wx.VERTICAL)

        sz.Add(wx.StaticText(page, label="게시판 아이디(바로가기 코드)(&I) - 예: green3:"),
               0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.post_bo_input = wx.TextCtrl(
            page, value=(KNOWN_BOARDS[0][0] if KNOWN_BOARDS else ""), name="게시판 아이디",
        )
        row.Add(self.post_bo_input, 1, wx.EXPAND | wx.RIGHT, 8)
        row.Add(wx.StaticText(page, label="페이지:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self.post_page_spin = wx.SpinCtrl(page, min=1, max=99999, initial=1, name="페이지 번호")
        self.post_page_spin.SetMinSize(wx.Size(80, -1))
        row.Add(self.post_page_spin, 0, wx.RIGHT, 8)
        self.post_load_btn = wx.Button(page, label="게시물 목록 불러오기(&L)")
        self.post_load_btn.Bind(wx.EVT_BUTTON, self._on_load_posts)
        row.Add(self.post_load_btn, 0)
        sz.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        known = ", ".join(f"{b}({n})" for b, n in KNOWN_BOARDS) or "(없음)"
        sz.Add(wx.StaticText(page, label=f"알려진 게시판: {known}"),
               0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        sz.Add(wx.StaticText(page, label="게시물 목록 - 스페이스 키로 선택/해제(&O):"),
               0, wx.LEFT | wx.RIGHT, 8)
        self.post_list = wx.CheckListBox(page, name="게시물 목록 (스페이스 키로 선택/해제)")
        self.post_list.SetMinSize(wx.Size(-1, 220))
        self.post_list.Bind(wx.EVT_KEY_DOWN, self._on_postlist_key)
        self.post_list.Bind(wx.EVT_SET_FOCUS, self._on_postlist_focus)
        self.post_list.Bind(wx.EVT_CHECKLISTBOX, self._on_postlist_clicked)
        sz.Add(self.post_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        sz.Add(wx.StaticText(page, label="대상 게시판 아이디 (이동/복사 시)(&T):"),
               0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.target_bo_input = wx.TextCtrl(page, name="대상 게시판 아이디")
        sz.Add(self.target_bo_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.post_copy_btn = wx.Button(page, label="선택 복사(&C)")
        self.post_move_btn = wx.Button(page, label="선택 이동(&M)")
        self.post_delete_btn = wx.Button(page, label="선택 삭제(&D)")
        self.post_copy_btn.Bind(wx.EVT_BUTTON, lambda e: self._on_post_action("copy"))
        self.post_move_btn.Bind(wx.EVT_BUTTON, lambda e: self._on_post_action("move"))
        self.post_delete_btn.Bind(wx.EVT_BUTTON, lambda e: self._on_post_action("delete"))
        for b in (self.post_copy_btn, self.post_move_btn, self.post_delete_btn):
            btn_row.Add(b, 0, wx.RIGHT, 8)
        sz.Add(btn_row, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)

        self.post_dump_btn = wx.Button(
            page, label="진단: 이 게시판 목록 페이지 원본 HTML 저장(&V)",
        )
        self.post_dump_btn.Bind(wx.EVT_BUTTON, self._on_save_list_html)
        sz.Add(self.post_dump_btn, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)

        sz.Add(wx.StaticText(page, label="처리 결과(&R):"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.post_result = wx.TextCtrl(
            page, style=wx.TE_MULTILINE | wx.TE_READONLY, name="처리 결과",
        )
        self.post_result.SetMinSize(wx.Size(-1, 90))
        sz.Add(self.post_result, 0, wx.EXPAND | wx.ALL, 8)

        self._set_post_buttons(False)
        page.SetSizer(sz)
        return page

    # ---------- 게시판 설정: 불러오기 ----------

    def _on_load_form(self, _event=None) -> None:
        bo = self.bo_input.GetValue().strip()
        if not bo:
            wx.MessageBox("게시판 아이디(bo_table)를 입력하세요.",
                          "입력 필요", wx.OK | wx.ICON_INFORMATION, self)
            return
        self.load_btn.Disable()
        self.save_btn.Disable()
        speak(f"{bo} 게시판 설정을 불러옵니다.")
        threading.Thread(
            target=self._load_form_in_thread, args=(bo,), daemon=True,
        ).start()

    def _load_form_in_thread(self, bo: str) -> None:
        try:
            form = fetch_board_form(self.session, bo)
        except BoardAdminError as e:
            wx.CallAfter(self._load_form_failed, str(e))
            return
        except Exception as e:
            wx.CallAfter(self._load_form_failed, f"예상치 못한 오류: {e}")
            return
        wx.CallAfter(self._render_form, form)

    def _load_form_failed(self, message: str) -> None:
        self.load_btn.Enable()
        speak("게시판 설정 불러오기 실패.")
        wx.MessageBox(message, "불러오기 실패", wx.OK | wx.ICON_ERROR, self)

    def _render_form(self, form: BoardForm) -> None:
        self._board_form = form
        self._field_widgets.clear()
        self.form_sizer.Clear(delete_windows=True)
        p = self.form_panel

        vis = form.visible_fields()
        for f in vis:
            ctrl: wx.Window
            if f.kind == "checkbox":
                ctrl = wx.CheckBox(p, label=f.label or f.name, name=f.label or f.name)
                ctrl.SetValue(f.checked)
                self.form_sizer.Add(ctrl, 0, wx.ALL, 4)
            elif f.kind == "radio":
                choices = [t or v for v, t in f.options] or ["(옵션 없음)"]
                # majorDimension=1 + RA_SPECIFY_COLS → 세로 1열, 위/아래 화살표로 이동.
                ctrl = wx.RadioBox(
                    p, label=f.label or f.name, choices=choices,
                    majorDimension=1, style=wx.RA_SPECIFY_COLS, name=f.label or f.name,
                )
                idx = next((i for i, (v, _t) in enumerate(f.options) if v == f.value), 0)
                if 0 <= idx < ctrl.GetCount():
                    ctrl.SetSelection(idx)
                self.form_sizer.Add(ctrl, 0, wx.EXPAND | wx.ALL, 4)
            elif f.kind == "select":
                self.form_sizer.Add(
                    wx.StaticText(p, label=f"{f.label or f.name}:"),
                    0, wx.LEFT | wx.RIGHT | wx.TOP, 4,
                )
                choices = [t or v for v, t in f.options] or ["(옵션 없음)"]
                ctrl = wx.Choice(p, choices=choices, name=f.label or f.name)
                idx = next((i for i, (v, _t) in enumerate(f.options) if v == f.value), 0)
                if 0 <= idx < ctrl.GetCount():
                    ctrl.SetSelection(idx)
                self.form_sizer.Add(ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)
            elif f.kind == "textarea":
                self.form_sizer.Add(
                    wx.StaticText(p, label=f"{f.label or f.name}:"),
                    0, wx.LEFT | wx.RIGHT | wx.TOP, 4,
                )
                ctrl = wx.TextCtrl(p, value=f.value, style=wx.TE_MULTILINE,
                                   name=f.label or f.name)
                ctrl.SetMinSize(wx.Size(-1, 70))
                self.form_sizer.Add(ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)
            else:  # text / number / password
                self.form_sizer.Add(
                    wx.StaticText(p, label=f"{f.label or f.name}:"),
                    0, wx.LEFT | wx.RIGHT | wx.TOP, 4,
                )
                style = wx.TE_PASSWORD if f.kind == "password" else 0
                ctrl = wx.TextCtrl(p, value=f.value, style=style, name=f.label or f.name)
                if f.maxlength > 0:
                    try:
                        ctrl.SetMaxLength(f.maxlength)
                    except Exception:
                        pass
                self.form_sizer.Add(ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)
            self._field_widgets[f.name] = (f, ctrl)

        self.form_sizer.Layout()
        self.form_panel.Layout()
        self.form_panel.SetupScrolling(scroll_x=False, scrollToTop=True)
        self.load_btn.Enable()
        self.save_btn.Enable()
        speak(f"{form.bo_table} 게시판 설정 {len(vis)}개 항목을 불러왔습니다.")

    # ---------- 게시판 설정: 저장 ----------

    def _collect_overrides(self) -> dict[str, object]:
        ov: dict[str, object] = {}
        for name, (f, ctrl) in self._field_widgets.items():
            try:
                if f.kind == "checkbox":
                    ov[name] = bool(ctrl.GetValue())
                elif f.kind == "radio":
                    idx = ctrl.GetSelection()
                    if 0 <= idx < len(f.options):
                        ov[name] = f.options[idx][0]
                elif f.kind == "select":
                    idx = ctrl.GetSelection()
                    if 0 <= idx < len(f.options):
                        ov[name] = f.options[idx][0]
                else:  # text/number/password/textarea
                    ov[name] = ctrl.GetValue()
            except Exception:
                continue
        return ov

    def _on_save_form(self, _event=None) -> None:
        if self._board_form is None:
            return
        bo = self._board_form.bo_table
        ans = wx.MessageBox(
            f"게시판 '{bo}' 의 설정을 저장합니다. 계속할까요?\n"
            "(화면에 보이는 모든 항목이 그대로 전송됩니다.)",
            "게시판 설정 저장", wx.YES_NO | wx.ICON_QUESTION | wx.NO_DEFAULT, self,
        )
        if ans != wx.YES:
            return
        overrides = self._collect_overrides()
        form = self._board_form
        self.save_btn.Disable()
        self.load_btn.Disable()
        speak(f"{bo} 게시판 설정 저장 중.")
        threading.Thread(
            target=self._save_form_in_thread, args=(form, overrides), daemon=True,
        ).start()

    def _save_form_in_thread(self, form: BoardForm, overrides: dict) -> None:
        try:
            result = submit_board_form(self.session, form, overrides)
        except BoardAdminError as e:
            wx.CallAfter(self._save_form_done, False, str(e))
            return
        except Exception as e:
            wx.CallAfter(self._save_form_done, False, f"예상치 못한 오류: {e}")
            return
        wx.CallAfter(self._save_form_done, result.ok, result.message)

    def _save_form_done(self, ok: bool, message: str) -> None:
        self.save_btn.Enable()
        self.load_btn.Enable()
        speak("게시판 설정 저장 완료." if ok else "게시판 설정 저장 실패.")
        wx.MessageBox(
            message + ("\n\n변경이 반영됐는지 '설정 불러오기' 로 다시 확인해 보세요." if ok else ""),
            "저장 결과" if ok else "저장 실패",
            wx.OK | (wx.ICON_INFORMATION if ok else wx.ICON_ERROR), self,
        )

    # ---------- 공지: 단일 ----------

    def _notice_inputs(self):
        return (
            self.notice_subject.GetValue().strip(),
            self.notice_content.GetValue(),
            self.as_notice_cb.IsChecked(),
            self.html_cb.IsChecked(),
        )

    def _on_single_notice(self, _event=None) -> None:
        bo = self.single_bo_input.GetValue().strip()
        subj, content, as_notice, use_html = self._notice_inputs()
        if not bo:
            wx.MessageBox("단일 공지: 대상 게시판 아이디를 입력하세요.",
                          "입력 필요", wx.OK | wx.ICON_INFORMATION, self)
            return
        if not subj:
            wx.MessageBox("제목을 입력하세요.", "입력 필요",
                          wx.OK | wx.ICON_INFORMATION, self)
            return
        kind = "공지" if as_notice else "일반 글"
        ans = wx.MessageBox(
            f"게시판 '{bo}' 에 {kind} '{subj}' 을(를) 작성합니다. 계속할까요?",
            "공지 작성 확인", wx.YES_NO | wx.ICON_QUESTION, self,
        )
        if ans != wx.YES:
            return
        self._set_notice_busy(True)
        speak(f"{bo} 게시판에 {kind} 작성 중.")
        threading.Thread(
            target=self._single_notice_in_thread,
            args=(bo, subj, content, as_notice, use_html), daemon=True,
        ).start()

    def _single_notice_in_thread(self, bo, subj, content, as_notice, use_html) -> None:
        try:
            result = write_post(
                self.session, bo, subj, content,
                as_notice=as_notice, use_html=use_html,
            )
        except Exception as e:
            wx.CallAfter(self._single_notice_done, bo, False, f"예상치 못한 오류: {e}")
            return
        wx.CallAfter(self._single_notice_done, bo, result.ok, result.message)

    def _single_notice_done(self, bo: str, ok: bool, message: str) -> None:
        mark = "[성공]" if ok else "[실패]"
        cur = self.notice_result.GetValue()
        self.notice_result.SetValue((cur + "\n" if cur else "") + f"{mark} {bo}: {message}")
        self._set_notice_busy(False)
        speak("공지 작성 완료." if ok else "공지 작성 실패.")
        wx.MessageBox(
            f"{bo}: {message}", "작성 결과" if ok else "작성 실패",
            wx.OK | (wx.ICON_INFORMATION if ok else wx.ICON_ERROR), self,
        )

    # ---------- 공지: 일괄 — 게시판 목록 키 처리 ----------

    @staticmethod
    def _board_check_label(bo_table: str, name: str, checked: bool) -> str:
        # "[선택됨|미선택]  green3 (우리들의 이야기)" — 줄 맨 앞에 상태 표시.
        return f"{'선택됨' if checked else '미선택'}  {bo_table} ({name})"

    def _board_name(self, idx: int) -> str:
        if 0 <= idx < len(KNOWN_BOARDS):
            b, n = KNOWN_BOARDS[idx]
            return f"{b} ({n})"
        return self.boards_check.GetString(idx) if 0 <= idx < self.boards_check.GetCount() else ""

    def _set_board_checked(self, idx: int, checked: bool) -> None:
        if 0 <= idx < len(KNOWN_BOARDS):
            b, n = KNOWN_BOARDS[idx]
            self.boards_check.SetString(idx, self._board_check_label(b, n, checked))
        self.boards_check.Check(idx, checked)

    def _on_boards_focus(self, event) -> None:
        # 포커스가 들어왔는데 강조(선택)된 항목이 없으면 첫 항목을 선택해 둔다.
        # (그래야 스페이스 키 토글 대상이 생긴다.)
        try:
            if (self.boards_check.GetSelection() == wx.NOT_FOUND
                    and self.boards_check.GetCount() > 0):
                self.boards_check.SetSelection(0)
        except Exception:
            pass
        event.Skip()

    def _on_boards_key(self, event) -> None:
        # 스페이스 키: 현재 강조된 게시판의 체크를 켜고/끄고, 새 상태를 음성으로 안내.
        if event.GetKeyCode() == wx.WXK_SPACE:
            sel = self.boards_check.GetSelection()
            if sel == wx.NOT_FOUND and self.boards_check.GetCount() > 0:
                sel = 0
                self.boards_check.SetSelection(0)
            if sel != wx.NOT_FOUND:
                new_state = not self.boards_check.IsChecked(sel)
                self._set_board_checked(sel, new_state)
                speak(f"{self._board_name(sel)} {'선택됨' if new_state else '선택 해제됨'}")
                return  # 기본 토글과 겹치지 않도록 여기서 끝낸다.
        event.Skip()

    def _on_boards_clicked(self, event) -> None:
        # 마우스로 체크박스를 눌러 상태가 바뀐 경우에도 라벨 갱신 + 음성 안내.
        idx = event.GetSelection()
        if 0 <= idx < self.boards_check.GetCount():
            checked = self.boards_check.IsChecked(idx)
            self._set_board_checked(idx, checked)
            speak(f"{self._board_name(idx)} {'선택됨' if checked else '선택 해제됨'}")
        event.Skip()

    # ---------- 공지: 일괄 ----------

    def _selected_bulk_boards(self) -> list[str]:
        out: list[str] = []
        for i in range(self.boards_check.GetCount()):
            if self.boards_check.IsChecked(i) and i < len(KNOWN_BOARDS):
                out.append(KNOWN_BOARDS[i][0])
        for extra in _split_board_ids(self.extra_boards_input.GetValue()):
            if extra not in out:
                out.append(extra)
        return out

    def _on_bulk_notice(self, _event=None) -> None:
        boards = self._selected_bulk_boards()
        subj, content, as_notice, use_html = self._notice_inputs()
        if not boards:
            wx.MessageBox(
                "일괄 공지: 대상 게시판을 하나 이상 선택하거나 '추가 게시판 아이디' 에 입력하세요.",
                "선택 필요", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        if not subj:
            wx.MessageBox("제목을 입력하세요.", "입력 필요",
                          wx.OK | wx.ICON_INFORMATION, self)
            return
        kind = "공지" if as_notice else "일반 글"
        listing = "\n".join(f"  - {b}" for b in boards)
        ans = wx.MessageBox(
            f"다음 {len(boards)}개 게시판에 {kind} '{subj}' 을(를) 작성합니다:\n{listing}\n\n계속할까요?",
            "일괄 공지 확인", wx.YES_NO | wx.ICON_QUESTION | wx.NO_DEFAULT, self,
        )
        if ans != wx.YES:
            return

        session = self.session

        def worker(progress_cb):
            return post_notice_to_boards(
                session, boards, subj, content,
                as_notice=as_notice, use_html=use_html,
                progress_cb=lambda c, t: progress_cb(c, t, f"공지 작성 {c}/{t}"),
            )

        self._set_notice_busy(True)
        dlg = ProgressTaskDialog(
            self, title="일괄 공지 작성", task=worker,
            message=f"{len(boards)}개 게시판에 작성 시작...",
        )
        try:
            dlg.run_modal()
        finally:
            dlg.Destroy()
            self._set_notice_busy(False)

        if dlg.error is not None:
            wx.MessageBox(f"일괄 공지 실패: {dlg.error}", "오류",
                          wx.OK | wx.ICON_ERROR, self)
            return
        results = dlg.result or []
        ok_n = sum(1 for r in results if r.ok)
        lines = [f"일괄 공지 결과: 성공 {ok_n} / 전체 {len(results)}"]
        for r in results:
            mark = "[성공]" if r.ok else "[실패]"
            lines.append(f"{mark} {r.bo_table}: {r.message}")
        text = "\n".join(lines)
        cur = self.notice_result.GetValue()
        self.notice_result.SetValue((cur + "\n" if cur else "") + text)
        speak(f"일괄 공지 완료. 성공 {ok_n}, 실패 {len(results) - ok_n}.")
        wx.MessageBox(text, "일괄 공지 결과", wx.OK | wx.ICON_INFORMATION, self)

    # ---------- 공지: 예약 발송 ----------

    def _all_notice_boards(self) -> list[str]:
        """예약 대상 = 단일 게시판 입력 + 일괄 선택 + 추가 입력 (중복 제거, 순서 보존)."""
        out: list[str] = []
        single = self.single_bo_input.GetValue().strip()
        if single:
            out.append(single)
        for b in self._selected_bulk_boards():
            if b not in out:
                out.append(b)
        return out

    def _read_schedule_dt(self) -> datetime | None:
        """'예약 날짜'(YYYY-MM-DD) + '예약 시각'(HH:MM) 텍스트 → 파이썬 datetime.
        형식이 틀리면 None."""
        date_str = self.sched_date.GetValue().strip()
        time_str = self.sched_time.GetValue().strip()
        try:
            return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            return None

    def _on_schedule_notice(self, _event=None) -> None:
        boards = self._all_notice_boards()
        subj, content, as_notice, use_html = self._notice_inputs()
        if not boards:
            wx.MessageBox(
                "예약 발송: 대상 게시판을 정하세요. '단일 공지 게시판 아이디' 에 적거나 "
                "위 '대상 게시판 선택' 에서 스페이스 키로 고르면 됩니다.",
                "대상 필요", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        if not subj:
            wx.MessageBox("제목을 입력하세요.", "입력 필요",
                          wx.OK | wx.ICON_INFORMATION, self)
            return
        dt = self._read_schedule_dt()
        if dt is None:
            wx.MessageBox(
                "예약 날짜·시각 형식이 올바르지 않습니다.\n"
                "날짜는 2026-06-15, 시각은 24시간제 14:30 형식으로 입력하세요.",
                "입력 필요", wx.OK | wx.ICON_INFORMATION, self)
            return
        if dt <= datetime.now():
            wx.MessageBox("예약 시각이 이미 지났습니다. 미래 시각으로 정해 주세요.",
                          "시각 확인", wx.OK | wx.ICON_WARNING, self)
            return
        kind = "공지" if as_notice else "일반 글"
        when = dt.strftime("%Y-%m-%d %H:%M")
        listing = "\n".join(f"  - {b}" for b in boards)
        ans = wx.MessageBox(
            f"{when} 에 다음 {len(boards)}개 게시판으로 {kind} '{subj}' 을(를)\n"
            f"자동 발송하도록 예약합니다:\n{listing}\n\n계속할까요?",
            "예약 발송 확인", wx.YES_NO | wx.ICON_QUESTION | wx.NO_DEFAULT, self,
        )
        if ans != wx.YES:
            return

        notice = ScheduledNotice(
            scheduled_at=dt.isoformat(timespec="seconds"),
            boards=boards, subject=subj, content=content,
            as_notice=as_notice, use_html=use_html,
        )
        try:
            ScheduledNoticeStore().add(notice)
        except Exception as e:
            wx.MessageBox(f"예약 저장 실패: {e}", "오류", wx.OK | wx.ICON_ERROR, self)
            return

        sched_msg = self._ensure_scheduler_registered()
        cur = self.notice_result.GetValue()
        self.notice_result.SetValue(
            (cur + "\n" if cur else "")
            + f"[예약됨] {when} → {', '.join(boards)} : {subj}"
        )
        speak(f"{when} 으로 예약했습니다.")
        wx.MessageBox(
            f"{when} 에 자동 발송하도록 예약했습니다.\n"
            f"대상 게시판: {', '.join(boards)}\n\n{sched_msg}",
            "예약 완료", wx.OK | wx.ICON_INFORMATION, self,
        )

    @staticmethod
    def _ensure_scheduler_registered() -> str:
        """post_scheduled 자동 작업이 미등록이면 등록. 결과 안내 문구 반환."""
        try:
            from core.scheduler_setup import (
                TASK_NAME_PREFIX,
                query_status,
                register_task,
            )
            statuses = query_status()
            already = any(
                st.task_key == "post_scheduled" and st.registered
                for st in statuses
            )
            if already:
                return ("자동 발송 작업이 이미 등록돼 있어, 그 시각에 PC 가 켜져 있으면 "
                        "자동으로 올라갑니다.")
            ok, msg = register_task("post_scheduled")
            if ok:
                return ("자동 발송 작업을 작업 스케줄러에 등록했습니다. "
                        "그 시각에 PC 가 켜져 있으면 약 10분 안에 올라갑니다.")
            return ("자동 발송 작업 등록에 실패했습니다(" + str(msg) + "). "
                    "'작업 - 자동 스케줄러 관리' 에서 'post_scheduled' 를 직접 등록해 주세요.")
        except Exception as e:  # noqa: BLE001
            return ("자동 발송 작업 등록 중 문제가 생겼습니다(" + str(e) + "). "
                    "'작업 - 자동 스케줄러 관리' 에서 직접 등록해 주세요.")

    # ---------- 게시물 관리: 목록 불러오기 ----------

    def _on_postlist_focus(self, event) -> None:
        try:
            if self.post_list.GetSelection() == wx.NOT_FOUND and self.post_list.GetCount() > 0:
                self.post_list.SetSelection(0)
        except Exception:
            pass
        event.Skip()

    def _on_postlist_key(self, event) -> None:
        if event.GetKeyCode() == wx.WXK_SPACE:
            sel = self.post_list.GetSelection()
            if sel == wx.NOT_FOUND and self.post_list.GetCount() > 0:
                sel = 0
                self.post_list.SetSelection(0)
            if sel != wx.NOT_FOUND:
                new_state = not self.post_list.IsChecked(sel)
                self._set_post_item_checked(sel, new_state)
                speak(f"{self._post_label(sel)} {'선택됨' if new_state else '선택 해제됨'}")
                return
        event.Skip()

    def _on_postlist_clicked(self, event) -> None:
        idx = event.GetSelection()
        if 0 <= idx < self.post_list.GetCount():
            checked = self.post_list.IsChecked(idx)
            if 0 <= idx < len(self._post_items):
                # 라벨에도 상태를 다시 박아 둔다 (마우스로 토글한 경우).
                self.post_list.SetString(idx, self._post_item_label(self._post_items[idx], checked))
                self.post_list.Check(idx, checked)
            speak(f"{self._post_label(idx)} {'선택됨' if checked else '선택 해제됨'}")
        event.Skip()

    def _post_label(self, idx: int) -> str:
        if 0 <= idx < len(self._post_items):
            return self._post_items[idx].subject or f"글번호 {self._post_items[idx].wr_id}"
        return self.post_list.GetString(idx) if 0 <= idx < self.post_list.GetCount() else ""

    def _post_item_label(self, item, checked: bool) -> str:
        # 목록 한 줄 = "[선택됨|미선택]  제목 [공지]  (글번호 N, 작성자, 날짜)".
        # 스크린리더가 확인란 상태를 따로 안 읽어줘도 줄 맨 앞 글자만 들으면 알 수 있다.
        base = item.subject or f"(글번호 {item.wr_id})"
        extras = [x for x in (item.author, item.date) if x]
        tag = " [공지]" if item.is_notice else ""
        meta = f", {', '.join(extras)}" if extras else ""
        state = "선택됨" if checked else "미선택"
        return f"{state}  {base}{tag}  (글번호 {item.wr_id}{meta})"

    def _set_post_item_checked(self, idx: int, checked: bool) -> None:
        if 0 <= idx < len(self._post_items):
            self.post_list.SetString(idx, self._post_item_label(self._post_items[idx], checked))
        # SetString 이 일부 플랫폼에서 체크 상태를 초기화할 수 있어 마지막에 다시 설정.
        self.post_list.Check(idx, checked)

    def _on_load_posts(self, _event=None) -> None:
        bo = self.post_bo_input.GetValue().strip()
        if not bo:
            wx.MessageBox("게시판 아이디(바로가기 코드)를 입력하세요.",
                          "입력 필요", wx.OK | wx.ICON_INFORMATION, self)
            return
        page = int(self.post_page_spin.GetValue())
        self.post_load_btn.Disable()
        self._set_post_buttons(False)
        speak(f"{bo} 게시판 {page}쪽 게시물 목록을 불러옵니다.")
        threading.Thread(
            target=self._load_posts_in_thread, args=(bo, page), daemon=True,
        ).start()

    def _load_posts_in_thread(self, bo: str, page: int) -> None:
        try:
            result = fetch_post_list(self.session, bo, page)
        except BoardAdminError as e:
            wx.CallAfter(self._load_posts_failed, str(e))
            return
        except Exception as e:
            wx.CallAfter(self._load_posts_failed, f"예상치 못한 오류: {e}")
            return
        wx.CallAfter(self._render_post_list, result)

    def _load_posts_failed(self, message: str) -> None:
        self.post_load_btn.Enable()
        self._set_post_buttons(self.post_list.GetCount() > 0)
        speak("게시물 목록 불러오기 실패.")
        wx.MessageBox(message, "불러오기 실패", wx.OK | wx.ICON_ERROR, self)

    def _render_post_list(self, result) -> None:
        self._post_items = list(result.items)
        self._post_list_form = dict(result.list_form)
        self._post_list_action_url = getattr(result, "list_action_url", "") or ""
        labels = [self._post_item_label(it, False) for it in self._post_items]
        self.post_list.Set(labels)
        self.post_load_btn.Enable()
        self._set_post_buttons(bool(labels))
        if labels:
            self.post_list.SetSelection(0)
        n = len(labels)
        speak(
            f"{result.bo_table} 게시판 {result.page}쪽 게시물 {n}개를 불러왔습니다. "
            + ("각 줄 맨 앞에 선택됨 또는 미선택이 표시됩니다. "
               "스페이스 키로 선택한 뒤 복사, 이동, 삭제 버튼을 누르세요." if n else "게시물이 없습니다.")
        )

    def _selected_post_wr_ids(self) -> list[str]:
        out: list[str] = []
        for i in range(self.post_list.GetCount()):
            if self.post_list.IsChecked(i) and i < len(self._post_items):
                out.append(self._post_items[i].wr_id)
        return out

    # ---------- 게시물 관리: 복사/이동/삭제 ----------

    def _set_post_buttons(self, enabled: bool) -> None:
        for b in (getattr(self, "post_copy_btn", None),
                  getattr(self, "post_move_btn", None),
                  getattr(self, "post_delete_btn", None)):
            if b is not None:
                b.Enable(enabled)

    def _on_post_action(self, action: str) -> None:
        wr_ids = self._selected_post_wr_ids()
        verb = {"copy": "복사", "move": "이동", "delete": "삭제"}[action]
        if not wr_ids:
            wx.MessageBox("먼저 목록에서 게시물을 선택하세요 (스페이스 키).",
                          "선택 필요", wx.OK | wx.ICON_INFORMATION, self)
            return
        bo = self.post_bo_input.GetValue().strip()
        target = self.target_bo_input.GetValue().strip()
        if action in ("move", "copy") and not target:
            wx.MessageBox(f"{verb}하려면 '대상 게시판 아이디' 를 입력하세요.",
                          "입력 필요", wx.OK | wx.ICON_INFORMATION, self)
            return
        if action == "delete":
            msg = (f"게시판 '{bo}' 에서 선택한 {len(wr_ids)}개 게시물을 삭제합니다.\n"
                   "삭제된 글은 되돌릴 수 없습니다. 계속할까요?")
            icon = wx.ICON_WARNING
        else:
            msg = (f"게시판 '{bo}' 의 선택한 {len(wr_ids)}개 게시물을 "
                   f"'{target}' 게시판으로 {verb}합니다. 계속할까요?")
            icon = wx.ICON_QUESTION
        if wx.MessageBox(msg, f"게시물 {verb} 확인",
                         wx.YES_NO | icon | wx.NO_DEFAULT, self) != wx.YES:
            return
        self.post_load_btn.Disable()
        self._set_post_buttons(False)
        speak(f"게시물 {verb} 중.")
        threading.Thread(
            target=self._post_action_in_thread,
            args=(action, bo, list(wr_ids), target), daemon=True,
        ).start()

    def _post_action_in_thread(self, action: str, bo: str, wr_ids: list, target: str) -> None:
        try:
            if action == "delete":
                res = delete_posts(self.session, bo, wr_ids,
                                   list_form=self._post_list_form,
                                   list_action_url=self._post_list_action_url)
            else:
                res = move_posts(self.session, bo, wr_ids, target,
                                 copy=(action == "copy"), list_form=self._post_list_form)
        except Exception as e:
            wx.CallAfter(self._post_action_done, action, False, f"예상치 못한 오류: {e}", bo, "", {})
            return
        wx.CallAfter(self._post_action_done, action, res.ok, res.message, bo,
                     getattr(res, "response_snippet", "") or "",
                     dict(getattr(res, "debug", {}) or {}))

    def _post_action_done(self, action: str, ok: bool, message: str, bo: str,
                          snippet: str = "", debug: dict | None = None) -> None:
        verb = {"copy": "복사", "move": "이동", "delete": "삭제"}.get(action, action)
        mark = "[성공]" if ok else "[실패]"
        cur = self.post_result.GetValue()
        lines = [f"{mark} {verb}: {message}"]
        if snippet:
            lines.append(f"  └ 사이트 응답(진단용): {snippet[:500]}")
        # 이동/복사 팝업 응답을 진단 파일로 저장 (실제로 처리됐는지 분석용)
        for path in self._save_debug_html(debug or {}, action, bo):
            lines.append(f"  └ 응답 원본 저장됨: {path}")
        self.post_result.SetValue((cur + "\n" if cur else "") + "\n".join(lines))
        self.post_load_btn.Enable()
        self._set_post_buttons(self.post_list.GetCount() > 0)
        speak(f"게시물 {verb} {'완료' if ok else '실패'}. {message}")
        if action in ("delete", "move"):
            # 성공이든 실패든 현재 목록 상태를 다시 보여 준다 (사용자가 직접 확인 가능).
            page = int(self.post_page_spin.GetValue())
            self.post_load_btn.Disable()
            self._set_post_buttons(False)
            threading.Thread(
                target=self._load_posts_in_thread, args=(bo, page), daemon=True,
            ).start()
        if not ok:
            wx.MessageBox(f"{verb}: {message}", "처리 실패", wx.OK | wx.ICON_ERROR, self)
        elif action == "copy":
            wx.MessageBox(f"{verb}: {message}", "처리 결과", wx.OK | wx.ICON_INFORMATION, self)

    @staticmethod
    def _save_debug_html(debug: dict, action: str, bo: str) -> list[str]:
        if not debug:
            return []
        import os
        import time
        try:
            from config import DUMPS_DIR
        except Exception:
            DUMPS_DIR = os.path.join(os.path.expanduser("~"), "chorok_dumps")
        out: list[str] = []
        try:
            os.makedirs(DUMPS_DIR, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            for label, html in debug.items():
                safe = "".join(c if c.isalnum() else "_" for c in str(label)).strip("_") or "resp"
                path = os.path.join(DUMPS_DIR, f"{ts}_{action}_{bo}_{safe}.html")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(html or "")
                out.append(path)
        except Exception:
            pass
        return out

    # ---------- 게시물 관리: 진단 (목록 페이지 HTML 저장) ----------

    def _on_save_list_html(self, _event=None) -> None:
        bo = self.post_bo_input.GetValue().strip()
        if not bo:
            wx.MessageBox("게시판 아이디를 먼저 입력하세요.",
                          "입력 필요", wx.OK | wx.ICON_INFORMATION, self)
            return
        page = int(self.post_page_spin.GetValue())
        self.post_dump_btn.Disable()
        speak("목록 페이지 원본을 저장합니다.")
        threading.Thread(
            target=self._save_list_html_in_thread, args=(bo, page), daemon=True,
        ).start()

    def _save_list_html_in_thread(self, bo: str, page: int) -> None:
        import os
        import time
        try:
            from config import DUMPS_DIR
        except Exception:
            DUMPS_DIR = os.path.join(os.path.expanduser("~"), "chorok_dumps")
        try:
            html = fetch_board_list_html(self.session, bo, page)
            os.makedirs(DUMPS_DIR, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            path = os.path.join(DUMPS_DIR, f"{ts}_board_{bo}_p{page}.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(html or "")
            wx.CallAfter(self._save_list_html_done, True, path)
        except Exception as e:
            wx.CallAfter(self._save_list_html_done, False, str(e))

    def _save_list_html_done(self, ok: bool, info: str) -> None:
        self.post_dump_btn.Enable()
        if ok:
            speak("목록 페이지 원본 HTML을 저장했습니다.")
            cur = self.post_result.GetValue()
            self.post_result.SetValue((cur + "\n" if cur else "") + f"[진단] 목록 HTML 저장됨: {info}")
            wx.MessageBox(
                "게시판 목록 페이지의 원본 HTML을 저장했습니다:\n"
                f"{info}\n\n"
                "복사/이동/삭제가 실제로 안 되는 경우, 이 파일을 개발자에게 보내 주시면 "
                "이 사이트 게시판 구조에 맞게 고칠 수 있습니다.",
                "저장 완료", wx.OK | wx.ICON_INFORMATION, self,
            )
        else:
            speak("목록 페이지 원본 저장 실패.")
            wx.MessageBox(f"저장 실패: {info}", "오류", wx.OK | wx.ICON_ERROR, self)

    # ---------- 공통 ----------

    def _set_notice_busy(self, busy: bool) -> None:
        for b in (self.single_btn, self.bulk_btn, self.schedule_btn):
            if busy:
                b.Disable()
            else:
                b.Enable()

    def _announce(self) -> None:
        speak(
            "게시판 관리 화면. 맨 위 '화면 선택' 라디오 버튼에서 '게시판 설정', '공지 작성', "
            "'게시물 관리' 중 하나를 고릅니다. '게시판 설정' 은 게시판 아이디를 넣고 불러와 "
            "설정 항목을 고치는 화면, '공지 작성' 은 단일 또는 일괄 공지를 올리거나 예약하는 화면, "
            "'게시물 관리' 는 게시물 목록을 불러와 스페이스 키로 골라 복사, 이동, 삭제하는 화면입니다."
        )
