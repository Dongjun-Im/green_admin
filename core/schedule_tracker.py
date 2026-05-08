"""작업 주기 추적 (last_run.json).

캘린더 기준 고정 도래일 방식:
  - 백업 (3개월마다): 매년 1월 1일, 4월 1일, 7월 1일, 10월 1일
  - 조정 (6개월마다): 매년 1월 1일, 7월 1일

is_*_due() 는 마지막 실행일이 "오늘 시점에서 가장 최근 도래일" 이전이면 True.
한 번 실행하면 다음 도래일까지는 다시 트리거되지 않는다.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

from config import (
    ADJUSTMENT_INTERVAL_MONTHS,
    BACKUP_INTERVAL_MONTHS,
    LAST_RUN_FILE,
    MVP_INTERVAL_MONTHS,
)


def _due_months(interval_months: int) -> list[int]:
    """주기에서 1월부터 시작하는 도래 월 리스트.
    interval=3 → [1, 4, 7, 10]
    interval=6 → [1, 7]
    """
    if interval_months <= 0 or 12 % interval_months != 0:
        return [1]
    return [1 + i * interval_months for i in range(12 // interval_months)]


def last_due_date(today: date, interval_months: int) -> date:
    """오늘 이전(포함) 가장 최근 도래일 (해당 월의 1일)."""
    months = _due_months(interval_months)
    candidates = [date(today.year, m, 1) for m in months if date(today.year, m, 1) <= today]
    if candidates:
        return max(candidates)
    return date(today.year - 1, max(months), 1)


def next_due_date(today: date, interval_months: int) -> date:
    """오늘 이후(불포함) 다음 도래일 (해당 월의 1일)."""
    months = _due_months(interval_months)
    candidates = [date(today.year, m, 1) for m in months if date(today.year, m, 1) > today]
    if candidates:
        return min(candidates)
    return date(today.year + 1, min(months), 1)


class ScheduleTracker:
    BACKUP_INTERVAL_MONTHS = BACKUP_INTERVAL_MONTHS
    ADJUSTMENT_INTERVAL_MONTHS = ADJUSTMENT_INTERVAL_MONTHS
    MVP_INTERVAL_MONTHS = MVP_INTERVAL_MONTHS

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or LAST_RUN_FILE)
        self._data: dict = {
            "last_backup": None,
            "last_adjustment": None,
            "last_mvp": None,
            "history": [],
        }

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
            self._data.setdefault("history", [])
            self._data.setdefault("last_mvp", None)
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def last_backup_date(self) -> Optional[date]:
        return self._parse(self._data.get("last_backup"))

    def last_adjustment_date(self) -> Optional[date]:
        return self._parse(self._data.get("last_adjustment"))

    def last_mvp_date(self) -> Optional[date]:
        return self._parse(self._data.get("last_mvp"))

    # ---- 도래 판단 ----

    def is_backup_due(self, today: Optional[date] = None) -> bool:
        today = today or date.today()
        last = self.last_backup_date()
        if last is None:
            return True
        return last < last_due_date(today, self.BACKUP_INTERVAL_MONTHS)

    def is_adjustment_due(self, today: Optional[date] = None) -> bool:
        today = today or date.today()
        last = self.last_adjustment_date()
        if last is None:
            return True
        return last < last_due_date(today, self.ADJUSTMENT_INTERVAL_MONTHS)

    def is_mvp_due(self, today: Optional[date] = None) -> bool:
        today = today or date.today()
        last = self.last_mvp_date()
        if last is None:
            return True
        return last < last_due_date(today, self.MVP_INTERVAL_MONTHS)

    def days_until_mvp(self, today: Optional[date] = None) -> int:
        today = today or date.today()
        if self.is_mvp_due(today):
            return 0
        return (next_due_date(today, self.MVP_INTERVAL_MONTHS) - today).days

    def next_mvp_date(self, today: Optional[date] = None) -> date:
        today = today or date.today()
        if self.is_mvp_due(today):
            return today
        return next_due_date(today, self.MVP_INTERVAL_MONTHS)

    def days_until_backup(self, today: Optional[date] = None) -> int:
        today = today or date.today()
        if self.is_backup_due(today):
            return 0
        return (next_due_date(today, self.BACKUP_INTERVAL_MONTHS) - today).days

    def days_until_adjustment(self, today: Optional[date] = None) -> int:
        today = today or date.today()
        if self.is_adjustment_due(today):
            return 0
        return (next_due_date(today, self.ADJUSTMENT_INTERVAL_MONTHS) - today).days

    def next_backup_date(self, today: Optional[date] = None) -> date:
        today = today or date.today()
        if self.is_backup_due(today):
            return today
        return next_due_date(today, self.BACKUP_INTERVAL_MONTHS)

    def next_adjustment_date(self, today: Optional[date] = None) -> date:
        today = today or date.today()
        if self.is_adjustment_due(today):
            return today
        return next_due_date(today, self.ADJUSTMENT_INTERVAL_MONTHS)

    # ---- 완료 기록 ----

    def mark_backup_done(self, count: int = 0, today: Optional[date] = None) -> None:
        today = today or date.today()
        self._data["last_backup"] = today.isoformat()
        self._data.setdefault("history", []).append({
            "type": "backup",
            "date": today.isoformat(),
            "count": count,
        })
        self.save()

    def mark_adjustment_done(
        self,
        demoted: int = 0,
        deleted: int = 0,
        today: Optional[date] = None,
    ) -> None:
        today = today or date.today()
        self._data["last_adjustment"] = today.isoformat()
        self._data.setdefault("history", []).append({
            "type": "adjustment",
            "date": today.isoformat(),
            "demoted": demoted,
            "deleted": deleted,
        })
        self.save()

    def mark_mvp_done(
        self,
        top_n: int = 0,
        quarter: str = "",
        today: Optional[date] = None,
    ) -> None:
        today = today or date.today()
        self._data["last_mvp"] = today.isoformat()
        self._data.setdefault("history", []).append({
            "type": "mvp",
            "date": today.isoformat(),
            "top_n": top_n,
            "quarter": quarter,
        })
        self.save()

    @staticmethod
    def _parse(s) -> Optional[date]:
        if not s:
            return None
        try:
            return date.fromisoformat(s)
        except (TypeError, ValueError):
            return None
