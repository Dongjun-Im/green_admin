"""작업 로그(operation_YYYYMM.log) 파싱 — 대시보드/뷰어용.

OperationLogWriter 가 남기는 두 형식:
  [TS] OK|FAIL action=X user=Y from=N to=M reason=R url=U msg=M
  [TS] EVENT <자유 텍스트>
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config import LOGS_DIR


@dataclass
class LogEntry:
    timestamp: datetime
    success: bool
    raw: str
    kind: str  # "action" | "event"
    action: Optional[str] = None
    user_id: Optional[str] = None
    from_level: Optional[int] = None
    to_level: Optional[int] = None
    reason: Optional[str] = None
    message: Optional[str] = None


_ACTION_RE = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s+(?P<status>OK|FAIL)\s+action=(?P<action>\S+)\s+"
    r"user=(?P<user>\S+)\s+from=(?P<from>\d+)\s+to=(?P<to>\d+)\s+"
    r"reason=(?P<reason>.+?)\s+url=(?P<url>\S+)\s+msg=(?P<msg>.+)$"
)
_EVENT_RE = re.compile(r"^\[(?P<ts>[^\]]+)\]\s+EVENT\s+(?P<event>.+)$")


def parse_line(line: str) -> Optional[LogEntry]:
    line = line.rstrip("\n").rstrip("\r")
    if not line:
        return None

    m = _ACTION_RE.match(line)
    if m:
        try:
            ts = datetime.fromisoformat(m.group("ts"))
        except ValueError:
            return None
        return LogEntry(
            timestamp=ts,
            success=(m.group("status") == "OK"),
            raw=line,
            kind="action",
            action=m.group("action"),
            user_id=m.group("user"),
            from_level=int(m.group("from")),
            to_level=int(m.group("to")),
            reason=m.group("reason"),
            message=m.group("msg"),
        )

    m = _EVENT_RE.match(line)
    if m:
        try:
            ts = datetime.fromisoformat(m.group("ts"))
        except ValueError:
            return None
        return LogEntry(
            timestamp=ts,
            success=True,
            raw=line,
            kind="event",
            message=m.group("event"),
        )
    return None


def load_log_file(path: Path) -> list[LogEntry]:
    entries: list[LogEntry] = []
    try:
        with Path(path).open("r", encoding="utf-8") as f:
            for line in f:
                e = parse_line(line)
                if e is not None:
                    entries.append(e)
    except OSError:
        pass
    return entries


def list_log_files(logs_dir: Optional[Path] = None) -> list[Path]:
    """operation_YYYYMM.log 파일 목록 (오래된 순)."""
    base = Path(logs_dir or LOGS_DIR)
    if not base.exists():
        return []
    return sorted(base.glob("operation_*.log"))


def load_recent_entries(
    days: int = 90, logs_dir: Optional[Path] = None
) -> list[LogEntry]:
    cutoff = datetime.now() - timedelta(days=days)
    out: list[LogEntry] = []
    for f in list_log_files(logs_dir):
        for e in load_log_file(f):
            if e.timestamp >= cutoff:
                out.append(e)
    return out


def classify_action(e: LogEntry) -> Optional[str]:
    """단건 액션 항목을 사용자 친화적 카테고리로 분류.

    "탈퇴" / "승급" / "강등" / "기타". 이벤트는 None.
    """
    if e.kind != "action":
        return None
    # to=1(탈퇴) 은 강등이 아닌 별도 분류
    if e.to_level == 1:
        return "탈퇴"
    if e.from_level is not None and e.to_level is not None:
        if e.to_level > e.from_level:
            return "승급"
        if e.to_level < e.from_level:
            return "강등"
    return "기타"


def count_actions(entries: list[LogEntry], success_only: bool = True) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in entries:
        if success_only and not e.success:
            continue
        cat = classify_action(e)
        if cat is None:
            continue
        out[cat] = out.get(cat, 0) + 1
    return out


def count_events(entries: list[LogEntry], substring: str) -> int:
    return sum(
        1 for e in entries
        if e.kind == "event" and substring in (e.message or "")
    )
