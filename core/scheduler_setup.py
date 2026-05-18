"""Windows 작업 스케줄러 등록·해제·조회 로직 (v1.3.1).

`schtasks.exe` 의 얇은 래퍼. CLI(`tools/register_scheduler.py`) 와 UI
(`ui/scheduler_dialog.py`) 가 모두 같은 함수를 호출하도록 추출.

전제:
  · 관리자 권한 없이도 작업을 사용자 컨텍스트에 등록할 수 있다.
  · 자격증명은 미리 일반 모드 로그인으로 저장돼 있어야 한다 (green_auth).
  · 작업 이름은 `ChorokGreenAdmin_<task_key>` 한 가지 패턴.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


TASK_NAME_PREFIX = "ChorokGreenAdmin_"

# (task_key, schedule_type, modifier, start_time, 설명)
DEFAULT_SCHEDULES: dict[str, tuple[str, str, str, str]] = {
    "activity_nudge": ("MONTHLY", "1", "09:00",
                       "매월 1일 09:00 — green3 6개월 글 없음 안내"),
    "inactive_warning": ("MONTHLY", "15", "09:00",
                         "매월 15일 09:00 — 1년+ 미접속 사전 경고"),
    "expiry_remind_7": ("DAILY", "1", "09:00",
                        "매일 09:00 — 7일 후 만료자 알림"),
    "expiry_remind_3": ("DAILY", "1", "09:00",
                        "매일 09:00 — 3일 후 만료자 알림"),
}


@dataclass
class TaskStatus:
    """한 작업의 schtasks 등록 상태 한 줄."""
    task_key: str
    description: str
    registered: bool
    next_run: str = ""        # "2026-05-20 09:00:00" 또는 빈 문자열
    last_result: str = ""     # schtasks 의 'Last Result' 코드 텍스트
    raw_task_name: str = ""   # ChorokGreenAdmin_<task_key>


def _find_exe() -> Path:
    """배포된 EXE 위치 찾기. 개발 모드면 main.py 사용."""
    here = Path(__file__).resolve().parent.parent
    dist_exe = here / "dist" / "초록등대회원관리" / "초록등대회원관리.exe"
    if dist_exe.exists():
        return dist_exe
    for p in (
        Path(r"C:\Program Files\초록등대 회원관리\초록등대회원관리.exe"),
        Path(r"C:\Program Files (x86)\초록등대 회원관리\초록등대회원관리.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" /
        "초록등대 회원관리" / "초록등대회원관리.exe",
    ):
        if p.exists():
            return p
    return here / "main.py"


def build_command(task_key: str) -> str:
    """schtasks /TR 에 넣을 명령 문자열. EXE 가 있으면 EXE, 없으면 py + main.py."""
    target = _find_exe()
    if target.suffix.lower() == ".exe":
        return f'"{target}" --task {task_key}'
    py_launcher = Path(r"C:\Windows\py.exe")
    if py_launcher.exists():
        return f'"{py_launcher}" -3.14 "{target}" --task {task_key}'
    return f'"{sys.executable}" "{target}" --task {task_key}'


def task_name(task_key: str) -> str:
    return TASK_NAME_PREFIX + task_key


def register_task(task_key: str) -> tuple[bool, str]:
    """schtasks /Create 로 작업 등록. 반환: (성공, 메시지)."""
    if task_key not in DEFAULT_SCHEDULES:
        return False, f"지원하지 않는 작업: {task_key}"
    sch_type, modifier, start_time, _desc = DEFAULT_SCHEDULES[task_key]
    name = task_name(task_key)
    cmd_str = build_command(task_key)
    args = [
        "schtasks.exe", "/Create", "/F",
        "/TN", name,
        "/TR", cmd_str,
        "/SC", sch_type,
        "/ST", start_time,
    ]
    if sch_type == "MONTHLY":
        args += ["/D", modifier]
    r = subprocess.run(args, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or f"exit {r.returncode}").strip()
    return True, name


def unregister_task(task_key: str) -> tuple[bool, str]:
    """schtasks /Delete 로 작업 해제. 반환: (성공, 메시지)."""
    name = task_name(task_key)
    r = subprocess.run(
        ["schtasks.exe", "/Delete", "/F", "/TN", name],
        capture_output=True, text=True, errors="replace",
    )
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or f"exit {r.returncode}").strip()
    return True, name


def _parse_schtasks_query_block(block_text: str) -> dict:
    """schtasks /Query /FO LIST /V 한 블록의 key:value 를 dict 로."""
    fields: dict[str, str] = {}
    for line in block_text.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        fields[k.strip()] = v.strip()
    return fields


def query_status() -> list[TaskStatus]:
    """모든 알려진 작업의 등록 상태를 한 번에 조회.

    각 작업이 등록돼 있지 않으면 registered=False 로 채워서 항상 같은 길이
    리스트를 돌려준다 (UI 가 표시할 항목 수가 일정해지도록).
    schtasks 호출 자체가 실패하면 모두 registered=False.
    """
    out: dict[str, TaskStatus] = {
        key: TaskStatus(
            task_key=key,
            description=DEFAULT_SCHEDULES[key][3],
            registered=False,
            raw_task_name=task_name(key),
        )
        for key in DEFAULT_SCHEDULES
    }
    r = subprocess.run(
        ["schtasks.exe", "/Query", "/FO", "LIST", "/V"],
        capture_output=True, text=True, errors="replace",
    )
    if r.returncode != 0:
        return list(out.values())

    block: list[str] = []
    for line in r.stdout.splitlines():
        if line.strip():
            block.append(line)
            continue
        # 빈 줄 = 블록 경계
        if block:
            joined = "\n".join(block)
            if TASK_NAME_PREFIX in joined:
                fields = _parse_schtasks_query_block(joined)
                # TaskName: "\ChorokGreenAdmin_activity_nudge" 형태
                full_name = (
                    fields.get("TaskName")
                    or fields.get("HostName")  # 일부 환경
                    or ""
                )
                # \ChorokGreenAdmin_xxx 또는 ChorokGreenAdmin_xxx 둘 다.
                bare = full_name.lstrip("\\")
                if bare.startswith(TASK_NAME_PREFIX):
                    key = bare[len(TASK_NAME_PREFIX):]
                    if key in out:
                        st = out[key]
                        st.registered = True
                        st.next_run = (
                            fields.get("Next Run Time")
                            or fields.get("다음 실행 시간", "")
                        )
                        st.last_result = (
                            fields.get("Last Result")
                            or fields.get("마지막 결과", "")
                        )
            block = []
    return list(out.values())
