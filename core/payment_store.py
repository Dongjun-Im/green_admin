"""자료실 구독비 결제 SQLite 저장소.

테이블 3종:
    transactions   토스 거래내역 1행 = 1 row. dedup_hash UNIQUE 로 재import 멱등.
    aliases        입금자명 → 회원 user_id 영구 매핑. 한 번 확정되면 다음부터 자동.
    subscriptions  구독 적용된 거래만 산정된 기간(period_from~period_to) 보관.

기간 계산 규칙 (config.SUBSCRIPTION_PRICING + 사용자 결정):
    · 단가표에 정확히 일치하는 입금만 구독 적용 (그 외는 "기타").
    · 동일 회원이 구독 활성 중에 또 결제하면 직전 구독 만료 다음날부터 연장.
    · 기간 = 결제일(또는 직전 만료 다음날) 부터 N개월 → 다음달 같은 일자 직전.
        예: 5/15 + 1개월 → 6/14
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator

from dateutil.relativedelta import relativedelta

from config import DATA_DIR, SUBSCRIPTION_PRICING
from core.models import FormApplicant
from core.toss_xlsx import TossDeposit


PAYMENTS_DB = Path(DATA_DIR) / "payments.db"


@dataclass(frozen=True)
class Transaction:
    id: int
    txn_at: datetime
    payer_name: str
    amount: int
    bank: str
    counterparty_account: str
    memo: str
    source_file: str
    imported_at: datetime


@dataclass(frozen=True)
class Subscription:
    id: int
    member_user_id: str
    transaction_id: int
    months: int
    period_from: date
    period_to: date


# ---------- 단가 → 개월 ----------

def lookup_months(amount: int, pricing: dict[int, int] | None = None) -> int | None:
    """단가표 정확히 일치 시 개월 수, 아니면 None."""
    table = pricing if pricing is not None else SUBSCRIPTION_PRICING
    return table.get(amount)


def compute_period(
    start: date, months: int
) -> tuple[date, date]:
    """start 부터 months 개월 구독 → (period_from, period_to).

    period_to 는 마지막 활성일 (포함). 즉, 5/15 + 1개월 → 6/14.
    """
    period_to = start + relativedelta(months=+months) - timedelta(days=1)
    return start, period_to


# ---------- 저장소 ----------

class PaymentStore:
    def __init__(self, path: Path | str = PAYMENTS_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                txn_at TEXT NOT NULL,
                payer_name TEXT NOT NULL,
                amount INTEGER NOT NULL,
                bank TEXT NOT NULL DEFAULT '',
                counterparty_account TEXT NOT NULL DEFAULT '',
                memo TEXT NOT NULL DEFAULT '',
                source_file TEXT NOT NULL DEFAULT '',
                imported_at TEXT NOT NULL,
                dedup_hash TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS idx_txn_payer ON transactions(payer_name);
            CREATE INDEX IF NOT EXISTS idx_txn_date  ON transactions(txn_at);

            CREATE TABLE IF NOT EXISTS aliases (
                payer_name      TEXT PRIMARY KEY,
                member_user_id  TEXT NOT NULL,
                confirmed_at    TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_alias_uid ON aliases(member_user_id);

            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                member_user_id  TEXT NOT NULL,
                transaction_id  INTEGER NOT NULL UNIQUE,
                months          INTEGER NOT NULL,
                period_from     TEXT NOT NULL,
                period_to       TEXT NOT NULL,
                computed_at     TEXT NOT NULL,
                FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_sub_member ON subscriptions(member_user_id);
            CREATE INDEX IF NOT EXISTS idx_sub_period ON subscriptions(period_from, period_to);

            -- 자료실 신청 구글 폼('설문지 응답 시트1') 행 캐시. member_user_id = 희망아이디.
            CREATE TABLE IF NOT EXISTS form_applicants (
                member_user_id TEXT PRIMARY KEY,
                applied_at  TEXT NOT NULL DEFAULT '',
                name        TEXT NOT NULL DEFAULT '',
                phone       TEXT NOT NULL DEFAULT '',
                email       TEXT NOT NULL DEFAULT '',
                plan_raw    TEXT NOT NULL DEFAULT '',
                plan_months INTEGER NOT NULL DEFAULT 0,
                agreed      TEXT NOT NULL DEFAULT '',
                synced_at   TEXT NOT NULL
            );
            """)
            # 마이그레이션: aliases 에 modified_at 추가 (구글시트 양방향 충돌 해결용).
            # 기존 행은 confirmed_at 을 그대로 modified_at 시드로 사용.
            try:
                c.execute("ALTER TABLE aliases ADD COLUMN modified_at TEXT NOT NULL DEFAULT ''")
                c.execute("UPDATE aliases SET modified_at = confirmed_at WHERE modified_at = ''")
            except sqlite3.OperationalError:
                pass  # 이미 컬럼 존재

    # ---------- transactions ----------

    def import_deposits(self, deposits: Iterable[TossDeposit]) -> tuple[int, int]:
        """거래내역 일괄 import. dedup_hash 충돌은 조용히 무시.

        Returns:
            (새로 추가된 행 수, 중복 무시된 행 수)
        """
        added = 0
        skipped = 0
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            for d in deposits:
                try:
                    c.execute(
                        """INSERT INTO transactions
                           (txn_at, payer_name, amount, bank, counterparty_account,
                            memo, source_file, imported_at, dedup_hash)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            d.txn_at.isoformat(timespec="seconds"),
                            d.payer_name,
                            d.amount,
                            d.bank,
                            d.counterparty_account,
                            d.memo,
                            d.source_file,
                            now,
                            d.dedup_hash,
                        ),
                    )
                    added += 1
                except sqlite3.IntegrityError:
                    skipped += 1
        return added, skipped

    def all_transactions(self) -> list[Transaction]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM transactions ORDER BY txn_at"
            ).fetchall()
        return [_row_to_txn(r) for r in rows]

    def transactions_for_payer(self, payer_name: str) -> list[Transaction]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM transactions WHERE payer_name = ? ORDER BY txn_at",
                (payer_name,),
            ).fetchall()
        return [_row_to_txn(r) for r in rows]

    # ---------- aliases ----------

    def get_alias(self, payer_name: str) -> str | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT member_user_id FROM aliases WHERE payer_name = ?",
                (payer_name,),
            ).fetchone()
        return row["member_user_id"] if row else None

    def set_alias(
        self,
        payer_name: str,
        member_user_id: str,
        modified_at: datetime | None = None,
    ) -> None:
        """매핑 저장 — modified_at 기본값은 현재 시각.

        구글시트에서 읽어온 매핑을 SQLite 에 반영할 때는 시트의 modified_at 을
        그대로 보존해야 충돌 해결 룰(가장 최근 수정이 이김)이 정확해진다.
        """
        ts = (modified_at or datetime.now()).isoformat(timespec="seconds")
        with self._conn() as c:
            c.execute(
                """INSERT INTO aliases (payer_name, member_user_id, confirmed_at, modified_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(payer_name) DO UPDATE SET
                     member_user_id = excluded.member_user_id,
                     confirmed_at   = excluded.confirmed_at,
                     modified_at    = excluded.modified_at""",
                (payer_name, member_user_id, ts, ts),
            )

    def all_aliases(self) -> dict[str, str]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT payer_name, member_user_id FROM aliases"
            ).fetchall()
        return {r["payer_name"]: r["member_user_id"] for r in rows}

    def delete_aliases_for_user(self, member_user_id: str) -> int:
        """user_id 로 매핑된 모든 입금자명 alias 삭제 — 구독/거래는 그대로.

        DSM 사용자 삭제 시 함께 정리해 새 사용자가 같은 입금자명으로 결제했을
        때 옛 user_id 로 잘못 매칭되는 사고를 막는다. 구독·거래 이력은 회계
        용도로 보존.

        Returns: 삭제된 alias 행 수.
        """
        target = (member_user_id or "").strip().lower()
        if not target:
            return 0
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM aliases WHERE LOWER(member_user_id) = ?",
                (target,),
            )
            return cur.rowcount or 0

    def all_aliases_detailed(self) -> list[tuple[str, str, datetime]]:
        """(payer_name, member_user_id, modified_at) — 구글시트 양방향 동기화용."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT payer_name, member_user_id, modified_at FROM aliases"
            ).fetchall()
        out: list[tuple[str, str, datetime]] = []
        for r in rows:
            ts_str = r["modified_at"] or r["confirmed_at"] if "confirmed_at" in r.keys() else r["modified_at"]
            try:
                ts = datetime.fromisoformat(ts_str)
            except (TypeError, ValueError):
                ts = datetime.now()
            out.append((r["payer_name"], r["member_user_id"], ts))
        return out

    # ---------- subscriptions ----------

    def latest_period_to(self, member_user_id: str) -> date | None:
        """그 회원의 가장 늦은 구독 만료일. 없으면 None."""
        with self._conn() as c:
            row = c.execute(
                "SELECT MAX(period_to) AS pt FROM subscriptions WHERE member_user_id = ?",
                (member_user_id,),
            ).fetchone()
        if not row or not row["pt"]:
            return None
        return date.fromisoformat(row["pt"])

    def add_subscription(
        self,
        *,
        member_user_id: str,
        transaction_id: int,
        months: int,
        period_from: date,
        period_to: date,
    ) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO subscriptions
                   (member_user_id, transaction_id, months,
                    period_from, period_to, computed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    member_user_id,
                    transaction_id,
                    months,
                    period_from.isoformat(),
                    period_to.isoformat(),
                    now,
                ),
            )
            return cur.lastrowid

    def subscriptions_for_member(self, member_user_id: str) -> list[Subscription]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM subscriptions WHERE member_user_id = ? "
                "ORDER BY period_from",
                (member_user_id,),
            ).fetchall()
        return [_row_to_sub(r) for r in rows]

    def all_subscriptions(self) -> list[Subscription]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM subscriptions ORDER BY member_user_id, period_from"
            ).fetchall()
        return [_row_to_sub(r) for r in rows]

    def subscription_exists_for_txn(self, transaction_id: int) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM subscriptions WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
        return row is not None

    def delete_subscriptions_for_user(self, member_user_id: str) -> int:
        """그 회원의 모든 구독 row 삭제 (거래 transactions 는 그대로 보존).

        보통은 보존이 원칙이지만, 운영자가 'DSM 계정 삭제 + 매트릭스에서 완전
        제거' 를 선택했을 때 호출 — 매트릭스에서 그 회원 행을 사라지게 한다.
        대소문자 무시. Returns: 삭제된 row 수.
        """
        target = (member_user_id or "").strip().lower()
        if not target:
            return 0
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM subscriptions WHERE LOWER(member_user_id) = ?",
                (target,),
            )
            return cur.rowcount or 0

    # ---------- 자료실 신청 폼 응답 캐시 ----------

    def upsert_form_applicants(self, applicants: Iterable[FormApplicant]) -> int:
        """폼 신청자 행을 member_user_id 기준 upsert. 반환=처리한 행 수.

        agreed(bool) 은 "동의함"/"" 문자열로 저장 — 빈 문자열이 아니면 동의로 본다.
        """
        now = datetime.now().isoformat(timespec="seconds")
        n = 0
        with self._conn() as c:
            for a in applicants:
                uid = (a.member_user_id or "").strip()
                if not uid:
                    continue
                c.execute(
                    """INSERT INTO form_applicants
                       (member_user_id, applied_at, name, phone, email,
                        plan_raw, plan_months, agreed, synced_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(member_user_id) DO UPDATE SET
                         applied_at  = excluded.applied_at,
                         name        = excluded.name,
                         phone       = excluded.phone,
                         email       = excluded.email,
                         plan_raw    = excluded.plan_raw,
                         plan_months = excluded.plan_months,
                         agreed      = excluded.agreed,
                         synced_at   = excluded.synced_at""",
                    (
                        uid,
                        a.applied_at or "",
                        a.name or "",
                        a.phone or "",
                        a.email or "",
                        a.plan_raw or "",
                        int(a.plan_months or 0),
                        "동의함" if a.agreed else "",
                        now,
                    ),
                )
                n += 1
        return n

    def all_form_applicants(self) -> list[FormApplicant]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM form_applicants ORDER BY name, member_user_id"
            ).fetchall()
        return [_row_to_form_applicant(r) for r in rows]

    def get_form_applicant(self, member_user_id: str) -> FormApplicant | None:
        target = (member_user_id or "").strip().lower()
        if not target:
            return None
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM form_applicants WHERE LOWER(member_user_id) = ?",
                (target,),
            ).fetchone()
        return _row_to_form_applicant(row) if row else None

    def delete_form_applicant(self, member_user_id: str) -> int:
        """그 회원의 폼 신청자 캐시 행 삭제. 대소문자 무시. Returns: 삭제된 행 수."""
        target = (member_user_id or "").strip().lower()
        if not target:
            return 0
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM form_applicants WHERE LOWER(member_user_id) = ?",
                (target,),
            )
            return cur.rowcount or 0


