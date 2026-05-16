"""자료실(시놀로지 NAS) 접속 로그 SQLite 저장소.

DSM 의 Log Center 는 파일 전송 로그와 연결 로그를 일정 기간만 보관하므로,
이 앱이 주기적으로 가져와 영구 보관한다(SQLite). 회원·기간·동작·카테고리로
필터 조회·내보내기를 빠르게 하려고 인덱스를 두 개만 둔다.

테이블 2개:
    nas_access_log   파싱된 로그 항목. (logged_at, dsm_user_id, action, raw_hash)
                     로 UNIQUE — 같은 줄을 여러 번 받아 와도 한 행으로 유지.
    nas_log_meta     수집 메타(key/value) — 마지막 fetch 의 epoch(증분 수집용),
                     마지막 시도 결과 ok 와 사람용 메시지(메뉴에 '(인증 필요)'
                     표시할 때 사용).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional

from config import DATA_DIR


NAS_LOG_DB = Path(DATA_DIR) / "nas_access_log.db"


# ---------- 데이터 모델 ----------

@dataclass(frozen=True)
class NasLogEntry:
    """파싱·정규화된 로그 한 줄."""
    logged_at: str        # ISO (KST). 예: "2026-05-12T14:23:11"
    dsm_user_id: str      # 소문자 정규화. 비면 "" (시스템/익명).
    ip: str = ""
    protocol: str = ""    # SMB / WebDAV / FileStation / DSM / SFTP 등
    action: str = ""      # login/logout/upload/download/delete/rename/move/copy/mkdir/other
    category: str = ""    # 경로의 첫 폴더 (자료실 카테고리)
    file_name: str = ""   # 경로의 마지막 세그먼트
    file_path: str = ""   # 원본 전체 경로
    raw_message: str = "" # DSM 원문(또는 직접 만든 표시 문자열)
    raw_hash: str = ""    # 중복 방지 — 보통 raw_message 의 sha1


@dataclass
class NasLogFilter:
    """목록 조회 필터. 비어 있는 항목은 무시."""
    start_date: Optional[date] = None      # 포함 (이 날 00:00 부터)
    end_date: Optional[date] = None        # 포함 (이 날 23:59 까지)
    dsm_user_id_like: str = ""             # 부분 일치 (소문자)
    action_in: list[str] = field(default_factory=list)  # 동작 enum 화이트리스트
    category_like: str = ""                # 부분 일치
    limit: int = 5000                      # 안전 상한 (UI 가 너무 무거워지지 않게)


# ---------- 저장소 ----------

class NasLogStore:
    def __init__(self, path: Path | str = NAS_LOG_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS nas_access_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                logged_at      TEXT NOT NULL,
                dsm_user_id    TEXT NOT NULL DEFAULT '',
                ip             TEXT NOT NULL DEFAULT '',
                protocol       TEXT NOT NULL DEFAULT '',
                action         TEXT NOT NULL DEFAULT '',
                category       TEXT NOT NULL DEFAULT '',
                file_name      TEXT NOT NULL DEFAULT '',
                file_path      TEXT NOT NULL DEFAULT '',
                raw_message    TEXT NOT NULL DEFAULT '',
                raw_hash       TEXT NOT NULL DEFAULT '',
                ingested_at    TEXT NOT NULL,
                UNIQUE (logged_at, dsm_user_id, action, raw_hash)
            );
            CREATE INDEX IF NOT EXISTS idx_nas_logged_at ON nas_access_log(logged_at DESC);
            CREATE INDEX IF NOT EXISTS idx_nas_user     ON nas_access_log(dsm_user_id);

            CREATE TABLE IF NOT EXISTS nas_log_meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );
            """)

    # ---------- upsert ----------

    def upsert_entries(self, entries: Iterable[NasLogEntry]) -> tuple[int, int]:
        """일괄 upsert. UNIQUE 충돌은 조용히 무시(이미 있는 행 유지).

        Returns:
            (새로 추가된 행, 중복으로 무시된 행)
        """
        added = 0
        skipped = 0
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            for e in entries:
                try:
                    c.execute(
                        """INSERT INTO nas_access_log
                           (logged_at, dsm_user_id, ip, protocol, action,
                            category, file_name, file_path,
                            raw_message, raw_hash, ingested_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            e.logged_at, e.dsm_user_id.lower(), e.ip, e.protocol,
                            e.action, e.category, e.file_name, e.file_path,
                            e.raw_message, e.raw_hash, now,
                        ),
                    )
                    added += 1
                except sqlite3.IntegrityError:
                    skipped += 1
        return added, skipped

    # ---------- 메타 ----------

    def _get_meta(self, key: str, default: str = "") -> str:
        with self._conn() as c:
            r = c.execute(
                "SELECT value FROM nas_log_meta WHERE key = ?", (key,),
            ).fetchone()
        return r["value"] if r else default

    def _set_meta(self, key: str, value: str) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO nas_log_meta(key, value) VALUES(?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (key, value),
            )

    def latest_epoch(self) -> int:
        """마지막 fetch 의 가장 큰 epoch (DSM 의 time 값). 없으면 0."""
        v = self._get_meta("latest_epoch", "0").strip()
        try:
            return int(v)
        except ValueError:
            return 0

    def set_latest_epoch(self, epoch: int) -> None:
        if epoch and epoch > self.latest_epoch():
            self._set_meta("latest_epoch", str(int(epoch)))

    def set_last_status(self, ok: bool, message: str = "") -> None:
        """마지막 수집 시도의 결과(메뉴에 '(인증 필요)' 등 표시용)."""
        self._set_meta("last_status_ok", "true" if ok else "false")
        self._set_meta("last_status_message", message)
        self._set_meta("last_status_at", datetime.now().isoformat(timespec="seconds"))

    def last_status(self) -> tuple[bool, str, str]:
        """반환: (ok, message, at)."""
        ok = self._get_meta("last_status_ok", "").lower() == "true"
        return (
            ok,
            self._get_meta("last_status_message", ""),
            self._get_meta("last_status_at", ""),
        )

    # DSM 자료실 그룹 멤버 캐시 — 다이얼로그 열 때 회원 매칭에 바로 쓰임.
    def set_dsm_group_members(self, user_ids) -> None:
        import json
        ids = [str(u).strip() for u in (user_ids or []) if str(u).strip()]
        self._set_meta("dsm_group_members", json.dumps(ids, ensure_ascii=False))

    def dsm_group_members(self) -> list[str]:
        import json
        raw = self._get_meta("dsm_group_members", "")
        if not raw:
            return []
        try:
            v = json.loads(raw)
            if isinstance(v, list):
                return [str(x) for x in v]
        except Exception:
            pass
        return []

    # ---------- 조회 ----------

    def count(self) -> int:
        with self._conn() as c:
            r = c.execute("SELECT COUNT(*) AS n FROM nas_access_log").fetchone()
        return int(r["n"])

    def entries(self, flt: Optional[NasLogFilter] = None) -> list[NasLogEntry]:
        flt = flt or NasLogFilter()
        clauses: list[str] = []
        params: list = []
        if flt.start_date is not None:
            clauses.append("logged_at >= ?")
            params.append(flt.start_date.isoformat() + "T00:00:00")
        if flt.end_date is not None:
            clauses.append("logged_at <= ?")
            params.append(flt.end_date.isoformat() + "T23:59:59")
        if flt.dsm_user_id_like:
            clauses.append("dsm_user_id LIKE ?")
            params.append(f"%{flt.dsm_user_id_like.strip().lower()}%")
        if flt.action_in:
            placeholders = ",".join("?" * len(flt.action_in))
            clauses.append(f"action IN ({placeholders})")
            params.extend(flt.action_in)
        if flt.category_like:
            clauses.append("category LIKE ?")
            params.append(f"%{flt.category_like.strip()}%")

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        limit = max(1, int(flt.limit or 5000))
        sql = (
            "SELECT logged_at, dsm_user_id, ip, protocol, action, "
            "category, file_name, file_path, raw_message, raw_hash "
            "FROM nas_access_log" + where +
            " ORDER BY logged_at DESC LIMIT ?"
        )
        params.append(limit)
        with self._conn() as c:
            rows = c.execute(sql, params).fetchall()
        return [_row_to_entry(r) for r in rows]


def _row_to_entry(r: sqlite3.Row) -> NasLogEntry:
    return NasLogEntry(
        logged_at=r["logged_at"],
        dsm_user_id=r["dsm_user_id"],
        ip=r["ip"],
        protocol=r["protocol"],
        action=r["action"],
        category=r["category"],
        file_name=r["file_name"],
        file_path=r["file_path"],
        raw_message=r["raw_message"],
        raw_hash=r["raw_hash"],
    )
