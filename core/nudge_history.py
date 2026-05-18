"""안내 메일(nudge) 발송 이력 저장소 — 중복 발송 방지용.

JSON 한 파일에 모든 이력 보관:

    {
      "anycall": {
        "activity_nudge":   "2026-05-17",
        "inactive_warning": "2026-04-10"
      },
      ...
    }

· '활동 안내 메일'(6개월 글 없음) 과 '장기미접속 경고 메일'(1년+ 미접속) 각각
  마지막 발송일을 회원별로 기록.
· `was_sent_within(user_id, kind, days)` 로 최근 N일 이내 발송 여부 확인 →
  UI 가 대상 목록에서 자동 제외할 때 사용.
· 파일 입출력 실패는 조용히 무시 (이력 손실되더라도 메일 발송 자체는 막지 않음).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

# 메일 종류 — 새 종류 추가 시 여기 상수에 추가.
KIND_ACTIVITY_NUDGE = "activity_nudge"
KIND_INACTIVE_WARNING = "inactive_warning"
ALL_KINDS = (KIND_ACTIVITY_NUDGE, KIND_INACTIVE_WARNING)


class NudgeHistoryStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._data: dict[str, dict[str, str]] = {}
        self._load()

    # ---------- I/O ----------

    def _load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            self._data = {}
            return
        try:
            raw = json.loads(text)
        except ValueError:
            self._data = {}
            return
        if not isinstance(raw, dict):
            self._data = {}
            return
        # 값 정규화 — 회원별 dict 가 아니면 무시.
        clean: dict[str, dict[str, str]] = {}
        for uid, kinds in raw.items():
            if not isinstance(kinds, dict):
                continue
            inner: dict[str, str] = {}
            for kind, when in kinds.items():
                if not isinstance(when, str):
                    continue
                inner[str(kind)] = when
            if inner:
                clean[str(uid).lower()] = inner
        self._data = clean

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except OSError:
            pass

    # ---------- 공개 API ----------

    def mark_sent(
        self, user_id: str, kind: str, when: Optional[date] = None,
    ) -> None:
        """user_id 의 kind 메일 발송일을 when (기본: 오늘) 으로 기록."""
        uid = (user_id or "").lower()
        if not uid:
            return
        d = when or date.today()
        bucket = self._data.setdefault(uid, {})
        bucket[kind] = d.isoformat()
        self._save()

    def mark_sent_many(
        self, user_ids: list[str], kind: str, when: Optional[date] = None,
    ) -> None:
        """여러 user_id 동시 기록 — 한 번에 disk write."""
        d = when or date.today()
        for user_id in user_ids:
            uid = (user_id or "").lower()
            if not uid:
                continue
            bucket = self._data.setdefault(uid, {})
            bucket[kind] = d.isoformat()
        self._save()

    def last_sent(self, user_id: str, kind: str) -> Optional[date]:
        """가장 최근 발송일 — 기록 없거나 파싱 실패 시 None."""
        uid = (user_id or "").lower()
        s = self._data.get(uid, {}).get(kind)
        if not s:
            return None
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None

    def was_sent_within(
        self, user_id: str, kind: str, days: int,
        *, today: Optional[date] = None,
    ) -> bool:
        """user_id 의 kind 메일이 today 로부터 days 이내에 발송됐는지."""
        last = self.last_sent(user_id, kind)
        if last is None:
            return False
        ref = today or date.today()
        return (ref - last).days < days

    def clear(self) -> None:
        """전체 이력 삭제 (테스트·재설정용)."""
        self._data = {}
        self._save()
