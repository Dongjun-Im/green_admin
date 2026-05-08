"""승급 임박 회원 다이얼로그.

일반회원(6) 만 분석 대상. '우리들의 이야기' 게시판 글 수가
다음 임계값에 가까운 회원을 표시 — 격려 메일 등을 위한 운영 보조.

기준 (POST_COUNT_PROMOTION_TABLE 의 임계값으로부터 일정 범위 내):
  · 우수 임박:    20 ~ 29 건
  · 최우수 임박:  40 ~ 49 건
  · 명예 임박:    80 ~ 99 건
"""
from __future__ import annotations

from dataclasses import dataclass

import wx

from config import (
    LEVEL_LABELS,
    POST_COUNT_PROMOTION_BASE_LEVEL,
    POST_COUNT_PROMOTION_TABLE,
)
from core.crawler import MemberCrawler
from core.models import Member
from core.post_count_green3 import Green3PostCounter
from screen_reader import speak
from ui.item_text_ctrl import ItemTextCtrl


# (target_level, threshold) 별 "임박" 시작 비율
IMMINENT_BANDS = [
    # threshold, lower (포함), upper (불포함)
    # 명예: 80~99
    (100, 9, 80),
    # 최우수: 40~49
    (50, 8, 40),
    # 우수: 20~29
    (30, 7, 20),
]


@dataclass
class ImminentItem:
    member: Member
    post_count: int
    target_level: int
    target_threshold: int  # 도달 시 목표 등급 가는 임계값
    gap: int               # threshold - post_count

    def display(self) -> str:
        nick = self.member.nickname or self.member.name or self.member.user_id
        target = LEVEL_LABELS.get(self.target_level, f"레벨 {self.target_level}")
        return (
            f"{self.member.user_id} / {nick} / 글 {self.post_count}건 / "
            f"{target} 까지 {self.gap}건"
        )


def find_imminent(
    members: list[Member], post_counter: Green3PostCounter, progress_cb=None
) -> list[ImminentItem]:
    candidates = [m for m in members if m.level == POST_COUNT_PROMOTION_BASE_LEVEL]
    out: list[ImminentItem] = []
    total = len(candidates)
    for idx, m in enumerate(candidates, start=1):
        if progress_cb:
            try:
                progress_cb(idx, total)
            except Exception:
                pass
        n = post_counter.fetch(m.user_id)
        if n is None:
            continue
        m.post_count = n
        # 가장 큰 목표(명예)부터 검사 — 가장 큰 임박만 표시 (중복 방지)
        for threshold, target_lv, lower in IMMINENT_BANDS:
            if lower <= n < threshold:
                out.append(ImminentItem(
                    member=m,
                    post_count=n,
                    target_level=target_lv,
                    target_threshold=threshold,
                    gap=threshold - n,
                ))
                break
    out.sort(key=lambda it: (-it.target_level, it.gap))
    return out


class PromotionImminentDialog(wx.Dialog):
    """승급 임박자 분석 + 표시. session 이 None 이면 분석 기능 비활성."""

    def __init__(self, parent, crawler: MemberCrawler):
        super().__init__(
            parent,
            title="승급 임박 회원",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.crawler = crawler
        self.items: list[ImminentItem] = []
        self._build_ui()
        self.SetMinSize(wx.Size(640, 480))
        self.Fit()
        self.Centre()

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        bands_label = (
            "임박 기준:\n"
            f"  · 우수회원 임박: 20~29 건 (다음 임계 30)\n"
            f"  · 최우수 임박:   40~49 건 (다음 임계 50)\n"
            f"  · 명예 임박:     80~99 건 (다음 임계 100)\n"
            f"분석 대상: 일반회원(6) 전체. "
            f"분석에는 1~2분 정도 걸릴 수 있습니다."
        )
        sizer.Add(wx.StaticText(panel, label=bands_label), 0, wx.ALL, 10)

        analyze_btn = wx.Button(panel, wx.ID_ANY, "분석 시작(&A)")
        sizer.Add(analyze_btn, 0, wx.ALIGN_CENTER | wx.ALL, 5)

        sizer.Add(
            wx.StaticText(panel, label="결과(&L):"),
            0, wx.LEFT | wx.RIGHT, 10,
        )
        self.list_box = wx.ListBox(
            panel, choices=[], style=wx.LB_SINGLE, name="임박자 목록",
        )
        sizer.Add(self.list_box, 1, wx.EXPAND | wx.ALL, 10)

        self.detail = ItemTextCtrl(
            panel, value="",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_DONTWRAP,
            name="상세",
        )
        sizer.Add(self.detail, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        close_btn = wx.Button(panel, wx.ID_CLOSE, "닫기(&C)")
        sizer.Add(close_btn, 0, wx.ALIGN_CENTER | wx.ALL, 10)

        panel.SetSizer(sizer)
        analyze_btn.Bind(wx.EVT_BUTTON, self._on_analyze)
        self.list_box.Bind(wx.EVT_LISTBOX, self._on_select)
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

    def _on_analyze(self, event):
        # 모달 다이얼로그에서 무거운 분석을 동기 실행 — UI 잠시 멈춤.
        # progress 는 wx.SafeYield 로 살짝 풀어줌.
        speak("일반회원의 게시물 수를 분석합니다. 잠시 기다려 주세요.")
        try:
            members = self.crawler.fetch_all_members()
        except Exception as e:
            wx.MessageBox(f"회원 목록 수집 실패: {e}", "오류", wx.OK | wx.ICON_ERROR)
            return

        counter = Green3PostCounter(self.crawler.session)

        def progress(i, total):
            try:
                if i % 5 == 0:
                    self.list_box.Set(
                        [f"분석 중... {i} / {total}"]
                    )
                    wx.SafeYield()
            except Exception:
                pass

        self.items = find_imminent(members, counter, progress_cb=progress)
        self.list_box.Set([it.display() for it in self.items])
        if self.items:
            self.list_box.SetSelection(0)
            self._show_detail(0)
            speak(f"임박자 {len(self.items)}명 발견")
        else:
            self.detail.SetValue("(임박자 없음)")
            speak("임박자 없음")

    def _on_select(self, event):
        idx = self.list_box.GetSelection()
        if 0 <= idx < len(self.items):
            self._show_detail(idx)

    def _show_detail(self, idx: int) -> None:
        it = self.items[idx]
        m = it.member
        target = LEVEL_LABELS.get(it.target_level, f"레벨 {it.target_level}")
        lines = [
            f"아이디: {m.user_id}",
            f"이름·닉: {m.name} / {m.nickname}",
            f"현재 글 수: {it.post_count}건",
            f"목표 등급: {target} (임계값 {it.target_threshold}건)",
            f"남은 글: {it.gap}건",
        ]
        self.detail.SetValue("\n".join(lines))
        speak(it.display())

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CLOSE)
            return
        event.Skip()
