"""자동 업데이트의 무인(silent) 설치 플래그 (v1.2.9).

main_frame.py 의 on_install_update 가 Inno Setup 설치관리자를 실행할 때 쓰는
플래그를 모듈 상수로 두어 테스트로 박제. installer.iss 의 [Run] 항목에서
skipifsilent 가 빠져 있는지도 함께 검증.
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import pytest


# main_frame import 는 wx 가 필요해서 무겁고 부수효과가 많다 — 상수만 보려면
# 파일에서 직접 정의를 발췌해 본다.
_MAIN_FRAME = Path(__file__).resolve().parent.parent / "ui" / "main_frame.py"


def _extract_silent_install_flags() -> tuple[str, ...]:
    """`SILENT_INSTALL_FLAGS: tuple[str, ...] = (...)` 정의를 정규식으로 발췌."""
    src = _MAIN_FRAME.read_text(encoding="utf-8")
    m = re.search(
        r"SILENT_INSTALL_FLAGS:\s*tuple\[str,\s*\.\.\.\]\s*=\s*\(([^)]+)\)",
        src, re.DOTALL,
    )
    assert m, "SILENT_INSTALL_FLAGS 정의를 찾지 못했습니다"
    flag_text = m.group(1)
    # 각 항목은 따옴표로 둘러싸인 문자열 — '"/X..."' 또는 "'/X...'"
    flags = re.findall(r'["\']([^"\']+)["\']', flag_text)
    return tuple(flags)


def test_silent_install_flags_contain_required_switches():
    flags = _extract_silent_install_flags()
    # /VERYSILENT — 설치 마법사 UI 완전히 숨김. 이게 없으면 무인 설치 의미 없음.
    assert "/VERYSILENT" in flags
    # /SUPPRESSMSGBOXES — 메시지박스 자동 처리.
    assert "/SUPPRESSMSGBOXES" in flags
    # /NORESTART — Windows 재부팅 안 함 (사용자 작업 보호).
    assert "/NORESTART" in flags


def test_silent_install_handles_application_lock():
    """본 EXE 가 잠겨 있어도 자동으로 닫고 다시 켜야 매끄러운 흐름."""
    flags = _extract_silent_install_flags()
    assert "/CLOSEAPPLICATIONS" in flags, "잠금된 EXE 자동 종료 플래그가 빠짐"
    assert "/RESTARTAPPLICATIONS" in flags, "닫은 앱 재시작 플래그가 빠짐"


def test_silent_install_skips_initial_splash():
    """/SP- — 'Are you sure you want to install?' 안내 화면 생략."""
    flags = _extract_silent_install_flags()
    assert "/SP-" in flags


def test_installer_iss_run_section_does_not_skipifsilent():
    """installer.iss [Run] 항목이 silent 모드에서도 새 EXE 를 실행해야 함.

    'Flags:' 절(=실제 Inno 가 읽는 부분) 만 보고, 같은 파일의 주석에 'skipifsilent'
    라는 단어가 등장해도 테스트가 어긋나지 않게 한다.
    """
    iss = Path(__file__).resolve().parent.parent / "installer.iss"
    text = iss.read_text(encoding="utf-8")
    # Filename: ... Flags: ... 행만 정확히 발췌 (주석 제외).
    m = re.search(r"^\s*Filename:[^\n]*Flags:\s*([^\n]+)$", text, re.MULTILINE)
    assert m, "[Run] Filename 항목의 Flags 절을 찾지 못했습니다"
    flags_clause = m.group(1).strip()
    flag_tokens = flags_clause.split()
    assert "skipifsilent" not in flag_tokens, (
        f"[Run] Flags 토큰에서 skipifsilent 를 제거해야 무인 설치 후 자동 실행 가능 "
        f"(현재 토큰: {flag_tokens})"
    )
    # postinstall + nowait 은 그대로 — 정상 대화형 설치 호환.
    assert "postinstall" in flag_tokens
    assert "nowait" in flag_tokens


# 추가 — 무인 설치 후 새 EXE 자동 실행 로직이 변경되지 않도록 main_frame 의
# Popen 호출이 SILENT_INSTALL_FLAGS 를 그대로 전달하는지 가벼운 확인.
def test_main_frame_uses_silent_install_flags_in_popen():
    src = _MAIN_FRAME.read_text(encoding="utf-8")
    # subprocess.Popen([..., *SILENT_INSTALL_FLAGS], ...) 패턴을 그대로 확인.
    assert "*SILENT_INSTALL_FLAGS" in src, (
        "on_install_update 가 SILENT_INSTALL_FLAGS 를 Popen 인자로 전달해야 함"
    )
    # DETACHED_PROCESS 가 사용돼야 본 EXE 종료 후에도 설치관리자가 살아 있음.
    assert "DETACHED_PROCESS" in src
    assert "CREATE_NEW_PROCESS_GROUP" in src
