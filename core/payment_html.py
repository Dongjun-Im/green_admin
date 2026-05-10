"""자료실 구독비 — 독립 HTML 리포트.

backups/payments_YYYY-MM-DD.html 로 저장.
v0.4 분기 리포트(core/html_report.py)와 의도·대상이 달라 별도 리포트로 분리.

섹션:
    요약 KPI / 구독 매트릭스 / 이번 달·다음 달 만료 / 활성 구독 상세 /
    단가표 참고 / 기타 입금 요약 (개별 행 노출 X — PII 보호)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from config import APP_NAME, APP_VERSION, BACKUPS_DIR, SUBSCRIPTION_PRICING
from core.payment_matrix import MonthHeader, cell_state, month_range, status_summary
from core.payment_store import lookup_months

if TYPE_CHECKING:
    from core.models import Member
    from core.payment_store import PaymentStore, Subscription


_MATRIX_MONTHS = 12

# v0.4 분기 리포트와 시각적으로 구분하기 위해 액센트만 노랑계로.
_HTML_HEAD = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: 'Malgun Gothic', sans-serif; margin: 24px; max-width: 1100px; }}
  h1 {{ border-bottom: 2px solid #d4a017; padding-bottom: 4px; }}
  h2 {{ color: #b8860b; margin-top: 28px; }}
  table {{ border-collapse: collapse; margin: 8px 0 24px 0; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; }}
  th {{ background: #fff3cd; }}
  td.num {{ text-align: right; }}
  td.cell-on  {{ background: #d4edda; text-align: center; font-weight: bold; }}
  td.cell-end {{ background: #fff3cd; text-align: center; }}
  td.cell-off {{ color: #aaa; text-align: center; }}
  .muted {{ color: #888; font-size: 0.9em; }}
  .kpi {{ display: inline-block; margin-right: 24px; padding: 8px 14px;
          background: #fffbe6; border: 1px solid #f0d97a; border-radius: 4px; }}
  .kpi b {{ font-size: 1.3em; color: #b8860b; }}
</style>
</head>
<body>
<h1>{title}</h1>
<p class="muted">생성: {generated} · {app} v{ver}</p>
"""

_HTML_TAIL = "</body></html>"


def _esc(s) -> str:
    return (
        str(s).replace("&", "&amp;")
              .replace("<", "&lt;")
              .replace(">", "&gt;")
    )


def default_html_path(today: date | None = None) -> Path:
    d = today or date.today()
    return Path(BACKUPS_DIR) / f"payments_{d.isoformat()}.html"


def render_payment_html(
    store: PaymentStore,
    members: list[Member],
    today: date | None = None,
) -> str:
    today = today or date.today()
    title = f"초록등대 자료실 구독 리포트 — {today.isoformat()}"

    members_by_uid = {m.user_id: m for m in members}
    subs_by_uid: dict[str, list[Subscription]] = {}
    for s in store.all_subscriptions():
        subs_by_uid.setdefault(s.member_user_id, []).append(s)

    head = _HTML_HEAD.format(
        title=_esc(title),
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        app=_esc(APP_NAME),
        ver=_esc(APP_VERSION),
    )
    parts = [
        head,
        _kpi_section(subs_by_uid, today),
        "<h2>구독 매트릭스 (최근 12개월)</h2>",
        _matrix_section(subs_by_uid, members_by_uid, today),
        "<h2>이번 달 / 다음 달 만료 회원</h2>",
        _expiring_section(subs_by_uid, members_by_uid, today),
        "<h2>활성 구독 상세 (만료 임박 순)</h2>",
        _active_detail_section(subs_by_uid, members_by_uid, today),
        "<h2>참고 — 단가표</h2>",
        _pricing_section(),
        "<h2>기타 입금 요약</h2>",
        _other_deposits_section(store),
        _HTML_TAIL,
    ]
    return "".join(parts)


def write_payment_html(
    path: Path | str,
    store: PaymentStore,
    members: list[Member],
    today: date | None = None,
) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_payment_html(store, members, today=today), encoding="utf-8")
    return out


# ---------- 섹션 ----------

