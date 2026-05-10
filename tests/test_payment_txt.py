"""payment_txt — 자료실 구독 TXT 리포트 단위 테스트."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from core.models import Member
from core.payment_store import PaymentStore, compute_period
from core.payment_txt import (
    default_txt_path,
    render_payment_txt,
    write_payment_txt,
)
from core.toss_xlsx import TossDeposit


@pytest.fixture
def store(tmp_path: Path) -> PaymentStore:
    return PaymentStore(tmp_path / "payments.db")


def _seed(store: PaymentStore) -> None:
    """홍길동 활성 구독(5/8~6/7) + 박영희 만료 구독(2/1~3/2) + 기타입금 1건."""
    deposits = [
        TossDeposit(
            txn_at=datetime(2026, 5, 8, 10, 0, 0),
            payer_name="홍길동", amount=3000,
            bank="토스뱅크", counterparty_account="",
            memo="", source_file="t.xlsx",
        ),
        TossDeposit(
            txn_at=datetime(2026, 2, 1, 10, 0, 0),
            payer_name="박영희", amount=3000,
            bank="토스뱅크", counterparty_account="",
            memo="", source_file="t.xlsx",
        ),
        TossDeposit(
            txn_at=datetime(2026, 5, 8, 11, 0, 0),
            payer_name="익명", amount=50000,
            bank="토스뱅크", counterparty_account="",
            memo="찬조", source_file="t.xlsx",
        ),
    ]
    store.import_deposits(deposits)
    for payer, uid in (("홍길동", "hong"), ("박영희", "park")):
        txn = store.transactions_for_payer(payer)[0]
        pf, pt = compute_period(txn.txn_at.date(), 1)
        store.add_subscription(
            member_user_id=uid, transaction_id=txn.id,
            months=1, period_from=pf, period_to=pt,
        )


def test_default_path_uses_backups_dir():
    p = default_txt_path(today=date(2026, 5, 9))
    assert p.name == "payments_2026-05-09.txt"


def test_render_includes_all_sections(store):
    out = render_payment_txt(store, members=[], today=date(2026, 5, 9))
    assert "[요약]" in out
    assert "[구독 매트릭스" in out
    assert "[이번 달 / 다음 달 만료 회원]" in out
    assert "[활성 구독 상세 — 만료 임박순]" in out
    assert "[참고 — 단가표]" in out
    assert "[기타 입금 요약]" in out


def test_render_summary_counts_match_seed(store):
    _seed(store)
    members = [
        Member(user_id="hong", name="홍길동", level=5),
        Member(user_id="park", name="박영희", level=5),
    ]
    out = render_payment_txt(store, members=members, today=date(2026, 5, 9))
    # 홍길동 활성 (만료 6/7), 박영희 만료
    assert "활성 구독자  : 1명" in out
    assert "이번 달 만료 : 0명" in out  # 6월 만료 → 다음 달
    assert "다음 달 만료 : 1명" in out


def test_render_includes_member_in_matrix_and_active(store):
    _seed(store)
    members = [
        Member(user_id="hong", name="홍길동", nickname="홍이", level=5),
        Member(user_id="park", name="박영희", level=5),
    ]
    out = render_payment_txt(store, members=members, today=date(2026, 5, 9))
    assert "hong" in out
    assert "홍길동" in out
    # 활성 구독 상세 섹션에 활성 회원 노출
    assert "남은 29일" in out  # 5/9 → 6/7 = 29일


def test_render_pricing_table_present(store):
    out = render_payment_txt(store, members=[], today=date(2026, 5, 9))
    assert "3,000원" in out
    assert "1개월" in out
    assert "12,000원" in out
    assert "6개월" in out
    assert "24,000원" in out
    assert "12개월" in out


def test_render_other_deposits_aggregated(store):
    _seed(store)
    out = render_payment_txt(store, members=[], today=date(2026, 5, 9))
    assert "1건 / 합계 50,000원" in out
    # 개별 거래(입금자명·메모) 노출 안 됨
    assert "익명" not in out
    assert "찬조" not in out


def test_render_empty_store_safe(store):
    out = render_payment_txt(store, members=[], today=date(2026, 5, 9))
    assert "구독 이력이 없습니다" in out
    assert "현재 활성 구독자 없음" in out
    assert "단가표 외 입금 없음" in out


def test_write_creates_file(store, tmp_path):
    out = write_payment_txt(
        tmp_path / "report.txt", store, members=[], today=date(2026, 5, 9)
    )
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "초록등대 자료실 구독 리포트" in text
    assert text.endswith("\n")
