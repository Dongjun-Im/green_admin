"""백업 diff 로직 — load_snapshot 은 xlsx 파일 의존이라 diff_backups 만 단위 테스트."""
from __future__ import annotations

from pathlib import Path

from core.backup_diff import BackupSnapshot, diff_backups


def _snap(folder_name: str, members: dict[str, tuple[int, str]]) -> BackupSnapshot:
    return BackupSnapshot(
        folder=Path(folder_name),
        members={
            uid: {
                "user_id": uid,
                "nickname": f"닉_{uid}",
                "level": lv,
                "level_label": label,
                "last_login": "",
                "join_date": "",
            }
            for uid, (lv, label) in members.items()
        },
    )


def test_diff_no_change():
    old = _snap("2026-01-01", {"a": (7, "우수회원"), "b": (8, "최우수회원")})
    new = _snap("2026-04-01", {"a": (7, "우수회원"), "b": (8, "최우수회원")})
    d = diff_backups(old, new)
    assert d.new_entries == [] and d.dropped == []
    assert d.promoted == [] and d.demoted == []
    assert len(d.unchanged) == 2


def test_diff_new_entries_and_dropped():
    old = _snap("Q1", {"a": (7, "우수회원")})
    new = _snap("Q2", {"b": (8, "최우수회원")})
    d = diff_backups(old, new)
    assert {m["user_id"] for m in d.new_entries} == {"b"}
    assert {m["user_id"] for m in d.dropped} == {"a"}


def test_diff_promoted():
    old = _snap("Q1", {"a": (7, "우수회원")})
    new = _snap("Q2", {"a": (8, "최우수회원")})
    d = diff_backups(old, new)
    assert len(d.promoted) == 1
    rec = d.promoted[0]
    assert rec["user_id"] == "a"
    assert rec["old_level"] == 7
    assert rec["new_level"] == 8


def test_diff_demoted():
    old = _snap("Q1", {"a": (8, "최우수회원")})
    new = _snap("Q2", {"a": (7, "우수회원")})
    d = diff_backups(old, new)
    assert len(d.demoted) == 1
    assert d.demoted[0]["new_level"] == 7


def test_summary_text_format():
    old = _snap("Q1", {"a": (7, "우수회원"), "b": (8, "최우수회원")})
    new = _snap("Q2", {"a": (8, "최우수회원"), "c": (7, "우수회원")})
    d = diff_backups(old, new)
    s = d.summary
    assert "신규" in s and "승급" in s and "강등" in s and "빠짐" in s and "유지" in s
