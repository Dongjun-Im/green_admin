"""토스 입금 거래 → 소리샘 회원 매칭 + 구독 기간 산정.

매칭 우선순위:
    1. aliases 테이블에 이미 확정된 매핑 → 그대로 사용
    2. Member.name 정확히 일치하는 회원이 1명 → 자동 매칭 (alias 저장 안 함)
    3. 0명 또는 2명 이상 (없음/동명이인) → 수동 매칭 콜백 호출

수동 매칭은 한 import 작업에서 같은 입금자명에 대해 1회만 묻고,
운영자가 회원을 고르면 그 매핑을 aliases 에 저장 → 다음 import 부터 자동.
사용자가 건너뛰면 그 회원의 거래는 이번엔 무시되며, 같은 입금자명이 다시
나오면 다시 묻는다.

기간 산정:
    · 거래는 시간순으로 처리.
    · 직전 구독 만료일이 미래면(=구독 활성 중) 만료 다음날부터 연장.
    · 없거나 과거면 결제일부터 시작.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Callable, Iterable

from core.models import Member
from core.payment_store import (
    PaymentStore,
    Transaction,
    compute_period,
    lookup_months,
)


class MatchOutcome(Enum):
    AUTO_FROM_ALIAS = "alias"      # aliases 테이블 hit
    AUTO_FROM_NAME = "name"        # Member.name 단일 일치
    NEED_MANUAL = "need_manual"    # 0명 또는 2명 이상


@dataclass(frozen=True)
class MatchResult:
    outcome: MatchOutcome
    member: Member | None          # NEED_MANUAL 일 때 None
    candidates: list[Member]       # 동명이인일 때 후보 리스트 (없을 수도 있음)


@dataclass
class ProcessSummary:
    auto_matched_payers: int = 0           # 자동 매칭된 입금자명 수
    manual_matched_payers: int = 0          # 수동 매칭된 입금자명 수
    skipped_unmatched_payers: int = 0       # 운영자가 건너뛴 입금자명 수
    new_subscriptions: int = 0              # 새로 추가된 구독 row
    already_subscribed_txns: int = 0        # 이미 구독으로 적용된 거래
    other_amount_txns: int = 0              # 단가표에 없는 입금 (기타로 분류)
    unmatched_payer_names: list[str] = field(default_factory=list)


# 콜백: (입금자명, 후보 회원 리스트, 그 입금자의 미처리 거래 리스트) → 선택된 Member 또는 None
ManualMatchCallback = Callable[
    [str, list[Member], list[Transaction]], Member | None
]


class PaymentMatcher:
    def __init__(
        self,
        store: PaymentStore,
        members: Iterable[Member],
        *,
        other_amount_months: int = 0,
    ) -> None:
        """other_amount_months > 0 이면 단가표에 없는 금액 입금도 그 개월수만큼의
        구독으로 산정한다 (0 = 기존 동작: '기타'로 분류하고 구독 안 만듦)."""
        self.store = store
        self.other_amount_months = max(0, int(other_amount_months or 0))
        members_list = list(members)
        self._by_uid: dict[str, Member] = {m.user_id: m for m in members_list}
        self._by_name: dict[str, list[Member]] = defaultdict(list)
        for m in members_list:
            if m.name:
                self._by_name[m.name.strip()].append(m)

    def _months_for(self, amount: int) -> int | None:
        """입금 금액 → 구독 개월수. 단가표에 없으면 other_amount_months(>0 일 때)."""
        m = lookup_months(amount)
        if m is not None:
            return m
        return self.other_amount_months or None

    def auto_match(self, payer_name: str) -> MatchResult:
        """입금자명 한 건의 자동 매칭 시도."""
        # 1) alias 우선
        alias_uid = self.store.get_alias(payer_name)
        if alias_uid:
            m = self._by_uid.get(alias_uid)
            if m is not None:
                return MatchResult(MatchOutcome.AUTO_FROM_ALIAS, m, [m])
            # alias 가 가리키는 회원이 회원목록에서 빠짐(예: 탈퇴) → 수동 재확인.

        # 2) 실명 단일 일치
        cands = self._by_name.get(payer_name.strip(), [])
        if len(cands) == 1:
            return MatchResult(MatchOutcome.AUTO_FROM_NAME, cands[0], cands)

        # 3) 0명 또는 2명 이상 → 수동
        return MatchResult(MatchOutcome.NEED_MANUAL, None, cands)

    def process(
        self,
        manual_callback: ManualMatchCallback | None = None,
    ) -> ProcessSummary:
        """미처리 거래 전체를 매칭·기간 산정·저장한다.

        Args:
            manual_callback: 자동 매칭 실패 시 운영자에게 묻는 콜백. None 이면
                수동 매칭이 필요한 거래는 모두 건너뛴다 (배치/CLI 용).

        Returns:
            처리 결과 요약.
        """
        summary = ProcessSummary()

        # 미처리 거래만 입금자별로 묶기 (한 입금자에 대해 수동 매칭 1회만 묻기 위해)
        by_payer: dict[str, list[Transaction]] = defaultdict(list)
        for t in self.store.all_transactions():
            if self.store.subscription_exists_for_txn(t.id):
                summary.already_subscribed_txns += 1
                continue
            if self._months_for(t.amount) is None:
                summary.other_amount_txns += 1
                continue
            by_payer[t.payer_name].append(t)

        for payer_name, txns in by_payer.items():
            result = self.auto_match(payer_name)
            chosen: Member | None = None

            if result.outcome in (MatchOutcome.AUTO_FROM_ALIAS, MatchOutcome.AUTO_FROM_NAME):
                chosen = result.member
                summary.auto_matched_payers += 1
            elif manual_callback is not None:
                chosen = manual_callback(payer_name, result.candidates, txns)
                if chosen is not None:
                    # 운영자가 확정한 매핑은 영구 저장 → 다음부터 자동.
                    self.store.set_alias(payer_name, chosen.user_id)
                    summary.manual_matched_payers += 1
                else:
                    summary.skipped_unmatched_payers += 1
                    summary.unmatched_payer_names.append(payer_name)
            else:
                summary.skipped_unmatched_payers += 1
                summary.unmatched_payer_names.append(payer_name)

            if chosen is None:
                continue

            # 거래를 시간순으로 처리해야 연장 체인이 정확.
            for t in sorted(txns, key=lambda x: x.txn_at):
                months = self._months_for(t.amount)
                if months is None:  # 방어적 — by_payer 에 들어올 때 이미 거른 건임.
                    continue
                start = self._next_period_start(chosen.user_id, t)
                period_from, period_to = compute_period(start, months)
                self.store.add_subscription(
                    member_user_id=chosen.user_id,
                    transaction_id=t.id,
                    months=months,
                    period_from=period_from,
                    period_to=period_to,
                )
                summary.new_subscriptions += 1

        return summary

    def _next_period_start(self, member_user_id: str, txn: Transaction):
        """체이닝 규칙: 만료가 미래면 만료 다음날, 아니면 결제일."""
        latest = self.store.latest_period_to(member_user_id)
        txn_date = txn.txn_at.date()
        if latest is None or latest < txn_date:
            return txn_date
        return latest + timedelta(days=1)
