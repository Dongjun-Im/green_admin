"""payment_matcher: 자동/수동 매칭 + 구독 체이닝 단위 테스트."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from core.models import Member
from core.payment_matcher import (
    MatchOutcome,
    PaymentMatcher,
)
from core.payment_store import PaymentStore
from core.toss_xlsx import TossDeposit


@pytest.fixture
def members() -> list[Member]:
    return [
        Member(user_id="hong", name="홍길동", level=5),
        Member(user_id="kim1", name="김철수", level=5),
        Member(user_id="kim2", name="김철수", level=5),  # 동명이인
        Member(user_id="park", name="박영희", level=6),
    ]


@pytest.fixture
def store(tmp_path: Path) -> PaymentStore:
    return PaymentStore(tmp_path / "payments.db")


# ---------- auto_match ----------

def test_auto_match_unique_name(store, members):
    matcher = PaymentMatcher(store, members)
    r = matcher.auto_match("홍길동")
    assert r.outcome == MatchOutcome.AUTO_FROM_NAME
    assert r.member.user_id == "hong"


def test_auto_match_no_member(store, members):
    matcher = PaymentMatcher(store, members)
    r = matcher.auto_match("이지은")
    assert r.outcome == MatchOutcome.NEED_MANUAL
    assert r.member is None
    assert r.candidates == []


def test_auto_match_homonym(store, members):
    matcher = PaymentMatcher(store, members)
    r = matcher.auto_match("김철수")
    assert r.outcome == MatchOutcome.NEED_MANUAL
    assert r.member is None
    assert {m.user_id for m in r.candidates} == {"kim1", "kim2"}


def test_auto_match_alias_wins_over_homonym(store, members):
    """동명이인이라도 alias 가 있으면 자동 매칭."""
    store.set_alias("김철수", "kim2")
    matcher = PaymentMatcher(store, members)
    r = matcher.auto_match("김철수")
    assert r.outcome == MatchOutcome.AUTO_FROM_ALIAS
    assert r.member.user_id == "kim2"


def test_auto_match_dangling_alias_falls_back_to_manual(store, members):
    """alias 가 가리키는 user_id 가 회원 목록에 없으면 수동으로."""
    store.set_alias("홍길동", "deleted_user")
    matcher = PaymentMatcher(store, members)
    r = matcher.auto_match("홍길동")
    assert r.outcome == MatchOutcome.AUTO_FROM_NAME  # 실명 단일 일치 fallback
    assert r.member.user_id == "hong"


# ---------- process ----------

def _imp(store: PaymentStore, *deposits: TossDeposit) -> None:
    store.import_deposits(deposits)


def _dep(when: datetime, payer: str, amount: int) -> TossDeposit:
    return TossDeposit(
        txn_at=when,
        payer_name=payer,
        amount=amount,
        bank="토스뱅크",
        counterparty_account="",
        memo="",
        source_file="t.xlsx",
    )


def test_process_auto_match_creates_subscription(store, members):
    _imp(store, _dep(datetime(2026, 5, 15, 10, 0), "홍길동", 3000))
    matcher = PaymentMatcher(store, members)
    summary = matcher.process()
    assert summary.auto_matched_payers == 1
    assert summary.new_subscriptions == 1
    subs = store.subscriptions_for_member("hong")
    assert len(subs) == 1
    assert subs[0].period_from == date(2026, 5, 15)
    assert subs[0].period_to == date(2026, 6, 14)


def test_process_skips_amount_not_in_pricing(store, members):
    """단가표에 없는 금액(1원, 5만원)은 기본적으로 구독 생성 안 됨."""
    _imp(store,
         _dep(datetime(2026, 5, 15, 10, 0), "홍길동", 1),
         _dep(datetime(2026, 5, 15, 11, 0), "홍길동", 50000))
    summary = PaymentMatcher(store, members).process()
    assert summary.other_amount_txns == 2
    assert summary.new_subscriptions == 0
    assert store.subscriptions_for_member("hong") == []


def test_process_other_amount_months_creates_subscription(store, members):
    """other_amount_months>0 이면 단가표 외 금액 입금도 그 개월수 구독으로 산정."""
    _imp(store, _dep(datetime(2026, 5, 15, 10, 0), "홍길동", 50000))
    summary = PaymentMatcher(store, members, other_amount_months=2).process()
    assert summary.new_subscriptions == 1
    assert summary.other_amount_txns == 0  # 더 이상 '기타' 아님
    subs = store.subscriptions_for_member("hong")
    assert len(subs) == 1
    assert subs[0].months == 2
    assert subs[0].period_from == date(2026, 5, 15)
    assert subs[0].period_to == date(2026, 7, 14)  # 5/15 + 2개월


def test_process_other_amount_months_zero_keeps_old_behavior(store, members):
    """other_amount_months=0(기본) 이면 기존대로 '기타'로 분류, 구독 안 만듦."""
    _imp(store, _dep(datetime(2026, 5, 15, 10, 0), "홍길동", 50000))
    summary = PaymentMatcher(store, members, other_amount_months=0).process()
    assert summary.other_amount_txns == 1
    assert summary.new_subscriptions == 0
    assert store.subscriptions_for_member("hong") == []


def test_process_other_amount_months_still_uses_pricing_table(store, members):
    """단가표에 있는 금액은 other_amount_months 와 무관하게 단가표 개월수 사용."""
    _imp(store, _dep(datetime(2026, 5, 15, 10, 0), "홍길동", 9000))  # 9000 = 3개월
    PaymentMatcher(store, members, other_amount_months=99).process()
    subs = store.subscriptions_for_member("hong")
    assert len(subs) == 1
    assert subs[0].months == 3  # 단가표 우선


def test_process_chains_extension(store, members):
    """동일 회원이 활성 중에 추가 입금 → 만료 다음날부터 연장."""
    _imp(store,
         _dep(datetime(2026, 5, 15, 10, 0), "홍길동", 3000),    # 5/15~6/14 (1개월)
         _dep(datetime(2026, 5, 20, 10, 0), "홍길동", 9000))    # 6/15~9/14 (3개월, 연장)
    summary = PaymentMatcher(store, members).process()
    assert summary.new_subscriptions == 2
    subs = store.subscriptions_for_member("hong")
    assert subs[0].period_from == date(2026, 5, 15)
    assert subs[0].period_to == date(2026, 6, 14)
    assert subs[1].period_from == date(2026, 6, 15)
    assert subs[1].period_to == date(2026, 9, 14)
    assert store.latest_period_to("hong") == date(2026, 9, 14)


def test_process_starts_fresh_when_lapsed(store, members):
    """만료 후 한참 뒤 입금 → 결제일부터 새로 시작 (연장 아님)."""
    _imp(store,
         _dep(datetime(2026, 1, 15, 10, 0), "홍길동", 3000),    # 1/15~2/14
         _dep(datetime(2026, 5, 20, 10, 0), "홍길동", 3000))    # 5/20~6/19 (lapsed, restart)
    summary = PaymentMatcher(store, members).process()
    subs = store.subscriptions_for_member("hong")
    assert subs[1].period_from == date(2026, 5, 20)
    assert subs[1].period_to == date(2026, 6, 19)


def test_process_idempotent(store, members):
    """같은 import 를 두 번 돌려도 구독은 한 번만 생성."""
    _imp(store, _dep(datetime(2026, 5, 15, 10, 0), "홍길동", 3000))
    matcher = PaymentMatcher(store, members)
    s1 = matcher.process()
    s2 = matcher.process()
    assert s1.new_subscriptions == 1
    assert s2.new_subscriptions == 0
    assert s2.already_subscribed_txns == 1
    assert len(store.subscriptions_for_member("hong")) == 1


def test_process_manual_callback_for_homonym(store, members):
    """동명이인: 콜백이 호출되고, 운영자가 고른 회원에 구독이 적용 + alias 저장."""
    _imp(store, _dep(datetime(2026, 5, 15, 10, 0), "김철수", 3000))

    calls: list[tuple] = []

    def cb(payer, candidates, txns):
        calls.append((payer, [m.user_id for m in candidates], len(txns)))
        # kim2 선택
        return next(m for m in candidates if m.user_id == "kim2")

    summary = PaymentMatcher(store, members).process(manual_callback=cb)
    assert calls == [("김철수", ["kim1", "kim2"], 1)]
    assert summary.manual_matched_payers == 1
    assert summary.new_subscriptions == 1
    assert store.get_alias("김철수") == "kim2"

    # 다시 import 한 새 거래 → alias hit 으로 자동 매칭, 콜백 호출 안 됨
    _imp(store, _dep(datetime(2026, 6, 16, 10, 0), "김철수", 3000))
    calls.clear()
    summary2 = PaymentMatcher(store, members).process(manual_callback=cb)
    assert calls == []
    assert summary2.auto_matched_payers == 1
    assert summary2.new_subscriptions == 1


def test_process_manual_skip_records_unmatched(store, members):
    _imp(store, _dep(datetime(2026, 5, 15, 10, 0), "이지은", 3000))

    def cb(payer, candidates, txns):
        return None  # 운영자가 건너뜀

    summary = PaymentMatcher(store, members).process(manual_callback=cb)
    assert summary.skipped_unmatched_payers == 1
    assert "이지은" in summary.unmatched_payer_names
    assert summary.new_subscriptions == 0
    assert store.get_alias("이지은") is None  # 건너뛰면 alias 저장 안 함
