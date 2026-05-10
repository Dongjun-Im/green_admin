"""payment_matrix: 회원·월 매트릭스 셀 상태 단위 테스트."""
from __future__ import annotations

from datetime import date

from core.payment_matrix import (
    MonthHeader,
    cell_state,
    combined_status,
    month_range,
    status_summary,
)
from core.payment_store import Subscription


def _sub(uid: str, pf: date, pt: date, sub_id: int = 1, txn_id: int = 1) -> Subscription:
    return Subscription(
        id=sub_id,
        member_user_id=uid,
        transaction_id=txn_id,
        months=1,
        period_from=pf,
        period_to=pt,
    )


def test_month_range_six_months_back():
    months = month_range(date(2026, 5, 8), 6)
    assert [(h.year, h.month) for h in months] == [
        (2025, 12), (2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5),
    ]


def test_month_header_last_day_handles_year_rollover():
    assert MonthHeader(2026, 12).last_day == date(2026, 12, 31)
    assert MonthHeader(2026, 2).last_day == date(2026, 2, 28)
    assert MonthHeader(2024, 2).last_day == date(2024, 2, 29)  # 윤년


def test_cell_state_full_month_active():
    sub = _sub("h", date(2026, 4, 15), date(2026, 6, 30))
    # 5월 전체 활성
    assert cell_state([sub], MonthHeader(2026, 5)) == "O"


def test_cell_state_expires_mid_month():
    sub = _sub("h", date(2026, 4, 15), date(2026, 5, 14))
    # 5월 14일에 만료
    assert cell_state([sub], MonthHeader(2026, 5)) == "14"


def test_cell_state_starts_mid_month_full_remainder():
    sub = _sub("h", date(2026, 5, 10), date(2026, 6, 30))
    # 5월 10일에 시작, 5월 마지막일까지 활성 → "O"
    assert cell_state([sub], MonthHeader(2026, 5)) == "O"


def test_cell_state_no_overlap():
    sub = _sub("h", date(2026, 1, 1), date(2026, 1, 31))
    assert cell_state([sub], MonthHeader(2026, 5)) == "."


def test_cell_state_picks_latest_when_multiple_overlap():
    """체이닝된 두 구간이 같은 월을 다 덮으면, 더 늦게 끝나는 것을 기준."""
    s1 = _sub("h", date(2026, 4, 15), date(2026, 5, 14), sub_id=1, txn_id=1)
    s2 = _sub("h", date(2026, 5, 15), date(2026, 8, 14), sub_id=2, txn_id=2)
    # 5월 전체 커버 → "O"
    assert cell_state([s1, s2], MonthHeader(2026, 5)) == "O"


def test_status_summary_active():
    sub = _sub("h", date(2026, 4, 15), date(2026, 6, 30))
    s = status_summary([sub], today=date(2026, 5, 8))
    assert "구독중" in s and "53일" in s  # 6/30 - 5/8 = 53


def test_status_summary_expired():
    sub = _sub("h", date(2026, 1, 1), date(2026, 2, 14))
    s = status_summary([sub], today=date(2026, 5, 8))
    assert s.startswith("만료")


def test_status_summary_no_history():
    assert status_summary([], today=date(2026, 5, 8)) == "구독 이력 없음"


# ---------- combined_status ----------

def test_combined_status_unknown_when_dsm_not_fetched():
    sub = _sub("h", date(2026, 1, 1), date(2026, 6, 30))
    assert combined_status([sub], date(2026, 5, 8), in_dsm_group=None) == "? (DSM 미조회)"


def test_combined_status_dsm_active_payment_active():
    sub = _sub("h", date(2026, 1, 1), date(2026, 6, 30))
    s = combined_status([sub], date(2026, 5, 8), in_dsm_group=True)
    assert s == "정합 (DSM 결제)"


def test_combined_status_dsm_yes_payment_expired():
    sub = _sub("h", date(2025, 1, 1), date(2025, 12, 31))
    s = combined_status([sub], date(2026, 5, 8), in_dsm_group=True)
    assert s == "DSM 잔류 (결제 만료)"


def test_combined_status_dsm_yes_no_payment_history():
    s = combined_status([], date(2026, 5, 8), in_dsm_group=True)
    assert s == "DSM 단독 (결제 없음)"


def test_combined_status_dsm_no_payment_active():
    sub = _sub("h", date(2026, 1, 1), date(2026, 6, 30))
    s = combined_status([sub], date(2026, 5, 8), in_dsm_group=False)
    assert s == "활성화 누락 (DSM 추가 필요)"


def test_combined_status_dsm_no_payment_expired():
    sub = _sub("h", date(2025, 1, 1), date(2025, 12, 31))
    s = combined_status([sub], date(2026, 5, 8), in_dsm_group=False)
    assert s == "이전 회원 (DSM 정리됨)"


def test_combined_status_neither():
    assert combined_status([], date(2026, 5, 8), in_dsm_group=False) == "-"


# ---------- 신청·결제 상태 라벨 ----------

def test_subscription_period_label_has_subs():
    from core.payment_matrix import subscription_period_label
    s = _sub("h", date(2026, 6, 1), date(2026, 12, 31))
    assert subscription_period_label([s], date(2026, 7, 1)) == "2026-06-01 ~ 2026-12-31"


def test_subscription_period_label_picks_latest():
    from core.payment_matrix import subscription_period_label
    s1 = _sub("h", date(2025, 1, 1), date(2025, 6, 30), sub_id=1)
    s2 = _sub("h", date(2026, 1, 1), date(2026, 6, 30), sub_id=2)
    assert subscription_period_label([s1, s2], date(2026, 3, 1)) == "2026-01-01 ~ 2026-06-30"


def test_subscription_period_label_no_subs():
    from core.payment_matrix import subscription_period_label
    assert subscription_period_label([], date(2026, 5, 1)) == "-"


def test_payment_state_label():
    from core.payment_matrix import payment_state_label
    assert payment_state_label(has_subscription=True, is_applicant=True) == "입금완료"
    assert payment_state_label(has_subscription=True, is_applicant=False) == "입금완료"
    assert payment_state_label(has_subscription=False, is_applicant=True) == "미입금"
    assert payment_state_label(has_subscription=False, is_applicant=False) == "-"


def test_short_subscription_status():
    from core.payment_matrix import short_subscription_status
    active = _sub("h", date(2026, 1, 1), date(2026, 12, 31))
    expired = _sub("h", date(2025, 1, 1), date(2025, 12, 31))
    today = date(2026, 5, 1)
    assert short_subscription_status([active], today, is_applicant=True) == "구독중"
    assert short_subscription_status([expired], today, is_applicant=True) == "만료"
    assert short_subscription_status([], today, is_applicant=True) == "결제 대기"
    assert short_subscription_status([], today, is_applicant=False) == "-"
