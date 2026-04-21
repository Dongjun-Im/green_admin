"""데이터 모델."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal, Optional


@dataclass
class Member:
    """초록등대 회원 한 명."""
    user_id: str
    name: str = ""           # 본명 (소리샘 컬럼: 이름)
    nickname: str = ""       # 닉네임 (소리샘 컬럼: 닉네임)
    level: int = 0           # 사이트 실제: 5 일반 / 6 우수 / 7 최우수 / 8 명예 / 9 관리자
    level_label: str = ""
    last_login_date: Optional[date] = None
    join_date: Optional[date] = None
    login_count: Optional[int] = None  # 사이트의 "접속수" 컬럼
    post_count: Optional[int] = None   # 게시물 수 (현재 미사용, 추후 추가)
    mb_no: Optional[str] = None
    raw_row_html: str = ""

    def display(self) -> str:
        last = self.last_login_date.isoformat() if self.last_login_date else "알 수 없음"
        return f"{self.user_id} / {self.nickname} / {self.level_label} / 마지막접속 {last}"


Action = Literal["demote", "delete", "skip"]


@dataclass
class AdjustmentItem:
    member: Member
    action: Action
    from_level: int
    to_level: Optional[int]
    reason: str

    def display(self) -> str:
        from config import LEVEL_LABELS
        nick = self.member.nickname or self.member.name or self.member.user_id
        if self.action == "demote":
            target = LEVEL_LABELS.get(self.to_level or 0, f"레벨 {self.to_level}")
            return (
                f"{self.member.user_id} / {nick} / "
                f"{self.member.level_label} → {target} / {self.reason}"
            )
        if self.action == "delete":
            return (
                f"{self.member.user_id} / {nick} / "
                f"{self.member.level_label} → 탈퇴 / {self.reason}"
            )
        return f"{self.member.user_id} / {nick} / 건너뜀 / {self.reason}"


@dataclass
class AdjustmentPlan:
    items: list[AdjustmentItem] = field(default_factory=list)
    total_scanned: int = 0
    cutoff_date: Optional[date] = None

    @property
    def actionable(self) -> list[AdjustmentItem]:
        return [i for i in self.items if i.action != "skip"]

    @property
    def demote_count(self) -> int:
        return sum(1 for i in self.items if i.action == "demote")

    @property
    def delete_count(self) -> int:
        return sum(1 for i in self.items if i.action == "delete")

    @property
    def skip_count(self) -> int:
        return sum(1 for i in self.items if i.action == "skip")


@dataclass
class BackupResult:
    txt_path: Path
    xlsx_path: Path
    member_count: int
    level_breakdown: dict[int, int] = field(default_factory=dict)


@dataclass
class AdminActionResult:
    success: bool
    message: str
    request_url: str = ""
    request_payload: dict = field(default_factory=dict)
    response_snippet: str = ""


@dataclass
class AdjustmentReport:
    succeeded_demote: list[Member] = field(default_factory=list)
    succeeded_delete: list[Member] = field(default_factory=list)
    failed: list[tuple[Member, str]] = field(default_factory=list)
    dry_run: bool = True

    @property
    def speak_summary(self) -> str:
        prefix = "미리보기 결과: " if self.dry_run else "조정 완료: "
        parts = [
            f"등급 강등 {len(self.succeeded_demote)}명",
            f"탈퇴 {len(self.succeeded_delete)}명",
        ]
        if self.failed:
            parts.append(f"실패 {len(self.failed)}건")
        else:
            parts.append("실패 0건")
        return prefix + ", ".join(parts)
