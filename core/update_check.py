"""GitHub Releases 자동 업데이트 확인 (v1.0).

매번 실행 시점에 호출하면 GitHub API rate limit 우려가 있어
하루 한 번만 실제 호출하고 결과를 data/last_update_check.json 에 캐시한다.

비교 규칙:
  · 현재 APP_VERSION 과 latest tag 의 시맨틱 버전 비교
  · 'v' 접두사는 제거 후 비교
  · 같거나 낮으면 None 반환 (알림 없음)
  · 더 높으면 UpdateInfo 반환 → 메인 프레임에서 사용자에게 표시

API:
  GET https://api.github.com/repos/Dongjun-Im/green_admin/releases/latest

오프라인이거나 API 호출 실패는 조용히 None 반환.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

from config import APP_VERSION, DATA_DIR, HTTP_TIMEOUT, USER_AGENT


CACHE_FILE = Path(DATA_DIR) / "last_update_check.json"
RELEASE_API = "https://api.github.com/repos/Dongjun-Im/green_admin/releases/latest"
CHECK_INTERVAL_HOURS = 24


@dataclass
class UpdateInfo:
    current: str
    latest: str
    release_url: str
    name: str = ""
    body: str = ""

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


def check_for_updates(force: bool = False) -> Optional[UpdateInfo]:
    """업데이트 확인. 더 높은 버전이 있으면 UpdateInfo, 아니면 None.

    force=False (기본) 일 때는 24시간 이내 캐시가 있으면 그대로 사용.
    """
    cache = _load_cache()
    if not force and not _should_check_now(cache):
        latest = cache.get("latest")
        if latest and is_newer(latest, APP_VERSION):
            return UpdateInfo(
                current=APP_VERSION,
                latest=latest,
                release_url=cache.get("release_url", ""),
                name=cache.get("name", ""),
                body=cache.get("body", "")[:500],
            )
        return None

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

    _save_cache({
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "latest": tag,
        "release_url": release_url,
        "name": name,
        "body": body[:500],
    })

    if not tag or not is_newer(tag, APP_VERSION):
        return None
    return UpdateInfo(
        current=APP_VERSION,
        latest=tag,
        release_url=release_url,
        name=name,
        body=body[:500],
    )
