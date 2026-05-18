"""운영자 대시보드 — 메인 화면 상단에 표시할 '오늘 해야 할 일' 요약 (v1.2.11).

UI 와 분리된 순수 로직 모음. wx 의존성 없음.

`build_dashboard_lines(...)` 가 (텍스트 라인 리스트) 를 반환하면 `main_frame`
이 그대로 `wx.TextCtrl.SetValue('\\n'.join(lines))` 로 표시한다.

대시보드는 다음 다섯 가지를 한 화면에 보여 준다:
  1) **작업 도래** — 분기/반기 백업·조정·MVP 의 도래 여부 한 줄 요약.
  2) **신규 가입 대기** — 승인 대기 회원 수 (없으면 '없음').
  3) **장기미접속 후보** — 6개월 이상 미접속이면서 admin 이 아닌 회원의
     예상 수 (활동량 면제 미반영 — 정확한 수는 미리보기에서 확인).
  4) **자료실 구독 만료 임박** — (v1.2.12+ 에 추가될 예정 — 현재는 자리만 잡음).
  5) **회원 목록 캐시 상태** — Ctrl+F 로 회원 목록을 한 번 받지 않으면 위
     숫자가 '?' 로 표시되니 사용자에게 명확히 안내.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

from core.models import Member


def _count_inactive_candidates(
    members: Iterable[Member], cutoff: date, admin_user_id: str,
) -> int:
    """6개월(=cutoff) 이전 마지막 접속 + 본인·관리자·미적용 등급 제외."""
    from config import LEVEL_TRANSITIONS

    count = 0
    admin = (admin_user_id or "").lower()
    for m in members:
        if m.user_id.lower() == admin:
            continue
        if getattr(m, "is_admin", False):
            continue
        if m.level not in LEVEL_TRANSITIONS:
            continue
        if m.last_login_date is None:
            continue
        if m.last_login_date <= cutoff:
            count += 1
    return count


def build_dashboard_lines(
    *,
    tracker,
    members: Optional[list[Member]] = None,
    pending_count: Optional[int] = None,
    admin_user_id: str,
    today: Optional[date] = None,
    inactivity_cutoff: Optional[date] = None,
) -> list[str]:
    """대시보드에 보여 줄 텍스트 줄들을 만들어 돌려준다.

    - tracker: ScheduleTracker. 백업/조정/MVP 도래 정보 출처.
    - members: 회원 목록 캐시. None 이면 '회원 목록 미수집' 안내만.
    - pending_count: 가입 대기 인원 (find_pending 결과). None 이면 'N/A'.
    - inactivity_cutoff: 6개월 이전 컷오프 (없으면 today - 180일).
    """
    today = today or date.today()
    if inactivity_cutoff is None:
        from datetime import timedelta
        inactivity_cutoff = today - timedelta(days=180)
    lines: list[str] = []

    # 1) 작업 도래
    overdue: list[str] = []
    upcoming: list[str] = []
    for label, due_check, next_date_fn, days_fn in (
        ("우수회원 백업", tracker.is_backup_due,
         tracker.next_backup_date, tracker.days_until_backup),
        ("장기미접속 조정", tracker.is_adjustment_due,
         tracker.next_adjustment_date, tracker.days_until_adjustment),
        ("분기 MVP 분석", tracker.is_mvp_due,
         tracker.next_mvp_date, tracker.days_until_mvp),
    ):
        try:
            if due_check():
                overdue.append(label)
            else:
                days = days_fn()
                if days is not None and days <= 7:
                    upcoming.append(f"{label} {days}일 후")
        except Exception:
            # tracker 가 결과를 못 내도 대시보드 자체는 안 죽어야 함.
            pass
    if overdue:
        lines.append(f"⚠ 도래한 작업: {' / '.join(overdue)}")
    elif upcoming:
        lines.append(f"임박한 작업: {' / '.join(upcoming)}")
    else:
        lines.append("작업 도래: 없음 (다음 일정까지 1주 이상)")

    # 2) 가입 대기
    if pending_count is None:
        lines.append("신규 가입 대기: ? (회원 목록 미수집)")
    elif pending_count > 0:
        lines.append(
            f"⚠ 신규 가입 대기: {pending_count}명 (작업 메뉴 → 신규 가입자 승인)"
        )
    else:
        lines.append("신규 가입 대기: 없음")

    # 3) 장기미접속 후보
    if members is None:
        lines.append("장기미접속 후보: ? (회원 목록 미수집)")
    else:
        n = _count_inactive_candidates(members, inactivity_cutoff, admin_user_id)
        if n == 0:
            lines.append("장기미접속 후보: 없음")
        else:
            lines.append(
                f"장기미접속 후보 (예상): 약 {n}명 (Ctrl+R 미리보기로 정확히 확인)"
            )

    # 4) 회원 목록 캐시 안내 (없을 때만)
    if members is None or pending_count is None:
        lines.append("→ Ctrl+F 회원 검색을 한 번 열면 위 숫자가 채워집니다.")

    return lines
