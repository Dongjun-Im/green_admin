"""core/scheduler_setup.py — schtasks 래퍼 (v1.3.1).

실제로 schtasks.exe 를 부르면 OS 상태가 바뀌므로, subprocess.run 을
monkeypatch 로 가짜화. 출력 텍스트만 구워서 파싱 로직을 검증.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _fake_run(returncode=0, stdout="", stderr=""):
    """subprocess.run 대체용 — completedprocess-like 객체 돌려준다."""
    def fn(*args, **kwargs):
        return SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr=stderr,
        )
    return fn


# ---------------------------------------------------------------------------
# 1. register_task / unregister_task — 인자 구성
# ---------------------------------------------------------------------------


def test_register_task_unknown_returns_failure(monkeypatch):
    from core.scheduler_setup import register_task
    ok, msg = register_task("nonsense")
    assert not ok
    assert "지원하지 않는" in msg


def test_register_task_calls_schtasks_with_correct_args(monkeypatch):
    """register_task 가 schtasks 에 /Create /TN ChorokGreenAdmin_<key> 같은 인자
    를 넘기는지."""
    from core import scheduler_setup
    captured: dict = {}
    def fake(*args, **kwargs):
        captured["args"] = args[0]
        return SimpleNamespace(returncode=0, stdout="OK", stderr="")
    monkeypatch.setattr(scheduler_setup.subprocess, "run", fake)
    ok, msg = scheduler_setup.register_task("expiry_remind_7")
    assert ok
    cli = captured["args"]
    assert cli[0] == "schtasks.exe"
    assert "/Create" in cli
    assert "ChorokGreenAdmin_expiry_remind_7" in cli
    # DAILY 작업이므로 /D 인자는 없어야 함
    assert "/D" not in cli


def test_register_monthly_task_includes_day_modifier(monkeypatch):
    from core import scheduler_setup
    captured = {}
    def fake(*args, **kwargs):
        captured["args"] = args[0]
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(scheduler_setup.subprocess, "run", fake)
    scheduler_setup.register_task("activity_nudge")
    cli = captured["args"]
    assert "/D" in cli
    # 매월 1일
    idx = cli.index("/D")
    assert cli[idx + 1] == "1"


def test_register_task_failure_propagates(monkeypatch):
    from core import scheduler_setup
    monkeypatch.setattr(
        scheduler_setup.subprocess, "run",
        _fake_run(returncode=1, stderr="액세스 거부됨"),
    )
    ok, msg = scheduler_setup.register_task("expiry_remind_3")
    assert not ok
    assert "액세스" in msg


def test_unregister_task_calls_schtasks_delete(monkeypatch):
    from core import scheduler_setup
    captured = {}
    def fake(*args, **kwargs):
        captured["args"] = args[0]
        return SimpleNamespace(returncode=0, stdout="OK", stderr="")
    monkeypatch.setattr(scheduler_setup.subprocess, "run", fake)
    ok, msg = scheduler_setup.unregister_task("activity_nudge")
    assert ok
    cli = captured["args"]
    assert "/Delete" in cli
    assert "ChorokGreenAdmin_activity_nudge" in cli


# ---------------------------------------------------------------------------
# 2. query_status — schtasks /Query 출력 파싱
# ---------------------------------------------------------------------------


def _sample_query_output() -> str:
    """schtasks /Query /FO LIST /V 의 한 블록 샘플 — 두 작업만 등록된 상태."""
    return """
HostName:                             MYPC
TaskName:                             \\ChorokGreenAdmin_expiry_remind_7
Next Run Time:                        2026-05-20 09:00:00
Status:                               Ready
Logon Mode:                           Interactive only
Last Run Time:                        2026-05-19 09:00:00
Last Result:                          0
Author:                               MYPC\\User
Task To Run:                          \"C:\\Program Files\\초록등대 회원관리\\초록등대회원관리.exe\" --task expiry_remind_7

HostName:                             MYPC
TaskName:                             \\ChorokGreenAdmin_activity_nudge
Next Run Time:                        2026-06-01 09:00:00
Status:                               Ready
Logon Mode:                           Interactive only
Last Run Time:                        N/A
Last Result:                          267011
Author:                               MYPC\\User
Task To Run:                          \"C:\\Program Files\\초록등대 회원관리\\초록등대회원관리.exe\" --task activity_nudge

HostName:                             MYPC
TaskName:                             \\SomeOtherTask
Next Run Time:                        2026-12-01 00:00:00
Status:                               Ready

"""


def test_query_status_returns_all_known_tasks_with_two_registered(monkeypatch):
    from core import scheduler_setup
    monkeypatch.setattr(
        scheduler_setup.subprocess, "run",
        _fake_run(returncode=0, stdout=_sample_query_output()),
    )
    statuses = scheduler_setup.query_status()
    # 네 작업이 항상 모두 들어 있어야 함 (등록 안 된 것도 registered=False 로)
    keys = {s.task_key for s in statuses}
    assert keys == {
        "activity_nudge", "inactive_warning",
        "expiry_remind_7", "expiry_remind_3",
    }
    by_key = {s.task_key: s for s in statuses}
    assert by_key["expiry_remind_7"].registered is True
    assert by_key["expiry_remind_7"].next_run == "2026-05-20 09:00:00"
    assert by_key["expiry_remind_7"].last_result == "0"
    assert by_key["activity_nudge"].registered is True
    assert by_key["inactive_warning"].registered is False
    assert by_key["expiry_remind_3"].registered is False


def test_query_status_handles_schtasks_failure(monkeypatch):
    """schtasks 가 실패해도 네 작업 모두 registered=False 로 채워서 반환."""
    from core import scheduler_setup
    monkeypatch.setattr(
        scheduler_setup.subprocess, "run",
        _fake_run(returncode=1, stderr="작업 스케줄러 서비스를 사용할 수 없음"),
    )
    statuses = scheduler_setup.query_status()
    assert len(statuses) == 4
    assert all(not s.registered for s in statuses)


def test_query_status_ignores_unrelated_tasks(monkeypatch):
    """샘플에 'SomeOtherTask' 가 있었는데 결과에 안 섞여야 함."""
    from core import scheduler_setup
    monkeypatch.setattr(
        scheduler_setup.subprocess, "run",
        _fake_run(returncode=0, stdout=_sample_query_output()),
    )
    statuses = scheduler_setup.query_status()
    keys = {s.task_key for s in statuses}
    assert "SomeOtherTask" not in keys


# ---------------------------------------------------------------------------
# 3. build_command / task_name 헬퍼
# ---------------------------------------------------------------------------


def test_task_name_prefix():
    from core.scheduler_setup import TASK_NAME_PREFIX, task_name
    assert task_name("activity_nudge") == TASK_NAME_PREFIX + "activity_nudge"


def test_build_command_contains_task_key(monkeypatch):
    from core.scheduler_setup import build_command
    cmd = build_command("expiry_remind_7")
    assert "--task expiry_remind_7" in cmd


# ---------------------------------------------------------------------------
# 4. DEFAULT_SCHEDULES 키 일관성 (scheduler_runner.ALL_TASKS 와 일치)
# ---------------------------------------------------------------------------


def test_default_schedules_match_runner_all_tasks():
    from core.scheduler_runner import ALL_TASKS
    from core.scheduler_setup import DEFAULT_SCHEDULES
    assert set(DEFAULT_SCHEDULES.keys()) == set(ALL_TASKS), (
        "scheduler_setup.DEFAULT_SCHEDULES 와 scheduler_runner.ALL_TASKS 가 "
        "어긋남 — 한 쪽에만 작업 추가됐을 가능성"
    )
