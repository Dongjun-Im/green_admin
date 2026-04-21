"""게시물 기반 자동 승급 서비스.

"우리들의 이야기" (green3) 게시판의 회원별 글 수를 기준으로 등급을 결정.

규칙 (사이트 실제 등급 기준):
  - 가입 초기(3=대기, 4=신청) + 3건 이상 → 일반회원(5)
  - 일반회원(5) + 30건 이상 → 우수회원(6)
  - 일반회원(5) + 50건 이상 → 최우수회원(7)
  - 일반회원(5) + 100건 이상 → 명예회원(8)

- 기존 등급 6 이상(우수/최우수/명예/관리자)은 그대로 유지 (단순 승급만)
- 본인(admin_user_id)은 무조건 제외
- 손님(0)/탈퇴(1)/거부(2)는 대상 아님
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from config import (
    INITIAL_FROM_LEVELS,
    INITIAL_PROMOTION_MIN_POSTS,
    INITIAL_TO_LEVEL,
    LEVEL_LABELS,
    POST_COUNT_PROMOTION_TABLE,
)
from core.crawler import MemberCrawler
from core.member_admin import MemberAdminAdapter
from core.models import Member
from core.post_count_green3 import Green3PostCounter


ProgressCB = Callable[[int, int], None]


def determine_target_level(current_level: int, post_count: int) -> Optional[int]:
    """주어진 등급/게시물 수에 해당하는 "목표 등급" 을 반환.

    이미 목표 등급 이상이면 None (변경 불필요).
    대상이 아닌 등급도 None.
    """
    if current_level in INITIAL_FROM_LEVELS:
        # 가입 초기: 일정 수 이상 쓰면 일반회원으로
        if post_count >= INITIAL_PROMOTION_MIN_POSTS:
            return INITIAL_TO_LEVEL
        return None

    if current_level == INITIAL_TO_LEVEL:  # 일반회원(5)
        # 높은 임계값부터 검사 (POST_COUNT_PROMOTION_TABLE 이 desc 로 정렬돼 있음)
        for threshold, target in POST_COUNT_PROMOTION_TABLE:
            if post_count >= threshold:
                if target > current_level:
                    return target
                return None
        return None

    # 6 이상은 승급 대상 아님 (강등도 없음)
    return None


@dataclass
class PromotionItem:
    member: Member
    post_count: int
    from_level: int
    to_level: int

    def display(self) -> str:
        from_label = LEVEL_LABELS.get(self.from_level, f"레벨 {self.from_level}")
        to_label = LEVEL_LABELS.get(self.to_level, f"레벨 {self.to_level}")
        nick = self.member.nickname or self.member.name or self.member.user_id
        return (
            f"{self.member.user_id} / {nick} / 게시물 {self.post_count}건 / "
            f"{from_label} → {to_label}"
        )


@dataclass
class PromotionPlan:
    items: list[PromotionItem] = field(default_factory=list)
    total_scanned: int = 0
    total_counted: int = 0   # 게시물 수를 실제로 조회한 회원 수


@dataclass
class PromotionReport:
    succeeded: list[PromotionItem] = field(default_factory=list)
    failed: list[tuple[Member, str]] = field(default_factory=list)
    dry_run: bool = True

    @property
    def speak_summary(self) -> str:
        prefix = "미리보기: " if self.dry_run else "승급 완료: "
        msg = f"{len(self.succeeded)}명 자동 승급"
        if self.failed:
            msg += f", 실패 {len(self.failed)}건"
        return prefix + msg


class PromotionService:
    """기존 인터페이스 유지. 내부 로직만 게시물 수 기반으로 교체."""

    def __init__(
        self,
        crawler: MemberCrawler,
        admin: MemberAdminAdapter,
        admin_user_id: str,
        log_writer=None,
    ) -> None:
        self.crawler = crawler
        self.admin = admin
        self.admin_user_id = (admin_user_id or "").lower()
        self.log_writer = log_writer
        # 게시물 카운터는 crawler.session 재사용
        self.post_counter = Green3PostCounter(crawler.session)

    def build_plan(
        self,
        progress_cb: Optional[ProgressCB] = None,
        members: Optional[list[Member]] = None,
    ) -> PromotionPlan:
        if members is None:
            members = self.crawler.fetch_all_members(progress_cb=progress_cb)

        # 후보 선정: 가입 초기(3,4) + 일반회원(5)
        candidates: list[Member] = []
        for m in members:
            if m.user_id.lower() == self.admin_user_id:
                continue
            if m.level in INITIAL_FROM_LEVELS or m.level == INITIAL_TO_LEVEL:
                candidates.append(m)

        # 게시물 수 조회 (가장 느린 단계)
        items: list[PromotionItem] = []
        total_counted = 0
        total = len(candidates)
        for idx, m in enumerate(candidates, start=1):
            if progress_cb:
                try:
                    progress_cb(idx, total)
                except Exception:
                    pass
            n = self.post_counter.fetch(m.user_id)
            if n is None:
                continue
            total_counted += 1
            m.post_count = n  # 모델에 반영
            target = determine_target_level(m.level, n)
            if target is None:
                continue
            items.append(PromotionItem(
                member=m,
                post_count=n,
                from_level=m.level,
                to_level=target,
            ))

        # 큰 승급부터 (new level desc, post_count desc)
        items.sort(key=lambda it: (-it.to_level, -it.post_count))

        return PromotionPlan(
            items=items,
            total_scanned=len(members),
            total_counted=total_counted,
        )

    def apply_plan(
        self,
        plan: PromotionPlan,
        progress_cb: Optional[ProgressCB] = None,
    ) -> PromotionReport:
        report = PromotionReport(dry_run=self.admin.dry_run)
        if not plan.items:
            return report

        # 일괄 폼 POST 로 한 번에 처리
        level_map = {it.member.user_id: it.to_level for it in plan.items}
        result = self.admin.bulk_apply(
            level_map,
            action_label=f"게시물 기반 자동 승급 ({len(level_map)}명)",
        )

        if result.success:
            report.succeeded = list(plan.items)
        else:
            report.failed = [(it.member, result.message) for it in plan.items]

        if self.log_writer:
            try:
                from core.models import AdjustmentItem
                for it in plan.items:
                    audit = AdjustmentItem(
                        member=it.member,
                        action="demote",  # 표시용
                        from_level=it.from_level,
                        to_level=it.to_level,
                        reason=f"게시물 {it.post_count}건 → {LEVEL_LABELS.get(it.to_level, it.to_level)}",
                    )
                    self.log_writer.write_action(audit, result)
            except Exception:
                pass

        return report
