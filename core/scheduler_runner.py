"""헤드리스 작업 실행기 — 윈도우 작업 스케줄러용 (v1.2.11).

`python main.py --task <name>` 으로 호출되면 wx UI 없이 그 작업만 수행하고
종료한다. 종료 코드:
  0 — 성공 (한 명 이상 메일 발송 또는 '대상 없음' 도 성공)
  1 — 실패 (자격증명 없음/로그인 실패/권한 없음/예외)

지원 작업:
  · activity_nudge    — green3 6개월 글 없음 안내 메일
  · inactive_warning  — 1년+ 미접속 사전 경고 메일
  · expiry_remind_7   — 자료실 구독 만료 7일 전 알림
  · expiry_remind_3   — 자료실 구독 만료 3일 전 알림

모든 작업은 'rtgreen' 아이디로 로그인된 상태에서만 실제 메일을 보낸다.
다른 아이디 자격증명으로 실행되면 MailSender 가 자동으로 스킵 (안전 장치).

로그: `data/logs/scheduler_<task>_YYYYMM.log` 형식, 한 줄 = 한 이벤트.
"""
from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from config import ADMIN_MEMBER_URL, DATA_DIR
# 모듈 상단 import — 테스트가 monkeypatch 로 교체할 수 있도록.
from core.crawler import MemberCrawler
from core.mail_sender import MailSender
from core.member_parser import MemberListParser
from core.nudge_history import NudgeHistoryStore


# 지원 작업 키. main.py 의 --task 인자가 이 키들 중 하나여야 함.
TASK_ACTIVITY_NUDGE = "activity_nudge"
TASK_INACTIVE_WARNING = "inactive_warning"
TASK_EXPIRY_REMIND_7 = "expiry_remind_7"
TASK_EXPIRY_REMIND_3 = "expiry_remind_3"
TASK_POST_SCHEDULED = "post_scheduled"   # 예약 공지 자동 발송 (v1.4)
ALL_TASKS = (
    TASK_ACTIVITY_NUDGE,
    TASK_INACTIVE_WARNING,
    TASK_EXPIRY_REMIND_7,
    TASK_EXPIRY_REMIND_3,
    TASK_POST_SCHEDULED,
)


@dataclass
class TaskResult:
    """작업 한 번의 결과."""
    task: str
    success: bool
    message: str
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    # 대상 회원 수 (메일 작업에서). 0 이면 '도래자 없음' — 그래도 success.
    targets: int = 0


# ---------------------------------------------------------------------------
# 로그
# ---------------------------------------------------------------------------


def _log_path(task: str) -> Path:
    ym = datetime.now().strftime("%Y%m")
    return Path(DATA_DIR) / "logs" / f"scheduler_{task}_{ym}.log"


