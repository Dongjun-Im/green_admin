"""신규 가입자(대기/신청 회원) 식별과 처리 기록 (v0.5).

기능:
  · 회원 목록에서 사이트 등급이 PENDING_LEVELS(3=대기, 4=신청) 인 회원 추출
  · 한번 알림한 회원·미루기(skip)한 회원 ID 를 디스크에 보관
    → 이미 본 회원은 기본 알림에서 제외, "다시 보기" 메뉴로 다시 노출

저장 위치: data/pending_seen.json
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from config import DATA_DIR, PENDING_LEVELS
from core.models import Member


SEEN_FILE = Path(DATA_DIR) / "pending_seen.json"


@dataclass
class PendingMember:
    """가입 대기/신청 상태인 회원 한 명."""
    member: Member
    seen_before: bool = False    # 이전 실행에서 이미 알림한 적이 있는가
    # 장기미접속으로 '탈퇴' 처리됐던 아이디가 다시 가입 신청으로 나타난 경우 True.
    # 이 경우 승인 화면에서 '승인' 버튼이 막힌다.
    was_withdrawn_inactive: bool = False
    withdrawn_info: Optional[dict] = None   # 명단에 기록된 부가정보 (날짜·사유 등)

    @property
    def join_date(self) -> Optional[date]:
        return self.member.join_date

    @property
    def days_since_join(self) -> Optional[int]:
        if self.member.join_date is None:
            return None
        return (date.today() - self.member.join_date).days


class PendingSeenStore:
    """이미 알림한 가입자 ID 목록 보관."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or SEEN_FILE)
        self._seen: dict[str, str] = {}  # user_id -> ISO timestamp
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            self._seen = {
                str(k): str(v) for k, v in data.get("seen", {}).items()
            }

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(
                    {"seen": self._seen},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    def has_seen(self, user_id: str) -> bool:
        return user_id in self._seen

    def mark_seen(self, user_id: str) -> None:
        self._seen[user_id] = datetime.now().isoformat(timespec="seconds")
        self._save()

    def clear(self, user_id: Optional[str] = None) -> None:
        if user_id is None:
            self._seen = {}
        else:
            self._seen.pop(user_id, None)
        self._save()

    def all_seen(self) -> dict[str, str]:
        return dict(self._seen)


# ---------- 식별 ----------

def find_pending(
    members: list[Member],
    seen_store: Optional[PendingSeenStore] = None,
    only_unseen: bool = True,
    blocklist=None,
) -> list[PendingMember]:
    """대기·신청 등급 회원 추출. only_unseen=True 면 이미 알림한 회원 제외.

    blocklist(core.withdrawn_blocklist.WithdrawnBlocklist) 가 주어지면, 장기미접속
    으로 '탈퇴' 처리됐던 아이디는 was_withdrawn_inactive=True 로 표시한다 (목록에서
    빼지는 않는다 — 관리자가 보고 거부 처리하도록).
    """
    out: list[PendingMember] = []
    for m in members:
        if m.level not in PENDING_LEVELS:
            continue
        seen = (
            seen_store is not None and seen_store.has_seen(m.user_id)
        )
        if only_unseen and seen:
            continue
        blocked = blocklist is not None and blocklist.contains(m.user_id)
        info = blocklist.info(m.user_id) if blocked else None
        out.append(PendingMember(
            member=m, seen_before=seen,
            was_withdrawn_inactive=blocked, withdrawn_info=info,
        ))

    # 가입일 최신 우선 (최신 신청자가 먼저 보임)
    out.sort(
        key=lambda pm: (
            -(pm.member.join_date.toordinal() if pm.member.join_date else 0),
            pm.member.user_id,
        )
    )
    return out
