"""GitHub Releases 자동 업데이트 확인 + 자산 다운로드 (v1.0 / v1.2.6).

매번 실행 시점에 호출하면 GitHub API rate limit 우려가 있어
하루 한 번만 실제 호출하고 결과를 data/last_update_check.json 에 캐시한다.

비교 규칙:
  · 현재 APP_VERSION 과 latest tag 의 시맨틱 버전 비교
  · 'v' 접두사는 제거 후 비교
  · 같거나 낮으면 None 반환 (알림 없음)
  · 더 높으면 UpdateInfo 반환 → 메인 프레임에서 사용자에게 표시

API:
  GET https://api.github.com/repos/Dongjun-Im/green_admin/releases/latest

v1.2.6: 응답의 `assets[]` 도 파싱하여 `_setup.exe` (Inno Setup 결과물) 또는
`_portable.zip` 의 직접 다운로드 URL 을 UpdateInfo 에 담는다. 메인 프레임은
이를 받아 ProgressTaskDialog 로 직접 다운로드 → 설치관리자 실행까지 자동화.

오프라인이거나 API 호출 실패는 조용히 None 반환.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

import requests

from config import APP_VERSION, DATA_DIR, HTTP_TIMEOUT, USER_AGENT


CACHE_FILE = Path(DATA_DIR) / "last_update_check.json"
RELEASE_API = "https://api.github.com/repos/Dongjun-Im/green_admin/releases/latest"
CHECK_INTERVAL_HOURS = 24

# 자산 우선순위 — Inno Setup 결과물 > 포터블 zip.
_INSTALLER_SUFFIX = "_setup.exe"
_PORTABLE_SUFFIX = "_portable.zip"


@dataclass
class UpdateInfo:
    current: str
    latest: str
    release_url: str
    name: str = ""
    body: str = ""
    # v1.2.6 신규 — 직접 다운로드용 자산 정보.
    download_url: str = ""        # 우선순위: *_setup.exe > *_portable.zip
    asset_name: str = ""          # 예: "초록등대회원관리_v1.2.6_setup.exe"
    asset_size: int = 0           # bytes (Content-Length 모를 때 fallback)
    is_installer: bool = False    # setup.exe 면 True, portable zip 이면 False

    def speak_summary(self) -> str:
        return f"새 버전 {self.latest} 가 사용 가능합니다. 현재는 {self.current}."


def _normalize(v: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2.3' / 'v1.2' → (1,2,3) 또는 (1,2,0)."""
    s = (v or "").strip().lstrip("vV")
    parts: list[int] = []
    for chunk in s.split("."):
        # '0', '0a1', '1-rc' 같은 케이스에서 앞의 숫자만 사용
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        try:
            parts.append(int(num) if num else 0)
        except ValueError:
            parts.append(0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def is_newer(latest: str, current: str) -> bool:
    return _normalize(latest) > _normalize(current)


def _pick_asset(assets: list[dict]) -> Optional[dict]:
    """GitHub Release `assets[]` 에서 가장 좋은 자산을 고른다.

    1) `_setup.exe` 로 끝나는 자산 (Inno Setup 설치관리자)
    2) `_portable.zip` 으로 끝나는 자산
    3) 둘 다 없으면 None
    """
    if not assets:
        return None
    installer: Optional[dict] = None
    portable: Optional[dict] = None
    for a in assets:
        name = (a.get("name") or "").lower()
        if not name:
            continue
        if name.endswith(_INSTALLER_SUFFIX) and installer is None:
            installer = a
        elif name.endswith(_PORTABLE_SUFFIX) and portable is None:
            portable = a
    return installer or portable


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(data: dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _should_check_now(cache: dict) -> bool:
    last = cache.get("checked_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    return datetime.now() - last_dt > timedelta(hours=CHECK_INTERVAL_HOURS)


def _from_cache(cache: dict) -> Optional[UpdateInfo]:
    latest = cache.get("latest") or ""
    if not latest or not is_newer(latest, APP_VERSION):
        return None
    asset_name = cache.get("asset_name") or ""
    return UpdateInfo(
        current=APP_VERSION,
        latest=latest,
        release_url=cache.get("release_url", ""),
        name=cache.get("name", ""),
        body=(cache.get("body", "") or "")[:500],
        download_url=cache.get("download_url", "") or "",
        asset_name=asset_name,
        asset_size=int(cache.get("asset_size") or 0),
        is_installer=bool(cache.get("is_installer")),
    )


def check_for_updates(force: bool = False) -> Optional[UpdateInfo]:
    """업데이트 확인. 더 높은 버전이 있으면 UpdateInfo, 아니면 None.

    force=False (기본) 일 때는 24시간 이내 캐시가 있으면 그대로 사용.
    """
    cache = _load_cache()
    if not force and not _should_check_now(cache):
        return _from_cache(cache)

    try:
        resp = requests.get(
            RELEASE_API,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/vnd.github+json",
            },
            timeout=HTTP_TIMEOUT,
        )
    except requests.exceptions.RequestException:
        return None

    if not resp.ok:
        # 404 = 아직 release 없음. 정상으로 취급하고 캐시만 갱신.
        _save_cache({
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "latest": cache.get("latest", ""),
            "release_url": cache.get("release_url", ""),
        })
        return None

    try:
        data = resp.json()
    except ValueError:
        return None

    tag = (data.get("tag_name") or "").strip()
    name = (data.get("name") or "").strip()
    release_url = (data.get("html_url") or "").strip()
    body = (data.get("body") or "").strip()
    asset = _pick_asset(data.get("assets") or [])
    asset_name = (asset or {}).get("name", "") or ""
    download_url = (asset or {}).get("browser_download_url", "") or ""
    asset_size = int((asset or {}).get("size") or 0)
    is_installer = asset_name.lower().endswith(_INSTALLER_SUFFIX)

    _save_cache({
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "latest": tag,
        "release_url": release_url,
        "name": name,
        "body": body[:500],
        "asset_name": asset_name,
        "download_url": download_url,
        "asset_size": asset_size,
        "is_installer": is_installer,
    })

    if not tag or not is_newer(tag, APP_VERSION):
        return None
    return UpdateInfo(
        current=APP_VERSION,
        latest=tag,
        release_url=release_url,
        name=name,
        body=body[:500],
        download_url=download_url,
        asset_name=asset_name,
        asset_size=asset_size,
        is_installer=is_installer,
    )


# ---------------------------------------------------------------------------
# v1.2.6 — 릴리스 자산 직접 다운로드
# ---------------------------------------------------------------------------

# 64KB 청크 — 큰 zip/exe 도 메모리 부담 없이 흘려보낸다.
_DOWNLOAD_CHUNK_BYTES = 64 * 1024


def download_release_asset(
    url: str,
    dest_path: Path,
    *,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    fallback_total: int = 0,
) -> Path:
    """`url` 로부터 자산을 스트리밍 다운로드해서 `dest_path` 에 저장.

    - `Content-Length` 가 있으면 total 로 사용, 없으면 `fallback_total` (보통
      릴리스 API 가 알려준 asset size) 로 대체. 둘 다 없으면 0 (indeterminate).
    - 청크마다 `progress_cb(downloaded, total, label)` 호출. progress_cb 가
      None 이어도 동작.
    - 임시 파일 `<dest>.part` 에 받고 완료 시 `rename`. 실패 시 `.part` 삭제.
    - 반환: 최종 파일 경로 (`dest_path`).
    """
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest_path.with_suffix(dest_path.suffix + ".part")
    # 이전 실패 잔존물 정리.
    try:
        part_path.unlink()
    except FileNotFoundError:
        pass

    resp = requests.get(
        url,
        stream=True,
        timeout=HTTP_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()

    total = 0
    try:
        total = int(resp.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        total = 0
    if total <= 0 and fallback_total > 0:
        total = int(fallback_total)

    downloaded = 0
    try:
        with part_path.open("wb") as fp:
            for chunk in resp.iter_content(chunk_size=_DOWNLOAD_CHUNK_BYTES):
                if not chunk:
                    continue
                fp.write(chunk)
                downloaded += len(chunk)
                if progress_cb is not None:
                    label = ""
                    if total > 0:
                        label = f"받는 중 {downloaded // 1024}KB / {total // 1024}KB"
                    else:
                        label = f"받는 중 {downloaded // 1024}KB"
                    try:
                        progress_cb(downloaded, total, label)
                    except Exception:
                        # progress 콜백 오류로 다운로드 자체가 죽지 않도록.
                        pass
        # 받은 직후 임시 파일을 최종 위치로 이동.
        if dest_path.exists():
            dest_path.unlink()
        part_path.rename(dest_path)
    except Exception:
        try:
            part_path.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        resp.close()

    return dest_path
