"""자동 스케줄러 관리 다이얼로그 — Windows 작업 스케줄러 등록/해제 (v1.3.1).

스크린리더 친화 ListBox 구조:
  · 작업 키, 설명, 등록 여부, 다음 실행 시각, 마지막 결과 코드 한 줄로.
  · 'V' (체크) / '.' (해제) 마커가 줄 맨 앞 — 음성으로 한 글자에 상태 인식.
"""
from __future__ import annotations

import threading
from typing import Optional

import wx

from core.scheduler_setup import (
    DEFAULT_SCHEDULES,
    TaskStatus,
    query_status,
    register_task,
    unregister_task,
)
from screen_reader import speak
from ui.item_text_ctrl import ItemTextCtrl


class SchedulerDialog(wx.Dialog):
    """자동 스케줄러 등록/해제 GUI.

    `core/scheduler_setup.py` 의 함수만 호출하므로 schtasks.exe 외 의존성 없음.
    """

    def __init__(self, parent) -> None:
        super().__init__(
            parent,
            title="자동 스케줄러 관리",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._statuses: list[TaskStatus] = []
        self._build_ui()
        self.SetMinSize(wx.Size(720, 480))
        self.Fit()
        self.Centre()
        wx.CallAfter(self._refresh)

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # 안내 문구
        intro = wx.StaticText(
            panel,
            label=(
                "Windows 작업 스케줄러에 등록된 자동 작업 목록입니다. "
                "선택하고 등록/해제 버튼을 누르세요. "
                "한 번 등록하면 PC 가 켜져 있고 잠금 화면이라도 정해진 시각에 자동 실행됩니다."
            ),
        )
        intro.Wrap(680)
        sizer.Add(intro, 0, wx.ALL, 10)

        # 사전 조건 안내
        prereq = wx.StaticText(
            panel,
            label=(
                "전제: 1) rtgreen 아이디로 일반 모드 로그인 한 번 완료 "
                "(자격증명 저장). 2) 2단계 인증 비활성. "
                "3) 작업이 실행되는 시각에 PC 가 켜져 있어야 함."
            ),
            style=wx.ST_NO_AUTORESIZE,
        )
        prereq.Wrap(680)
        sizer.Add(prereq, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        list_label = wx.StaticText(
            panel,
            label="작업 목록(&L) — V 등록됨, 점 미등록. 위/아래 화살표로 이동:",
        )
        sizer.Add(list_label, 0, wx.LEFT | wx.RIGHT, 10)

        self.list_box = wx.ListBox(
            panel, choices=[], style=wx.LB_SINGLE, name="작업 목록",
        )
        sizer.Add(self.list_box, 1, wx.EXPAND | wx.ALL, 10)

        detail_label = wx.StaticText(panel, label="선택한 작업 상세(&D):")
        sizer.Add(detail_label, 0, wx.LEFT | wx.RIGHT, 10)
        self.detail = ItemTextCtrl(
            panel,
            value="",
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_DONTWRAP,
            name="작업 상세",
        )
        sizer.Add(self.detail, 0, wx.EXPAND | wx.ALL, 10)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.register_btn = wx.Button(panel, wx.ID_ANY, "선택 등록(&R)")
        self.unregister_btn = wx.Button(panel, wx.ID_ANY, "선택 해제(&U)")
        self.register_all_btn = wx.Button(panel, wx.ID_ANY, "모두 등록(&A)")
        self.unregister_all_btn = wx.Button(panel, wx.ID_ANY, "모두 해제(&E)")
        self.refresh_btn = wx.Button(panel, wx.ID_ANY, "새로고침(&F)")
        close_btn = wx.Button(panel, wx.ID_CLOSE, "닫기(&C)")
        for b in (
            self.register_btn, self.unregister_btn,
            self.register_all_btn, self.unregister_all_btn,
            self.refresh_btn, close_btn,
        ):
            btn_sizer.Add(b, 1, wx.ALL | wx.EXPAND, 4)
        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 4)

        panel.SetSizer(sizer)

        self.list_box.Bind(wx.EVT_LISTBOX, self._on_select)
        self.register_btn.Bind(wx.EVT_BUTTON, self._on_register)
        self.unregister_btn.Bind(wx.EVT_BUTTON, self._on_unregister)
        self.register_all_btn.Bind(wx.EVT_BUTTON, self._on_register_all)
        self.unregister_all_btn.Bind(wx.EVT_BUTTON, self._on_unregister_all)
        self.refresh_btn.Bind(wx.EVT_BUTTON, lambda e: self._refresh())
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))

    # ---------- 상태 조회 ----------

    def _refresh(self) -> None:
        """schtasks 를 조회해 목록을 다시 그린다 (스레드 안 씀 — schtasks 가
        빠른 명령이라 메인 스레드에서도 즉시 응답)."""
        self.list_box.SetSelection(wx.NOT_FOUND)
        try:
            self._statuses = query_status()
        except Exception as e:
            self._statuses = []
            wx.MessageBox(
                f"스케줄러 조회 실패: {e}\n\n"
                "Windows 작업 스케줄러 서비스가 켜져 있는지 확인해 주세요.",
                "오류", wx.OK | wx.ICON_ERROR,
            )
            return
        self.list_box.Set([self._format_entry(st) for st in self._statuses])
        if self._statuses:
            self.list_box.SetSelection(0)
            self._update_detail(0)
        registered = sum(1 for st in self._statuses if st.registered)
        speak(f"전체 {len(self._statuses)}개 중 {registered}개 등록됨")

    def _format_entry(self, st: TaskStatus) -> str:
        mark = "[V]" if st.registered else "[ . ]"
        return f"{mark} {st.task_key:18s} — {st.description}"

    def _update_detail(self, idx: int) -> None:
        if not (0 <= idx < len(self._statuses)):
            return
        st = self._statuses[idx]
        lines = [
            f"작업 키: {st.task_key}",
            f"작업 이름: {st.raw_task_name}",
            f"설명: {st.description}",
            f"등록 여부: {'등록됨' if st.registered else '등록 안 됨'}",
        ]
        if st.registered:
            if st.next_run:
                lines.append(f"다음 실행: {st.next_run}")
            if st.last_result:
                lines.append(f"마지막 결과 코드: {st.last_result}")
        self.detail.SetValue("\n".join(lines))
        speak(self._format_entry(st))

    def _on_select(self, event) -> None:
        idx = self.list_box.GetSelection()
        if idx >= 0:
            self._update_detail(idx)

    # ---------- 등록/해제 ----------

    def _selected_status(self) -> Optional[TaskStatus]:
        idx = self.list_box.GetSelection()
        if 0 <= idx < len(self._statuses):
            return self._statuses[idx]
        return None

    def _on_register(self, event=None) -> None:
        st = self._selected_status()
        if st is None:
            return
        self._do_register([st.task_key])

    def _on_unregister(self, event=None) -> None:
        st = self._selected_status()
        if st is None:
            return
        self._do_unregister([st.task_key])

    def _on_register_all(self, event=None) -> None:
        confirm = wx.MessageBox(
            f"모든 작업({len(self._statuses)}개) 을 자동 스케줄러에 등록합니다.\n"
            "이미 등록된 작업은 같은 설정으로 덮어쓰입니다.\n계속하시겠습니까?",
            "모두 등록", wx.YES_NO | wx.ICON_QUESTION,
        )
        if confirm != wx.YES:
            return
        self._do_register([st.task_key for st in self._statuses])

    def _on_unregister_all(self, event=None) -> None:
        registered = [st for st in self._statuses if st.registered]
        if not registered:
            wx.MessageBox(
                "해제할 작업이 없습니다. (등록된 작업이 없음)",
                "해제 없음", wx.OK | wx.ICON_INFORMATION,
            )
            return
        confirm = wx.MessageBox(
            f"등록된 {len(registered)}개 작업을 모두 해제합니다.\n계속하시겠습니까?",
            "모두 해제", wx.YES_NO | wx.ICON_QUESTION,
        )
        if confirm != wx.YES:
            return
        self._do_unregister([st.task_key for st in registered])

    def _do_register(self, task_keys: list[str]) -> None:
        speak(f"{len(task_keys)}개 작업 등록 중")
        ok_keys: list[str] = []
        fail: list[tuple[str, str]] = []
        for key in task_keys:
            ok, msg = register_task(key)
            if ok:
                ok_keys.append(key)
            else:
                fail.append((key, msg))
        self._refresh()
        self._show_result("등록", ok_keys, fail)

    def _do_unregister(self, task_keys: list[str]) -> None:
        speak(f"{len(task_keys)}개 작업 해제 중")
        ok_keys: list[str] = []
        fail: list[tuple[str, str]] = []
        for key in task_keys:
            ok, msg = unregister_task(key)
            if ok:
                ok_keys.append(key)
            else:
                fail.append((key, msg))
        self._refresh()
        self._show_result("해제", ok_keys, fail)

    def _show_result(
        self, action_label: str, ok_keys: list[str],
        fail: list[tuple[str, str]],
    ) -> None:
        parts = [f"{action_label} 결과: 성공 {len(ok_keys)}개, 실패 {len(fail)}개"]
        if ok_keys:
            parts.append("\n[성공] " + ", ".join(ok_keys))
        if fail:
            parts.append(
                "\n[실패]\n" + "\n".join(f"  · {k}: {m}" for k, m in fail)
            )
        msg = "\n".join(parts)
        speak(parts[0])
        wx.MessageBox(
            msg, f"스케줄러 {action_label} 결과",
            wx.OK | (wx.ICON_INFORMATION if not fail else wx.ICON_WARNING),
        )
