"""DSM(Synology) 설정·자격증명 저장.

green_auth/credentials.py 와 동일한 Fernet+INI 패턴 — 머신 고유 키로
계정 비밀번호를 암호화하여 data/dsm_credentials.ini 에 저장한다.

평문으로 저장:
    url            DSM 베이스 URL (예: https://dsm.example.com:5001)
    group_name     자료실 회원이 속할 그룹 이름
    use_2fa        2단계 인증 사용 여부 ("true"/"false")
    verify_ssl     자가 서명 인증서 등으로 끄고 싶을 때 ("true"/"false")

암호화 저장:
    account
    password

저장 위치는 .gitignore 에 등록되어 커밋되지 않는다.
"""
from __future__ import annotations

import base64
import configparser
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from config import DATA_DIR


DSM_CREDENTIALS_FILE = Path(DATA_DIR) / "dsm_credentials.ini"

# 머신 고유 키 도출용 salt — 다른 PC 로 파일을 옮겨도 복호화되지 않게.
# 기존 green_auth ENCRYPTION_SALT 와는 별도 (DSM 따로).
_DSM_SALT = b"chorok_green_dsm_v1_salt_001"


def _get_fernet() -> Fernet:
    seed = (os.getlogin() + os.environ.get("COMPUTERNAME", "default")).encode()
    key_material = hashlib.pbkdf2_hmac("sha256", seed, _DSM_SALT, 100000)
    key = base64.urlsafe_b64encode(key_material[:32])
    return Fernet(key)


@dataclass
class DsmSettings:
    url: str = ""
    account: str = ""
    password: str = ""
    group_name: str = ""
    use_2fa: bool = False
    verify_ssl: bool = True

    @property
    def is_complete(self) -> bool:
        return bool(self.url and self.account and self.password and self.group_name)


def save_dsm_settings(s: DsmSettings, path: Path = DSM_CREDENTIALS_FILE) -> None:
    fernet = _get_fernet()
    enc_account = fernet.encrypt(s.account.encode()).decode()
    enc_password = fernet.encrypt(s.password.encode()).decode()

    cfg = configparser.ConfigParser()
    cfg["dsm"] = {
        "url": s.url,
        "group_name": s.group_name,
        "use_2fa": "true" if s.use_2fa else "false",
        "verify_ssl": "true" if s.verify_ssl else "false",
        "account": enc_account,
        "password": enc_password,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        cfg.write(f)


def load_dsm_settings(path: Path = DSM_CREDENTIALS_FILE) -> DsmSettings | None:
    """저장된 설정을 복호화해 반환. 파일 없거나 해독 실패 시 None."""
    if not path.exists():
        return None
    cfg = configparser.ConfigParser()
    try:
        cfg.read(path, encoding="utf-8")
    except (OSError, configparser.Error):
        return None
    if "dsm" not in cfg:
        return None
    section = cfg["dsm"]
    try:
        fernet = _get_fernet()
        account = fernet.decrypt(section.get("account", "").encode()).decode()
        password = fernet.decrypt(section.get("password", "").encode()).decode()
    except (InvalidToken, ValueError):
        # 다른 PC 로 파일을 옮겼거나 파일 손상 — 사용자가 다시 입력해야 함.
        return None
    return DsmSettings(
        url=section.get("url", ""),
        account=account,
        password=password,
        group_name=section.get("group_name", ""),
        use_2fa=section.get("use_2fa", "false").lower() == "true",
        verify_ssl=section.get("verify_ssl", "true").lower() == "true",
    )


def delete_dsm_settings(path: Path = DSM_CREDENTIALS_FILE) -> None:
    if path.exists():
        path.unlink()
