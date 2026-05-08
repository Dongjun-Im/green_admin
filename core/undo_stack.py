"""실행 취소(Undo) 스택.

회원 등급 변경이 일어나는 모든 경로(장기미접속 조정, 자동 승급, 단건 변경)에서
"바뀌기 전" 상태를 한 묶음으로 push 한다. 사용자가 Ctrl+Z 를 누르면 가장 최근
묶음을 꺼내 from_level 로 일괄 복구한다.

저장 위치: data/undo_stack.json (개인정보 포함 가능 → .gitignore 의 data/ 하위)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import DATA_DIR


UNDO_FILE = Path(DATA_DIR) / "undo_stack.json"
MAX_STACK_DEPTH = 10


@dataclass
class UndoItem:
    """한 회원의 등급 변경 (이전 등급 복구용)."""
    user_id: str
    nickname: str
    from_level: int   # 작업 직전 등급 (Undo 시 복구할 값)
    to_level: int     # 작업이 설정한 등급

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "nickname": self.nickname,
            "from_level": self.from_level,
            "to_level": self.to_level,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UndoItem":
        return cls(
            user_id=d.get("user_id", ""),
            nickname=d.get("nickname", ""),
            from_level=int(d.get("from_level", 0)),
            to_level=int(d.get("to_level", 0)),
        )


@dataclass
class UndoEntry:
    """한 작업 단위 (n명 일괄 처리)."""
    timestamp: str
    label: str           # 예: "장기미접속 조정 (12명)"
    items: list[UndoItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "label": self.label,
            "items": [i.to_dict() for i in self.items],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UndoEntry":
        return cls(
            timestamp=d.get("timestamp", ""),
            label=d.get("label", ""),
            items=[UndoItem.from_dict(x) for x in d.get("items", [])],
        )


class UndoStack:
    """파일 기반 LIFO 스택. 최근 MAX_STACK_DEPTH 개만 유지."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or UNDO_FILE)
        self._entries: list[UndoEntry] = []
        self._load()

    # ---------- 영속 ----------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        items = data.get("entries", []) if isinstance(data, dict) else []
        self._entries = [UndoEntry.from_dict(e) for e in items]

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(
                    {"entries": [e.to_dict() for e in self._entries]},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    # ---------- 조작 ----------

    def push(self, label: str, items: list[UndoItem]) -> Optional[UndoEntry]:
        items = [i for i in items if i.from_level != i.to_level]
        if not items:
            return None
        entry = UndoEntry(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            label=label,
            items=items,
        )
        self._entries.append(entry)
        # 깊이 제한
        if len(self._entries) > MAX_STACK_DEPTH:
            self._entries = self._entries[-MAX_STACK_DEPTH:]
        self._save()
        return entry

    def peek(self) -> Optional[UndoEntry]:
        return self._entries[-1] if self._entries else None

    def pop(self) -> Optional[UndoEntry]:
        if not self._entries:
            return None
        entry = self._entries.pop()
        self._save()
        return entry

    def all(self) -> list[UndoEntry]:
        return list(self._entries)

    def clear(self) -> None:
        self._entries = []
        self._save()
