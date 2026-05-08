"""회원별 메모/태그 (로컬 SQLite).

저장 위치: data/member_notes.db
관리자만 보는 사적 메모 (예: "오프모임 단골", "민원 이력") — 사이트와 무관.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import DATA_DIR


NOTES_DB = Path(DATA_DIR) / "member_notes.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS member_notes (
    user_id    TEXT PRIMARY KEY,
    note       TEXT NOT NULL DEFAULT '',
    tags       TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
"""


@dataclass
class MemberNote:
    user_id: str
    note: str = ""
    tags: list[str] = None  # type: ignore[assignment]
    updated_at: Optional[str] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []

    @property
    def has_content(self) -> bool:
        return bool(self.note.strip()) or bool(self.tags)

    def display_summary(self) -> str:
        parts: list[str] = []
        if self.tags:
            parts.append("[" + ", ".join(self.tags) + "]")
        if self.note:
            first_line = self.note.split("\n", 1)[0].strip()
            if len(first_line) > 30:
                first_line = first_line[:30] + "…"
            parts.append(first_line)
        return " ".join(parts) if parts else ""


class MemberNotesStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or NOTES_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(_SCHEMA)

    # ---------- CRUD ----------

    def get(self, user_id: str) -> Optional[MemberNote]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM member_notes WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        return MemberNote(
            user_id=row["user_id"],
            note=row["note"] or "",
            tags=[t for t in (row["tags"] or "").split(",") if t.strip()],
            updated_at=row["updated_at"],
        )

    def upsert(self, note: MemberNote) -> None:
        ts = datetime.now().isoformat(timespec="seconds")
        tags_csv = ",".join(t.strip() for t in note.tags if t.strip())
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO member_notes(user_id, note, tags, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    note=excluded.note,
                    tags=excluded.tags,
                    updated_at=excluded.updated_at
                """,
                (note.user_id, note.note, tags_csv, ts),
            )
        note.updated_at = ts

    def delete(self, user_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM member_notes WHERE user_id = ?", (user_id,))

    def get_many(self, user_ids: list[str]) -> dict[str, MemberNote]:
        if not user_ids:
            return {}
        placeholders = ",".join("?" * len(user_ids))
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM member_notes WHERE user_id IN ({placeholders})",
                user_ids,
            ).fetchall()
        return {
            r["user_id"]: MemberNote(
                user_id=r["user_id"],
                note=r["note"] or "",
                tags=[t for t in (r["tags"] or "").split(",") if t.strip()],
                updated_at=r["updated_at"],
            )
            for r in rows
        }

    def all_tagged(self) -> list[MemberNote]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM member_notes WHERE note != '' OR tags != '' "
                "ORDER BY updated_at DESC"
            ).fetchall()
        return [
            MemberNote(
                user_id=r["user_id"],
                note=r["note"] or "",
                tags=[t for t in (r["tags"] or "").split(",") if t.strip()],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]
