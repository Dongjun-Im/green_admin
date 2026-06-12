"""예약 공지 큐 저장소 단위 테스트.

tmp_path 로 격리 — 사용자 data/scheduled_notices.json 을 절대 건드리지 않음.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from core.scheduled_notice import (
    STATUS_CANCELED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_POSTED,
    ScheduledNotice,
    ScheduledNoticeStore,
)


def _store(tmp_path):
    return ScheduledNoticeStore(tmp_path / "scheduled_notices.json")


def _notice(when: datetime, subject="제목", boards=("green1",)):
    return ScheduledNotice(
        scheduled_at=when.isoformat(timespec="seconds"),
        boards=list(boards), subject=subject, content="본문",
    )


def test_add_assigns_id_and_created(tmp_path):
    store = _store(tmp_path)
    n = store.add(_notice(datetime.now() + timedelta(hours=1)))
    assert n.id
    assert n.created_at
    assert n.status == STATUS_PENDING


def test_roundtrip_persists_to_disk(tmp_path):
    path = tmp_path / "scheduled_notices.json"
    s1 = ScheduledNoticeStore(path)
    s1.add(_notice(datetime.now() + timedelta(hours=2), subject="안녕"))
    # 같은 파일을 새 인스턴스로 다시 읽기
    s2 = ScheduledNoticeStore(path)
    items = s2.all()
    assert len(items) == 1
    assert items[0].subject == "안녕"
    assert items[0].boards == ["green1"]


def test_due_filters_by_time_and_status(tmp_path):
    store = _store(tmp_path)
    now = datetime.now()
    past = store.add(_notice(now - timedelta(minutes=5), subject="지남"))
    future = store.add(_notice(now + timedelta(hours=1), subject="미래"))
    due = store.due(now=now)
    ids = {n.id for n in due}
    assert past.id in ids
    assert future.id not in ids


def test_due_excludes_non_pending(tmp_path):
    store = _store(tmp_path)
    now = datetime.now()
    n = store.add(_notice(now - timedelta(minutes=5)))
    store.mark_posted(n.id, "성공 1/1 게시판")
    assert store.due(now=now) == []
    assert store.get(n.id).status == STATUS_POSTED


def test_mark_failed_and_cancel(tmp_path):
    store = _store(tmp_path)
    now = datetime.now()
    a = store.add(_notice(now - timedelta(minutes=1), subject="a"))
    b = store.add(_notice(now + timedelta(hours=1), subject="b"))
    store.mark_failed(a.id, "사이트 오류")
    store.cancel(b.id)
    assert store.get(a.id).status == STATUS_FAILED
    assert store.get(a.id).result == "사이트 오류"
    assert store.get(b.id).status == STATUS_CANCELED
    # 둘 다 대기 아님 → due 없음
    assert store.due(now=now) == []


def test_remove(tmp_path):
    store = _store(tmp_path)
    n = store.add(_notice(datetime.now() + timedelta(hours=1)))
    assert store.remove(n.id) is True
    assert store.all() == []
    assert store.remove("nonexistent") is False


def test_load_corrupt_file_is_empty(tmp_path):
    path = tmp_path / "scheduled_notices.json"
    path.write_text("{ not valid json", encoding="utf-8")
    store = ScheduledNoticeStore(path)
    assert store.all() == []


def test_from_dict_ignores_unknown_keys(tmp_path):
    n = ScheduledNotice.from_dict({
        "scheduled_at": "2026-06-12T14:00:00",
        "subject": "x", "boards": ["green1"], "bogus": 123,
    })
    assert n.subject == "x"
    assert n.scheduled_dt() == datetime(2026, 6, 12, 14, 0, 0)
