"""Windows 작업 스케줄러에 헤드리스 작업을 등록·해제하는 헬퍼 (v1.2.11).

`schtasks.exe` 를 호출해 작업을 등록한다. 관리자 권한 없이도 사용자 컨텍스트
에서 등록 가능.

지원하는 작업과 기본 권장 시각 (사용자가 바꿀 수 있음):
  · activity_nudge    — 매월 1일 09:00  (월 1회 활동 안내)
  · inactive_warning  — 매월 15일 09:00 (월 1회 장기미접속 경고)
  · expiry_remind_7   — 매일 09:00      (그날 정확히 7일 후 만료자 알림)
  · expiry_remind_3   — 매일 09:00      (그날 정확히 3일 후 만료자 알림)

사용법:
    py -3.14 tools/register_scheduler.py register --task expiry_remind_7
    py -3.14 tools/register_scheduler.py register --task all
    py -3.14 tools/register_scheduler.py unregister --task expiry_remind_7
    py -3.14 tools/register_scheduler.py list

⚠ 미리 한 번 일반 모드로 로그인해서 credentials.ini 가 만들어져 있어야 함.
⚠ rtgreen 아이디 자격증명이 아니면 메일 발송이 자동 스킵됨 (정상 동작).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


TASK_NAME_PREFIX = "ChorokGreenAdmin_"

# (task_key, schedule_type, modifier, start_time, 설명)
DEFAULT_SCHEDULES = {
    "activity_nudge": ("MONTHLY", "1", "09:00",
                       "매월 1일 — green3 6개월 글 없음 안내"),
    "inactive_warning": ("MONTHLY", "15", "09:00",
                         "매월 15일 — 1년+ 미접속 사전 경고"),
    "expiry_remind_7": ("DAILY", "1", "09:00",
                        "매일 — 7일 후 만료자 알림"),
    "expiry_remind_3": ("DAILY", "1", "09:00",
                        "매일 — 3일 후 만료자 알림"),
}


def _find_exe() -> Path:
    """배포된 EXE 위치 찾기. 개발 모드면 main.py + python 경로 사용."""
    here = Path(__file__).resolve().parent.parent
    # PyInstaller 빌드 후 dist 폴더
    dist_exe = here / "dist" / "초록등대회원관리" / "초록등대회원관리.exe"
    if dist_exe.exists():
        return dist_exe
    # 설치된 위치 (Program Files)
    for p in (
        Path(r"C:\Program Files\초록등대 회원관리\초록등대회원관리.exe"),
        Path(r"C:\Program Files (x86)\초록등대 회원관리\초록등대회원관리.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" /
        "초록등대 회원관리" / "초록등대회원관리.exe",
    ):
        if p.exists():
            return p
    # 개발 모드 — main.py 를 python 으로 실행하는 명령 구성
    return here / "main.py"


def _build_command(task_key: str) -> str:
    """schtasks /TR 에 넣을 명령 문자열. EXE 가 있으면 EXE, 없으면 py + main.py."""
    target = _find_exe()
    if target.suffix.lower() == ".exe":
        return f'"{target}" --task {task_key}'
    # 개발 모드 — 어떤 python 으로 실행할지 결정.
    # py launcher 가 있으면 그걸 우선 (대부분 Windows 에 깔려 있음).
    py_launcher = Path(r"C:\Windows\py.exe")
    if py_launcher.exists():
        return f'"{py_launcher}" -3.14 "{target}" --task {task_key}'
    # 최후의 수단 — 현재 python.
    return f'"{sys.executable}" "{target}" --task {task_key}'


def _task_name(task_key: str) -> str:
    return TASK_NAME_PREFIX + task_key


def register_one(task_key: str) -> int:
    if task_key not in DEFAULT_SCHEDULES:
        sys.stderr.write(f"지원하지 않는 작업: {task_key}\n")
        return 1
    sch_type, modifier, start_time, desc = DEFAULT_SCHEDULES[task_key]
    name = _task_name(task_key)
    cmd_str = _build_command(task_key)

    # /F 는 기존 같은 이름 작업이 있으면 덮어쓰기.
    args = [
        "schtasks.exe", "/Create", "/F",
        "/TN", name,
        "/TR", cmd_str,
        "/SC", sch_type,
        "/ST", start_time,
    ]
    if sch_type == "MONTHLY":
        args += ["/D", modifier]   # 매월 며칠
    # DAILY 는 modifier 기본값(1) 이라 따로 안 넘김.

    print(f"[등록] {name}")
    print(f"  실행: {cmd_str}")
    print(f"  주기: {desc}")
    r = subprocess.run(args, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        sys.stderr.write(f"  실패: {r.stderr.strip()}\n")
        return r.returncode
    print(f"  OK")
    return 0


def unregister_one(task_key: str) -> int:
    name = _task_name(task_key)
    args = ["schtasks.exe", "/Delete", "/F", "/TN", name]
    print(f"[해제] {name}")
    r = subprocess.run(args, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        # 이미 없는 작업이면 schtasks 가 ERROR 코드를 주지만 무시 가능.
        sys.stderr.write(f"  실패: {r.stderr.strip()}\n")
        return r.returncode
    print("  OK")
    return 0


def list_tasks() -> int:
    args = ["schtasks.exe", "/Query", "/FO", "LIST", "/V"]
    r = subprocess.run(args, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        sys.stderr.write(f"조회 실패: {r.stderr}\n")
        return r.returncode
    found_any = False
    block: list[str] = []
    for line in r.stdout.splitlines():
        if line.strip():
            block.append(line)
        else:
            joined = "\n".join(block)
            if TASK_NAME_PREFIX in joined:
                print(joined)
                print("-" * 40)
                found_any = True
            block = []
    if not found_any:
        print(f"등록된 '{TASK_NAME_PREFIX}*' 작업이 없습니다.")
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
        return list_tasks()

    targets = (
        list(DEFAULT_SCHEDULES.keys()) if args.task == "all" else [args.task]
    )
    fn = register_one if args.cmd == "register" else unregister_one
    rc = 0
    for t in targets:
        rc = max(rc, fn(t))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
