"""payment_store + 단가 → 기간 산정 단위 테스트."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from core.payment_store import (
    PaymentStore,
    compute_period,
    lookup_months,
)
from core.toss_xlsx import TossDeposit


# ---------- 단가표 ----------

def test_lookup_months_exact_match():
    assert lookup_months(3000) == 1
    assert lookup_months(9000) == 3
    assert lookup_months(12000) == 6
    assert lookup_months(24000) == 12


def test_lookup_months_no_match_returns_none():
    # 단가표에 없는 금액은 None — "기타 입금" 분류용.
    assert lookup_months(1) is None
    assert lookup_months(5000) is None
    assert lookup_months(100000) is None


# ---------- 기간 산정 ----------

def test_compute_period_one_month():
    # 5/15 + 1개월 → 6/14 (다음달 같은 일자 직전)
    pf, pt = compute_period(date(2026, 5, 15), 1)
    assert pf == date(2026, 5, 15)
    assert pt == date(2026, 6, 14)


def test_compute_period_six_months():
    pf, pt = compute_period(date(2026, 1, 1), 6)
    assert pf == date(2026, 1, 1)
    assert pt == date(2026, 6, 30)


def test_compute_period_twelve_months_year_rollover():
    pf, pt = compute_period(date(2026, 5, 15), 12)
    assert pf == date(2026, 5, 15)
    assert pt == date(2027, 5, 14)


def test_compute_period_end_of_month_clamp():
    # 1/31 + 1개월 → 2/28 (relativedelta 가 실재하는 날짜로 클램프) → -1일 = 2/27.
    # 회계상 다소 불리할 수 있으나 약관·관행 충돌 시 사용자 결정. 회귀 보호용으로만 고정.
    pf, pt = compute_period(date(2026, 1, 31), 1)
    assert pf == date(2026, 1, 31)
    assert pt == date(2026, 2, 27)


# ---------- PaymentStore ----------

@pytest.fixture
def store(tmp_path: Path) -> PaymentStore:
    return PaymentStore(tmp_path / "payments.db")


def _mk_deposit(
    *,
    when: datetime,
    payer: str = "홍길동",
    amount: int = 3000,
) -> TossDeposit:
    return TossDeposit(
        txn_at=when,
        payer_name=payer,
        amount=amount,
        bank="토스뱅크",
        counterparty_account="",
        memo="",
        source_file="test.xlsx",
    )


def test_import_deposits_idempotent(store: PaymentStore):
    d1 = _mk_deposit(when=datetime(2026, 5, 15, 10, 0, 0))
    added, skipped = store.import_deposits([d1])
    assert (added, skipped) == (1, 0)

    # 두 번째 import — 동일 거래는 dedup_hash 충돌 → skip.
    added, skipped = store.import_deposits([d1])
    assert (added, skipped) == (0, 1)
    assert len(store.all_transactions()) == 1


def test_import_deposits_distinguishes_same_payer_different_time(store: PaymentStore):
    # 같은 사람이 다른 시각에 같은 금액 입금하면 별개 거래.
    d1 = _mk_deposit(when=datetime(2026, 5, 15, 10, 0, 0))
    d2 = _mk_deposit(when=datetime(2026, 5, 15, 11, 0, 0))
    added, _ = store.import_deposits([d1, d2])
    assert added == 2


def test_alias_set_and_get(store: PaymentStore):
    assert store.get_alias("홍길동") is None
    store.set_alias("홍길동", "hongkd")
    assert store.get_alias("홍길동") == "hongkd"

    # 덮어쓰기 (회원 변경)
    store.set_alias("홍길동", "different_uid")
    assert store.get_alias("홍길동") == "different_uid"


def test_subscription_extension_chain(store: PaymentStore):
    # 첫 결제 5/15 + 1개월 → 5/15~6/14
    d1 = _mk_deposit(when=datetime(2026, 5, 15, 10, 0, 0))
    store.import_deposits([d1])
    txn = store.transactions_for_payer("홍길동")[0]
    pf, pt = compute_period(d1.txn_at.date(), 1)
    sub_id = store.add_subscription(
        member_user_id="hongkd",
        transaction_id=txn.id,
        months=1,
        period_from=pf,
        period_to=pt,
    )
    assert sub_id > 0
    assert store.latest_period_to("hongkd") == date(2026, 6, 14)

    # 활성 중에 추가 결제 — 만료 다음날(6/15)부터 연장 → 6/15~9/14 (3개월)
    d2 = _mk_deposit(when=datetime(2026, 5, 20, 10, 0, 0), amount=9000)
    store.import_deposits([d2])
    txn2 = [t for t in store.transactions_for_payer("홍길동") if t.amount == 9000][0]
    latest = store.latest_period_to("hongkd")
    new_start = latest + (date(2026, 6, 15) - date(2026, 6, 15)).__class__()  # noqa
    # 위 줄은 timedelta() = 0일. 실제 연장 로직은 matcher 에서 +1일을 처리.
    # 여기서는 store API 만 검증 — 연장 시작일 = 6/15 가 맞음을 외부에서 계산해 넣는 것.
    from datetime import timedelta
    new_start = latest + timedelta(days=1)
    pf2, pt2 = compute_period(new_start, 3)
    store.add_subscription(
        member_user_id="hongkd",
        transaction_id=txn2.id,
        months=3,
        period_from=pf2,
        period_to=pt2,
    )
    assert store.latest_period_to("hongkd") == date(2026, 9, 14)
    assert len(store.subscriptions_for_member("hongkd")) == 2


def test_subscription_unique_per_transaction(store: PaymentStore):
    """한 거래는 하나의 구독으로만 적용 — UNIQUE 제약 검증."""
    import sqlite3
    d = _mk_deposit(when=datetime(2026, 5, 15, 10, 0, 0))
    store.import_deposits([d])
    txn = store.transactions_for_payer("홍길동")[0]

    pf, pt = compute_period(d.txn_at.date(), 1)
    store.add_subscription(
        member_user_id="hongkd",
        transaction_id=txn.id,
        months=1,
        period_from=pf,
        period_to=pt,
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.add_subscription(
            member_user_id="hongkd",
            transaction_id=txn.id,
            months=1,
            period_from=pf,
            period_to=pt,
        )


# ---------- delete_aliases_for_user ----------

def test_delete_aliases_for_user_removes_all_payer_names_for_uid(tmp_path):
    db = tmp_path / "p.db"
    store = PaymentStore(db)
    store.set_alias("홍길동", "hongkd")
    store.set_alias("홍길동(외)", "hongkd")
    store.set_alias("김철수", "kim")
    n = store.delete_aliases_for_user("hongkd")
    assert n == 2
    assert store.get_alias("홍길동") is None
    assert store.get_alias("홍길동(외)") is None
    # 다른 사용자 alias 는 그대로
    assert store.get_alias("김철수") == "kim"


def test_delete_aliases_for_user_case_insensitive(tmp_path):
    db = tmp_path / "p.db"
    store = PaymentStore(db)
    store.set_alias("홍", "HongKD")
    n = store.delete_aliases_for_user("hongkd")
    assert n == 1


def test_delete_aliases_for_user_no_match(tmp_path):
    db = tmp_path / "p.db"
    store = PaymentStore(db)
    store.set_alias("김", "kim")
    n = store.delete_aliases_for_user("ghost")
    assert n == 0
    # 기존 alias 영향 없음
    assert store.get_alias("김") == "kim"


def test_delete_aliases_for_user_empty_uid_returns_zero(tmp_path):
    db = tmp_path / "p.db"
    store = PaymentStore(db)
    store.set_alias("김", "kim")
    assert store.delete_aliases_for_user("") == 0
    assert store.delete_aliases_for_user("   ") == 0


# ---------- form_applicants ----------

def test_upsert_and_all_form_applicants(tmp_path):
    from core.models import FormApplicant
    db = tmp_path / "p.db"
    store = PaymentStore(db)
    n = store.upsert_form_applicants([
        FormApplicant(member_user_id="hong", applied_at="2026-06-01 10:00:00",
                      name="홍길동", phone="010", email="h@a.com",
                      plan_raw="3000=1개월", plan_months=1, agreed=True),
        FormApplicant(member_user_id="kim", name="김철수", plan_raw="12000=6개월",
                      plan_months=6, agreed=False),
    ])
    assert n == 2
    rows = store.all_form_applicants()
    # name 순 정렬 — 김철수 < 홍길동
    assert [r.member_user_id for r in rows] == ["kim", "hong"]
    hong = next(r for r in rows if r.member_user_id == "hong")
    assert hong.plan_months == 1
    assert hong.agreed is True
    kim = next(r for r in rows if r.member_user_id == "kim")
    assert kim.agreed is False


def test_upsert_form_applicants_idempotent(tmp_path):
    from core.models import FormApplicant
    db = tmp_path / "p.db"
    store = PaymentStore(db)
    store.upsert_form_applicants([FormApplicant(member_user_id="hong", name="구버전")])
    store.upsert_form_applicants([FormApplicant(member_user_id="hong", name="신버전", plan_months=3)])
    rows = store.all_form_applicants()
    assert len(rows) == 1
    assert rows[0].name == "신버전"
    assert rows[0].plan_months == 3


def test_upsert_form_applicants_skips_empty_uid(tmp_path):
    from core.models import FormApplicant
    db = tmp_path / "p.db"
    store = PaymentStore(db)
    n = store.upsert_form_applicants([
        FormApplicant(member_user_id="", name="x"),
        FormApplicant(member_user_id="  ", name="y"),
        FormApplicant(member_user_id="ok", name="z"),
    ])
    assert n == 1
    assert [r.member_user_id for r in store.all_form_applicants()] == ["ok"]


def test_get_form_applicant_case_insensitive(tmp_path):
    from core.models import FormApplicant
    db = tmp_path / "p.db"
    store = PaymentStore(db)
    store.upsert_form_applicants([FormApplicant(member_user_id="Hong", name="홍길동")])
    assert store.get_form_applicant("hong").name == "홍길동"
    assert store.get_form_applicant("HONG").member_user_id == "Hong"
    assert store.get_form_applicant("ghost") is None
    assert store.get_form_applicant("") is None


# ---------- delete_subscriptions_for_user / delete_form_applicant ----------

def test_delete_subscriptions_for_user_keeps_transactions(tmp_path):
    db = tmp_path / "p.db"
    store = PaymentStore(db)
    d = TossDeposit(
        txn_at=datetime(2026, 5, 15, 10, 0), payer_name="홍길동", amount=3000,
        bank="토스뱅크", counterparty_account="", memo="", source_file="t.xlsx",
    )
    store.import_deposits([d])
    txn = store.all_transactions()[0]
    pf, pt = compute_period(txn.txn_at.date(), 1)
    store.add_subscription(member_user_id="hong", transaction_id=txn.id, months=1,
                           period_from=pf, period_to=pt)
    assert len(store.subscriptions_for_member("hong")) == 1
    n = store.delete_subscriptions_for_user("HONG")  # 대소문자 무시
    assert n == 1
    assert store.subscriptions_for_member("hong") == []
    # 거래는 그대로
    assert len(store.all_transactions()) == 1


def test_delete_subscriptions_for_user_no_match(tmp_path):
    store = PaymentStore(tmp_path / "p.db")
    assert store.delete_subscriptions_for_user("ghost") == 0
    assert store.delete_subscriptions_for_user("") == 0


def test_delete_form_applicant(tmp_path):
    from core.models import FormApplicant
    store = PaymentStore(tmp_path / "p.db")
    store.upsert_form_applicants([
        FormApplicant(member_user_id="hong", name="홍길동"),
        FormApplicant(member_user_id="kim", name="김철수"),
    ])
    n = store.delete_form_applicant("HONG")
    assert n == 1
    assert store.get_form_applicant("hong") is None
    assert store.get_form_applicant("kim") is not None
    assert store.delete_form_applicant("ghost") == 0
    assert store.delete_form_applicant("") == 0
