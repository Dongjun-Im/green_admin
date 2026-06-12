"""헤드리스 작업 실행기 — 디스패치 + 결과 처리 (v1.2.11).

실제 HTTP 호출은 없고, fake 크롤러 + fake mail_sender + 임시 history 로
디스패치 로직과 결과 집계만 검증.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.models import Member


def _make_member(uid, level=5, *, last_login=None, is_admin=False):
    return Member(
        user_id=uid, name=uid.upper(), nickname=f"닉_{uid}",
        level=level, level_label="", last_login_date=last_login,
        is_admin=is_admin,
    )


def _patch_data_dir(monkeypatch, tmp_path):
    """scheduler_runner 의 DATA_DIR 가 tmp 폴더를 가리키도록 — 테스트가
    실제 data/ 폴더를 안 건드리도록."""
    monkeypatch.setattr("core.scheduler_runner.DATA_DIR", str(tmp_path))
    # NudgeHistoryStore 도 같은 tmp 폴더에 저장됨 (_build_services 가 DATA_DIR 사용).
    return tmp_path


# ---------------------------------------------------------------------------
# 1. 미지원 작업 키
# ---------------------------------------------------------------------------


def test_run_task_unknown_returns_failure(monkeypatch, tmp_path):
    _patch_data_dir(monkeypatch, tmp_path)
    from core.scheduler_runner import _run_task_with_session
    fake_session = MagicMock()
    res = _run_task_with_session("nonsense", fake_session, "rtgreen")
    assert not res.success
    assert "지원하지 않는" in res.message


# ---------------------------------------------------------------------------
# 2. 대상이 없으면 success + targets=0
# ---------------------------------------------------------------------------


def test_activity_nudge_no_targets(monkeypatch, tmp_path):
    _patch_data_dir(monkeypatch, tmp_path)
    from core.scheduler_runner import TASK_ACTIVITY_NUDGE, _run_task_with_session

    # crawler: 회원 0명 — 따라서 대상도 0명
    def fake_crawler_factory(session, url, parser=None):
        c = MagicMock()
        c.session = session
        c.fetch_all_members = lambda: []
        return c

    # mail_sender: 만들어지긴 하지만 호출되지 않을 거임
    monkeypatch.setattr("core.scheduler_runner.MemberCrawler", fake_crawler_factory)
    # MailSender.enabled 는 어떻게 되든 무관 (대상 0명이라 send 호출 자체가 없음)

    res = _run_task_with_session(
        TASK_ACTIVITY_NUDGE, session=MagicMock(), user_id="rtgreen",
    )
    assert res.success
    assert res.targets == 0
    assert res.message == "대상 없음"


# ---------------------------------------------------------------------------
# 3. 장기미접속 경고 — 대상 있음 시 메일 발송 + history 기록
# ---------------------------------------------------------------------------


def test_inactive_warning_sends_and_records(monkeypatch, tmp_path):
    history_path = _patch_data_dir(monkeypatch, tmp_path) / "nudge_history.json"
    from core.mail_sender import MailResult
    from core.scheduler_runner import TASK_INACTIVE_WARNING, _run_task_with_session

    today = date.today()
    one_year_ago = today - timedelta(days=370)

    # crawler: 1년+ 미접속 회원 2명
    def fake_crawler_factory(session, url, parser=None):
        c = MagicMock()
        c.session = session
        c.fetch_all_members = lambda: [
            _make_member("u1", 5, last_login=one_year_ago),
            _make_member("u2", 5, last_login=one_year_ago),
        ]
        return c

    monkeypatch.setattr("core.scheduler_runner.MemberCrawler", fake_crawler_factory)

    # mail_sender: 두 회원 모두 성공
    sent_calls: list[list[str]] = []
    def fake_sender(session, user_id):
        s = MagicMock()
        s.enabled = True
        def send(recipients, subject, body, mode=None):
            sent_calls.append(list(recipients))
            return [MailResult(success=True, message="ok",
                               recipients=list(recipients))]
        s.send = send
        return s
    monkeypatch.setattr("core.scheduler_runner.MailSender", fake_sender)

    res = _run_task_with_session(
        TASK_INACTIVE_WARNING, session=MagicMock(), user_id="rtgreen",
    )
    assert res.success
    assert res.targets == 2
    assert res.sent == 2
    assert res.failed == 0
    # 두 회원 모두 한 번씩 발송 호출됨
    sent_uids = {c[0] for c in sent_calls}
    assert sent_uids == {"u1", "u2"}
    # history 가 디스크에 기록됨
    assert history_path.exists()


# ---------------------------------------------------------------------------
# 4. mail_sender 가 disabled 면 모두 skipped 로 집계, 성공으로 처리
# ---------------------------------------------------------------------------


def test_inactive_warning_sender_disabled_counts_skipped(monkeypatch, tmp_path):
    _patch_data_dir(monkeypatch, tmp_path)
    from core.mail_sender import MailResult
    from core.scheduler_runner import TASK_INACTIVE_WARNING, _run_task_with_session

    today = date.today()
    one_year_ago = today - timedelta(days=370)

    def fake_crawler_factory(session, url, parser=None):
        c = MagicMock()
        c.session = session
        c.fetch_all_members = lambda: [_make_member("u", 5, last_login=one_year_ago)]
        return c
    monkeypatch.setattr("core.scheduler_runner.MemberCrawler", fake_crawler_factory)

    def fake_sender(session, user_id):
        s = MagicMock()
        s.enabled = False
        s.send = lambda recipients, subject, body, mode=None: [
            MailResult(skipped=True, message="rtgreen 아님",
                       recipients=list(recipients))
        ]
        return s
    monkeypatch.setattr("core.scheduler_runner.MailSender", fake_sender)

    res = _run_task_with_session(
        TASK_INACTIVE_WARNING, session=MagicMock(), user_id="other",
    )
    # 작업은 끝까지 돌고 success (대상도 있었고 시도도 했음)
    assert res.success
    assert res.targets == 1
    assert res.sent == 0
    assert res.skipped == 1


# ---------------------------------------------------------------------------
# 5. log_event 가 파일에 줄을 추가한다
# ---------------------------------------------------------------------------


def test_log_event_appends_line(monkeypatch, tmp_path):
    _patch_data_dir(monkeypatch, tmp_path)
    from core.scheduler_runner import log_event, _log_path

    log_event("activity_nudge", "test_line_one")
    log_event("activity_nudge", "test_line_two")
    path = _log_path("activity_nudge")
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "test_line_one" in text
    assert "test_line_two" in text


# ---------------------------------------------------------------------------
# 6. 작업 키 상수 일관성
# ---------------------------------------------------------------------------


def test_all_tasks_constant():
    from core.scheduler_runner import (
        ALL_TASKS,
        TASK_ACTIVITY_NUDGE,
        TASK_EXPIRY_REMIND_3,
        TASK_EXPIRY_REMIND_7,
        TASK_INACTIVE_WARNING,
        TASK_POST_SCHEDULED,
    )
    assert set(ALL_TASKS) == {
        TASK_ACTIVITY_NUDGE, TASK_INACTIVE_WARNING,
        TASK_EXPIRY_REMIND_7, TASK_EXPIRY_REMIND_3,
        TASK_POST_SCHEDULED,
    }
    # 5개 — 새 작업 추가하면 이 테스트도 같이 갱신해야 함 (의도된 회귀 보호).
    assert len(ALL_TASKS) == 5


# ---------------------------------------------------------------------------
# 6b. 예약 공지 발송 (post_scheduled)
# ---------------------------------------------------------------------------


def _patch_notice_store(monkeypatch, tmp_path):
    """ScheduledNoticeStore 기본 경로를 tmp 로 — 실제 data/ 안 건드림."""
    path = tmp_path / "scheduled_notices.json"
    monkeypatch.setattr("core.scheduled_notice.SCHEDULED_NOTICES_FILE", str(path))
    return path


def test_post_scheduled_no_due_is_success(monkeypatch, tmp_path):
    _patch_data_dir(monkeypatch, tmp_path)
    _patch_notice_store(monkeypatch, tmp_path)
    from core.scheduler_runner import TASK_POST_SCHEDULED, _run_task_with_session

    res = _run_task_with_session(
        TASK_POST_SCHEDULED, session=MagicMock(), user_id="rtgreen",
    )
    assert res.success
    assert res.targets == 0
    assert "예약 없음" in res.message


def test_post_scheduled_posts_due_and_marks(monkeypatch, tmp_path):
    _patch_data_dir(monkeypatch, tmp_path)
    _patch_notice_store(monkeypatch, tmp_path)
    from datetime import datetime
    from core.scheduled_notice import (
        STATUS_POSTED,
        ScheduledNotice,
        ScheduledNoticeStore,
    )
    from core.scheduler_runner import TASK_POST_SCHEDULED, _run_task_with_session

    # 1분 전으로 예약된(=도래한) 공지 한 건.
    past = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")
    store = ScheduledNoticeStore()
    store.add(ScheduledNotice(
        scheduled_at=past, boards=["green1", "green3"],
        subject="테스트 공지", content="본문",
    ))

    calls: list[tuple] = []

    def fake_post(session, boards, subject, content, *, as_notice=True, use_html=False):
        calls.append((tuple(boards), subject))
        return [
            SimpleNamespace(ok=True, bo_table=b, message="등록됨") for b in boards
        ]
    monkeypatch.setattr("core.board_admin.post_notice_to_boards", fake_post)

    res = _run_task_with_session(
        TASK_POST_SCHEDULED, session=MagicMock(), user_id="rtgreen",
    )
    assert res.success
    assert res.targets == 1
    assert res.sent == 1
    assert res.failed == 0
    assert calls and calls[0][0] == ("green1", "green3")
    # 디스크에 posted 로 기록됐는지 다시 로드해 확인.
    reloaded = ScheduledNoticeStore().all()
    assert len(reloaded) == 1
    assert reloaded[0].status == STATUS_POSTED


# ---------------------------------------------------------------------------
# 6c. 창 모드 EXE 회귀 — sys.stdout/stderr 가 None 이어도 죽지 않아야 함
# ---------------------------------------------------------------------------


def test_run_task_post_scheduled_no_due_with_none_streams(monkeypatch, tmp_path):
    """console=False 빌드(창 모드 EXE)는 sys.stdout/stderr 가 None.
    예약 도래분 없을 때 run_task 가 None.write 로 죽지 않고 0 을 반환해야 함."""
    import sys
    _patch_data_dir(monkeypatch, tmp_path)
    _patch_notice_store(monkeypatch, tmp_path)  # 빈 큐
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    from core.scheduler_runner import run_task

    # AttributeError('NoneType' object has no attribute 'write') 가 나면 실패.
    assert run_task("post_scheduled") == 0


def test_run_task_unknown_with_none_stderr(monkeypatch):
    """미지원 작업 키 경로의 sys.stderr.write 도 None 안전해야 함."""
    import sys
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    from core.scheduler_runner import run_task

    assert run_task("nonsense_task") == 1


# ---------------------------------------------------------------------------
# 7. main.py 의 --task 인자 파싱
# ---------------------------------------------------------------------------


def test_main_parse_args_accepts_valid_task():
    """main.py 가 ALL_TASKS 의 키를 받아들이는지."""
    import main as main_module
    args = main_module._parse_args(["--task", "activity_nudge"])
    assert args.task == "activity_nudge"


def test_main_parse_args_rejects_invalid_task():
    """잘못된 task 이름이면 argparse 가 SystemExit 로 종료."""
    import main as main_module
    with pytest.raises(SystemExit):
        main_module._parse_args(["--task", "nonsense"])


def test_main_parse_args_no_task_is_ui_mode():
    import main as main_module
    args = main_module._parse_args([])
    assert args.task is None
