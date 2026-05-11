"""장기미접속으로 '탈퇴' 처리된 회원 명단 (v1.0.x).

장기미접속 등급 조정에서 cl_level 을 '탈퇴'(WITHDRAW_LEVEL=1) 로 내린 회원의
아이디를 영구 보관한다. 이 명단에 있는 아이디가 나중에 다시 가입 신청(대기 등급)
으로 나타나면, 신규 가입자 승인 화면에서 '승인' 버튼을 막아 재가입을 자동으로
거른다. 관리자가 명시적으로 명단에서 빼면 다시 승인할 수 있다.

저장 위치: data/inactivity_withdrawn.json
형식:
    {"withdrawn": {
        "<user_id_소문자>": {"user_id": "<원본>", "nickname": "...",
                             "reason": "...", "date": "ISO-8601"}
    }}
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from config import DATA_DIR


BLOCKLIST_FILE = Path(DATA_DIR) / "inactivity_withdrawn.json"


def _norm(user_id) -> str:
    return (str(user_id) if user_id is not None else "").strip().lower()


class WithdrawnBlocklist:
    """장기미접속 탈퇴자 아이디 명단 — 디스크에 보관."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or BLOCKLIST_FILE)
        self._items: dict[str, dict] = {}   # 소문자 user_id -> 정보 dict
        self._load()

    # ---------- 영속화 ----------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            raw = data.get("withdrawn", {})
            if isinstance(raw, dict):
                out: dict[str, dict] = {}
                for k, v in raw.items():
                    key = _norm(k)
                    if not key:
                        continue
                    if isinstance(v, dict):
                        out[key] = v
                    else:
                        # 구버전(값이 ISO 문자열뿐)이거나 손상된 경우 복구
                        out[key] = {"user_id": str(k), "nickname": "",
                                    "reason": "", "date": str(v)}
                self._items = out

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"withdrawn": self._items},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    # ---------- 조회 ----------

    def contains(self, user_id) -> bool:
        return _norm(user_id) in self._items

    def info(self, user_id) -> Optional[dict]:
        return self._items.get(_norm(user_id))

    def all(self) -> dict[str, dict]:
        return dict(self._items)

    def __len__(self) -> int:
        return len(self._items)

    # ---------- 변경 ----------

    def add(self, user_id, nickname: str = "", reason: str = "") -> None:
        key = _norm(user_id)
        if not key:
            return
        self._items[key] = {
            "user_id": str(user_id),
            "nickname": nickname or "",
            "reason": reason or "",
            "date": datetime.now().isoformat(timespec="seconds"),
        }
        self._save()

    def add_many(self, entries: Iterable[tuple]) -> int:
        """entries: (user_id, nickname, reason) 튜플들. 추가된 건수 반환."""
        added = 0
        for e in entries:
            try:
                uid, nick, reason = (list(e) + ["", ""])[:3]
            except (TypeError, ValueError):
                continue
            key = _norm(uid)
            if not key:
                continue
            self._items[key] = {
                "user_id": str(uid),
                "nickname": nick or "",
                "reason": reason or "",
                "date": datetime.now().isoformat(timespec="seconds"),
            }
            added += 1
        if added:
            self._save()
        return added

    def remove(self, user_id) -> bool:
        key = _norm(user_id)
        if key in self._items:
            del self._items[key]
            self._save()
            return True
        return False

    def clear(self) -> None:
        self._items = {}
        self._save()
