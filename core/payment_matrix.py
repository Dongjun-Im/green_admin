"""회원 × 월 구독 매트릭스 계산.

UI 와 분리된 순수 계산 로직 — 단위 테스트하기 좋게.

규칙:
    · 각 셀은 (회원, 월) 의 구독 상태 한 글자/짧은 문자열.
    · 구독 구간이 그 월과 겹치고 월 마지막날까지 활성 → "O"
    · 그 월에 만료 → 만료일(2자리 숫자, 예: "14")
    · 겹침 없음 → "."
    · 다음 만료까지 남은 일수 같은 KPI 는 별도 함수에서 계산.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Sequence

from core.payment_store import Subscription


@dataclass(frozen=True)
class MonthHeader:
    year: int
    month: int

    @property
    def label(self) -> str:
        return f"{self.year % 100:02d}-{self.month:02d}"

    @property
    def first_day(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def last_day(self) -> date:
        if self.month == 12:
            nxt = date(self.year + 1, 1, 1)
        else:
            nxt = date(self.year, self.month + 1, 1)
        return nxt - timedelta(days=1)


def month_range(end: date, count: int) -> list[MonthHeader]:
    """end 가 속한 월 기준 과거 count 개월의 MonthHeader 리스트 (오래된 순)."""
    out: list[MonthHeader] = []
    y, m = end.year, end.month
    for _ in range(count):
        out.append(MonthHeader(y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def cell_state(subs: Sequence[Subscription], header: MonthHeader) -> str:
    """그 회원의 모든 구독 구간과 한 달의 겹침 상태.

    Returns:
        "O"  : 월 전체 활성
        "DD" : 그 달에 만료 (만료일자 2자리)
        "."  : 비활성
    """
    overlapping = [
        s for s in subs
        if s.period_from <= header.last_day and s.period_to >= header.first_day
    ]
    if not overlapping:
        return "."
    # 가장 늦게 끝나는 구간을 기준으로 표시
    latest = max(overlapping, key=lambda s: s.period_to)
    if latest.period_to >= header.last_day:
        return "O"
    return f"{latest.period_to.day:02d}"


def status_summary(subs: Sequence[Subscription], today: date) -> str:
    """회원 한 명의 현재 구독 상태 한 줄 요약."""
    if not subs:
        return "구독 이력 없음"
    latest = max(subs, key=lambda s: s.period_to)
    if latest.period_to < today:
        return f"만료 ({latest.period_to.isoformat()})"
    days_left = (latest.period_to - today).days
    return f"구독중 ({latest.period_to.isoformat()} 만료, 남은 {days_left}일)"


def combined_status(
    subs: Sequence[Subscription],
    today: date,
    *,
    in_dsm_group: bool | None,
) -> str:
    """DSM 자료실 그룹 멤버십 + 결제 활성 상태를 결합한 정합성 라벨.

    in_dsm_group=None 이면 DSM 정보가 아직 없는 상태 — "?" 반환.
    그 외 (True/False) × (결제활성/결제만료/이력없음) 6가지 조합을 운영자가
    한눈에 읽을 수 있는 한국어 라벨로.

    조합:
        DSM 있음 + 활성구독       -> "정합 (DSM 결제)"
        DSM 있음 + 만료구독       -> "DSM 잔류 (결제 만료)"
        DSM 있음 + 구독 이력 없음 -> "DSM 단독 (결제 없음)"
        DSM 없음 + 활성구독       -> "활성화 누락 (DSM 추가 필요)"
        DSM 없음 + 만료구독       -> "이전 회원 (DSM 정리됨)"
        DSM 없음 + 이력 없음       -> "-"
    """
    if in_dsm_group is None:
        return "? (DSM 미조회)"
    has_subs = bool(subs)
    if has_subs:
        latest = max(subs, key=lambda s: s.period_to)
        active = latest.period_to >= today
    else:
        active = False

    if in_dsm_group and active:
        return "정합 (DSM 결제)"
    if in_dsm_group and has_subs:
        return "DSM 잔류 (결제 만료)"
    if in_dsm_group:
        return "DSM 단독 (결제 없음)"
    if active:
        return "활성화 누락 (DSM 추가 필요)"
    if has_subs:
        return "이전 회원 (DSM 정리됨)"
    return "-"


# ---------- 신청·결제 상태 라벨 (구글 폼 신청자 + 토스 결제 결합) ----------

def subscription_period_label(subs: Sequence[Subscription], today: date) -> str:
    """가장 늦은 구독의 '시작일 ~ 만료일' 문자열. 구독 이력 없으면 '-'.

    today 인자는 시그니처 일관성을 위해 받지만 현재 표시에는 쓰지 않음.
    """
    if not subs:
        return "-"
    latest = max(subs, key=lambda s: s.period_to)
    return f"{latest.period_from.isoformat()} ~ {latest.period_to.isoformat()}"


def payment_state_label(*, has_subscription: bool, is_applicant: bool) -> str:
    """토스 입금 기준 상태.

    구독 레코드가 있으면(=토스 입금이 매칭돼 구독이 산정됨) '입금완료'.
    구독은 없는데 폼 신청자면 '미입금'. 둘 다 아니면 '-'.
    """
    if has_subscription:
        return "입금완료"
    if is_applicant:
        return "미입금"
    return "-"


def short_subscription_status(
    subs: Sequence[Subscription], today: date, *, is_applicant: bool,
) -> str:
    """매트릭스 '구독 상태' 컬럼용 짧은 라벨.

    활성 구독 있음 -> '구독중'; 만료 구독만 있음 -> '만료';
    구독 없고 폼/앱 신청자 -> '결제 대기'; 그 외 -> '-'.
    """
    if subs:
        latest = max(subs, key=lambda s: s.period_to)
        return "구독중" if latest.period_to >= today else "만료"
    if is_applicant:
        return "결제 대기"
    return "-"


def matrix_status_label(
    subs: Sequence[Subscription], today: date, *, is_applicant: bool,
) -> str:
    """매트릭스 '구독 상태' 컬럼용 — 구독중/만료/안 함을 만료일·남은 일수까지 명확히.

        활성 구독       -> '구독중 — 2026-06-30 까지 (5일 남음)'  (오늘이 만료일이면 '오늘까지')
        만료 구독만     -> '구독 만료 — 2026-03-31'
        구독 이력 없음  -> 폼/앱 신청자면 '구독 안 함 (결제 대기)', 아니면 '구독 안 함'
    """
    if subs:
        latest = max(subs, key=lambda s: s.period_to)
        end = latest.period_to.isoformat()
        if latest.period_to < today:
            return f"구독 만료 — {end}"
        days_left = (latest.period_to - today).days
        if days_left <= 0:
            return f"구독중 — {end} 까지 (오늘까지)"
        return f"구독중 — {end} 까지 ({days_left}일 남음)"
    return "구독 안 함 (결제 대기)" if is_applicant else "구독 안 함"
