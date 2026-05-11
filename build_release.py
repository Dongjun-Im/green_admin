"""초록등대 회원관리 — 릴리스 빌드 스크립트.

하는 일:
  1) PyInstaller 로 onedir 빌드 (chorok_green_admin.spec)        → dist\초록등대회원관리\
  2) 무설치(포터블) ZIP 생성                                     → release\초록등대회원관리_v{ver}_portable.zip
  3) Inno Setup(ISCC.exe) 이 있으면 설치 EXE 생성               → installer_out\초록등대회원관리_v{ver}_setup.exe

사용:  py -3.12 build_release.py            (전부 실행)
       py -3.12 build_release.py --no-build (이미 dist 가 있으면 PyInstaller 건너뜀)

git: release\, installer_out\, dist\, build\ 는 .gitignore 에 등록 — 산출물은 커밋하지 않음.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# Windows 콘솔 코드페이지(cp949 등)에서 한글/특수기호 print 가 깨지지 않도록.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
APP_NAME = "초록등대회원관리"
DIST_APP = ROOT / "dist" / APP_NAME
SPEC = ROOT / "chorok_green_admin.spec"


def _app_version() -> str:
    # config.py 의 APP_VERSION 을 그대로 읽어 ZIP/설치 파일 이름에 사용
    txt = (ROOT / "config.py").read_text(encoding="utf-8")
    import re
    m = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', txt)
    return m.group(1) if m else "0.0.0"


def _run_pyinstaller() -> None:
    print("[1/3] PyInstaller 빌드 ...")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", str(SPEC)]
    subprocess.run(cmd, cwd=str(ROOT), check=True)
    if not DIST_APP.is_dir():
        raise SystemExit(f"빌드 결과 폴더가 없습니다: {DIST_APP}")


def _make_portable_zip(version: str) -> Path:
    print("[2/3] 무설치 ZIP 생성 ...")
    out_dir = ROOT / "release"
    out_dir.mkdir(exist_ok=True)
    zip_path = out_dir / f"{APP_NAME}_v{version}_portable.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        base = DIST_APP.parent  # dist\
        for path in DIST_APP.rglob("*"):
            if path.is_file():
                # ZIP 안의 최상위 폴더가 "초록등대회원관리\" 가 되도록 dist 기준 상대경로
                zf.write(path, path.relative_to(base))
    print(f"      → {zip_path}  ({zip_path.stat().st_size / (1024*1024):.1f} MB)")
    return zip_path


def _find_iscc() -> str | None:
    # PATH 우선, 그다음 흔한 설치 경로
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return found
    for p in (
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
    ):
        if os.path.isfile(p):
            return p
    return None


def _make_installer(version: str) -> Path | None:
    print("[3/3] 설치 EXE 생성 (Inno Setup) ...")
    iscc = _find_iscc()
    if not iscc:
        print("      → ISCC.exe 를 찾지 못해 건너뜁니다. "
              "https://jrsoftware.org/isdl.php 에서 Inno Setup 6 을 설치하면 자동으로 생성됩니다.")
        return None
    iss = ROOT / "installer.iss"
    subprocess.run([iscc, str(iss)], cwd=str(ROOT), check=True)
    out = ROOT / "installer_out" / f"{APP_NAME}_v{version}_setup.exe"
    if out.exists():
        print(f"      → {out}  ({out.stat().st_size / (1024*1024):.1f} MB)")
        return out
    print("      → 컴파일은 됐는데 출력 파일을 못 찾았습니다. installer_out\\ 폴더를 확인하세요.")
    return None


def main(argv: list[str]) -> None:
    version = _app_version()
    print(f"=== 초록등대 회원관리 v{version} 릴리스 빌드 ===")
    if "--no-build" not in argv:
        _run_pyinstaller()
    elif not DIST_APP.is_dir():
        raise SystemExit("--no-build 인데 dist 폴더가 없습니다. 먼저 빌드하세요.")
    zip_path = _make_portable_zip(version)
    setup_path = _make_installer(version)
    print()
    print("=== 완료 ===")
    print(f"  무설치(포터블): {zip_path}")
    print(f"  설치 버전     : {setup_path if setup_path else '(생략 - Inno Setup 미설치)'}")
    print("  → GitHub Releases 에 위 파일들을 첨부하세요.")


if __name__ == "__main__":
    main(sys.argv[1:])
