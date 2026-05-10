"""자료실 구독비 import 시 입금자 ↔ 회원 매핑 다이얼로그.

자동 매칭에 실패한 입금자명 한 건씩 운영자가 회원을 지정한다.
선택 결과는 PaymentMatcher 가 aliases 테이블에 저장 → 다음 import 부터 자동.

키보드 흐름:
    · 화면 열리면 입금자명·거래 정보가 자동 음성 안내.
    · 후보 회원이 있으면 라디오버튼에 포커스, 화살표로 선택.
    · 후보 외 다른 회원은 검색창에서 이름·닉네임·아이디로 좁힌 뒤
      목록에서 골라 "선택" 버튼 (Enter).
    · 건너뛰기(ESC) 시 이번 거래는 매핑 안 함, 다음 import 에서 다시 묻는다.
"""
from __future__ import annotations

import wx

from core.models import Member
from core.payment_store import Transaction
from screen_reader import speak


class PaymentAliasDialog(wx.Dialog):
    """반환값: self.chosen_member (선택 시 Member, 건너뛰면 None)."""

    def __init__(
        self,
        parent,
        *,
        payer_name: str,
        candidates: list[Member],
        unprocessed_txns: list[Transaction],
        all_members: list[Member],
    ) -> None:
        title = f"입금자 매핑 — {payer_name}"
        super().__init__(
            parent,
            title=title,
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.payer_name = payer_name
        self.candidates = candidates
        self.txns = unprocessed_txns
        self.all_members = all_members
        # 검색 필터 적용된 결과 (회원 객체 list)
        self._filtered: list[Member] = list(all_members)
        # 선택 결과
        self.chosen_member: Member | None = None

        self._build_ui()
        self.SetMinSize(wx.Size(560, 520))
        self.Fit()
        self.Centre()
        wx.CallAfter(self._announce_open)

    # ---------- UI ----------

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # 입금 요약
        total = sum(t.amount for t in self.txns)
        latest = max((t.txn_at for t in self.txns), default=None)
        info_lines = [
            f"입금자명: {self.payer_name}",
            f"미처리 거래: {len(self.txns)}건  /  총 {total:,}원",
        ]
        if latest is not None:
            info_lines.append(f"최근 거래: {latest.strftime('%Y-%m-%d %H:%M')}")
        info_text = wx.StaticText(panel, label="\n".join(info_lines))
        sizer.Add(info_text, 0, wx.ALL, 10)

        # 후보 회원 (동명이인) — 있으면 라디오, 없으면 안내문
        if self.candidates:
            choices = [self._fmt(m) for m in self.candidates]
            self.cand_radio = wx.RadioBox(
                panel,
                label="실명이 일치하는 회원(&C)",
                choices=choices,
                majorDimension=1,
                style=wx.RA_SPECIFY_COLS,
            )
            sizer.Add(self.cand_radio, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        else:
            self.cand_radio = None
            sizer.Add(
                wx.StaticText(
                    panel,
                    label="실명이 정확히 일치하는 회원이 없습니다. "
                          "아래에서 검색해 주세요.",
                ),
                0, wx.LEFT | wx.RIGHT, 10,
            )

        # 검색 입력
        sizer.Add(
            wx.StaticText(panel, label="다른 회원 검색(&S) — 아이디·이름·닉네임:"),
            0, wx.TOP | wx.LEFT | wx.RIGHT, 10,
        )
        self.search_input = wx.TextCtrl(panel, name="검색")
        self.search_input.Bind(wx.EVT_TEXT, self._on_search)
        sizer.Add(self.search_input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # 회원 목록
        self.list_box = wx.ListBox(panel, choices=[self._fmt(m) for m in self._filtered])
        self.list_box.Bind(wx.EVT_LISTBOX_DCLICK, self._on_pick_from_list)
        sizer.Add(self.list_box, 1, wx.EXPAND | wx.ALL, 10)

        # 버튼
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.ok_btn = wx.Button(panel, wx.ID_OK, "이 회원으로 매핑(&M)")
        self.skip_btn = wx.Button(panel, wx.ID_CANCEL, "건너뛰기(&S)")
        self.ok_btn.Bind(wx.EVT_BUTTON, self._on_ok)
        btn_sizer.Add(self.ok_btn, 0, wx.RIGHT, 8)
        btn_sizer.Add(self.skip_btn, 0)
        sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        panel.SetSizer(sizer)
        self.SetEscapeId(wx.ID_CANCEL)
        self.SetAffirmativeId(wx.ID_OK)

        # 초기 포커스: 후보가 있으면 라디오, 없으면 검색 입력
        if self.cand_radio is not None:
            self.cand_radio.SetFocus()
        else:
            self.search_input.SetFocus()

    @staticmethod
    def _fmt(m: Member) -> str:
        nick = f" ({m.nickname})" if m.nickname else ""
        return f"{m.user_id} / {m.name}{nick} / {m.level_label or '레벨 ' + str(m.level)}"

    # ---------- 이벤트 ----------

    def _on_search(self, _event=None) -> None:
        kw = self.search_input.GetValue().strip().lower()
        if not kw:
            self._filtered = list(self.all_members)
        else:
            self._filtered = [
                m for m in self.all_members
                if kw in m.user_id.lower()
                or kw in (m.name or "").lower()
                or kw in (m.nickname or "").lower()
            ]
        self.list_box.Set([self._fmt(m) for m in self._filtered])

    def _on_pick_from_list(self, _event=None) -> None:
        idx = self.list_box.GetSelection()
        if idx < 0 or idx >= len(self._filtered):
            return
        # 후보 라디오 선택은 무효화하고 목록 선택을 우선시
        if self.cand_radio is not None:
            # 라디오엔 "후보 사용 안 함" 상태가 없어, 사용자가 목록에서 골랐으면
            # _on_ok 에서 list_box 우선으로 처리하도록 플래그 대신 selection 만 사용.
            pass
        self.chosen_member = self._filtered[idx]
        self.EndModal(wx.ID_OK)

    def _on_ok(self, _event=None) -> None:
        # 결정 우선순위:
        #   1) 검색 목록에서 선택된 항목이 있으면 그것
        #   2) 그렇지 않으면 후보 라디오의 선택
        list_idx = self.list_box.GetSelection()
        if list_idx >= 0 and list_idx < len(self._filtered):
            self.chosen_member = self._filtered[list_idx]
        elif self.cand_radio is not None:
            ci = self.cand_radio.GetSelection()
            if 0 <= ci < len(self.candidates):
                self.chosen_member = self.candidates[ci]

        if self.chosen_member is None:
            speak("매핑할 회원이 선택되지 않았습니다. 후보를 고르거나 목록에서 선택해 주세요.")
            wx.MessageBox(
                "후보 회원을 선택하거나 검색 목록에서 한 명을 골라 주세요.",
                "선택 필요",
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        self.EndModal(wx.ID_OK)

    # ---------- 음성 안내 ----------

    def _announce_open(self) -> None:
        msg = f"입금자 매핑. {self.payer_name}, 미처리 {len(self.txns)}건. "
        if self.candidates:
            msg += f"실명 일치 회원 {len(self.candidates)}명. "
        else:
            msg += "실명 일치 회원 없음. 검색에서 회원을 찾아 주세요. "
        speak(msg)
