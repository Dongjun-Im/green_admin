"""수동 동호회관리자 플래그 영구 저장 (v1.0.4)."""
from __future__ import annotations

import os


def test_mark_unmark_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "config.DATA_DIR", str(tmp_path), raising=False
    )
    from core.admin_flags import AdminFlagsStore
    store = AdminFlagsStore(path=tmp_path / "admin_flags.json")
    assert not store.is_admin("hong")
    store.mark("hong")
    assert store.is_admin("hong")
    store.unmark("hong")
    assert not store.is_admin("hong")


def test_persists_across_instances(tmp_path):
    from core.admin_flags import AdminFlagsStore
    p = tmp_path / "admin_flags.json"
    s1 = AdminFlagsStore(path=p)
    s1.mark("a")
    s1.mark("b")
    assert s1.all_ids() == {"a", "b"}

    # 새 인스턴스에서도 보여야 함
    s2 = AdminFlagsStore(path=p)
    assert s2.is_admin("a")
    assert s2.is_admin("b")
    assert not s2.is_admin("c")


def test_mark_many_and_unmark_many(tmp_path):
    from core.admin_flags import AdminFlagsStore
    s = AdminFlagsStore(path=tmp_path / "admin_flags.json")
    s.mark_many(["x", "y", "z"])
    assert s.all_ids() == {"x", "y", "z"}
    removed = s.unmark_many(["x", "z", "missing"])
    assert removed == 2
    assert s.all_ids() == {"y"}


def test_handles_missing_file_gracefully(tmp_path):
    from core.admin_flags import AdminFlagsStore
    p = tmp_path / "no_such_file.json"
    assert not p.exists()
    s = AdminFlagsStore(path=p)
    assert s.all_ids() == set()
    s.mark("hello")
    assert p.exists()


def test_handles_corrupted_json(tmp_path):
    from core.admin_flags import AdminFlagsStore
    p = tmp_path / "broken.json"
    p.write_text("this is not json", encoding="utf-8")
    s = AdminFlagsStore(path=p)
    # 깨진 내용은 빈 dict 로 폴백
    assert s.all_ids() == set()
    s.mark("ok")
    assert s.is_admin("ok")