# ---------- 행 변환 ----------

def _row_to_form_applicant(r: sqlite3.Row) -> FormApplicant:
    return FormApplicant(
        member_user_id=r["member_user_id"],
        applied_at=r["applied_at"] or "",
        name=r["name"] or "",
        phone=r["phone"] or "",
        email=r["email"] or "",
        plan_raw=r["plan_raw"] or "",
        plan_months=int(r["plan_months"] or 0),
        agreed=bool((r["agreed"] or "").strip()),
    )

def _row_to_txn(r: sqlite3.Row) -> Transaction:
    return Transaction(
        id=r["id"],
        txn_at=datetime.fromisoformat(r["txn_at"]),
        payer_name=r["payer_name"],
        amount=r["amount"],
        bank=r["bank"],
        counterparty_account=r["counterparty_account"],
        memo=r["memo"],
        source_file=r["source_file"],
        imported_at=datetime.fromisoformat(r["imported_at"]),
    )


def _row_to_sub(r: sqlite3.Row) -> Subscription:
    return Subscription(
        id=r["id"],
        member_user_id=r["member_user_id"],
        transaction_id=r["transaction_id"],
        months=r["months"],
        period_from=date.fromisoformat(r["period_from"]),
        period_to=date.fromisoformat(r["period_to"]),
    )
