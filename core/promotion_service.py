"""활동점수 기반 자동 승급 서비스 (v0.5).

게시판 글 수 + 댓글 수 × 가중치 = 활동점수.
산정 게시판: 우리들의 이야기(green3) + 질문게시판(green9) — config.ACTIVITY_BOARDS

규칙 (사이트 실제 등급 기준, v0.5):
  - 가입 초기(3=대기, 4=신청) + 활동점수 3 이상 → 준회원(5)
  - 준회원(5) + 활동점수 5 이상 → 일반회원(6)        ← v0.5 신규
  - 일반회원(6) + 활동점수 30 이상  → 우수회원(7)
  - 일반회원(6) + 활동점수 60 이상  → 최우수회원(8)
  - 일반회원(6) + 활동점수 300 이상 → 명예회원(9)

- 본인(admin_user_id)은 무조건 제외
- 손님(0)/탈퇴(1)/거부(2)는 대상 아님
- 7 이상 등급은 단순 승급만 (강등 없음)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from config import (
    ACTIVITY_BOARDS,
    ACTIVITY_PROMOTION_BASE_LEVEL,
    ACTIVITY_PROMOTION_TABLE,
    INITIAL_FROM_LEVELS,
    INITIAL_PROMOTION_MIN_SCORE,
    INITIAL_TO_LEVEL,
    INTERMEDIATE_PROMOTION_FROM_LEVEL,
    INTERMEDIATE_PROMOTION_MIN_SCORE,
    INTERMEDIATE_PROMOTION_TO_LEVEL,
    LEVEL_LABELS,
)
from core.activity_counter import ActivityCounter, MemberActivity
from core.crawler import MemberCrawler
from core.member_admin import MemberAdminAdapter
from core.models import Member


ProgressCB = Callable[[int, int], None]


def determine_target_level(
    current_level: int, score: float
) -> Optional[int]:
    """현재 등급과 활동점수 → 목표 등급. 변경 불필요면 None.

    Args:
        current_level: 현재 사이트 등급 (0~9)
        score: 활동점수 = 글수 + 댓글수 × COMMENT_WEIGHT
    """
    # 가입 초기(대기/신청) → 준회원
    if current_level in INITIAL_FROM_LEVELS:
        if score >= INITIAL_PROMOTION_MIN_SCORE:
            return INITIAL_TO_LEVEL
        return None

    # 준회원 → 일반회원 (v0.5 신규)
    if current_level == INTERMEDIATE_PROMOTION_FROM_LEVEL:
        if score >= INTERMEDIATE_PROMOTION_MIN_SCORE:
            return INTERMEDIATE_PROMOTION_TO_LEVEL
        return None

    # 일반회원 → 우수/최우수/명예 (높은 임계부터 검사 — desc 정렬)
    if current_level == ACTIVITY_PROMOTION_BASE_LEVEL:
        for threshold, target in ACTIVITY_PROMOTION_TABLE:
            if score >= threshold:
                if target > current_level:
                    return target
                return None
        return None

    # 7 이상은 승급/강등 없음
    return None


# 구버전 호환 — 정수 게시글 수만 받던 시그니처를 그대로 둠 (테스트/외부 호출 보호)
def determine_target_level_by_posts(current_level: int, post_count: int) -> Optional[int]:
    return determine_target_level(current_level, float(post_count))


@dataclass
class PromotionItem:
    member: Member
    post_count: int       # 글 수 (활동점수에서 분리해 표시)
    comment_count: int    # 댓글 수
    score: float          # 활동점수
    from_level: int
    to_level: int

    def display(self) -> str:
        from_label = LEVEL_LABELS.get(self.from_level, f"레벨 {self.from_level}")
        to_label = LEVEL_LABELS.get(self.to_level, f"레벨 {self.to_level}")
        nick = self.member.nickname or self.member.name or self.member.user_id
        return (
            f"{self.member.user_id} / {nick} / "
            f"글 {self.post_count}건·댓글 {self.comment_count}건 (점수 {self.score:.1f}) / "
            f"{from_label} → {to_label}"
        )


@dataclass
class PromotionPlan:
    items: list[PromotionItem] = field(default_factory=list)
    total_scanned: int = 0
    total_counted: int = 0   # 활동점수를 실제로 조회한 회원 수


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
    """활동점수 기반 자동 승급 (v0.5).

    후보:
      · 가입 초기(3, 4) → 준회원(5)
      · 준회원(5)       → 일반회원(6)
      · 일반회원(6)     → 우수(7) / 최우수(8) / 명예(9)
    """

    def __init__(
        self,
        crawler: MemberCrawler,
        admin: MemberAdminAdapter,
        admin_user_id: str,
        log_writer=None,
        boards: tuple[str, ...] = ACTIVITY_BOARDS,
    ) -> None:
        self.crawler = crawler
        self.admin = admin
        self.admin_user_id = (admin_user_id or "").lower()
        self.log_writer = log_writer
        self.boards = boards
        # 활동 카운터는 crawler.session 재사용
        self.activity_counter = ActivityCounter(crawler.session, boards=boards)

    def build_plan(
        self,
        progress_cb: Optional[ProgressCB] = None,
        members: Optional[list[Member]] = None,
    ) -> PromotionPlan:
        if members is None:
            members = self.crawler.fetch_all_members(progress_cb=progress_cb)

        # 후보 선정: 가입 초기(3,4) + 준회원(5) + 일반회원(6)
        # v1.0.2: 동호회관리자(is_admin) 는 자동 승급 대상이 아님.
        candidate_levels = set(INITIAL_FROM_LEVELS) | {
            INTERMEDIATE_PROMOTION_FROM_LEVEL,
            ACTIVITY_PROMOTION_BASE_LEVEL,
        }
        candidates: list[Member] = []
        for m in members:
            if m.user_id.lower() == self.admin_user_id:
                continue
            if getattr(m, "is_admin", False):
                continue
            if m.level in candidate_levels:
                candidates.append(m)

        # 활동점수 조회 (가장 느린 단계 — 회원당 게시판 수 × 2 회 호출)
        items: list[PromotionItem] = []
        total_counted = 0
        total = len(candidates)
        for idx, m in enumerate(candidates, start=1):
            if progress_cb:
                try:
                    progress_cb(idx, total)
                except Exception:
                    pass
            ma: MemberActivity = self.activity_counter.fetch_member(m.user_id)
            # 글 수가 None 이 아니라 0 이라도 카운트로 인정 (실제로 활동 없음)
            total_counted += 1
            posts = ma.total_posts
            comments = ma.total_comments
            score = ma.score
            m.post_count = posts  # 모델 호환
            target = determine_target_level(m.level, score)
            if target is None:
                continue
            items.append(PromotionItem(
                member=m,
                post_count=posts,
                comment_count=comments,
                score=score,
                from_level=m.level,
                to_level=target,
            ))

        # 큰 승급부터 (new level desc, score desc)
        items.sort(key=lambda it: (-it.to_level, -it.score))

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
                        reason=(
                            f"활동점수 {it.score:.1f} "
                            f"(글 {it.post_count}/댓글 {it.comment_count}) "
                            f"→ {LEVEL_LABELS.get(it.to_level, it.to_level)}"
                        ),
                    )
                    self.log_writer.write_action(audit, result)
            except Exception:
                pass

        return report