def log_event(task: str, line: str) -> None:
    """`data/logs/scheduler_<task>_YYYYMM.log` 끝에 ISO 타임스탬프 + 한 줄 추가."""
    path = _log_path(task)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            ts = datetime.now().isoformat(timespec="seconds")
            f.write(f"[{ts}] {line}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 작업 실행
# ---------------------------------------------------------------------------


def _build_services(session, user_id: str):
    """헤드리스 모드에서 필요한 핵심 서비스 한 묶음을 만들어 돌려준다."""
    parser = MemberListParser()
    crawler = MemberCrawler(session, ADMIN_MEMBER_URL, parser=parser)
    mail_sender = MailSender(session, user_id)
    nudge_history = NudgeHistoryStore(Path(DATA_DIR) / "nudge_history.json")
    return crawler, mail_sender, nudge_history


def _send_nudge_kind(
    *, kind: str, crawler, mail_sender, nudge_history, user_id: str,
) -> TaskResult:
    """KIND_ACTIVITY_NUDGE 또는 KIND_INACTIVE_WARNING 한 종류 처리."""
    from core.activity_counter import ActivityCounter
    from core.mail_sender import SEND_MODE_INDIVIDUAL
    from core.nudge_history import KIND_ACTIVITY_NUDGE, KIND_INACTIVE_WARNING
    from core.nudge_mail import (
        find_activity_nudge_targets,
        find_inactive_warning_targets,
        template_activity_nudge,
        template_inactive_warning,
    )

    task_label = (
        TASK_ACTIVITY_NUDGE if kind == KIND_ACTIVITY_NUDGE
        else TASK_INACTIVE_WARNING
    )
    log_event(task_label, f"start kind={kind} actor={user_id}")

    members = crawler.fetch_all_members()
    log_event(task_label, f"fetched_members count={len(members)}")

    if kind == KIND_ACTIVITY_NUDGE:
        counter = ActivityCounter(crawler.session)
        targets = find_activity_nudge_targets(
            members, counter,
            admin_user_id=user_id, history=nudge_history,
        )
        template_fn = template_activity_nudge
    else:
        targets = find_inactive_warning_targets(
            members, admin_user_id=user_id, history=nudge_history,
        )
        template_fn = template_inactive_warning

    log_event(task_label, f"targets count={len(targets)}")
    if not targets:
        log_event(task_label, "no_targets — success")
        return TaskResult(task=task_label, success=True,
                          message="대상 없음", targets=0)

    ok = fail = skipped = 0
    sent_uids: list[str] = []
    for i, t in enumerate(targets, start=1):
        m = t.member
        subject, body = template_fn(m)
        try:
            results = mail_sender.send(
                [m.user_id], subject, body, mode=SEND_MODE_INDIVIDUAL,
            )
        except Exception as e:
            fail += 1
            log_event(task_label, f"send_failed uid={m.user_id} err={e}")
            continue
        if not results:
            fail += 1
            continue
        r = results[0]
        if r.skipped:
            skipped += 1
            log_event(task_label, f"skipped uid={m.user_id} reason={r.message}")
            continue
        if r.success:
            ok += 1
            sent_uids.append(m.user_id)
        else:
            fail += 1
            log_event(task_label, f"send_failed uid={m.user_id} msg={r.message}")

    if sent_uids:
        nudge_history.mark_sent_many(sent_uids, kind)
    log_event(task_label, f"done ok={ok} failed={fail} skipped={skipped}")
    return TaskResult(
        task=task_label, success=True, targets=len(targets),
        sent=ok, failed=fail, skipped=skipped,
        message=f"성공 {ok}건, 실패 {fail}건, 스킵 {skipped}건",
    )


def _send_expiry_kind(
    *, days_before: int, crawler, mail_sender, nudge_history, user_id: str,
) -> TaskResult:
    """expiry_remind_7 / expiry_remind_3 처리."""
    from core.expiry_reminder import (
        REMINDER_DAYS_BEFORE,
        find_expiry_targets,
        template_for_kind,
    )
    from core.mail_sender import SEND_MODE_INDIVIDUAL
    from core.payment_store import PaymentStore

    task_label = (
        TASK_EXPIRY_REMIND_7 if days_before == 7 else TASK_EXPIRY_REMIND_3
    )
    kind = REMINDER_DAYS_BEFORE[days_before]
    log_event(task_label, f"start kind={kind} actor={user_id}")

    store = PaymentStore()
    members = crawler.fetch_all_members()
    log_event(task_label, f"fetched_members count={len(members)}")

    targets = find_expiry_targets(
        members, payment_store=store, days_before=days_before,
        admin_user_id=user_id, history=nudge_history,
    )
    log_event(task_label, f"targets count={len(targets)}")
    if not targets:
        log_event(task_label, "no_targets — success")
        return TaskResult(task=task_label, success=True,
                          message="대상 없음", targets=0)

    template_fn = template_for_kind(kind)
    ok = fail = skipped = 0
    sent_pairs: list[tuple] = []
    for t in targets:
        m = t.member
        subject, body = template_fn(m, t.expiry_date)
        try:
            results = mail_sender.send(
                [m.user_id], subject, body, mode=SEND_MODE_INDIVIDUAL,
            )
        except Exception as e:
            fail += 1
            log_event(task_label, f"send_failed uid={m.user_id} err={e}")
            continue
        if not results:
            fail += 1
            continue
        r = results[0]
        if r.skipped:
            skipped += 1
            log_event(task_label, f"skipped uid={m.user_id} reason={r.message}")
            continue
        if r.success:
            ok += 1
            sent_pairs.append((m.user_id, t.expiry_date))
        else:
            fail += 1
            log_event(task_label, f"send_failed uid={m.user_id} msg={r.message}")

    for uid, period_to in sent_pairs:
        nudge_history.mark_sent(uid, kind, when=period_to)
    log_event(task_label, f"done ok={ok} failed={fail} skipped={skipped}")
    return TaskResult(
        task=task_label, success=True, targets=len(targets),
        sent=ok, failed=fail, skipped=skipped,
        message=f"성공 {ok}건, 실패 {fail}건, 스킵 {skipped}건",
    )


def _post_scheduled_notices(session, user_id: str) -> TaskResult:
    """예약 공지 큐에서 도래분을 찾아 게시판에 올린다 (v1.4)."""
    from core.board_admin import post_notice_to_boards
    from core.scheduled_notice import ScheduledNoticeStore

    task_label = TASK_POST_SCHEDULED
    store = ScheduledNoticeStore()
    due = store.due()
    log_event(task_label, f"start actor={user_id} due={len(due)}")
    if not due:
        log_event(task_label, "no_due — success")
        return TaskResult(task=task_label, success=True,
                          message="발송할 예약 없음", targets=0)

    posted = failed = 0
    for n in due:
        try:
            results = post_notice_to_boards(
                session, n.boards, n.subject, n.content,
                as_notice=n.as_notice, use_html=n.use_html,
            )
        except Exception as e:
            failed += 1
            store.mark_failed(n.id, f"예외: {e}")
            log_event(task_label, f"post_failed id={n.id} err={e}")
            continue
        ok_n = sum(1 for r in results if r.ok)
        summary = f"성공 {ok_n}/{len(results)} 게시판"
        if ok_n == len(results) and results:
            posted += 1
            store.mark_posted(n.id, summary)
            log_event(task_label, f"posted id={n.id} {summary}")
        else:
            failed += 1
            detail = "; ".join(
                f"{r.bo_table}:{r.message}" for r in results if not r.ok
            )
            store.mark_failed(n.id, f"{summary} ({detail})")
            log_event(task_label, f"post_partial_fail id={n.id} {summary} {detail}")

    log_event(task_label, f"done posted={posted} failed={failed}")
    return TaskResult(
        task=task_label, success=True, targets=len(due),
        sent=posted, failed=failed,
        message=f"예약 공지 발송 — 성공 {posted}건, 실패 {failed}건",
    )


def _run_task_with_session(task: str, session, user_id: str) -> TaskResult:
    """이미 로그인된 session 으로 task 실행. 테스트하기 좋은 진입점."""
    from core.nudge_history import (
        KIND_ACTIVITY_NUDGE,
        KIND_INACTIVE_WARNING,
    )

    if task == TASK_POST_SCHEDULED:
        return _post_scheduled_notices(session, user_id)

    crawler, mail_sender, nudge_history = _build_services(session, user_id)
    if task == TASK_ACTIVITY_NUDGE:
        return _send_nudge_kind(
            kind=KIND_ACTIVITY_NUDGE,
            crawler=crawler, mail_sender=mail_sender,
            nudge_history=nudge_history, user_id=user_id,
        )
    if task == TASK_INACTIVE_WARNING:
        return _send_nudge_kind(
            kind=KIND_INACTIVE_WARNING,
            crawler=crawler, mail_sender=mail_sender,
            nudge_history=nudge_history, user_id=user_id,
        )
    if task == TASK_EXPIRY_REMIND_7:
        return _send_expiry_kind(
            days_before=7,
            crawler=crawler, mail_sender=mail_sender,
            nudge_history=nudge_history, user_id=user_id,
        )
    if task == TASK_EXPIRY_REMIND_3:
        return _send_expiry_kind(
            days_before=3,
            crawler=crawler, mail_sender=mail_sender,
            nudge_history=nudge_history, user_id=user_id,
        )
    return TaskResult(
        task=task, success=False,
        message=f"지원하지 않는 작업: {task}. 지원값: {', '.join(ALL_TASKS)}",
    )


# ---------------------------------------------------------------------------
# 메인 진입점 — main.py 에서 호출
# ---------------------------------------------------------------------------


def run_task(task: str) -> int:
    """저장된 자격증명으로 로그인 후 task 실행. 반환: 종료 코드(0/1)."""
    if task not in ALL_TASKS:
        sys.stderr.write(
            f"지원하지 않는 작업: {task}. 지원값: {', '.join(ALL_TASKS)}\n"
        )
        return 1

    # 예약 공지: 발송할 도래분이 없으면 로그인조차 하지 않고 가볍게 종료.
    if task == TASK_POST_SCHEDULED:
        from core.scheduled_notice import ScheduledNoticeStore
        if not ScheduledNoticeStore().due():
            log_event(task, "no_due — skip login")
            sys.stdout.write("[post_scheduled] 발송할 예약 없음\n")
            return 0

    # 1) 자격증명 + 로그인
    from green_auth.authenticator import AuthResult, Authenticator
    from green_auth.credentials import load_credentials
    creds = load_credentials()
    if creds is None:
        sys.stderr.write(
            "저장된 자격증명이 없습니다. 먼저 일반 모드로 한 번 로그인해 주세요.\n"
        )
        log_event(task, "abort no_credentials")
        return 1
    user_id, password = creds

    auth = Authenticator()
    result = auth.authenticate(user_id, password)
    if not result.is_success:
        sys.stderr.write(f"로그인 실패: {result.message}\n")
        log_event(task, f"abort login_failed status={result.status} msg={result.message}")
        return 1

    # 2) 권한 체크
    from core.permission import admin_permission_check
    ok, reason = admin_permission_check(auth.session, auth.user_id)
    if not ok:
        sys.stderr.write(f"권한 거부: {reason}\n")
        log_event(task, f"abort permission_denied reason={reason}")
        return 1

    # 3) 작업 실행
    try:
        res = _run_task_with_session(task, auth.session, auth.user_id)
    except Exception as e:
        tb = traceback.format_exc(limit=8)
        sys.stderr.write(f"작업 중 예외: {e}\n{tb}\n")
        log_event(task, f"abort exception err={e}")
        return 1

    # 결과 출력 (sys.stdout 으로 — schtasks 가 로그 파일로 캡처할 수 있도록).
    summary = f"[{res.task}] {res.message}"
    if res.targets:
        summary += f" (대상 {res.targets}명)"
    sys.stdout.write(summary + "\n")
    return 0 if res.success else 1
