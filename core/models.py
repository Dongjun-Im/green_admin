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
    level: int = 0           # 사이트 실제: 5 준회원 / 6 일반 / 7 우수 / 8 최우수 / 9 명예
    level_label: str = ""
    last_login_date: Optional[date] = None
    join_date: Optional[date] = None
    login_count: Optional[int] = None  # 사이트의 "접속수" 컬럼
    post_count: Optional[int] = None   # 게시물 수 (현재 미사용, 추후 추가)
    mb_no: Optional[str] = None
    raw_row_html: str = ""
    # v1.0.2: 동호회관리자 플래그 — cl_level 과 별개로 사이트에 표시되는
    # admin/관리자 체크박스·표기로부터 추출. True 일 때 cl_level 이 어떤 값이든
    # 화면에는 "동호회관리자" 로 표시되어 일반 회원과 즉시 구분된다.
    is_admin: bool = False

    def display(self) -> str:
        last = self.last_login_date.isoformat() if self.last_login_date else "알 수 없음"
        return f"{self.user_id} / {self.nickname} / {self.level_label} / 마지막접속 {last}"


@dataclass(frozen=True)
class FormApplicant:
    """자료실 신청 구글 폼('설문지 응답 시트1') 한 행.

    매칭 키는 member_user_id(폼의 '희망아이디' 열) — 소리샘/DSM user_id 와 같다고 본다.
    applied_at 은 구글 폼 타임스탬프 원본 문자열(형식이 환경마다 달라 그대로 보관).
    plan_months 는 요금제 문자열에서 파싱한 개월 수 (0 = 못 파싱).
    """
    member_user_id: str
    applied_at: str = ""
    name: str = ""
    phone: str = ""
    email: str = ""
    plan_raw: str = ""
    plan_months: int = 0
    agreed: bool = False


Action = Literal["demote", "delete", "skip"]


@dataclass
class AdjustmentItem:
    member: Member
    action: Action
    from_level: int
    to_level: Optional[int]
    reason: str
    # v1.2.7: 장기미접속 판정에 같이 본 green3 활동량. None = 조회 안 했거나 실패.
    green3_posts: Optional[int] = None
    green3_comments: Optional[int] = None

    def display(self) -> str:
        from config import LEVEL_LABELS
        nick = self.member.nickname or self.member.name or self.member.user_id
        # v1.0.2: 등급 라벨은 LEVEL_LABELS(정수 매핑) 우선 — 사이트 셀렉트 옵션 텍스트가
        # 다르게 보일 수 있으므로 사용자 확정 매핑을 정답으로 사용.
        cur_label = LEVEL_LABELS.get(
            self.from_level, self.member.level_label or f"레벨 {self.from_level}"
        )
        last = (
            self.member.last_login_date.isoformat()
            if self.member.last_login_date else "알 수 없음"
        )
        activity = ""
        if self.green3_posts is not None and self.green3_comments is not None:
            activity = f" / 글 {self.green3_posts}건 / 댓글 {self.green3_comments}건"
        if self.action == "demote":
            target = LEVEL_LABELS.get(self.to_level or 0, f"레벨 {self.to_level}")
            return (
                f"{self.member.user_id} / {nick} / {cur_label} → {target} / "
                f"마지막접속 {last}{activity}"
            )
        if self.action == "delete":
            return (
                f"{self.member.user_id} / {nick} / {cur_label} → 탈퇴 / "
                f"마지막접속 {last}{activity}"
            )
        return (
            f"{self.member.user_id} / {nick} / 건너뜀 / "
            f"마지막접속 {last}"
        )


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
    # 진단용으로 디스크에 저장한 HTML 덤프 경로들 ({라벨: 파일경로}).
    # 실제 등급 변경이 사이트에 안 먹는 경우 이 파일을 보면 폼 구조·토큰·응답을 확인 가능.
    debug: dict = field(default_factory=dict)
    # 변경 후 재조회로 실제 반영 여부를 확인한 결과 (True/False/None=확인불가).
    verified: bool | None = None
    # 실제로 사이트에 보낸 옵션 값({mb_id: 실제 site value(int)}). LEVEL_LABELS 가
    # 사이트 폼의 옵션 매핑과 어긋난 스킨에서, 호출자가 메모리 모델을 갱신할 때 쓰는 값.
    effective_levels: dict = field(default_factory=dict)
    # 변경 후 사이트가 보여 준 등급 라벨({mb_id: 라벨 문자열}). UI 갱신용 — LEVEL_LABELS
    # 매핑보다 사이트 폼이 실제로 쓰는 텍스트가 더 정확하다.
    effective_labels: dict = field(default_factory=dict)


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
