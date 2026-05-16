"""사용자 환경 옵션 — data/app_options.json 에 저장.

작은 JSON 파일에 키-값 저장. 현재 노출 옵션:
    auto_run_adjustment   장기미접속 조정 도래 시 앱 시작 직후 자동 실행 여부.
                          False(기본) 면 시작 시 음성으로 안내만 하고,
                          사용자가 Ctrl+R 또는 메뉴로 직접 실행해야 한다.
    auto_fetch_dsm_on_open  자료실 메인 열 때 DSM 멤버를 자동 가져올지 (기본 False).
    other_amount_subscription_months
                          토스 단가표(3000/9000/12000/24000)에 없는 금액 입금도
                          이 개월수만큼의 구독으로 인정 (0 = 비활성, 기본). >0 이면
                          기타 금액 입금이 구독으로 산정돼 매트릭스에 '구독중' 으로 뜬다.

방식이 단순한 이유: 옵션이 아주 적고, 형식 변경 시 무시하고 기본값으로
회귀하면 충분하다. 잘못 저장된 파일은 다음 토글에서 덮어써진다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import DATA_DIR


OPTIONS_FILE = Path(DATA_DIR) / "app_options.json"

DEFAULTS: dict[str, Any] = {
    "auto_run_adjustment": False,            # 기본: 사용자가 직접 실행
    "auto_fetch_dsm_on_open": False,         # 기본: 메인 열 때 DSM 자동 가져오기 안 함
    "other_amount_subscription_months": 0,   # 0 = 단가표 외 입금은 '기타' (구독 인정 안 함)
    "auto_fetch_nas_log_on_start": True,     # 시작 시 NAS 접속 로그 백그라운드 수집 (2FA 활성 시 자동 스킵)
}


def _load_raw() -> dict[str, Any]:
    if not OPTIONS_FILE.exists():
        return {}
    try:
        return json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def get(key: str, default: Any = None) -> Any:
    """옵션 조회 — 저장 파일 → DEFAULTS → 호출자 default 순서로 폴백."""
    raw = _load_raw()
    if key in raw:
        return raw[key]
    if key in DEFAULTS:
        return DEFAULTS[key]
    return default


def set_value(key: str, value: Any) -> None:
    """단일 옵션 저장 — 다른 키는 그대로 유지."""
    raw = _load_raw()
    raw[key] = value
    OPTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    OPTIONS_FILE.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
