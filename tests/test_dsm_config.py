"""DSM 설정·자격증명 저장 단위 테스트 — 암호화 라운드트립."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.dsm_config import (
    DsmSettings,
    delete_dsm_settings,
    load_dsm_settings,
    save_dsm_settings,
)


def test_round_trip(tmp_path: Path):
    path = tmp_path / "dsm.ini"
    s = DsmSettings(
        url="https://dsm.example.com:5001",
        account="admin",
        password="secret-pw-한글-!@#",
        group_name="자료실 회원",
        use_2fa=True,
        verify_ssl=False,
    )
    save_dsm_settings(s, path=path)
    assert path.exists()
    loaded = load_dsm_settings(path=path)
    assert loaded is not None
    assert loaded.url == s.url
    assert loaded.account == s.account
    assert loaded.password == s.password
    assert loaded.group_name == s.group_name
    assert loaded.use_2fa is True
    assert loaded.verify_ssl is False


def test_load_missing_file_returns_none(tmp_path: Path):
    assert load_dsm_settings(path=tmp_path / "nope.ini") is None


def test_password_is_encrypted_at_rest(tmp_path: Path):
    """파일 안에 평문 비밀번호가 보이면 안 됨."""
    path = tmp_path / "dsm.ini"
    s = DsmSettings(
        url="https://dsm.example.com",
        account="admin",
        password="plaintext_should_not_appear",
        group_name="자료실 회원",
    )
    save_dsm_settings(s, path=path)
    raw = path.read_text(encoding="utf-8")
    assert "plaintext_should_not_appear" not in raw
    # account 도 암호화 — 평문 노출 X
    assert "admin\n" not in raw  # 평문 줄로 따로 노출되지는 않음


def test_is_complete_flag():
    assert DsmSettings(url="https://x", account="a", password="p", group_name="g").is_complete
    assert not DsmSettings().is_complete
    assert not DsmSettings(url="x", account="a", password="p").is_complete  # group 없음


def test_delete_removes_file(tmp_path: Path):
    path = tmp_path / "dsm.ini"
    save_dsm_settings(
        DsmSettings(url="x", account="a", password="p", group_name="g"),
        path=path,
    )
    assert path.exists()
    delete_dsm_settings(path)
    assert not path.exists()
    # 멱등 — 없는 상태에서 삭제 호출도 안전
    delete_dsm_settings(path)
