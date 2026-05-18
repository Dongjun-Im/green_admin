"""Windows 작업 스케줄러 등록·해제·조회 CLI (v1.2.11+).

실제 로직은 `core/scheduler_setup.py` 에. 이 파일은 argparse 진입점만.

사용법:
    py -3.14 tools/register_scheduler.py register --task expiry_remind_7
    py -3.14 tools/register_scheduler.py register --task all
    py -3.14 tools/register_scheduler.py unregister --task expiry_remind_7
    py -3.14 tools/register_scheduler.py list

⚠ 미리 한 번 일반 모드로 로그인해서 credentials.ini 가 만들어져 있어야 함.
⚠ rtgreen 아이디 자격증명이 아니면 메일 발송이 자동 스킵됨 (정상 동작).

GUI 로 같은 일을 하려면: 작업 메뉴 → '자동 스케줄러 관리...'.
"""
from __future__ import annotations

import argparse
import sys

from core.scheduler_setup import (
    DEFAULT_SCHEDULES,
    query_status,
    register_task,
    unregister_task,
)


def _register_one(task_key: str) -> int:
    sch_type, modifier, start_time, desc = DEFAULT_SCHEDULES[task_key]
    print(f"[등록] {task_key} — {desc}")
    ok, msg = register_task(task_key)
    if not ok:
        sys.stderr.write(f"  실패: {msg}\n")
        return 1
    print(f"  OK ({msg})")
    return 0


def _unregister_one(task_key: str) -> int:
    print(f"[해제] {task_key}")
    ok, msg = unregister_task(task_key)
    if not ok:
        sys.stderr.write(f"  실패: {msg}\n")
        return 1
    print(f"  OK ({msg})")
    return 0


def _list_status() -> int:
    statuses = query_status()
    if not statuses:
        print("등록된 ChorokGreenAdmin_* 작업이 없습니다.")
        return 0
    for st in statuses:
        mark = "✓" if st.registered else "·"
        print(f"  [{mark}] {st.task_key:18s}  {st.description}")
        if st.registered:
            if st.next_run:
                print(f"       다음 실행: {st.next_run}")
            if st.last_result:
                print(f"       마지막 결과: {st.last_result}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="register_scheduler",
        description="초록등대 회원관리 — Windows 작업 스케줄러 등록 헬퍼.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    for verb in ("register", "unregister"):
        sp = sub.add_parser(verb)
        sp.add_argument(
            "--task",
            choices=list(DEFAULT_SCHEDULES.keys()) + ["all"],
            required=True,
            help="작업 키 또는 'all' (모두).",
        )
    sub.add_parser("list", help="등록된 ChorokGreenAdmin_* 작업 보기")

    args = parser.parse_args(argv)
    if args.cmd == "list":
        return _list_status()

    targets = (
        list(DEFAULT_SCHEDULES.keys()) if args.task == "all" else [args.task]
    )
    fn = _register_one if args.cmd == "register" else _unregister_one
    rc = 0
    for t in targets:
        rc = max(rc, fn(t))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
