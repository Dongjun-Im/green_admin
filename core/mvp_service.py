"""MVP TOP N 분석 서비스 (v0.5 → v1.1 since-date 적용).

분기마다 한 번씩 우리들의 이야기(green3) + 질문게시판(green9) 의
글·댓글을 종합해 활동점수가 가장 높은 회원 N명을 뽑는다.

- 산정 가중치: 활동점수 = 글수 + 댓글수 × COMMENT_WEIGHT
- 명예회원(9) 은 이미 최고 등급이므로 제외 (MVP_EXCLUDED_LEVELS)
- 동호회관리자는 페이지 권한으로 판정 — admin_user_id 만 명시 제외
- since=MVP_SINCE_DATE (config) 이후(포함) 작성된 글·댓글만 카운트
- 출력: 정렬된 list[MvpItem]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Optional

from config import (
    ACTIVITY_BOARDS,
    BACKUPS_DIR,
    COMMENT_WEIGHT,
    LEVEL_LABELS,
    MVP_EXCLUDED_LEVELS,
    MVP_SINCE_DATE,
    MVP_TOP_N,
)
from core.activity_counter import ActivityCounter, MemberActivity
from core.crawler import MemberCrawler
from core.models import Member


ProgressCB = Callable[[int, int], None]


@dataclass
class MvpItem:
    rank: int
    member: Member
    posts: int
    comments: int
    score: float
    by_board: dict[str, dict] = field(default_factory=dict)  # {board: {posts, comments}}

    def display(self) -> str:
        nick = self.member.nickname or self.member.name or self.member.user_id
        # v1.0.2: 동호회관리자는 cl_level 과 무관하게 명시적으로 표시.
        if getattr(self.member, "is_admin", False):
            level_part = f"동호회관리자(cl_level={self.member.level})"
        else:
            level_label = LEVEL_LABELS.get(
                self.member.level, f"레벨 {self.member.level}"
            )
            level_part = f"{level_label}(레벨 {self.member.level})"
        return (
            f"{self.rank:>2}위. {self.member.user_id} ({nick}) / "
            f"{level_part} / "
            f"활동점수 {self.score:.1f} (글 {self.posts}, 댓글 {self.comments})"
        )


@dataclass
class MvpReport:
    quarter: str  # 예: "2026-Q2"
    items: list[MvpItem] = field(default_factory=list)
    total_scanned: int = 0
    total_counted: int = 0
    boards: tuple[str, ...] = ACTIVITY_BOARDS
    since: Optional[date] = None  # 산정 시작일 (None 이면 전체)

    def speak_summary(self) -> str:
        if not self.items:
            return f"{self.quarter} MVP: 산정 가능한 회원이 없습니다."
        top = self.items[0]
        nick = top.member.nickname or top.member.user_id
        since_part = (
            f"({self.since.isoformat()} 이후) " if self.since else ""
        )
        return (
            f"{self.quarter} MVP TOP {len(self.items)} {since_part}. "
            f"1위 {nick}, 활동점수 {top.score:.1f}점."
        )


def quarter_label(d: Optional[date] = None) -> str:
    d = d or date.today()
    q = (d.month - 1) // 3 + 1
    return f"{d.year}-Q{q}"


class MvpService:
    """MVP TOP N 산정.

    산정 대상: ACTIVITY_PROMOTION_BASE_LEVEL(=일반회원) 이상 ~ MVP_EXCLUDED_LEVELS 미만.
    즉 일반회원(6) / 우수(7) / 최우수(8) 만 후보. 명예회원(9)은 제외.
    """

    def __init__(
        self,
        crawler: MemberCrawler,
        admin_user_id: str,
        boards: tuple[str, ...] = ACTIVITY_BOARDS,
        top_n: int = MVP_TOP_N,
        excluded_levels: tuple[int, ...] = MVP_EXCLUDED_LEVELS,
        since: Optional[date] = MVP_SINCE_DATE,
    ) -> None:
        self.crawler = crawler
        self.admin_user_id = (admin_user_id or "").lower()
        self.boards = boards
        self.top_n = top_n
        self.excluded_levels = set(excluded_levels)
        self.since = since
        self.activity_counter = ActivityCounter(crawler.session, boards=boards)

    def run(
        self,
        progress_cb: Optional[ProgressCB] = None,
        members: Optional[list[Member]] = None,
    ) -> MvpReport:
        if members is None:
            members = self.crawler.fetch_all_members(progress_cb=progress_cb)

        # 후보: 일반회원(5) ~ 최우수(7). 명예회원과 그 이하 등급은 제외 (excluded_levels).
        # 동호회관리자(is_admin) 도 MVP 산정 대상에서 제외.
        candidates = [
            m for m in members
            if m.level >= 5
            and m.level not in self.excluded_levels
            and not getattr(m, "is_admin", False)
            and m.user_id.lower() != self.admin_user_id
        ]

        items: list[MvpItem] = []
        total = len(candidates)
        total_counted = 0
        for idx, m in enumerate(candidates, start=1):
            if progress_cb:
                try:
                    progress_cb(idx, total)
                except Exception:
                    pass
            ma: MemberActivity = self.activity_counter.fetch_member(
                m.user_id, since=self.since,
            )
            total_counted += 1
            if ma.score <= 0:
                continue
            by_board: dict[str, dict] = {}
            for bo, ba in ma.by_board.items():
                by_board[bo] = {"posts": ba.posts, "comments": ba.comments}
            items.append(MvpItem(
                rank=0,  # 정렬 후 부여
                member=m,
                posts=ma.total_posts,
                comments=ma.total_comments,
                score=ma.score,
                by_board=by_board,
            ))

        items.sort(key=lambda it: -it.score)
        items = items[: self.top_n]
        for i, it in enumerate(items, start=1):
            it.rank = i

        return MvpReport(
            quarter=quarter_label(),
            items=items,
            total_scanned=len(members),
            total_counted=total_counted,
            boards=self.boards,
            since=self.since,
        )


def write_mvp_report(report: MvpReport, path: Optional[Path] = None) -> Path:
    """리포트를 backups/mvp_top10_YYYY-Q.txt 로 저장."""
    out = Path(path or (Path(BACKUPS_DIR) / f"mvp_top{len(report.items)}_{report.quarter}.txt"))
    out.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append(f"초록등대 MVP TOP {len(report.items)} — {report.quarter}")
    lines.append(
        f"분석 대상 회원 {report.total_scanned}명 중 활동점수 산정 {report.total_counted}명"
    )
    lines.append(
        f"산정 게시판: {', '.join(report.boards)} "
        f"(가중치: 댓글 = {COMMENT_WEIGHT})"
    )
    if report.since is not None:
        lines.append(f"산정 시작일: {report.since.isoformat()} (이후 작성된 글·댓글만)")
    lines.append("=" * 60)
    if not report.items:
        lines.append("(MVP 후보 없음)")
    else:
        for it in report.items:
            lines.append(it.display())
            for bo, c in it.by_board.items():
                lines.append(
                    f"      └ {bo}: 글 {c['posts']}건 / 댓글 {c['comments']}건"
                )
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
