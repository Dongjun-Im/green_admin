"""작업 오디트 로그 기록기."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from config import LOGS_DIR
from core.models import AdjustmentItem, AdminActionResult


class OperationLogWriter:
    def __init__(self, log_dir: Optional[Path] = None) -> None:
        self.log_dir = Path(log_dir or LOGS_DIR)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _path_for_today(self) -> Path:
        return self.log_dir / f"operation_{datetime.now():%Y%m}.log"

    def write_action(self, item: AdjustmentItem, result: AdminActionResult) -> None:
        ts = datetime.now().isoformat(timespec="seconds")
        line = (
            f"[{ts}] {'OK' if result.success else 'FAIL'} "
            f"action={item.action} "
            f"user={item.member.user_id} "
            f"from={item.from_level} to={item.to_level} "
            f"reason={item.reason} "
            f"url={result.request_url} "
            f"msg={result.message}"
        )
        with self._path_for_today().open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def write_event(self, event: str) -> None:
        ts = datetime.now().isoformat(timespec="seconds")
        with self._path_for_today().open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] EVENT {event}\n")
