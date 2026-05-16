"""자료실 접속 로그 — 독립 HTML 리포트.

backups/nas_access_log_YYYY-MM-DD.html. 검색 가능한 한 페이지짜리 표.
"""
from __future__ import annotations

import html
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from config import APP_NAME, APP_VERSION, BACKUPS_DIR
from core.nas_log_service import ACTION_LABELS, enrich_with_members

if TYPE_CHECKING:
    from core.models import Member
    from core.nas_log_store import NasLogFilter, NasLogStore


_HTML_HEAD = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          margin: 16px; color: #222; }}
  h1 {{ font-size: 1.4em; margin-bottom: 4px; }}
  .meta {{ color: #666; margin-bottom: 16px; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 8px; text-align: left;
            font-size: 0.92em; vertical-align: top; }}
  th {{ background: #f3f6fa; }}
  tr:nth-child(even) td {{ background: #fafbfc; }}
  .num {{ text-align: right; }}
  .filter {{ background: #fff8e1; padding: 8px 12px; border-radius: 6px;
             margin-bottom: 12px; font-size: 0.95em; }}
</style>
</head>
<body>
"""


def default_html_path(today: date | None = None) -> Path:
    d = today or date.today()
    return Path(BACKUPS_DIR) / f"nas_access_log_{d.isoformat()}.html"


def write_nas_log_html(
    path: Path | str,
    store: "NasLogStore",
    members: list["Member"],
    *,
    flt: "NasLogFilter | None" = None,
    today: date | None = None,
) -> Path:
    today = today or date.today()
    entries = store.entries(flt)
    rows = enrich_with_members(entries, members)

    title = f"초록등대 자료실 접속 로그 ({today.isoformat()})"
    parts: list[str] = [_HTML_HEAD.format(title=html.escape(title))]
    parts.append(f"<h1>{html.escape(title)}</h1>")
    parts.append(
        f"<div class='meta'>생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        f" - {html.escape(APP_NAME)} v{html.escape(APP_VERSION)} - 항목 {len(rows)}건</div>"
    )

    # 필터 요약
    if flt is not None and (flt.start_date or flt.end_date
                            or flt.dsm_user_id_like or flt.action_in
                            or flt.category_like):
        flt_lines: list[str] = []
        if flt.start_date or flt.end_date:
            s = flt.start_date.isoformat() if flt.start_date else "(처음)"
            e = flt.end_date.isoformat() if flt.end_date else "(끝)"
            flt_lines.append(f"기간 {html.escape(s)} ~ {html.escape(e)}")
        if flt.dsm_user_id_like:
            flt_lines.append(f"회원 ID: {html.escape(flt.dsm_user_id_like)}")
        if flt.action_in:
            labels = [ACTION_LABELS.get(a, a) for a in flt.action_in]
            flt_lines.append("동작: " + html.escape(", ".join(labels)))
        if flt.category_like:
            flt_lines.append(f"카테고리: {html.escape(flt.category_like)}")
        parts.append("<div class='filter'>필터 - " + " / ".join(flt_lines) + "</div>")

    parts.append(
        "<table><thead><tr>"
        "<th>시간</th><th>회원</th><th>DSM 아이디</th><th>IP</th>"
        "<th>프로토콜</th><th>동작</th><th>카테고리</th><th>파일</th><th>전체 경로</th>"
        "</tr></thead><tbody>"
    )
    for r in rows:
        e = r.entry
        when = e.logged_at.replace("T", " ")
        cols = [
            when, r.display_name, e.dsm_user_id, e.ip, e.protocol,
            ACTION_LABELS.get(e.action, e.action),
            e.category, e.file_name, e.file_path,
        ]
        parts.append(
            "<tr>" + "".join(f"<td>{html.escape(str(c or ''))}</td>" for c in cols) + "</tr>"
        )
    parts.append("</tbody></table></body></html>")

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path
