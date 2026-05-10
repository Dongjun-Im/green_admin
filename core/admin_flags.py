"""수동 동호회관리자 플래그 저장 (v1.0.4).

자동 감지(member_parser._detect_admin_flag) 가 사이트의 명시적 cl_admin 필드만
신뢰하기 때문에, 사이트가 그런 필드를 안 쓰는 경우 운영진이 누구인지 파일로
직접 보존한다.

저장 위치: data/admin_flags.json — `{"user_id": "마지막 표시 시각", ...}`
한 번 표시한 회원은 다음에 회원 목록을 다시 불러와도 동호회관리자 그룹에
계속 머문다 (등급 변경으로 명시적으로 해제하기 전까지).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from config import DATA_DIR


ADMIN_FLAGS_FILE = Path(DATA_DIR) / "admin_flags.json"


class AdminFlagsStore:
    def __init__(self, path: Path = ADMIN_FLAGS_FILE) -> None:
        self.path = Path(path)
        self._cache: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._cache = {}
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # 두 가지 형식 모두 지원:
                #   구버전: {"uid": "2025-01-01T..."}   (str = ts)
                #   신버전: {"uid": {"ts": "...", "prev_level": 6}}
                normalized: dict[str, dict] = {}
                for k, v in data.items():
                    key = str(k)
                    if isinstance(v, dict):
                        normalized[key] = {
                            "ts": str(v.get("ts", "")),
                            "prev_level": v.get("prev_level"),
                        }
                    elif isinstance(v, str):
                        normalized[key] = {"ts": v, "prev_level": None}
                    else:
                        normalized[key] = {"ts": "", "prev_level": None}
                self._cache = normalized
            else:
                self._cache = {}
        except (OSError, json.JSONDecodeError):
            self._cache = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    # ---------- public ----------

    def all_ids(self) -> set[str]:
        return set(self._cache.keys())

    def is_admin(self, user_id: str) -> bool:
        return user_id in self._cache

    def get_prev_level(self, user_id: str) -> int | None:
        """동호회관리자로 표시하기 직전의 등급. 해제 시 사이트 등급 복원에 사용."""
        entry = self._cache.get(user_id)
        if not entry:
            return None
        prev = entry.get("prev_level")
        return prev if isinstance(prev, int) else None

    def mark(self, user_id: str, prev_level: int | None = None) -> None:
        self._cache[user_id] = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "prev_level": prev_level,
        }
        self._save()

    def unmark(self, user_id: str) -> None:
        if user_id in self._cache:
            self._cache.pop(user_id, None)
            self._save()

    def mark_many(self, user_ids: Iterable[str]) -> int:
        added = 0
        ts = datetime.now().isoformat(timespec="seconds")
        for uid in user_ids:
            if uid not in self._cache:
                added += 1
            self._cache[uid] = {"ts": ts, "prev_level": None}
        if added or any(True for _ in user_ids):
            self._save()
        return added

    def unmark_many(self, user_ids: Iterable[str]) -> int:
        removed = 0
        for uid in user_ids:
            if uid in self._cache:
                self._cache.pop(uid, None)
                removed += 1
        if removed:
            self._save()
        return removed
