"""예약 공지 큐 저장소 (v1.4).

'공지 작성' 화면에서 "예약 발송" 한 공지를 한 건씩 JSON 파일에 쌓아 둔다.
약 10분마다 도는 헤드리스 작업(`core/scheduler_runner.py:post_scheduled`)이
도래분(`due`)을 찾아 사이트에 올린다.

wx·네트워크 의존이 전혀 없어 헤드리스 모드와 단위 테스트에서 안전하게 쓸 수 있다.
저장 파일: `data/scheduled_notices.json` (개인정보/런타임 상태 — .gitignore 의 data/ 규칙).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import SCHEDULED_NOTICES_FILE


# 상태값
STATUS_PENDING = "pending"      # 발송 대기
STATUS_POSTED = "posted"        # 발송 완료
STATUS_FAILED = "failed"        # 발송 시도했으나 실패
STATUS_CANCELED = "canceled"    # 사용자가 취소


@dataclass
class ScheduledNotice:
    """예약 공지 한 건."""
    scheduled_at: str               # ISO 로컬 datetime "2026-06-12T14:00:00"
    boards: list[str] = field(default_factory=list)
    subject: str = ""
    content: str = ""
    as_notice: bool = True
    use_html: bool = False
    id: str = ""
    created_at: str = ""
    status: str = STATUS_PENDING
    posted_at: str = ""
    result: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ScheduledNotice":
        # 알 수 없는 키는 무시하고, 빠진 키는 기본값으로.
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

    def scheduled_dt(self) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(self.scheduled_at)
        except (ValueError, TypeError):
            return None


class ScheduledNoticeStore:
    """예약 공지 목록을 JSON 파일에 보관."""

    def __init__(self, path: Optional[Path | str] = None) -> None:
        self.path = Path(path or SCHEDULED_NOTICES_FILE)
        self._items: list[ScheduledNotice] = []
        self.load()

    # ---------- 파일 입출력 ----------

    def load(self) -> list[ScheduledNotice]:
        """파일에서 목록을 읽어 들인다. 없거나 손상되면 빈 목록."""
        self._items = []
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            return self._items
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return self._items
        if isinstance(data, list):
            self._items = [
                ScheduledNotice.from_dict(d) for d in data if isinstance(d, dict)
            ]
        return self._items

    def save(self) -> None:
        """현재 목록을 파일에 쓴다 (임시 파일 → 교체로 손상 방지)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(
                [n.to_dict() for n in self._items],
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    # ---------- 조회 ----------

    def all(self) -> list[ScheduledNotice]:
        return list(self._items)

    def get(self, notice_id: str) -> Optional[ScheduledNotice]:
        return next((n for n in self._items if n.id == notice_id), None)

    def pending(self) -> list[ScheduledNotice]:
        return [n for n in self._items if n.status == STATUS_PENDING]

    def due(self, now: Optional[datetime] = None) -> list[ScheduledNotice]:
        """status=pending 이고 예약 시각이 now 이하인 항목 (시각 오름차순)."""
        now = now or datetime.now()
        out = []
        for n in self._items:
            if n.status != STATUS_PENDING:
                continue
            dt = n.scheduled_dt()
            if dt is not None and dt <= now:
                out.append(n)
        out.sort(key=lambda n: n.scheduled_at)
        return out

    # ---------- 변경 ----------

    def add(self, notice: ScheduledNotice) -> ScheduledNotice:
        """예약 한 건 추가 후 저장. id/created_at 비어 있으면 채워 준다."""
        if not notice.id:
            notice.id = uuid.uuid4().hex[:8]
        if not notice.created_at:
            notice.created_at = datetime.now().isoformat(timespec="seconds")
        self._items.append(notice)
        self.save()
        return notice

    def _set_status(self, notice_id: str, status: str, result: str = "",
                    posted_at: str = "") -> bool:
        n = self.get(notice_id)
        if n is None:
            return False
        n.status = status
        if result:
            n.result = result
        if posted_at:
            n.posted_at = posted_at
        self.save()
        return True

    def mark_posted(self, notice_id: str, result: str = "") -> bool:
        return self._set_status(
            notice_id, STATUS_POSTED, result=result,
            posted_at=datetime.now().isoformat(timespec="seconds"),
        )

    def mark_failed(self, notice_id: str, result: str = "") -> bool:
        return self._set_status(
            notice_id, STATUS_FAILED, result=result,
            posted_at=datetime.now().isoformat(timespec="seconds"),
        )

    def cancel(self, notice_id: str) -> bool:
        return self._set_status(notice_id, STATUS_CANCELED, result="사용자 취소")

    def remove(self, notice_id: str) -> bool:
        before = len(self._items)
        self._items = [n for n in self._items if n.id != notice_id]
        if len(self._items) != before:
            self.save()
            return True
        return False
