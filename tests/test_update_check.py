"""버전 비교 로직 (네트워크 호출은 안 건드림)."""
from __future__ import annotations


def test_normalize_version():
    from core.update_check import _normalize
    assert _normalize("1.0.0") == (1, 0, 0, 0)
    assert _normalize("v1.0.0") == (1, 0, 0, 0)
    assert _normalize("V0.5.1") == (0, 5, 1, 0)
    assert _normalize("1.0") == (1, 0, 0, 0)
    assert _normalize("") == (0, 0, 0, 0)


def test_is_newer():
    from core.update_check import is_newer
    assert is_newer("v1.0.0", "0.5.0")
    assert is_newer("1.0.1", "1.0.0")
    assert is_newer("1.1.0", "1.0.99")
    assert not is_newer("1.0.0", "1.0.0")
    assert not is_newer("v0.4.9", "1.0.0")
    assert not is_newer("0.4.0", "0.5.0")


def test_normalize_handles_pre_release():
    from core.update_check import _normalize
    # 시멘틱 변형 — pre-release 표기는 숫자만 추출
    n = _normalize("1.0.0-rc1")
    assert n[:3] == (1, 0, 0)
