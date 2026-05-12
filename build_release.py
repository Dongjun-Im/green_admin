r"""초록등대 회원관리 — 릴리스 빌드 스크립트.

하는 일:
  0) data\google_credentials.json 이 없으면 리포 밖 마스터 사본에서 자동 복사
     (spec 이 이 파일을 EXE 에 번들 → 사용자는 재다운로드 불필요).
  1) PyInstaller 로 onedir 빌드 (chorok_green_admin.spec)        → dist\초록등대회원관리\
  2) 무설치(포터블) ZIP 생성                                     → release\초록등대회원관리_v{ver}_portable.zip
  3) Inno Setup(ISCC.exe) 이 있으면 설치 EXE 생성               → installer_out\초록등대회원관리_v{ver}_setup.exe

사용:  py -3.12 build_release.py            (전부 실행)
       py -3.12 build_release.py --no-build (이미 dist 가 있으면 PyInstaller 건너뜀)

OAuth 자격증명(구글시트 동기화용) 마스터 사본 위치 — 둘 중 하나에 두면 매 빌드 자동 포함:
  · 환경변수 GREEN_ADMIN_GOOGLE_CREDENTIALS 가 가리키는 파일
  · %USERPROFILE%\.green_admin\google_credentials.json   (다른 PC 면 그 PC 의 홈 폴더 기준)
파일 내용은 PC 와 무관 — 같은 파일을 각 작업 PC 의 홈 폴더 .green_admin\ 에 두기만 하면 된다.

git: release\, installer_out\, dist\, build\ 는 .gitignore 에 등록 — 산출물은 커밋하지 않음.
     data\google_credentials.json 도 .gitignore — 그래서 리포 밖 마스터 사본에서 끌어온다.
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


GOOGLE_CRED_NAME = "google_credentials.json"


def _ensure_google_credentials() -> None:
    """data\\google_credentials.json 이 없으면 리포 밖 마스터 사본에서 복사해 온다.

    chorok_green_admin.spec 이 빌드 시점에 이 파일이 있으면 EXE 안에 번들하고,
    실행 시 core.sheets_sync._ensure_credentials_file 이 자동 복원하므로,
    사용자는 한 번만 받아 두면 이후 재다운로드가 필요 없다. 단 이 파일은
    .gitignore 라 리포를 새로 클론하면 따라오지 않으므로, 리포 밖 고정 위치에
    마스터 사본을 두고 빌드 때마다 끌어온다.

    찾는 순서:
      1) 환경변수 GREEN_ADMIN_GOOGLE_CREDENTIALS 가 가리키는 파일
      2) ~/.green_admin/google_credentials.json   (= %USERPROFILE%\\.green_admin\\)
    (둘 다 없으면 경고만 — 빌드는 계속, 구글시트 동기화만 사용자가 직접 넣어야 함)
    """
    dest = ROOT / "data" / GOOGLE_CRED_NAME
    if dest.is_file():
        print(f"[0/3] OAuth 자격증명 OK — {dest}")
        return
    candidates: list[Path] = []
    env_path = os.environ.get("GREEN_ADMIN_GOOGLE_CREDENTIALS")
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.append(Path.home() / ".green_admin" / GOOGLE_CRED_NAME)
    for src in candidates:
        try:
            if src.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                print(f"[0/3] OAuth 자격증명 복사: {src}  →  {dest}")
                return
        except OSError as e:
            print(f"      ! {src} 복사 실패: {e}")
    print("[0/3] OAuth 자격증명(google_credentials.json) 마스터 사본을 못 찾았습니다 — "
          "이 빌드에는 구글시트 동기화용 OAuth 클라이언트가 포함되지 않습니다.")
    print("      아래 위치 중 하나에 한 번만 두면 다음 빌드부터 자동 포함됩니다:")
    print(f"        {Path.home() / '.green_admin' / GOOGLE_CRED_NAME}")
    print("        또는 환경변수 GREEN_ADMIN_GOOGLE_CREDENTIALS 에 파일 경로 지정")


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
    _ensure_google_credentials()  # spec 이 읽기 전에 data\ 에 채워 둔다
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
