"""자료실 접속 로그 — 텍스트(TXT) 내보내기.

backups/nas_access_log_YYYY-MM-DD.txt. 한 줄에 한 항목, 스크린리더로
한 줄씩 읽기 좋게 ' | ' 구분.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from config import APP_NAME, APP_VERSION, BACKUPS_DIR
from core.nas_log_service import ACTION_LABELS, enrich_with_members

if TYPE_CHECKING:
    from core.models import Member
    from core.nas_log_store import NasLogFilter, NasLogStore


def default_txt_path(today: date | None = None) -> Path:
    d = today or date.today()
    return Path(BACKUPS_DIR) / f"nas_access_log_{d.isoformat()}.txt"


def render_nas_log_txt(
    store: "NasLogStore",
    members: list["Member"],
    *,
    flt: "NasLogFilter | None" = None,
    today: date | None = None,
) -> str:
    today = today or date.today()
    entries = store.entries(flt)
    rows = enrich_with_members(entries, members)

    lines: list[str] = []
    lines.append(f"초록등대 자료실 접속 로그 ({today.isoformat()})")
    lines.append(
        f"생성: {datetime.now().strftime('%Y-%m-%d %H:%M')} - {APP_NAME} v{APP_VERSION}"
    )
    lines.append(f"항목 수: {len(rows)}")
    if flt is not None:
        if flt.start_date or flt.end_date:
            s = flt.start_date.isoformat() if flt.start_date else "(처음)"
            e = flt.end_date.isoformat() if flt.end_date else "(끝)"
            lines.append(f"기간: {s} ~ {e}")
        if flt.dsm_user_id_like:
            lines.append(f"회원 ID 필터: {flt.dsm_user_id_like}")
        if flt.action_in:
            labels = [ACTION_LABELS.get(a, a) for a in flt.action_in]
            lines.append(f"동작 필터: {', '.join(labels)}")
        if flt.category_like:
            lines.append(f"카테고리 필터: {flt.category_like}")
    lines.append("")
    lines.append("-" * 100)
    lines.append("시간                | 회원                | IP             | 프로토콜    | 동작     | 카테고리        | 파일")
    lines.append("-" * 100)
    for r in rows:
        e = r.entry
        when = e.logged_at.replace("T", " ")
        who = r.display_name
        action_ko = ACTION_LABELS.get(e.action, e.action or "-")
        cat = e.category or "-"
        fname = e.file_name or "-"
        lines.append(
            f"{when} | {who[:18]:<18} | {e.ip[:14]:<14} | {e.protocol[:10]:<10} | "
            f"{action_ko[:8]:<8} | {cat[:14]:<14} | {fname}"
        )
    lines.append("-" * 100)
    return "\n".join(lines) + "\n"


def write_nas_log_txt(
    path: Path | str,
    store: "NasLogStore",
    members: list["Member"],
    *,
    flt: "NasLogFilter | None" = None,
    today: date | None = None,
) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_nas_log_txt(store, members, flt=flt, today=today),
        encoding="utf-8",
    )
    return out
