"""회원 등급 변경 영구 이력 (v1.0).

Undo 스택과 별개로 모든 등급 변경을 영구 보관한다.
저장: data/level_history.db (SQLite)

사용처:
  · 자동 승급 적용 후
  · 장기미접속 조정 적용 후
  · 검색에서 단건 등급 변경 후
  · 신규 가입 승인/거부 후
  · Ctrl+Z 되돌리기 후 (inverse 항목으로)

조회:
  · 회원별 변경 이력 (검색에서 호출)
  · 전체 시간순 / 사유별 / 행위자별
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import DATA_DIR


HISTORY_DB = Path(DATA_DIR) / "level_history.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS level_changes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT    NOT NULL,
    user_id      TEXT    NOT NULL,
    nickname     TEXT    NOT NULL DEFAULT '',
    from_level   INTEGER NOT NULL,
    to_level     INTEGER NOT NULL,
    source       TEXT    NOT NULL DEFAULT '',
    reason       TEXT    NOT NULL DEFAULT '',
    actor        TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_user_id ON level_changes(user_id);
CREATE INDEX IF NOT EXISTS idx_timestamp ON level_changes(timestamp);
"""


@dataclass
class LevelChange:
    id: Optional[int]
    timestamp: str
    user_id: str
    nickname: str
    from_level: int
    to_level: int
    source: str   # 예: "auto_promote", "level_adjust", "manual", "approve", "reject", "undo"
    reason: str
    actor: str    # 작업한 관리자 user_id

    def display(self) -> str:
        return (
            f"{self.timestamp} | {self.user_id} ({self.nickname}) | "
            f"{self.from_level} → {self.to_level} | {self.source} | {self.reason}"
        )


class LevelHistoryStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or HISTORY_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(_SCHEMA)

    # ---------- 쓰기 ----------

    def record(
        self,
        user_id: str,
        from_level: int,
        to_level: int,
        source: str,
        reason: str = "",
        nickname: str = "",
        actor: str = "",
        timestamp: Optional[str] = None,
    ) -> int:
        """변경 한 건 기록. id 반환."""
        if from_level == to_level:
            return 0  # 변경 없음 → 기록하지 않음
        ts = timestamp or datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO level_changes "
                "(timestamp, user_id, nickname, from_level, to_level, source, reason, actor) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, user_id, nickname, from_level, to_level, source, reason, actor),
            )
            return cur.lastrowid or 0

    def record_batch(
        self,
        items: list[dict],
        source: str,
        actor: str = "",
    ) -> int:
        """일괄 기록 — 자동 승급·장기미접속 조정 같은 묶음 작업.

        items: [{"user_id", "from_level", "to_level", "nickname"?, "reason"?}, ...]
        반환: 실제 기록된 행 수.
        """
        ts = datetime.now().isoformat(timespec="seconds")
        rows = [
            (
                ts, it["user_id"], it.get("nickname", ""),
                int(it["from_level"]), int(it["to_level"]),
                source, it.get("reason", ""), actor,
            )
            for it in items
            if int(it["from_level"]) != int(it["to_level"])
        ]
        if not rows:
            return 0
        with self._conn() as c:
            c.executemany(
                "INSERT INTO level_changes "
                "(timestamp, user_id, nickname, from_level, to_level, source, reason, actor) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    # ---------- 조회 ----------

    def for_user(self, user_id: str, limit: int = 100) -> list[LevelChange]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM level_changes WHERE user_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [_row_to_change(r) for r in rows]

    def recent(self, limit: int = 200) -> list[LevelChange]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM level_changes ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_change(r) for r in rows]

    def search(
        self,
        user_id: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 500,
    ) -> list[LevelChange]:
        clauses, args = [], []
        if user_id:
            clauses.append("user_id LIKE ?")
            args.append(f"%{user_id}%")
        if source:
            clauses.append("source = ?")
            args.append(source)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM level_changes {where} "
                f"ORDER BY id DESC LIMIT ?",
                (*args, limit),
            ).fetchall()
        return [_row_to_change(r) for r in rows]

    def count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM level_changes").fetchone()[0]


def _row_to_change(r) -> LevelChange:
    return LevelChange(
        id=r["id"],
        timestamp=r["timestamp"],
        user_id=r["user_id"],
        nickname=r["nickname"] or "",
        from_level=int(r["from_level"]),
        to_level=int(r["to_level"]),
        source=r["source"] or "",
        reason=r["reason"] or "",
        actor=r["actor"] or "",
    )
