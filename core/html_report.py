"""HTML 분기 리포트 생성.

내용:
  · 등급별 인원 표
  · 분기 백업 결과 (있으면)
  · 최근 30/90일 작업 카운트

의존성 추가 없이 순수 문자열 포매팅으로 작성 — jinja2 미사용.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional

from config import APP_NAME, APP_VERSION, BACKUPS_DIR, LEVEL_LABELS
from core.log_reader import count_actions, count_events, load_recent_entries
from core.models import BackupResult, Member


_HTML_HEAD = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: 'Malgun Gothic', sans-serif; margin: 24px; max-width: 900px; }}
  h1 {{ border-bottom: 2px solid #0a8; padding-bottom: 4px; }}
  h2 {{ color: #0a8; margin-top: 28px; }}
  table {{ border-collapse: collapse; margin: 8px 0 24px 0; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 12px; text-align: left; }}
  th {{ background: #eef; }}
  td.num {{ text-align: right; }}
  .muted {{ color: #888; font-size: 0.9em; }}
</style>
</head>
<body>
<h1>{title}</h1>
<p class="muted">생성: {generated} · {app} v{ver}</p>
"""

_HTML_TAIL = """</body></html>"""


def _esc(s) -> str:
    return (
        str(s).replace("&", "&amp;")
              .replace("<", "&lt;")
              .replace(">", "&gt;")
    )


def _level_table(members: list[Member]) -> str:
    if not members:
        return "<p>회원 데이터가 없습니다.</p>"
    counts: dict[int, int] = {}
    for m in members:
        counts[m.level] = counts.get(m.level, 0) + 1
    rows = []
    for lv in sorted(counts.keys(), reverse=True):
        label = LEVEL_LABELS.get(lv, f"레벨 {lv}")
        rows.append(
            f"<tr><td>레벨 {lv}</td><td>{_esc(label)}</td>"
            f"<td class='num'>{counts[lv]}</td></tr>"
        )
    body = "".join(rows)
    return (
        f"<p>총 회원 수: <b>{len(members)}</b>명</p>"
        f"<table><thead><tr><th>레벨</th><th>등급</th><th>인원</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def _activity_table() -> str:
    try:
        recent_30 = load_recent_entries(days=30)
        recent_90 = load_recent_entries(days=90)
    except Exception as e:
        return f"<p>활동 로그 로드 실패: {_esc(e)}</p>"
    c30 = count_actions(recent_30)
    c90 = count_actions(recent_90)
    keys = sorted(set(c30.keys()) | set(c90.keys()))
    if not keys:
        return "<p>최근 활동 기록이 없습니다.</p>"
    rows = []
    for k in keys:
        rows.append(
            f"<tr><td>{_esc(k)}</td>"
            f"<td class='num'>{c30.get(k, 0)}</td>"
            f"<td class='num'>{c90.get(k, 0)}</td></tr>"
        )
    body = "".join(rows)
    backup_30 = count_events(recent_30, "backup")
    backup_90 = count_events(recent_90, "backup")
    return (
        f"<table><thead>"
        f"<tr><th>유형</th><th>최근 30일</th><th>최근 90일</th></tr>"
        f"</thead><tbody>{body}</tbody></table>"
        f"<p>백업 이벤트: 30일 {backup_30}회 / 90일 {backup_90}회</p>"
    )


def _backup_summary(backup: Optional[BackupResult]) -> str:
    if backup is None:
        return "<p>이번 세션에서 실행된 백업 결과가 없습니다.</p>"
    breakdown = ", ".join(
        f"레벨 {lv} ({_esc(LEVEL_LABELS.get(lv, str(lv)))}) {cnt}명"
        for lv, cnt in sorted(backup.level_breakdown.items(), reverse=True)
    )
    return (
        f"<p>총 백업 회원: <b>{backup.member_count}</b>명</p>"
        f"<p>구성: {breakdown or '없음'}</p>"
        f"<p>저장 위치:<br>"
        f"  TXT: <code>{_esc(backup.txt_path)}</code><br>"
        f"  XLSX: <code>{_esc(backup.xlsx_path)}</code></p>"
    )


def render_report(
    members: list[Member],
    backup: Optional[BackupResult] = None,
    title: Optional[str] = None,
) -> str:
    today = date.today()
    title = title or f"초록등대 분기 리포트 — {today.isoformat()}"
    head = _HTML_HEAD.format(
        title=_esc(title),
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        app=_esc(APP_NAME),
        ver=_esc(APP_VERSION),
    )
    parts = [
        head,
        "<h2>등급별 인원</h2>",
        _level_table(members),
        "<h2>최근 활동</h2>",
        _activity_table(),
        "<h2>이번 백업 요약</h2>",
        _backup_summary(backup),
        _HTML_TAIL,
    ]
    return "".join(parts)


def write_report(
    path: Path,
    members: list[Member],
    backup: Optional[BackupResult] = None,
    title: Optional[str] = None,
) -> Path:
    html = render_report(members, backup=backup, title=title)
    Path(path).write_text(html, encoding="utf-8")
    return Path(path)


def default_report_path() -> Path:
    """`backups/report_YYYY-MM-DD.html` — 백업 폴더에 함께 보관."""
    return Path(BACKUPS_DIR) / f"report_{date.today().isoformat()}.html"
