"""장기미접속 회원 등급 조정 서비스."""
from __future__ import annotations

from datetime import date
from typing import Callable, Optional

from dateutil.relativedelta import relativedelta

from config import GREEN3_BOARD, INACTIVITY_MONTHS, LEVEL_TRANSITIONS, WITHDRAW_LEVEL
from core.crawler import MemberCrawler
from core.member_admin import MemberAdminAdapter
from core.models import (
    AdjustmentItem,
    AdjustmentPlan,
    AdjustmentReport,
    Member,
)


ProgressCB = Callable[[int, int], None]


class LevelAdjustmentService:
    INACTIVITY_MONTHS = INACTIVITY_MONTHS
    LEVEL_TRANSITIONS = LEVEL_TRANSITIONS
    # 활동 기반 면제 임계 — green3('우리들의 이야기') 게시판에서
    # 글·댓글이 각각 이 값 이상이면 6개월 미접속이어도 조정 대상에서 제외.
    GREEN3_MIN_POSTS = 3
    GREEN3_MIN_COMMENTS = 3

    def __init__(
        self,
        crawler: MemberCrawler,
        admin: MemberAdminAdapter,
        admin_user_id: str,
        cutoff_provider: Optional[Callable[[], date]] = None,
        log_writer=None,
        blocklist=None,
        activity_counter=None,
        green3_board: str = GREEN3_BOARD,
    ) -> None:
        self.crawler = crawler
        self.admin = admin
        self.admin_user_id = (admin_user_id or "").lower()
        self.cutoff_provider = cutoff_provider or (
            lambda: date.today() - relativedelta(months=self.INACTIVITY_MONTHS)
        )
        self.log_writer = log_writer
        # 장기미접속으로 '탈퇴'(WITHDRAW_LEVEL) 처리된 회원 아이디를 보관해 두는
        # 명단(core.withdrawn_blocklist.WithdrawnBlocklist). 재가입 시 자동 거름용.
        self.blocklist = blocklist
        # activity_counter 가 주어지면 6개월 미접속 후보에 대해 green3 글·댓글을
        # 추가로 조회. 둘 다 GREEN3_MIN_POSTS/MIN_COMMENTS 이상이면 '접속자'로
        # 인정하고 조정 대상에서 빼 준다. None 이면 로그인 날짜만 보던 예전 동작.
        self.activity_counter = activity_counter
        self.green3_board = green3_board

    def build_plan(
        self,
        progress_cb: Optional[ProgressCB] = None,
        members: Optional[list[Member]] = None,
        activity_progress_cb: Optional[ProgressCB] = None,
    ) -> AdjustmentPlan:
        if members is None:
            members = self.crawler.fetch_all_members(progress_cb=progress_cb)
        cutoff = self.cutoff_provider()

        # 1단계: 로그인 날짜 기준으로 1차 후보 추림. 'skip' 항목(파싱 실패)도
        # 그대로 모은다. 활동 점검은 'delete'/'demote' 후보에만 적용.
        items: list[AdjustmentItem] = []
        candidates: list[AdjustmentItem] = []  # 활동 점검 대상
        for m in members:
            # 본인 절대 제외
            if m.user_id.lower() == self.admin_user_id:
                continue
            # v1.0.2: 동호회관리자(is_admin) 도 절대 자동 강등 대상이 아님.
            if getattr(m, "is_admin", False):
                continue
            # LEVEL_TRANSITIONS 에 없는 등급(0~4 가입 단계, 9 명예회원)은 절대 제외
            if m.level not in self.LEVEL_TRANSITIONS:
                continue

            if m.last_login_date is None:
                items.append(AdjustmentItem(
                    member=m,
                    action="skip",
                    from_level=m.level,
                    to_level=None,
                    reason="마지막 접속일 파싱 실패 — 안전을 위해 제외",
                ))
                continue

            if m.last_login_date > cutoff:
                # 6개월 이내 접속 → 조정 안 함
                continue

            action, to_level = self.LEVEL_TRANSITIONS[m.level]
            days = (date.today() - m.last_login_date).days
            reason = f"{days}일 미접속 (기준: {cutoff.isoformat()} 이전)"
            candidates.append(AdjustmentItem(
                member=m,
                action=action,
                from_level=m.level,
                to_level=to_level,
                reason=reason,
            ))

        # 2단계: 활동 점검. activity_counter 가 없으면 1차 후보를 그대로 사용
        # (예전 동작 유지). 있으면 green3 글·댓글 카운트를 조회해서
        # 글>=3 AND 댓글>=3 이면 '접속자'로 인정하고 빼 준다.
        if self.activity_counter is None or not candidates:
            items.extend(candidates)
        else:
            total = len(candidates)
            for idx, item in enumerate(candidates, start=1):
                if activity_progress_cb is not None:
                    try:
                        activity_progress_cb(idx, total)
                    except Exception:
                        pass
                posts, comments = self._fetch_green3_activity(item.member.user_id)
                if (
                    posts is not None and comments is not None
                    and posts >= self.GREEN3_MIN_POSTS
                    and comments >= self.GREEN3_MIN_COMMENTS
                ):
                    # 활동 충분 → 접속자로 인정, 조정 대상에서 제외
                    continue
                # 활동 카운트는 그대로 항목에 실어 둠 (UI 목록상자가 그대로 표시).
                item.green3_posts = posts
                item.green3_comments = comments
                if posts is None or comments is None:
                    # 활동 카운트 조회 실패 → 안전하게 로그인 기준으로만 처리.
                    items.append(item)
                else:
                    # 활동도 부족 → 미접속자로 분류. 활동량을 사유에도 함께 기록.
                    item.reason = (
                        f"{item.reason}, green3 글 {posts}건/댓글 {comments}건 "
                        f"(기준 미만)"
                    )
                    items.append(item)

        return AdjustmentPlan(
            items=items,
            total_scanned=len(members),
            cutoff_date=cutoff,
        )

    def _fetch_green3_activity(self, user_id: str) -> tuple[Optional[int], Optional[int]]:
        """주어진 회원의 green3 글·댓글 수. 조회 실패 시 (None, None)."""
        if self.activity_counter is None:
            return (None, None)
        try:
            ma = self.activity_counter.fetch_member(
                user_id, boards=(self.green3_board,),
            )
        except Exception:
            return (None, None)
        ba = ma.by_board.get(self.green3_board)
        if ba is None:
            return (None, None)
        return (ba.posts, ba.comments)

    def apply_plan(
        self,
        plan: AdjustmentPlan,
        progress_cb: Optional[ProgressCB] = None,
    ) -> AdjustmentReport:
        report = AdjustmentReport(dry_run=self.admin.dry_run)
        actionable = plan.actionable
        if not actionable:
            return report

        # 사이트가 일괄 폼을 지원하므로 한 번의 POST 로 모든 변경 처리
        level_map = {
            item.member.user_id: item.to_level
            for item in actionable
            if item.to_level is not None
        }

        if progress_cb:
            try:
                progress_cb(1, 1)
            except Exception:
                pass

        result = self.admin.bulk_apply(
            level_map,
            action_label=f"장기미접속 일괄 조정 ({len(level_map)}명)",
        )

        if result.success:
            for item in actionable:
                if item.action == "delete":
                    report.succeeded_delete.append(item.member)
                else:
                    report.succeeded_demote.append(item.member)
            # 장기미접속 '탈퇴' 처리된 회원을 재가입 차단 명단에 기록.
            # dry_run 모의 실행에서는 기록하지 않는다.
            if self.blocklist is not None and not getattr(self.admin, "dry_run", False):
                try:
                    entries = [
                        (item.member.user_id,
                         getattr(item.member, "nickname", "") or "",
                         f"장기미접속 탈퇴 ({item.reason})")
                        for item in actionable
                        if item.to_level == WITHDRAW_LEVEL
                    ]
                    if entries:
                        self.blocklist.add_many(entries)
                except Exception:
                    pass
        else:
            for item in actionable:
                report.failed.append((item.member, result.message))

        if self.log_writer:
            try:
                for item in actionable:
                    self.log_writer.write_action(item, result)
            except Exception:
                pass

        return report
