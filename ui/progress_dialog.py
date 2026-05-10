"""범용 진행률 다이얼로그 + 진행 비프 통합.

`task` 인자에 워커 함수를 넘기면 별도 스레드에서 실행하고, 워커가 호출하는
콜백을 통해 진행률을 업데이트한다. 진행이 올라갈수록 음높이가 상승하는
짧은 비프가 함께 재생된다.

워커 함수 시그니처:
    def task(progress_cb: Callable[[int, int, str], None]) -> Any
        progress_cb(current, total, message="") 형태로 진행률 보고.

사용 예:
    def worker(progress_cb):
        members = crawler.fetch_all_members(
            progress_cb=lambda c, t: progress_cb(c, t, f"페이지 {c}/{t}")
        )
        return members

    with ProgressTaskDialog(parent, title="회원 목록", task=worker) as dlg:
        dlg.run_modal()
        result = dlg.result        # 워커 반환값
        error = dlg.error          # 워커 예외 (없으면 None)
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

import wx

from core.progress_audio import ProgressAudio, beep


WorkerFn = Callable[[Callable[..., None]], Any]


class ProgressTaskDialog(wx.Dialog):
    def __init__(
        self,
        parent,
        *,
        title: str,
        task: WorkerFn,
        message: str = "진행 중입니다...",
        can_cancel: bool = False,
    ) -> None:
        super().__init__(
            parent, title=title,
            style=wx.CAPTION | wx.RESIZE_BORDER,
        )
        self._task = task
        self._can_cancel = can_cancel
        self._cancel_event = threading.Event()
        self._audio = ProgressAudio()
        self.result: Any = None
        self.error: Optional[Exception] = None
        self._build_ui(message)
        self.SetMinSize(wx.Size(420, 180))
        self.Fit()
        self.Centre()

    def _build_ui(self, message: str) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.label = wx.StaticText(
            panel, label=message, name="진행 안내",
        )
        sizer.Add(self.label, 0, wx.ALL | wx.EXPAND, 12)

        self.gauge = wx.Gauge(
            panel, range=100, name="진행률 게이지",
            style=wx.GA_HORIZONTAL,
        )
        self.gauge.SetValue(0)
        sizer.Add(self.gauge, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 12)

        self.detail = wx.StaticText(
            panel, label="0% (0/0)", name="진행 상세",
        )
        sizer.Add(self.detail, 0, wx.ALL, 12)

        if self._can_cancel:
            self.cancel_btn = wx.Button(panel, wx.ID_CANCEL, "취소(&C)")
            self.cancel_btn.Bind(wx.EVT_BUTTON, self._on_cancel)
            sizer.Add(self.cancel_btn, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        panel.SetSizer(sizer)
        sizer.SetSizeHints(self)

    # ---------- 외부 API ----------

    def run_modal(self) -> int:
        """워커를 백그라운드에서 시작하고 다이얼로그를 modal 로 띄운다."""
        self._audio.reset()
        beep(660, 80)  # 시작 신호
        threading.Thread(target=self._worker_main, daemon=True).start()
        return self.ShowModal()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    # ---------- 내부 ----------

    def _on_cancel(self, _event=None) -> None:
        # 워커가 협력적으로 cancel 검사를 한다는 가정 — 검사 없으면 무시됨.
        self._cancel_event.set()
        if hasattr(self, "cancel_btn"):
            self.cancel_btn.Disable()
        self.label.SetLabel("취소 요청됨 - 곧 종료합니다...")

    def _worker_main(self) -> None:
        try:
            self.result = self._task(self._progress_cb)
        except Exception as e:
            self.error = e
        wx.CallAfter(self._finish)

    def _progress_cb(
        self, current: int, total: int, message: str = "",
    ) -> None:
        """워커 콜백 — 백그라운드 스레드에서 호출됨. UI 갱신은 CallAfter."""
        if self._cancel_event.is_set():
            return
        # 비프는 백그라운드에서 즉시 (별도 스레드를 다시 씀).
        self._audio.update(current, total)
        wx.CallAfter(self._apply_progress, current, total, message)

    def _apply_progress(
        self, current: int, total: int, message: str,
    ) -> None:
        if total <= 0:
            pct = 0
        else:
            pct = int(min(100, max(0, current / total * 100)))
        self.gauge.SetValue(pct)
        self.detail.SetLabel(f"{pct}% ({current}/{total})")
        if message:
            self.label.SetLabel(message)

    def _finish(self) -> None:
        # 완료 비프 — 톤 한 번 내려서 마무리감.
        beep(523, 120)
        if self.IsModal():
            self.EndModal(wx.ID_OK if self.error is None else wx.ID_CANCEL)
        else:
            self.Close()
