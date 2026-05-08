"""UndoStack — push/pop/persist/depth-limit."""
from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture
def tmp_stack():
    from core.undo_stack import UndoStack
    with tempfile.TemporaryDirectory() as td:
        yield UndoStack(path=os.path.join(td, "undo.json"))


def _item(uid: str, frm: int, to: int):
    from core.undo_stack import UndoItem
    return UndoItem(user_id=uid, nickname=f"닉_{uid}", from_level=frm, to_level=to)


def test_initial_empty(tmp_stack):
    assert tmp_stack.peek() is None
    assert tmp_stack.pop() is None
    assert tmp_stack.all() == []


def test_push_and_peek(tmp_stack):
    tmp_stack.push("작업1", [_item("a", 7, 6), _item("b", 6, 1)])
    e = tmp_stack.peek()
    assert e is not None
    assert e.label == "작업1"
    assert len(e.items) == 2


def test_push_skips_noop(tmp_stack):
    """from_level == to_level 인 항목은 자동 스킵."""
    tmp_stack.push("noop", [_item("c", 5, 5)])
    assert tmp_stack.peek() is None


def test_pop_lifo(tmp_stack):
    tmp_stack.push("first", [_item("a", 7, 6)])
    tmp_stack.push("second", [_item("b", 6, 5)])
    e = tmp_stack.pop()
    assert e.label == "second"
    assert tmp_stack.peek().label == "first"


def test_depth_limit():
    """MAX_STACK_DEPTH(10) 초과 시 가장 오래된 것부터 잘림."""
    from core.undo_stack import MAX_STACK_DEPTH, UndoStack
    with tempfile.TemporaryDirectory() as td:
        s = UndoStack(path=os.path.join(td, "undo.json"))
        for i in range(MAX_STACK_DEPTH + 5):
            s.push(f"작업{i}", [_item(f"u{i}", 5, 6)])
        all_entries = s.all()
        assert len(all_entries) == MAX_STACK_DEPTH
        # 가장 최근 엔트리가 마지막
        assert all_entries[-1].label == f"작업{MAX_STACK_DEPTH + 4}"


def test_persistence():
    """파일에 저장된 후 다시 로드해도 동일 상태."""
    from core.undo_stack import UndoStack
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "undo.json")
        s1 = UndoStack(path=path)
        s1.push("x", [_item("a", 7, 6)])
        s2 = UndoStack(path=path)
        assert len(s2.all()) == 1
        assert s2.peek().label == "x"


def test_clear(tmp_stack):
    tmp_stack.push("x", [_item("a", 7, 6)])
    tmp_stack.clear()
    assert tmp_stack.all() == []
