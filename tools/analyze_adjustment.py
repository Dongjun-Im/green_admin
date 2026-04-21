"""장기미접속 조정 대상 121명을 분류·저장 (dry-run only).

사이트에 아무 영향 없음. 전체 회원 크롤 → LevelAdjustmentService.build_plan()
→ 탈퇴/강등 분류 → 통계 출력 + TXT 저장.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from datetime import datetime

from green_auth.authenticator import Authenticator
from green_auth.credentials import load_credentials
from config import ADMIN_MEMBER_URL, LEVEL_LABELS
from core.crawler import MemberCrawler
from core.level_adjustment import LevelAdjustmentService
from core.member_admin import MemberAdminAdapter
from core.member_parser import MemberListParser


def main() -> int:
    creds = load_credentials()
    if not creds:
        print("NO CREDENTIALS")
        return 1
    user_id, password = creds
    print(f"[1/3] login: {user_id}")
    auth = Authenticator()
    result = auth.authenticate(user_id, password)
    if not result.is_success:
        print(f"AUTH FAIL: {result.message}")
        return 1
    session = auth.session

    print("[2/3] fetching all members (may take ~1 minute)")
    parser = MemberListParser()
    crawler = MemberCrawler(session, ADMIN_MEMBER_URL, parser=parser)
    all_members = crawler.fetch_all_members(progress_cb=lambda cur, tot: print(f"  page {cur}"))
    print(f"  total crawled: {len(all_members)}")

    print("[3/3] building adjustment plan (dry-run)")
    admin = MemberAdminAdapter(session, dry_run=True)
    las = LevelAdjustmentService(
        crawler, admin, admin_user_id=user_id
    )
    plan = las.build_plan(members=all_members)

    print()
    print("=" * 60)
    print(f"cutoff date : {plan.cutoff_date}")
    print(f"total       : {plan.total_scanned}")
    print(f"actionable  : {len(plan.actionable)}")
    print(f"  demote    : {plan.demote_count}")
    print(f"  delete    : {plan.delete_count}")
    print(f"  skip      : {plan.skip_count}")
    print("=" * 60)

    # 카테고리별 분류
    by_from: dict[int, list] = {}
    for it in plan.items:
        by_from.setdefault(it.from_level, []).append(it)

    print()
    print("by from_level:")
    for lv in sorted(by_from.keys()):
        items = by_from[lv]
        actions = {}
        for it in items:
            actions[it.action] = actions.get(it.action, 0) + 1
        label = LEVEL_LABELS.get(lv, f"lv{lv}")
        print(f"  {lv} ({label}): {len(items)}  {actions}")

    # TXT 저장 (한글 + 정렬)
    out_dir = os.path.join(ROOT, "backups", "analysis")
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_path = os.path.join(out_dir, f"adjustment_plan_{ts}.txt")

    lines: list[str] = []
    lines.append(f"장기미접속 조정 분석 ({datetime.now():%Y-%m-%d %H:%M})")
    lines.append(f"기준일: {plan.cutoff_date.isoformat()} 이전 접속자")
    lines.append(f"전체 스캔: {plan.total_scanned}명")
    lines.append(f"조정 대상: {len(plan.actionable)}명 (강등 {plan.demote_count}명 / 탈퇴 {plan.delete_count}명 / 건너뜀 {plan.skip_count}명)")
    lines.append("=" * 70)
    lines.append("")

    # 탈퇴 먼저, 강등 나중
    delete_items = [it for it in plan.items if it.action == "delete"]
    demote_items = [it for it in plan.items if it.action == "demote"]
    skip_items = [it for it in plan.items if it.action == "skip"]

    def fmt(it, idx):
        m = it.member
        last = m.last_login_date.isoformat() if m.last_login_date else "알수없음"
        join = m.join_date.isoformat() if m.join_date else "알수없음"
        login_cnt = f"{m.login_count}회" if m.login_count is not None else "?"
        if it.action == "delete":
            target = "탈퇴"
        else:
            target = LEVEL_LABELS.get(it.to_level or 0, f"lv{it.to_level}")
        return (
            f"{idx:>4}. [{m.level_label}] {m.user_id:<15} "
            f"{(m.name or ''):<6} {(m.nickname or ''):<12} "
            f"→ {target:<10}  접속 {last}  가입 {join}  접속수 {login_cnt}"
        )

    if delete_items:
        lines.append(f"▶ 탈퇴 대상 ({len(delete_items)}명)")
        lines.append("-" * 70)
        for i, it in enumerate(delete_items, start=1):
            lines.append(fmt(it, i))
        lines.append("")

    if demote_items:
        lines.append(f"▶ 강등 대상 ({len(demote_items)}명)")
        lines.append("-" * 70)
        for i, it in enumerate(demote_items, start=1):
            lines.append(fmt(it, i))
        lines.append("")

    if skip_items:
        lines.append(f"▶ 건너뜀 ({len(skip_items)}명) — 접속일 파싱 실패")
        lines.append("-" * 70)
        for i, it in enumerate(skip_items, start=1):
            lines.append(fmt(it, i))
        lines.append("")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print()
    print(f"saved: {txt_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
