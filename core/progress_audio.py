"""진행률 비프 — 진행이 올라갈수록 음높이가 올라가는 피드백.

긴 작업(회원 목록 수집, MVP 산정 등) 중 사용자가 "얼마나 남았나?"를 들어서
체감할 수 있도록, 일정 간격으로 winsound.Beep 한 번을 짧게 친다. 진행률
0% → 100% 사이에서 주파수가 선형으로 상승해 마지막에는 가장 높은 톤이
들린다 (예: 400Hz → 1600Hz).

설계 원칙
- 화자 발화(speak)와 충돌하지 않는 짧은 비프 (50~80ms).
- 워커 스레드를 막지 않도록 별도 데몬 스레드에서 재생.
- 같은 진행 단계가 빠르게 반복돼도 청각 부담을 주지 않게 진행률
  STEP_PERCENT 단위로 throttle (기본 5%).
- Windows 외 환경(테스트 등) 에서는 조용히 no-op.
"""
from __future__ import annotations

import platform
import threading


# 음역 — 시작·끝 주파수 (Hz). 인간 청각이 명확히 구분 가능한 범위.
DEFAULT_FREQ_LOW = 400
DEFAULT_FREQ_HIGH = 1600
# 비프 길이 — 너무 길면 발화·UI 와 충돌, 너무 짧으면 못 들음.
DEFAULT_DURATION_MS = 70
# 같은 작업 안에서 N% 이상 변화했을 때만 다음 비프 — 청각 부담 완화.
DEFAULT_STEP_PERCENT = 5


def _winsound_beep(freq: int, duration_ms: int) -> bool:
    if platform.system() != "Windows":
        return False
    try:
        import winsound  # type: ignore
    except Exception:
        return False
    try:
        winsound.Beep(int(freq), int(duration_ms))
        return True
    except Exception:
        return False


class ProgressAudio:
    """진행률 → 음높이. 한 작업 사이클 동안 반복적으로 update() 호출.

    Usage:
        audio = ProgressAudio()
        audio.update(0, 100)
        ...
        audio.update(50, 100)
        ...
        audio.update(100, 100)
    """

    def __init__(
        self,
        *,
        freq_low: int = DEFAULT_FREQ_LOW,
        freq_high: int = DEFAULT_FREQ_HIGH,
        duration_ms: int = DEFAULT_DURATION_MS,
        step_percent: int = DEFAULT_STEP_PERCENT,
    ) -> None:
        self.freq_low = max(37, freq_low)        # winsound.Beep 최저 37Hz
        self.freq_high = max(self.freq_low + 1, freq_high)
        self.duration_ms = duration_ms
        self.step_percent = max(1, step_percent)
        self._last_step: int = -1   # 마지막에 비프 친 step 단계 (0..100/step)

    def reset(self) -> None:
        """새 작업 시작 — 다음 update() 부터 다시 비프 시작."""
        self._last_step = -1

    def update(self, current: int, total: int) -> None:
        """진행률을 받아 step 경계를 넘었으면 비프 재생 (백그라운드)."""
        if total <= 0:
            return
        pct = (current / total) * 100.0
        if pct < 0:
            pct = 0.0
        if pct > 100:
            pct = 100.0
        step = int(pct // self.step_percent)
        if step <= self._last_step:
            return
        self._last_step = step
        freq = self._freq_for(pct)
        # 백그라운드 — 워커 스레드(통상 net I/O) 가 이 비프 70ms 기다리지 않도록.
        threading.Thread(
            target=_winsound_beep,
            args=(freq, self.duration_ms),
            daemon=True,
        ).start()

    def _freq_for(self, pct: float) -> int:
        ratio = pct / 100.0
        return int(self.freq_low + (self.freq_high - self.freq_low) * ratio)


# 단발 비프 — "시작/완료" 같은 단순 신호용.
def beep(frequency: int = 880, duration_ms: int = 100) -> None:
    """단발 비프. UI 스레드 차단 방지를 위해 별도 스레드에서 재생."""
    threading.Thread(
        target=_winsound_beep,
        args=(frequency, duration_ms),
        daemon=True,
    ).start()
