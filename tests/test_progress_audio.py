"""ProgressAudio — 진행률 → 음높이 매핑 + step throttling 회귀 보호.

실제 winsound.Beep 호출은 monkeypatch 로 가로채서 (frequency, duration) 튜플
리스트에 기록한 뒤 검증한다. 음을 직접 검증하지 않으므로 테스트 환경
독립적이다.
"""
from __future__ import annotations

from core import progress_audio
from core.progress_audio import ProgressAudio


def _patch_beep(monkeypatch):
    calls: list[tuple[int, int]] = []

    def fake_beep(freq, dur):
        calls.append((int(freq), int(dur)))
        return True

    monkeypatch.setattr(progress_audio, "_winsound_beep", fake_beep)
    # 백그라운드 스레드 대신 동기 호출 — 테스트 결정성을 위해.
    import core.progress_audio as pa

    class _FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr(pa.threading, "Thread", _FakeThread)
    return calls


# ---------- 음높이 매핑 ----------

def test_freq_at_zero_percent_is_low(monkeypatch):
    calls = _patch_beep(monkeypatch)
    audio = ProgressAudio(freq_low=400, freq_high=1600, step_percent=5)
    audio.update(0, 100)
    assert calls
    assert calls[0][0] == 400


def test_freq_at_full_is_high(monkeypatch):
    calls = _patch_beep(monkeypatch)
    audio = ProgressAudio(freq_low=400, freq_high=1600, step_percent=5)
    audio.update(100, 100)
    assert calls[-1][0] == 1600


def test_freq_rises_monotonically(monkeypatch):
    calls = _patch_beep(monkeypatch)
    audio = ProgressAudio(freq_low=400, freq_high=1600, step_percent=10)
    for c in (10, 30, 60, 100):
        audio.update(c, 100)
    freqs = [f for f, _ in calls]
    assert freqs == sorted(freqs)
    assert freqs[0] < freqs[-1]


# ---------- step throttling ----------

def test_within_same_step_no_extra_beep(monkeypatch):
    calls = _patch_beep(monkeypatch)
    audio = ProgressAudio(step_percent=10)
    audio.update(5, 100)   # step 0 → 비프
    audio.update(7, 100)   # 같은 step 0 → no
    audio.update(9, 100)   # 같은 step 0 → no
    assert len(calls) == 1


def test_step_boundary_triggers_new_beep(monkeypatch):
    calls = _patch_beep(monkeypatch)
    audio = ProgressAudio(step_percent=10)
    audio.update(5, 100)    # step 0
    audio.update(11, 100)   # step 1
    audio.update(21, 100)   # step 2
    assert len(calls) == 3


def test_reset_allows_first_step_again(monkeypatch):
    calls = _patch_beep(monkeypatch)
    audio = ProgressAudio(step_percent=10)
    audio.update(50, 100)
    n_after_first = len(calls)
    audio.reset()
    audio.update(0, 100)
    assert len(calls) == n_after_first + 1


# ---------- edge cases ----------

def test_zero_total_no_beep(monkeypatch):
    calls = _patch_beep(monkeypatch)
    audio = ProgressAudio()
    audio.update(0, 0)
    audio.update(5, 0)
    assert calls == []


def test_overflow_clipped_to_high(monkeypatch):
    calls = _patch_beep(monkeypatch)
    audio = ProgressAudio(freq_low=400, freq_high=1600, step_percent=5)
    audio.update(150, 100)  # 100% 로 클리핑
    assert calls[-1][0] == 1600


def test_negative_clipped_to_low(monkeypatch):
    calls = _patch_beep(monkeypatch)
    audio = ProgressAudio(freq_low=400, freq_high=1600, step_percent=5)
    audio.update(-10, 100)
    assert calls[0][0] == 400
