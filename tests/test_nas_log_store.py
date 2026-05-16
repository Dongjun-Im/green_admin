"""NasLogStore — SQLite 저장/필터/메타 단위 테스트.

실제 DSM 호출 없이 NasLogEntry 를 직접 만들어 upsert 한 뒤 필터·메타를 검증.
임시 디렉터리에 DB 를 만들어 사용자 data/ 는 절대 건드리지 않음.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from core.nas_log_store import NasLogEntry, NasLogFilter, NasLogStore


def _entry(
    when: str = "2026-05-12T14:23:11",
    user: str = "anycall",
    action: str = "download",
    path: str = "/photo/엔터테인먼트/a.mp3",
    raw_hash: str | None = None,
    ip: str = "121.129.43.13",
) -> NasLogEntry:
    return NasLogEntry(
        logged_at=when,
        dsm_user_id=user,
        ip=ip,
        protocol="WebDAV",
        action=action,
        category=path.lstrip("/").split("/", 1)[0] if path else "",
        file_name=Path(path).name if path else "",
        file_path=path,
        raw_message=f"User [{user}] downloaded [{path}]",
        raw_hash=raw_hash or f"{when}-{user}-{action}-{path}",
    )


def _store(tmp_path: Path) -> NasLogStore:
    return NasLogStore(tmp_path / "nas.db")


# ---------- 기본 생성·upsert ----------

def test_init_creates_db_and_tables(tmp_path):
    store = _store(tmp_path)
    assert (tmp_path / "nas.db").exists()
    assert store.count() == 0


def test_upsert_inserts_and_dedups(tmp_path):
    store = _store(tmp_path)
    e1 = _entry()
    e2 = _entry(when="2026-05-12T14:24:00", user="kim", action="upload",
                path="/photo/foo.jpg")
    added, skipped = store.upsert_entries([e1, e2])
    assert (added, skipped) == (2, 0)
    # 같은 raw_hash 로 다시 upsert → 중복 건너뜀
    added2, skipped2 = store.upsert_entries([e1, e2])
    assert (added2, skipped2) == (0, 2)
    assert store.count() == 2


def test_upsert_normalizes_user_to_lower(tmp_path):
    store = _store(tmp_path)
    store.upsert_entries([_entry(user="AnyCall", raw_hash="x")])
    rows = store.entries()
    assert rows[0].dsm_user_id == "anycall"


# ---------- 메타 (latest_epoch / last_status) ----------

def test_latest_epoch_default_zero(tmp_path):
    assert _store(tmp_path).latest_epoch() == 0


def test_latest_epoch_monotonic(tmp_path):
    store = _store(tmp_path)
    store.set_latest_epoch(1000)
    assert store.latest_epoch() == 1000
    # 더 작은 값으로는 안 내려간다 (증분 수집의 안정성 보장).
    store.set_latest_epoch(500)
    assert store.latest_epoch() == 1000
    store.set_latest_epoch(2000)
    assert store.latest_epoch() == 2000


def test_last_status_roundtrip(tmp_path):
    store = _store(tmp_path)
    store.set_last_status(True, "10건 추가")
    ok, msg, at = store.last_status()
    assert ok is True
    assert msg == "10건 추가"
    assert at  # ISO 타임스탬프 문자열


def test_last_status_failure(tmp_path):
    store = _store(tmp_path)
    store.set_last_status(False, "DSM 설정이 없습니다")
    ok, msg, _ = store.last_status()
    assert ok is False and "DSM" in msg


# ---------- 필터 ----------

def test_filter_by_date_range(tmp_path):
    store = _store(tmp_path)
    store.upsert_entries([
        _entry(when="2026-05-10T10:00:00", raw_hash="a"),
        _entry(when="2026-05-12T10:00:00", raw_hash="b"),
        _entry(when="2026-05-15T10:00:00", raw_hash="c"),
    ])
    flt = NasLogFilter(start_date=date(2026, 5, 11), end_date=date(2026, 5, 13))
    rows = store.entries(flt)
    assert {r.raw_hash for r in rows} == {"b"}


def test_filter_by_user_partial(tmp_path):
    store = _store(tmp_path)
    store.upsert_entries([
        _entry(user="anycall", raw_hash="a"),
        _entry(user="anyhow",  raw_hash="b"),
        _entry(user="kmk8030", raw_hash="c"),
    ])
    rows = store.entries(NasLogFilter(dsm_user_id_like="any"))
    assert {r.dsm_user_id for r in rows} == {"anycall", "anyhow"}


def test_filter_by_action(tmp_path):
    store = _store(tmp_path)
    store.upsert_entries([
        _entry(action="download", raw_hash="a"),
        _entry(action="delete",   raw_hash="b"),
        _entry(action="login",    raw_hash="c"),
    ])
    rows = store.entries(NasLogFilter(action_in=["delete", "download"]))
    assert {r.action for r in rows} == {"delete", "download"}


def test_filter_by_category(tmp_path):
    store = _store(tmp_path)
    store.upsert_entries([
        _entry(path="/엔터테인먼트/a.mp3", raw_hash="a"),
        _entry(path="/시리즈/b.mp4",      raw_hash="b"),
        _entry(path="/엔터테인먼트/c.mp3", raw_hash="c"),
    ])
    rows = store.entries(NasLogFilter(category_like="엔터"))
    assert {r.raw_hash for r in rows} == {"a", "c"}


def test_filter_combined(tmp_path):
    store = _store(tmp_path)
    store.upsert_entries([
        _entry(when="2026-05-10T10:00:00", user="anycall", action="delete",
               path="/엔터테인먼트/a.mp3", raw_hash="A"),
        _entry(when="2026-05-10T10:00:00", user="anycall", action="download",
               path="/엔터테인먼트/a.mp3", raw_hash="B"),
        _entry(when="2026-05-10T10:00:00", user="kim", action="delete",
               path="/엔터테인먼트/a.mp3", raw_hash="C"),
    ])
    rows = store.entries(NasLogFilter(
        start_date=date(2026, 5, 1), end_date=date(2026, 5, 31),
        dsm_user_id_like="anycall", action_in=["delete"],
        category_like="엔터",
    ))
    assert {r.raw_hash for r in rows} == {"A"}


def test_entries_ordered_desc(tmp_path):
    store = _store(tmp_path)
    store.upsert_entries([
        _entry(when="2026-05-10T10:00:00", raw_hash="a"),
        _entry(when="2026-05-12T10:00:00", raw_hash="b"),
        _entry(when="2026-05-11T10:00:00", raw_hash="c"),
    ])
    rows = store.entries()
    # 최신순(DESC) 정렬
    assert [r.raw_hash for r in rows] == ["b", "c", "a"]


def test_entries_respects_limit(tmp_path):
    store = _store(tmp_path)
    store.upsert_entries([
        _entry(when=f"2026-05-{i:02d}T10:00:00", raw_hash=str(i))
        for i in range(1, 11)
    ])
    rows = store.entries(NasLogFilter(limit=3))
    assert len(rows) == 3