def _kpi_section(subs_by_uid: dict, today: date) -> str:
    active = 0
    expiring_this = 0
    expiring_next = 0
    next_month = today.replace(day=1) + timedelta(days=32)  # 안전한 다음 달
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

    return (
        f'<div class="kpi"><b>{active}</b>명 활성 구독자</div>'
        f'<div class="kpi"><b>{expiring_this}</b>명 이번 달 만료</div>'
        f'<div class="kpi"><b>{expiring_next}</b>명 다음 달 만료</div>'
        f'<div class="kpi">기준일 <b>{today.isoformat()}</b></div>'
    )


def _matrix_section(
    subs_by_uid: dict, members_by_uid: dict, today: date
) -> str:
    if not subs_by_uid:
        return "<p>구독 이력이 없습니다.</p>"
    months = month_range(today, _MATRIX_MONTHS)

    # 만료 임박순
    items = []
    for uid, subs in subs_by_uid.items():
        latest = max((s.period_to for s in subs), default=date.min)
        items.append((uid, subs, latest))
    items.sort(key=lambda x: x[2])

    # 헤더
    hdr_cells = ["<th>아이디</th><th>이름 / 닉</th><th>상태</th>"]
    for h in months:
        hdr_cells.append(f"<th>{h.label}</th>")
    rows = ["<tr>" + "".join(hdr_cells) + "</tr>"]

    for uid, subs, _ in items:
        m = members_by_uid.get(uid)
        name = (m.name if m else "") or "(목록 외)"
        nick = (m.nickname if m else "") or ""
        name_col = _esc(name) + (f" / {_esc(nick)}" if nick else "")
        status = _esc(status_summary(subs, today=today))
        cells = [f"<td>{_esc(uid)}</td><td>{name_col}</td><td>{status}</td>"]
        for h in months:
            v = cell_state(subs, h)
            cls = (
                "cell-on" if v == "O"
                else "cell-off" if v == "."
                else "cell-end"
            )
            cells.append(f'<td class="{cls}">{_esc(v)}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")

    return "<table>" + "".join(rows) + "</table>"


def _expiring_section(
    subs_by_uid: dict, members_by_uid: dict, today: date
) -> str:
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
        return "<p>이번 달·다음 달 만료 회원이 없습니다.</p>"
    rows.sort(key=lambda r: r[3])
    body = "".join(
        f"<tr><td>{_esc(uid)}</td><td>{_esc(name)}</td>"
        f"<td>{_esc(nick)}</td><td>{end.isoformat()}</td></tr>"
        for uid, name, nick, end in rows
    )
    return (
        "<table><thead><tr><th>아이디</th><th>이름</th>"
        "<th>닉네임</th><th>만료일</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def _active_detail_section(
    subs_by_uid: dict, members_by_uid: dict, today: date
) -> str:
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
        return "<p>현재 활성 구독자가 없습니다.</p>"
    rows.sort(key=lambda r: r[4])
    body = "".join(
        f"<tr><td>{_esc(uid)}</td><td>{_esc(name)}</td>"
        f"<td>{_esc(nick)}</td><td>{start.isoformat()}</td>"
        f"<td>{end.isoformat()}</td>"
        f'<td class="num">{days}</td>'
        f'<td class="num">{months}</td></tr>'
        for uid, name, nick, start, end, days, months in rows
    )
    return (
        "<table><thead><tr><th>아이디</th><th>이름</th><th>닉네임</th>"
        "<th>시작일</th><th>만료일</th><th>남은일수</th>"
        "<th>총구독개월</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def _pricing_section() -> str:
    items = sorted(SUBSCRIPTION_PRICING.items())
    body = "".join(
        f'<tr><td class="num">{amount:,}원</td>'
        f'<td class="num">{months}개월</td></tr>'
        for amount, months in items
    )
    return (
        "<table><thead><tr><th>입금액</th><th>구독 개월</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def _other_deposits_section(store: PaymentStore) -> str:
    """단가표 외 입금 카운트·합계만 노출 — 개별 행은 PII 위험으로 숨김."""
    others = [t for t in store.all_transactions() if lookup_months(t.amount) is None]
    if not others:
        return "<p>단가표 외 입금이 없습니다.</p>"
    total = sum(t.amount for t in others)
    return (
        f"<p><b>{len(others)}</b>건 / 합계 <b>{total:,}원</b></p>"
        '<p class="muted">'
        "개별 거래 명세는 앱의 \"기타 입금 보기\" 메뉴에서만 확인 — "
        "리포트에는 PII 보호를 위해 카운트만 표시합니다."
        "</p>"
    )
