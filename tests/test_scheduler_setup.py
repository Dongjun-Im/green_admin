"""schtasks 출력 파싱 + 작업당 개별 조회 (v1.3.2 버그 수정).

이전엔 전체 schtasks /Query 출력을 파싱했는데 한국어 Windows 에서 'TaskName'
필드 영문 표기를 못 찾아 등록된 작업도 미등록(.) 으로 보였다. v1.3.2 에서
작업당 /TN 개별 조회로 바꾸고 한국어 필드명 별칭(다음 실행 시간 / 마지막 결과)
을 추가. 이 테스트는 두 변경을 회귀 보호.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# 한국어 Windows schtasks /Query /TN <name> /FO LIST /V 가 내는 출력 샘플.
_KOREAN_OUTPUT = """
폴더: \\

호스트 이름:                            MYPC
작업 이름:                              \\ChorokGreenAdmin_activity_nudge
다음 실행 시간:                         2026-06-01 9:00:00
상태:                                   준비
로그온 모드:                            대화형/백그라운드
마지막 실행 시간:                       2026-05-01 9:00:00
마지막 결과:                            0
작성자:                                 MYPC\\user
""".strip()

# 영문 Windows 출력 샘플.
_ENGLISH_OUTPUT = """
Folder: \\

HostName:                             MYPC
TaskName:                             \\ChorokGreenAdmin_expiry_remind_7
Next Run Time:                        5/20/2026 9:00:00 AM
Status:                               Ready
Logon Mode:                           Interactive/Background
Last Run Time:                        5/19/2026 9:00:00 AM
Last Result:                          0
""".strip()


def test_parse_korean_schtasks_extracts_next_run_and_last_result():
    from core.scheduler_setup import _parse_schtasks_query_block, _lookup
    from core.scheduler_setup import _FIELD_LAST_RESULT_KEYS, _FIELD_NEXT_RUN_KEYS

    fields = _parse_schtasks_query_block(_KOREAN_OUTPUT)
    assert _lookup(fields, _FIELD_NEXT_RUN_KEYS) == "2026-06-01 9:00:00"
    assert _lookup(fields, _FIELD_LAST_RESULT_KEYS) == "0"


def test_parse_english_schtasks_also_works():
    from core.scheduler_setup import _parse_schtasks_query_block, _lookup
    from core.scheduler_setup import _FIELD_LAST_RESULT_KEYS, _FIELD_NEXT_RUN_KEYS

    fields = _parse_schtasks_query_block(_ENGLISH_OUTPUT)
    assert _lookup(fields, _FIELD_NEXT_RUN_KEYS) == "5/20/2026 9:00:00 AM"
    assert _lookup(fields, _FIELD_LAST_RESULT_KEYS) == "0"


def test_parse_handles_korean_fullwidth_colon():
    """한국어 IME 가 전각 콜론 'ＴａｓｋＮａｍｅ：' 를 섞어 넣을 때 회귀."""
    from core.scheduler_setup import _parse_schtasks_query_block, _lookup
    from core.scheduler_setup import _FIELD_NEXT_RUN_KEYS

    fragment = "다음 실행 시간：    2026-07-15 9:00:00"
    fields = _parse_schtasks_query_block(fragment)
    assert _lookup(fields, _FIELD_NEXT_RUN_KEYS) == "2026-07-15 9:00:00"


# ---------------------------------------------------------------------------
# _query_one 통합 — subprocess.run 만 monkeypatch
# ---------------------------------------------------------------------------


def _fake_completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    # MagicMock 의 자동 속성은 truthy 라 'r.stderr or r.stdout' 분기를 망친다.
    # 명시적으로 세 필드를 다 설정.
    cp = MagicMock()
    cp.stdout = stdout
    cp.stderr = stderr
    cp.returncode = returncode
    return cp


def test_query_one_registered_korean(monkeypatch):
    """schtasks 가 exit 0 + 한국어 필드를 돌려주면 registered=True 로 채워야 함."""
    from core import scheduler_setup
    monkeypatch.setattr(
        scheduler_setup.subprocess, "run",
        lambda *a, **kw: _fake_completed(_KOREAN_OUTPUT, 0),
    )
    st = scheduler_setup._query_one("activity_nudge")
    assert st.registered is True
    assert st.task_key == "activity_nudge"
    assert st.raw_task_name == "ChorokGreenAdmin_activity_nudge"
    assert st.next_run == "2026-06-01 9:00:00"
    assert st.last_result == "0"


def test_query_one_not_registered_returns_default(monkeypatch):
    """schtasks 가 exit 1 ('찾을 수 없음') 이면 registered=False."""
    from core import scheduler_setup
    monkeypatch.setattr(
        scheduler_setup.subprocess, "run",
        lambda *a, **kw: _fake_completed(
            "오류: 지정된 파일을 찾을 수 없습니다.\n", returncode=1,
        ),
    )
    st = scheduler_setup._query_one("activity_nudge")
    assert st.registered is False
    assert st.next_run == ""
    assert st.last_result == ""


def test_query_one_subprocess_oserror_returns_default(monkeypatch):
    """schtasks.exe 자체를 못 찾는 환경 (예: Wine, 비-Windows) — registered=False."""
    from core import scheduler_setup
    def raises(*a, **kw):
        raise OSError("not found")
    monkeypatch.setattr(scheduler_setup.subprocess, "run", raises)
    st = scheduler_setup._query_one("activity_nudge")
    assert st.registered is False


# ---------------------------------------------------------------------------
# query_status — 작업당 한 번씩 호출
# ---------------------------------------------------------------------------


def test_query_status_calls_one_per_task(monkeypatch):
    """등록된 2개 + 미등록 2개가 섞여 있어도 정확히 분류."""
    from core import scheduler_setup

    # activity_nudge / expiry_remind_7 만 등록됐다고 가정.
    registered_keys = {"activity_nudge", "expiry_remind_7"}

    def fake_run(args, **kw):
        # args = ["schtasks.exe", "/Query", "/TN", "ChorokGreenAdmin_<key>", ...]
        # /TN 다음 인자가 작업 이름.
        name = args[args.index("/TN") + 1]
        key = name.replace(scheduler_setup.TASK_NAME_PREFIX, "")
        if key in registered_keys:
            return _fake_completed(
                _KOREAN_OUTPUT.replace(
                    "ChorokGreenAdmin_activity_nudge", name,
                ), 0,
            )
        return _fake_completed("error\n", 1)

    monkeypatch.setattr(scheduler_setup.subprocess, "run", fake_run)
    statuses = scheduler_setup.query_status()
    assert len(statuses) == len(scheduler_setup.DEFAULT_SCHEDULES)
    by_key = {s.task_key: s for s in statuses}
    assert by_key["activity_nudge"].registered is True
    assert by_key["expiry_remind_7"].registered is True
    assert by_key["inactive_warning"].registered is False
    assert by_key["expiry_remind_3"].registered is False


# ---------------------------------------------------------------------------
# register_task / unregister_task — schtasks 인자 구성 회귀
# ---------------------------------------------------------------------------


def test_register_task_builds_monthly_args(monkeypatch):
    """MONTHLY 작업은 /SC MONTHLY + /D <day> 가 들어가야 함."""
    from core import scheduler_setup
    captured = {}
    def capture(args, **kw):
        captured["args"] = args
        return _fake_completed("성공\n", 0)
    monkeypatch.setattr(scheduler_setup.subprocess, "run", capture)
    ok, msg = scheduler_setup.register_task("activity_nudge")
    assert ok is True
    args = captured["args"]
    assert "/Create" in args
    assert "/F" in args
    assert "/SC" in args and "MONTHLY" in args
    assert "/D" in args and "1" in args  # 매월 1일
    assert "/ST" in args and "09:00" in args
    assert "/TN" in args
    tn_idx = args.index("/TN")
    assert args[tn_idx + 1] == "ChorokGreenAdmin_activity_nudge"


def test_register_task_builds_daily_args_without_modifier(monkeypatch):
    """DAILY 작업은 /SC DAILY 만 들어가고 /D 는 빠져야 함."""
    from core import scheduler_setup
    captured = {}
    def fake(args, **kw):
        captured["args"] = args
        return _fake_completed("성공\n", 0)
    monkeypatch.setattr(scheduler_setup.subprocess, "run", fake)
    scheduler_setup.register_task("expiry_remind_7")
    args = captured["args"]
    assert "DAILY" in args
    # /D 는 MONTHLY 전용 — DAILY 에는 없어야 함
    assert "/D" not in args


def test_register_unknown_task_rejected(monkeypatch):
    from core import scheduler_setup
    # subprocess.run 이 호출되면 안 됨 — 호출되면 예외로 잡힘
    monkeypatch.setattr(
        scheduler_setup.subprocess, "run",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    ok, msg = scheduler_setup.register_task("nonsense")
    assert ok is False
    assert "지원하지 않는" in msg


def test_register_propagates_schtasks_failure(monkeypatch):
    from core import scheduler_setup
    monkeypatch.setattr(
        scheduler_setup.subprocess, "run",
        lambda *a, **kw: _fake_completed(
            "", returncode=2, stderr="ERROR: access denied\n",
        ),
    )
    ok, msg = scheduler_setup.register_task("activity_nudge")
    assert ok is False
    assert "access denied" in msg.lower() or "error" in msg.lower()


def test_unregister_task_calls_delete(monkeypatch):
    from core import scheduler_setup
    captured = {}
    def fake(args, **kw):
        captured["args"] = args
        return _fake_completed("성공\n", 0)
    monkeypatch.setattr(scheduler_setup.subprocess, "run", fake)
    ok, _ = scheduler_setup.unregister_task("activity_nudge")
    assert ok is True
    args = captured["args"]
    assert "/Delete" in args
    assert "/F" in args
    tn_idx = args.index("/TN")
    assert args[tn_idx + 1] == "ChorokGreenAdmin_activity_nudge"


# ---------------------------------------------------------------------------
# DEFAULT_SCHEDULES 와 scheduler_runner.ALL_TASKS 일관성
# ---------------------------------------------------------------------------


def test_default_schedules_matches_all_tasks():
    """새 작업을 한 곳에만 추가하고 다른 곳에 빠뜨리면 사용자가 등록·실행 불일치를
    겪는다. 두 곳의 키 셋이 정확히 같은지 확인."""
    from core.scheduler_runner import ALL_TASKS
    from core.scheduler_setup import DEFAULT_SCHEDULES
    assert set(DEFAULT_SCHEDULES.keys()) == set(ALL_TASKS)
