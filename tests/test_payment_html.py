"""payment_html — 자료실 구독 HTML 리포트 단위 테스트."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from core.models import Member
from core.payment_html import (
    default_html_path,
    render_payment_html,
    write_payment_html,
)
from core.payment_store import PaymentStore, compute_period
from core.toss_xlsx import TossDeposit


@pytest.fixture
def store(tmp_path: Path) -> PaymentStore:
    return PaymentStore(tmp_path / "payments.db")


def _seed_two_subs(store: PaymentStore) -> None:
    """홍길동 활성 구독(5/8~6/7) + 박영희 만료 구독(2/1~3/2)."""
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
    p = default_html_path(today=date(2026, 5, 9))
    assert p.name == "payments_2026-05-09.html"


def test_render_starts_with_doctype_and_includes_kpi(store):
    html = render_payment_html(store, members=[], today=date(2026, 5, 9))
    assert html.startswith("<!doctype html>")
    assert "활성 구독자" in html
    assert "이번 달 만료" in html
    assert "다음 달 만료" in html


def test_render_kpi_counts_match_seed(store):
    _seed_two_subs(store)
    members = [
        Member(user_id="hong", name="홍길동", level=5),
        Member(user_id="park", name="박영희", level=5),
    ]
    today = date(2026, 5, 9)
    html = render_payment_html(store, members=members, today=today)
    # 홍길동 = 활성 (만료 6/7), 박영희 = 만료된 상태 (3/2 만료, 5/9 기준 만료됨)
    # 활성 구독자 1명 / 이번 달 만료 0명 (홍은 6월에 만료) / 다음 달 만료 1명 (홍 6/7)
    assert "<b>1</b>명 활성 구독자" in html
    assert "<b>0</b>명 이번 달 만료" in html
    assert "<b>1</b>명 다음 달 만료" in html


def test_render_includes_member_in_matrix(store):
    _seed_two_subs(store)
    members = [
        Member(user_id="hong", name="홍길동", nickname="홍이", level=5),
        Member(user_id="park", name="박영희", level=5),
    ]
    html = render_payment_html(store, members=members, today=date(2026, 5, 9))
    assert "hong" in html
    assert "홍길동" in html
    assert "park" in html


def test_render_escapes_member_name_with_html_chars(store):
    """회원 이름에 HTML 특수문자가 들어와도 raw 출력되지 않아야."""
    deposit = TossDeposit(
        txn_at=datetime(2026, 5, 8, 10, 0, 0),
        payer_name="홍<x>", amount=3000,
        bank="토스뱅크", counterparty_account="",
        memo="", source_file="t.xlsx",
    )
    store.import_deposits([deposit])
    txn = store.all_transactions()[0]
    pf, pt = compute_period(date(2026, 5, 8), 1)
    store.add_subscription(
        member_user_id="hxss",
        transaction_id=txn.id, months=1,
        period_from=pf, period_to=pt,
    )
    members = [Member(user_id="hxss", name="<script>alert(1)</script>", level=5)]
    html = render_payment_html(store, members=members, today=date(2026, 5, 9))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_pricing_table_present(store):
    html = render_payment_html(store, members=[], today=date(2026, 5, 9))
    # 단가표 섹션 — 사용자 단가표(3000=1, 9000=3, 12000=6, 24000=24)
    assert "3,000원" in html
    assert "1개월" in html
    assert "12,000원" in html
    assert "6개월" in html


def test_render_other_deposits_section_hides_individual_rows(store):
    """단가표 외 입금은 카운트·합계만, 개별 행은 노출 안 함."""
    deposit = TossDeposit(
        txn_at=datetime(2026, 5, 8, 10, 0, 0),
        payer_name="익명기부자", amount=50000,
        bank="토스뱅크", counterparty_account="",
        memo="찬조금", source_file="t.xlsx",
    )
    store.import_deposits([deposit])
    html = render_payment_html(store, members=[], today=date(2026, 5, 9))
    assert "<b>1</b>건" in html
    assert "<b>50,000원</b>" in html
    # 개별 거래의 입금자명·메모는 노출 안 됨
    assert "익명기부자" not in html
    assert "찬조금" not in html


def test_write_creates_file(store, tmp_path):
    out = write_payment_html(
        tmp_path / "report.html", store, members=[], today=date(2026, 5, 9)
    )
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert text.startswith("<!doctype html>")
