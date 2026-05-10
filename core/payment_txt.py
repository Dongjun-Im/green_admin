"""자료실 구독비 — 텍스트(TXT) 리포트.

backups/payments_YYYY-MM-DD.txt 로 저장. XLSX/HTML 과 동일 데이터를 평문으로.
스크린리더로 한 줄씩 읽기 편한 고정폭 표 형태.

섹션:
    헤더 (제목 + 생성 시각)
    [요약]                — KPI 4종
    [구독 매트릭스]        — 회원 × 12개월, 5자 너비 컬럼
    [이번 달·다음 달 만료] — follow-up 명단
    [활성 구독 상세]       — 만료 임박순 한 줄씩
    [참고 — 단가표]
    [기타 입금 요약]       — 카운트·합계만 (PII 보호)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from config import APP_NAME, APP_VERSION, BACKUPS_DIR, SUBSCRIPTION_PRICING
from core.payment_matrix import cell_state, month_range, status_summary
from core.payment_store import lookup_months

if TYPE_CHECKING:
    from core.models import Member
    from core.payment_store import PaymentStore, Subscription


_MATRIX_MONTHS = 12
_DIVIDER = "-" * 110


def default_txt_path(today: date | None = None) -> Path:
    d = today or date.today()
    return Path(BACKUPS_DIR) / f"payments_{d.isoformat()}.txt"


def render_payment_txt(
    store: PaymentStore,
    members: list[Member],
    today: date | None = None,
) -> str:
    today = today or date.today()
    members_by_uid = {m.user_id: m for m in members}
    subs_by_uid: dict[str, list[Subscription]] = {}
    for s in store.all_subscriptions():
        subs_by_uid.setdefault(s.member_user_id, []).append(s)

    lines: list[str] = []
    lines.append(f"초록등대 자료실 구독 리포트 ({today.isoformat()})")
    lines.append(
        f"생성: {datetime.now().strftime('%Y-%m-%d %H:%M')} · {APP_NAME} v{APP_VERSION}"
    )
    lines.append("")
    _append_summary(lines, subs_by_uid, today)
    lines.append("")
    _append_matrix(lines, subs_by_uid, members_by_uid, today)
    lines.append("")
    _append_expiring(lines, subs_by_uid, members_by_uid, today)
    lines.append("")
    _append_active_detail(lines, subs_by_uid, members_by_uid, today)
    lines.append("")
    _append_pricing(lines)
    lines.append("")
    _append_other_deposits(lines, store)

    return "\n".join(lines) + "\n"


def write_payment_txt(
    path: Path | str,
    store: PaymentStore,
    members: list[Member],
    today: date | None = None,
) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_payment_txt(store, members, today=today), encoding="utf-8"
    )
    return out


# ---------- 섹션 ----------

def _append_summary(lines: list[str], subs_by_uid: dict, today: date) -> None:
    active = 0
    expiring_this = 0
    expiring_next = 0
    next_month = today.replace(day=1) + timedelta(days=32)
    next_y, next_m = next_month.year, next_month.month
    for subs in subs_by_uid.values():
        if not subs:
            continue
        latest = max(subs, key=lambda s: s.period_to).period_to
        if latest >= today:
            active += 1
        if latest.year == today.year and latest.month == today.month:
            expiring_this += 1
        if latest.year == next_y and latest.month == next_m:
            expiring_next += 1

    lines.append("[요약]")
    lines.append(f"  활성 구독자  : {active}명")
    lines.append(f"  이번 달 만료 : {expiring_this}명")
    lines.append(f"  다음 달 만료 : {expiring_next}명")
    lines.append(f"  기준일       : {today.isoformat()}")


def _append_matrix(
    lines: list[str], subs_by_uid: dict, members_by_uid: dict, today: date
) -> None:
    lines.append("[구독 매트릭스 — 최근 12개월]")
    if not subs_by_uid:
        lines.append("  (구독 이력이 없습니다)")
        return

    months = month_range(today, _MATRIX_MONTHS)

    # 만료 임박순
    items = []
    for uid, subs in subs_by_uid.items():
        latest = max((s.period_to for s in subs), default=date.min)
        items.append((uid, subs, latest))
    items.sort(key=lambda x: x[2])

    # 헤더
    month_cols = "  ".join(h.label for h in months)
    lines.append(f"  {'아이디':<14} {'이름':<10} {month_cols}  상태")
    lines.append(_DIVIDER)

    for uid, subs, _ in items:
        m = members_by_uid.get(uid)
        name = (m.name if m else "") or "(목록 외)"
        cells = "  ".join(
            f"{cell_state(subs, h):>5}" for h in months
        )
        # cell_state 는 1~2자 → 5자폭 우측정렬은 좀 헐겁지만 헤더 폭과 일치 (YY-MM=5자)
        # 좁히려면 헤더도 좁아야 함. 가독성 위해 5자 유지.
        lines.append(
            f"  {_pad(uid, 14)} {_pad(name, 10)} {cells}  "
            f"{status_summary(subs, today=today)}"
        )


def _append_expiring(
    lines: list[str], subs_by_uid: dict, members_by_uid: dict, today: date
) -> None:
    lines.append("[이번 달 / 다음 달 만료 회원]")
    next_month = today.replace(day=1) + timedelta(days=32)
    next_y, next_m = next_month.year, next_month.month

    rows: list[tuple[str, str, str, date]] = []
    for uid, subs in subs_by_uid.items():
        latest = max(subs, key=lambda s: s.period_to).period_to
        in_this = latest.year == today.year and latest.month == today.month
        in_next = latest.year == next_y and latest.month == next_m
        if not (in_this or in_next):
            continue
        m = members_by_uid.get(uid)
        rows.append((
            uid,
            (m.name if m else "") or "(목록 외)",
            (m.nickname if m else "") or "",
            latest,
        ))
    if not rows:
        lines.append("  (해당 회원 없음)")
        return
    rows.sort(key=lambda r: r[3])
    for uid, name, nick, end in rows:
        nick_part = f" ({nick})" if nick else ""
        lines.append(f"  {_pad(uid, 14)} {name}{nick_part}  →  {end.isoformat()}")


def _append_active_detail(
    lines: list[str], subs_by_uid: dict, members_by_uid: dict, today: date
) -> None:
    lines.append("[활성 구독 상세 — 만료 임박순]")
    rows: list[tuple[str, str, str, date, date, int, int]] = []
    for uid, subs in subs_by_uid.items():
        latest = max(subs, key=lambda s: s.period_to)
        if latest.period_to < today:
            continue
        m = members_by_uid.get(uid)
        first_start = min(s.period_from for s in subs)
        days_left = (latest.period_to - today).days
        total_months = sum(s.months for s in subs)
        rows.append((
            uid,
            (m.name if m else "") or "(목록 외)",
            (m.nickname if m else "") or "",
            first_start,
            latest.period_to,
            days_left,
            total_months,
        ))
    if not rows:
        lines.append("  (현재 활성 구독자 없음)")
        return
    rows.sort(key=lambda r: r[4])
    for uid, name, nick, start, end, days, months in rows:
        nick_part = f" ({nick})" if nick else ""
        lines.append(
            f"  {_pad(uid, 14)} {name}{nick_part}  "
            f"{start.isoformat()} → {end.isoformat()}  "
            f"(남은 {days}일, 총 {months}개월)"
        )


def _append_pricing(lines: list[str]) -> None:
    lines.append("[참고 — 단가표]")
    for amount, months in sorted(SUBSCRIPTION_PRICING.items()):
        lines.append(f"  {amount:>8,}원  →  {months:>2}개월")


def _append_other_deposits(lines: list[str], store: PaymentStore) -> None:
    lines.append("[기타 입금 요약]")
    others = [t for t in store.all_transactions() if lookup_months(t.amount) is None]
    if not others:
        lines.append("  (단가표 외 입금 없음)")
        return
    total = sum(t.amount for t in others)
    lines.append(f"  {len(others)}건 / 합계 {total:,}원")
    lines.append('  (개별 거래는 앱의 "기타 입금 보기"에서 확인 — '
                 "리포트에는 PII 보호를 위해 카운트만 표시)")


# ---------- 헬퍼 ----------

def _pad(s: str, width: int) -> str:
    """한글이 1셀이 아니라 2셀이 표시되는 콘솔 환경 고려.
    파일은 그냥 평문이라 단순 ljust 면 충분 — 가독성용 최소 보정."""
    return s.ljust(width)
