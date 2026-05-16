"""DSM Log Center 응답을 파싱·정규화하여 NasLogStore 에 저장하는 서비스 레이어.

DSM 의 로그 항목은 빌드·언어 설정에 따라 다음과 같이 다양하게 들어온다:

- 구조화 필드: ``{"time": 1715500000, "user": "anycall", "ip": "121.x", "descr": "User [anycall] from [121.x] via [WebDAV] downloaded file [/photo/foo.jpg]."}``
- 단일 문장만 있는 경우: ``"User [anycall] logged in from [121.x] via [DSM]."``
- 한글 메시지: ``"사용자 [anycall] 가 [121.x] 에서 [SMB] 를 통해 [/photo/foo.zip] 을 삭제했습니다."``
- 이벤트 타입만 있는 경우 (빌드에 따라): ``{"event_type": "Download", "user": "anycall", "filepath": "..."}``

따라서 구조화 필드(event_type/type/op/user/ip/time/descr 등)를 우선 사용하고, 부족한
부분은 ``descr`` 의 대괄호 안 값을 순서대로 뽑아 채우는 식으로 robust 하게 파싱한다.

회원 매칭은 두 단계 우선순위:
    1) 소리샘 회원 user_id 와 정확 일치 (소리샘 정보가 가장 가치 있음)
    2) DSM 자료실 그룹 멤버 — 소리샘에서 못 찾아도 '(자료실 회원)' 으로 표시
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

from core.dsm_client import DsmAuthError, DsmClient
from core.models import Member
from core.nas_log_store import NasLogEntry, NasLogStore


log = logging.getLogger(__name__)

# 한국 표준시 — DSM 의 epoch(UTC) 를 한국 시간 ISO 로 저장한다.
_KST = timezone(timedelta(hours=9))


# ---------- 동작 enum ----------

ACTION_LOGIN = "login"
ACTION_LOGOUT = "logout"
ACTION_UPLOAD = "upload"
ACTION_DOWNLOAD = "download"
ACTION_DELETE = "delete"
ACTION_RENAME = "rename"
ACTION_MOVE = "move"
ACTION_COPY = "copy"
ACTION_MKDIR = "mkdir"
ACTION_FAIL = "connect_fail"
ACTION_OTHER = "other"

# 영문·한글 키워드 → 정규화된 enum. 우선순위 순서대로 검사.
# 키워드는 descr 안에 부분 일치로 찾고, 먼저 매치되는 항목으로 결정.
# 영문 동사형은 lower-cased 본문에서, 한글은 원문에서 검색.
#
# 인증 실패(접속 실패)는 LOGIN/LOGOUT 보다 먼저 검사 — DSM 의 실패 메시지에 "sign in"
# 같은 LOGIN 동사가 함께 들어 있어 순서가 뒤바뀌면 LOGIN 으로 오분류된다.
_ACTION_KEYWORDS: list[tuple[str, str]] = [
    # 인증 실패 — LOGIN/LOGOUT 보다 먼저!
    ("failed to sign in", ACTION_FAIL),       # DSM 표준: "User [x] failed to sign in to [DSM] ..."
    ("failed to log in", ACTION_FAIL),
    ("failed to logon", ACTION_FAIL),
    ("failed to log on", ACTION_FAIL),
    ("failed to connect", ACTION_FAIL),
    ("sign-in failure", ACTION_FAIL),
    ("sign in failed", ACTION_FAIL),
    ("login failed", ACTION_FAIL),
    ("login failure", ACTION_FAIL),
    ("authorization failure", ACTION_FAIL),
    ("authorization denied", ACTION_FAIL),
    ("authentication fail", ACTION_FAIL),     # 'fail/failed/failure' 모두 부분 일치
    ("auth fail", ACTION_FAIL),
    ("access denied", ACTION_FAIL),
    ("permission denied", ACTION_FAIL),
    ("login denied", ACTION_FAIL),
    ("로그인 실패", ACTION_FAIL),
    ("로그인 거부", ACTION_FAIL),
    ("인증 실패", ACTION_FAIL),
    ("인증 거부", ACTION_FAIL),
    ("접근 거부", ACTION_FAIL),
    ("권한 거부", ACTION_FAIL),
    # 인증 성공 (실패 검사 뒤)
    ("logged in", ACTION_LOGIN),
    ("signed in", ACTION_LOGIN),
    ("signed in to", ACTION_LOGIN),
    ("sign in to", ACTION_LOGIN),
    ("log in to", ACTION_LOGIN),
    ("log on to", ACTION_LOGIN),
    ("logon to", ACTION_LOGIN),
    ("log in", ACTION_LOGIN),
    ("login", ACTION_LOGIN),
    ("connected to", ACTION_LOGIN),
    ("connected", ACTION_LOGIN),
    ("로그인", ACTION_LOGIN),
    ("접속 시작", ACTION_LOGIN),
    ("logged out", ACTION_LOGOUT),
    ("signed out", ACTION_LOGOUT),
    ("disconnected", ACTION_LOGOUT),
    ("logout", ACTION_LOGOUT),
    ("로그아웃", ACTION_LOGOUT),
    ("접속 종료", ACTION_LOGOUT),
    # 폴더 생성 — '생성' 키워드가 다른 동작에 안 섞이도록 먼저 검사
    ("created folder", ACTION_MKDIR),
    ("create folder", ACTION_MKDIR),
    ("created directory", ACTION_MKDIR),
    ("make folder", ACTION_MKDIR),
    ("make directory", ACTION_MKDIR),
    ("new folder", ACTION_MKDIR),
    ("mkdir", ACTION_MKDIR),
    ("폴더 생성", ACTION_MKDIR),
    ("폴더생성", ACTION_MKDIR),
    ("디렉터리 생성", ACTION_MKDIR),
    # 파일 동작
    ("deleted", ACTION_DELETE),
    ("delete", ACTION_DELETE),
    ("removed", ACTION_DELETE),
    ("remove", ACTION_DELETE),
    ("erased", ACTION_DELETE),
    ("trash", ACTION_DELETE),
    ("삭제", ACTION_DELETE),
    ("지움", ACTION_DELETE),
    ("uploaded", ACTION_UPLOAD),
    ("upload", ACTION_UPLOAD),
    ("write", ACTION_UPLOAD),
    ("wrote", ACTION_UPLOAD),
    ("put file", ACTION_UPLOAD),
    ("업로드", ACTION_UPLOAD),
    ("올림", ACTION_UPLOAD),
    ("올린", ACTION_UPLOAD),
    ("downloaded", ACTION_DOWNLOAD),
    ("download", ACTION_DOWNLOAD),
    ("read file", ACTION_DOWNLOAD),
    ("get file", ACTION_DOWNLOAD),
    ("fetched", ACTION_DOWNLOAD),
    ("다운로드", ACTION_DOWNLOAD),
    ("내려받", ACTION_DOWNLOAD),
    ("받음", ACTION_DOWNLOAD),
    ("renamed", ACTION_RENAME),
    ("rename", ACTION_RENAME),
    ("이름변경", ACTION_RENAME),
    ("이름 변경", ACTION_RENAME),
    ("이름을 바꿈", ACTION_RENAME),
    ("이름이 바뀜", ACTION_RENAME),
    ("moved", ACTION_MOVE),
    ("move ", ACTION_MOVE),         # 'remove' 와 겹치지 않도록 trailing space
    ("이동", ACTION_MOVE),
    ("옮김", ACTION_MOVE),
    ("옮긴", ACTION_MOVE),
    ("copied", ACTION_COPY),
    ("copy ", ACTION_COPY),          # 단어 'copy' 만 (다른 단어 끝부분 겹침 방지)
    ("duplicated", ACTION_COPY),
    ("복사", ACTION_COPY),
]

# DSM 구조화 필드(event_type / type / op / event 등) 의 값이 그대로 이 매핑에 있으면
# 즉시 결정 (대소문자 무시). descr 키워드 스캔보다 우선.
_STRUCTURED_ACTION_MAP: dict[str, str] = {
    "login": ACTION_LOGIN,
    "log_in": ACTION_LOGIN,
    "logon": ACTION_LOGIN,
    "connect": ACTION_LOGIN,
    "logout": ACTION_LOGOUT,
    "log_out": ACTION_LOGOUT,
    "logoff": ACTION_LOGOUT,
    "disconnect": ACTION_LOGOUT,
    "login_fail": ACTION_FAIL,
    "auth_fail": ACTION_FAIL,
    "upload": ACTION_UPLOAD,
    "write": ACTION_UPLOAD,
    "put": ACTION_UPLOAD,
    "create": ACTION_UPLOAD,
    "download": ACTION_DOWNLOAD,
    "read": ACTION_DOWNLOAD,
    "get": ACTION_DOWNLOAD,
    "delete": ACTION_DELETE,
    "remove": ACTION_DELETE,
    "unlink": ACTION_DELETE,
    "trash": ACTION_DELETE,
    "rename": ACTION_RENAME,
    "move": ACTION_MOVE,
    "mv": ACTION_MOVE,
    "copy": ACTION_COPY,
    "cp": ACTION_COPY,
    "mkdir": ACTION_MKDIR,
    "create_folder": ACTION_MKDIR,
    "createdir": ACTION_MKDIR,
    "new_folder": ACTION_MKDIR,
}

# 사람이 보기 좋게 표시할 한글 라벨 — 내보내기·UI 둘 다에서 사용.
ACTION_LABELS: dict[str, str] = {
    ACTION_LOGIN: "로그인",
    ACTION_LOGOUT: "로그아웃",
    ACTION_UPLOAD: "업로드",
    ACTION_DOWNLOAD: "다운로드",
    ACTION_DELETE: "삭제",
    ACTION_RENAME: "이름변경",
    ACTION_MOVE: "이동",
    ACTION_COPY: "복사",
    ACTION_MKDIR: "폴더생성",
    ACTION_FAIL: "접속 실패",
    ACTION_OTHER: "기타",
}

# UI 필터 그룹 — 사용자 친화적 묶음.
ACTION_GROUPS: list[tuple[str, list[str]]] = [
    ("모두", []),
    ("로그인/로그아웃", [ACTION_LOGIN, ACTION_LOGOUT, ACTION_FAIL]),
    ("업로드", [ACTION_UPLOAD]),
    ("다운로드", [ACTION_DOWNLOAD]),
    ("삭제", [ACTION_DELETE]),
    ("이름변경", [ACTION_RENAME]),
    ("이동/복사", [ACTION_MOVE, ACTION_COPY]),
    ("폴더생성", [ACTION_MKDIR]),
    ("기타", [ACTION_OTHER]),
]


# 흔히 등장하는 프로토콜 라벨 — descr 본문에 등장하는 토큰을 그대로 인식.
_PROTOCOL_KEYWORDS = [
    "WebDAV", "File Station", "FileStation", "DSM",
    "SMB", "AFP", "SFTP", "FTP", "rsync", "NFS",
    "Cloud Sync", "Drive",
]


# 대괄호 안 값 추출 — 한글·공백·기호 모두 허용.
_BRACKET_RE = re.compile(r"\[([^\[\]]*)\]")
# 흔한 IP/IPv6 패턴
_IP_RE = re.compile(r"\b(?:\d{1,3}(?:\.\d{1,3}){3}|[0-9a-fA-F:]+:[0-9a-fA-F:]+)\b")
# 'DOMAIN\user' / 'DOMAIN/user' 형식의 AD 도메인 접두사 제거용.
_DOMAIN_PREFIX_RE = re.compile(r"^[^\\/]+[\\/]")


def _clean_dsm_username(s: str) -> str:
    """DSM 응답의 user 필드를 정규화 — 도메인 접두사·이메일 접미사·공백 제거.

    예) ``"DOMAIN\\anycall"`` → ``"anycall"``
        ``"anycall@gmail.com"`` → ``"anycall"``
        ``"  AnyCall  "`` → ``"anycall"``
    """
    s = (s or "").strip().lower()
    if not s:
        return ""
    # AD 도메인 접두사 (DOMAIN\user 또는 DOMAIN/user) 제거
    s = _DOMAIN_PREFIX_RE.sub("", s, count=1)
    # 이메일 접미사 제거
    if "@" in s:
        s = s.split("@", 1)[0]
    return s.strip()


@dataclass
class NasFetchResult:
    ok: bool
    added: int = 0
    skipped: int = 0
    raw_count: int = 0
    other_count: int = 0   # 'other'(=분류 안 됨) 항목 수 — 파서 보강 신호
    other_sample_path: str = ""
    dsm_group_member_ids: list[str] = field(default_factory=list)
    # 로그 종류별 수신 건수 — UI 가 '파일 전송 로그가 0건' 같은 안내를 띄울 때 사용.
    file_transfer_count: int = 0
    connection_count: int = 0
    file_transfer_seems_disabled: bool = False
    message: str = ""


@dataclass(frozen=True)
class EnrichedEntry:
    """UI/내보내기용 — NasLogEntry + 매칭된 회원 정보.

    표시 우선순위:
        1) 소리샘 회원이 매칭되면 ``이름(uid)`` 으로 표시.
        2) 매칭 안 됐지만 DSM 자료실 그룹 멤버라면 ``(자료실 회원) uid``.
        3) 둘 다 아니면 ``(미등록) uid``.
        4) uid 자체가 비면 ``(시스템)``.
    """
    entry: NasLogEntry
    member: Optional[Member] = None
    is_dsm_group: bool = False

    @property
    def display_name(self) -> str:
        uid = (self.entry.dsm_user_id or "").strip()
        if not uid:
            return "(시스템)"
        if self.member is not None:
            name = (self.member.name or self.member.nickname or "").strip()
            return f"{name}({uid})" if name else uid
        if self.is_dsm_group:
            return f"(자료실 회원) {uid}"
        # 미등록 — 접속 실패면 외부 침입 시도일 가능성 높아 시각적으로 구분.
        if (self.entry.action or "").lower() == "connect_fail":
            return f"(외부 시도) {uid}"
        return f"(미등록) {uid}"


# ---------- 회원 매칭 ----------

def enrich_with_members(
    entries: Iterable[NasLogEntry],
    members: Iterable[Member] | None = None,
    dsm_group_member_ids: Iterable[str] | None = None,
) -> list[EnrichedEntry]:
    """엔트리들의 dsm_user_id 를 소리샘 회원(1순위) / DSM 자료실 그룹(2순위) 으로 매칭.

    - 소리샘 회원이 user_id 로 일치하면 그 Member 를 ``member`` 에 attach (이름·닉네임 표시).
    - 소리샘 매칭은 안 됐지만 DSM 자료실 그룹 멤버라면 ``is_dsm_group=True`` 만 표시.
    - 둘 다 아니면 미등록.
    """
    # 소리샘 user_id 인덱스 — 다양한 표기를 흡수하기 위해 raw·cleaned 양쪽 키로 등록.
    sorisem_index: dict[str, Member] = {}
    for m in (members or []):
        raw = (m.user_id or "").strip().lower()
        cleaned = _clean_dsm_username(raw)
        for k in {raw, cleaned}:
            if k and k not in sorisem_index:
                sorisem_index[k] = m
    # DSM 자료실 그룹 멤버 셋
    dsm_set: set[str] = set()
    for u in (dsm_group_member_ids or []):
        c = _clean_dsm_username(str(u))
        if c:
            dsm_set.add(c)
    out: list[EnrichedEntry] = []
    for e in entries:
        uid_raw = (e.dsm_user_id or "").strip().lower()
        uid_clean = _clean_dsm_username(uid_raw)
        member = sorisem_index.get(uid_clean) or sorisem_index.get(uid_raw)
        is_dsm = bool(uid_clean and uid_clean in dsm_set)
        out.append(EnrichedEntry(entry=e, member=member, is_dsm_group=is_dsm))
    return out


# ---------- 파싱 ----------

def _coerce_epoch(item: dict[str, Any]) -> int:
    """item 에서 epoch(초) 값을 가능한 키들에서 추출. 못 찾으면 0."""
    for k in ("time", "log_time", "logtime", "t", "timestamp"):
        v = item.get(k)
        if v in (None, ""):
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            # 문자열 형식("2026-05-12 14:23:11" / "2026/05/12 14:23:11") 대응
            try:
                s = str(v).replace("/", "-")
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_KST)
                return int(dt.timestamp())
            except ValueError:
                continue
    return 0


def _epoch_to_kst_iso(epoch: int) -> str:
    if not epoch:
        # 시간 없는 항목은 받은 시각(KST) 으로 대체 — UI 에 빈 셀로 보이지 않게.
        return datetime.now(_KST).strftime("%Y-%m-%dT%H:%M:%S")
    return datetime.fromtimestamp(int(epoch), tz=_KST).strftime("%Y-%m-%dT%H:%M:%S")


def _structured_action(item: dict[str, Any]) -> str:
    """item 에 명시적으로 들어 있는 event_type / cmd / type / op / event 같은 값이
    `_STRUCTURED_ACTION_MAP` 에 있으면 그 enum 반환. 모두 없으면 빈 문자열.

    WebDAV 로그(소리샘 NAS 의 webdavxfer)는 ``cmd`` 필드가 동사를 갖는다 —
    ``"delete" / "download" / "upload" / ...`` 같은 값. 이 필드를 우선 검사.
    """
    for key in (
        "cmd", "command",
        "event_type", "eventtype", "event", "type", "op", "action",
        "operation", "category",
    ):
        raw = item.get(key)
        if not raw:
            continue
        v = str(raw).strip().lower()
        if not v:
            continue
        # 정확 일치 우선
        if v in _STRUCTURED_ACTION_MAP:
            return _STRUCTURED_ACTION_MAP[v]
        # 부분 일치 (예: "FILE_DOWNLOAD", "download_file")
        for key2, action in _STRUCTURED_ACTION_MAP.items():
            if key2 in v:
                return action
    return ""


def _detect_action(text: str) -> str:
    """descr/log/event 의 사람용 메시지에서 동작 추론. 못 찾으면 ACTION_OTHER."""
    if not text:
        return ACTION_OTHER
    low = text.lower()
    for kw, action in _ACTION_KEYWORDS:
        # 영문 키워드는 lower 본문에서, 한글은 원문에서 검색.
        if kw in low or kw in text:
            return action
    return ACTION_OTHER


def _detect_protocol(text: str) -> str:
    low = text.lower()
    for p in _PROTOCOL_KEYWORDS:
        if p.lower() in low:
            return p
    return ""


def _split_path(file_path: str) -> tuple[str, str]:
    """경로 → (category, file_name).

    category = lstrip('/') 의 첫 폴더 — 즉 그 다음에 더 깊은 경로가 있을 때만 채운다.
    파일이 루트 바로 아래면 category="" (분류 없음).
    file_name = basename.
    """
    if not file_path:
        return "", ""
    p = file_path.replace("\\", "/").strip()
    head = p.lstrip("/")
    parts = [x for x in head.split("/") if x]
    if not parts:
        return "", ""
    if len(parts) == 1:
        # 루트 바로 아래 파일 — 카테고리 없음.
        return "", parts[0]
    return parts[0], parts[-1]


def _looks_like_path(s: str) -> bool:
    s = s.strip()
    return bool(s) and ("/" in s or "\\" in s)


def _parse_entry(item: dict[str, Any]) -> Optional[NasLogEntry]:
    """DSM 한 줄을 NasLogEntry 로 정규화. 파싱 자체가 실패해도 raw_message 만
    채워 반환(잃지 않는다)."""
    # 1) 사람용 메시지 — DSM 빌드별로 키가 다양하다.
    raw = ""
    for k in ("descr", "msg", "message", "log", "event", "desc", "detail"):
        v = item.get(k)
        if v:
            raw = str(v)
            break
    if not raw:
        # 마지막 수단 — 항목 전체를 문자열로.
        raw = str(item)

    # 2) 구조화 필드 우선 — DSM 빌드별로 user 필드 이름이 천차만별:
    #    user / username / uid / account / who(소리샘 DSM 빌드가 쓰는 키) / mb_id
    user_id = str(
        item.get("user")
        or item.get("username")
        or item.get("uid")
        or item.get("account")
        or item.get("who")
        or item.get("mb_id")
        or item.get("user_id")
        or ""
    ).strip()
    ip = str(item.get("ip") or item.get("client_ip") or item.get("from") or item.get("host") or "").strip()
    protocol = str(item.get("protocol") or "").strip()
    # WebDAV/FileStation 빌드는 logtype 자체가 프로토콜 이름.
    if not protocol:
        lt = str(item.get("logtype") or "").strip()
        if lt and lt.lower() not in ("connection", "conn", "system", "audit", "audit_log"):
            protocol = lt
    file_path = str(item.get("file") or item.get("filepath") or item.get("path") or "").strip()

    # 3) descr 안의 [...] 값을 보조로 사용
    brackets = _BRACKET_RE.findall(raw)
    # 첫 번째 [] 는 보통 user
    if not user_id and brackets:
        user_id = brackets[0].strip()
    # IP 비어 있으면 descr 에서 IP 패턴 찾기
    if not ip:
        m = _IP_RE.search(raw)
        if m:
            ip = m.group(0)
    # 프로토콜 비어 있으면 키워드 스캔
    if not protocol:
        protocol = _detect_protocol(raw)
    # 경로 비어 있으면 후보 탐색:
    #   (a) descr 자체가 경로인 빌드 (WebDAV: descr="/엔터테인먼트/foo.zip"): raw 그대로 사용
    #   (b) descr 안 [...] 값 중 경로처럼 보이는 마지막 값
    if not file_path:
        if raw and (raw.startswith("/") or raw.startswith("\\")) and "\n" not in raw:
            file_path = raw.strip()
        else:
            path_candidates = [b for b in brackets if _looks_like_path(b)]
            if path_candidates:
                file_path = path_candidates[-1].strip()

    # 4) 동작 탐지 — 우선순위:
    #    (a) 구조화 필드 값이 _STRUCTURED_ACTION_MAP 에 있으면 즉시 결정
    #    (b) 그 필드 값을 _detect_action 으로 키워드 추론
    #    (c) descr 본문에서 키워드 추론
    #    (d) logtype 기반 폴백 (connection 류는 fail/실패 여부로 login vs connect_fail)
    action = _structured_action(item)
    if not action or action == ACTION_OTHER:
        action_raw = str(
            item.get("action") or item.get("event_type") or item.get("event")
            or item.get("type") or item.get("op") or ""
        ).strip()
        if action_raw:
            action = _detect_action(action_raw)
        if not action or action == ACTION_OTHER:
            action = _detect_action(raw)
    if not action or action == ACTION_OTHER:
        # 마지막 폴백 — DSM 의 logtype 필드가 connection 류면 인증 관련 이벤트로 간주.
        # 'orginalLogType' 은 DSM 빌드의 오타 — 의도가 아니라 그대로 들어오는 값이다.
        logtype_raw = str(
            item.get("logtype") or item.get("orginalLogType") or item.get("originalLogType") or ""
        ).strip().lower()
        body_low = raw.lower()
        is_conn = "connection" in logtype_raw or "conn" == logtype_raw
        if is_conn:
            if any(k in body_low for k in ("fail", "denied", "rejected", "blocked")) \
                    or any(k in raw for k in ("실패", "거부", "차단")):
                action = ACTION_FAIL
            elif any(k in body_low for k in ("sign in", "log in", "log on", "logon", "connect")):
                action = ACTION_LOGIN
            elif any(k in body_low for k in ("sign out", "log out", "disconnect")):
                action = ACTION_LOGOUT

    # 5) 카테고리·파일명 분리
    category, file_name = _split_path(file_path)

    # 6) 시간 — epoch(UTC) → KST ISO
    epoch = _coerce_epoch(item)
    logged_at = _epoch_to_kst_iso(epoch)

    # 7) user_id 정규화 — 도메인 접두사·이메일 접미사 제거 (매칭 정확도 향상)
    user_clean = _clean_dsm_username(user_id) or user_id.lower()

    # 8) raw_hash — 중복 차단 키
    raw_hash = hashlib.sha1(
        f"{epoch}|{user_clean}|{action}|{file_path}|{raw}".encode("utf-8")
    ).hexdigest()

    return NasLogEntry(
        logged_at=logged_at,
        dsm_user_id=user_clean,
        ip=ip,
        protocol=protocol,
        action=action,
        category=category,
        file_name=file_name,
        file_path=file_path,
        raw_message=raw,
        raw_hash=raw_hash,
    )


# ---------- 수집 ----------

ProgressCb = Callable[[int, int, str], None]


def fetch_and_store_logs(
    client: DsmClient,
    store: NasLogStore,
    *,
    since_epoch: Optional[int] = None,
    limit: int = 2000,
    progress_cb: Optional[ProgressCb] = None,
    dsm_group_name: Optional[str] = None,
    dump_dir: Optional[str] = None,
) -> NasFetchResult:
    """DSM 의 파일 전송 + 연결 로그를 가져와 store 에 저장.

    since_epoch 가 주어지면 그 epoch 이후만 가져오기를 시도(증분). 두 로그 종류 중
    하나가 실패해도 다른 하나는 계속 시도한다.

    Args:
        progress_cb: ``(current, total, message)`` 콜백. ProgressTaskDialog 와
            호환되도록 5단계로 보고 (시작·파일전송·연결·자료실 그룹·저장).
        dsm_group_name: 함께 가져올 자료실 그룹 이름. 가져온 멤버 id 는 store 의
            메타에 캐시되고 결과의 ``dsm_group_member_ids`` 로 반환된다 (회원 매칭용).
        dump_dir: 'other' 로 분류된 항목 샘플을 떠 둘 폴더. 파서 보강용.

    Returns:
        NasFetchResult — ok / added / skipped / other_count / dsm_group_member_ids 등.
    """
    if since_epoch is None:
        since_epoch = store.latest_epoch() or None

    def report(current: int, total: int, msg: str) -> None:
        if progress_cb:
            try:
                progress_cb(current, total, msg)
            except Exception:
                pass

    TOTAL = 5
    report(1, TOTAL, "DSM 자료실 그룹 멤버 확인 중...")
    # ----- DSM 자료실 그룹 멤버 (회원 매칭 정확도 향상) -----
    group_member_ids: list[str] = []
    if dsm_group_name:
        try:
            members = client.list_group_members(dsm_group_name)
            group_member_ids = [
                str((m.get("name") or "").strip()) for m in (members or [])
                if isinstance(m, dict) and m.get("name")
            ]
        except Exception as e:  # 그룹 조회 실패는 치명적 아님 — 매칭 정확도만 손해
            log.warning("[nas_log] 자료실 그룹 조회 실패: %s", e)
        # 메타에 캐시 (다음 다이얼로그 열 때 바로 매칭에 쓰임)
        try:
            store.set_dsm_group_members(group_member_ids)
        except Exception:
            pass

    raw_items: list[dict[str, Any]] = []
    errors: list[str] = []
    file_transfer_count = 0
    connection_count = 0

    report(2, TOTAL, "파일 전송 로그 가져오는 중...")
    # DSM 빌드마다 파일 동작이 잡히는 logtype 이름이 천차만별:
    #   - 기본 syslog: file_transfer
    #   - Log Center 패키지: webdav / filestation / smb 같은 프로토콜별 logtype
    #   - 일부 빌드는 audit / audit_log
    # 각각 시도해서 가장 먼저 데이터가 들어오는 logtype 을 채택. (list_audit_logs 자체도
    # API 변종을 폴백하므로 여기서는 logtype 만 바꾼다.)
    ft_items: list[dict[str, Any]] = []
    ft_attempted_any = False
    ft_logtype_used = ""
    file_transfer_logtypes = (
        "file_transfer", "FileTransfer", "transfer", "file",
        "webdav", "WebDAV",
        "filestation", "FileStation", "file_station",
        "smb", "SMB",
        "audit", "audit_log",
    )
    for ft_logtype in file_transfer_logtypes:
        try:
            ft_attempted_any = True
            chunk = client.list_audit_logs(
                logtype=ft_logtype, start_epoch=since_epoch, limit=limit,
            )
            if chunk:
                ft_items = list(chunk)
                ft_logtype_used = ft_logtype
                break    # 데이터가 들어오는 logtype 에서 멈춤
        except DsmAuthError as e:
            # logtype 자체가 거부되는 경우(잘 알려진 fallback 코드 102/400 등) 는 조용히
            # 다음 logtype 으로 — 마지막에 모두 실패면 한 번만 errors 에 기록.
            log.info("[nas_log] file_transfer logtype '%s' 거부: %s", ft_logtype, e)
            if ft_logtype == file_transfer_logtypes[-1]:
                errors.append(f"file_transfer: {e}")
        except Exception as e:
            errors.append(f"file_transfer({ft_logtype}): 예외 {e}")
            log.warning("[nas_log] 파일 전송 로그 예외: %s", e)
            break
    raw_items.extend(ft_items)
    file_transfer_count = len(ft_items)
    if ft_logtype_used:
        log.info("[nas_log] 파일 전송 로그 채택 logtype='%s' (%d건)", ft_logtype_used, file_transfer_count)
    # 모든 표기를 시도해 보았는데 0건이면 — Log Center 설정에서 꺼져 있을 가능성 높음.
    file_transfer_seems_disabled = ft_attempted_any and file_transfer_count == 0

    report(3, TOTAL, "로그인/로그아웃 로그 가져오는 중...")
    cn_items: list[dict[str, Any]] = []
    for cn_logtype in ("connection", "Connection", "conn"):
        try:
            chunk = client.list_audit_logs(
                logtype=cn_logtype, start_epoch=since_epoch, limit=limit,
            )
            if chunk:
                cn_items = list(chunk)
                break
        except DsmAuthError as e:
            if cn_logtype == "conn":
                errors.append(f"connection: {e}")
                log.warning("[nas_log] 연결 로그 실패: %s", e)
            else:
                log.info("[nas_log] connection '%s' 시도 실패(%s) — 다음 표기 시도", cn_logtype, e)
        except Exception as e:
            errors.append(f"connection({cn_logtype}): 예외 {e}")
            log.warning("[nas_log] 연결 로그 예외: %s", e)
            break
    raw_items.extend(cn_items)
    connection_count = len(cn_items)

    # 모든 종류 실패 → 전체 실패
    if errors and not raw_items:
        message = "DSM 로그 가져오기 실패: " + " / ".join(errors)
        store.set_last_status(False, message)
        report(TOTAL, TOTAL, "실패")
        return NasFetchResult(
            ok=False, message=message,
            dsm_group_member_ids=group_member_ids,
            file_transfer_count=file_transfer_count,
            connection_count=connection_count,
            file_transfer_seems_disabled=file_transfer_seems_disabled,
        )

    report(4, TOTAL, f"받은 {len(raw_items)}건 파싱 중...")
    entries: list[NasLogEntry] = []
    other_items: list[dict[str, Any]] = []
    max_epoch = 0
    for it in raw_items:
        e = _parse_entry(it)
        if e is None:
            continue
        entries.append(e)
        if e.action == ACTION_OTHER:
            other_items.append(it)
        ep = _coerce_epoch(it)
        if ep > max_epoch:
            max_epoch = ep

    report(5, TOTAL, "저장 중...")
    added, skipped = store.upsert_entries(entries)
    if max_epoch:
        store.set_latest_epoch(max_epoch)

    # 'other' 가 많이 잡히면 샘플 덤프 — 파서 보강에 활용 (개인정보 minimal).
    other_sample_path = ""
    if dump_dir and other_items and (len(other_items) >= 10 or len(other_items) >= max(1, len(raw_items) // 3)):
        other_sample_path = _dump_other_samples(other_items, dump_dir)

    detail = (
        f"{added}건 추가, 중복 {skipped}건 "
        f"(파일 전송 {file_transfer_count} / 연결 {connection_count})"
    )
    if other_items:
        detail += f", '기타' {len(other_items)}건"
    if errors:
        detail += f" (부분 실패 {len(errors)}건)"
    if file_transfer_seems_disabled and connection_count > 0:
        detail += " — [경고] 파일 전송 로그가 0건 (DSM 설정 확인 필요)"
    store.set_last_status(ok=True, message=detail)
    report(TOTAL, TOTAL, detail)

    return NasFetchResult(
        ok=True, added=added, skipped=skipped, raw_count=len(raw_items),
        other_count=len(other_items),
        other_sample_path=other_sample_path,
        dsm_group_member_ids=group_member_ids,
        file_transfer_count=file_transfer_count,
        connection_count=connection_count,
        file_transfer_seems_disabled=file_transfer_seems_disabled,
        message=detail,
    )


def _dump_other_samples(items: list[dict[str, Any]], dump_dir: str) -> str:
    """'기타' 분류된 원본 항목 중 일부를 JSON 으로 저장. 파서 보강용."""
    try:
        os.makedirs(dump_dir, exist_ok=True)
        ts = _time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(str(dump_dir), f"{ts}_nas_log_unknown.json")
        sample = items[:20]   # 너무 많은 PII 가 안 들어가도록 일부만
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"sample_size": len(sample), "total_unknown": len(items), "items": sample},
                f, ensure_ascii=False, indent=2, default=str,
            )
        return path
    except Exception:
        return ""


# ---------- 진단 ----------

def save_diagnostic_dump(
    client: DsmClient, dest_dir: str | os.PathLike,
) -> str:
    """list_audit_logs 의 fallback 시도 전체를 JSON 으로 떠서 파일에 저장.

    반환: 저장된 파일 경로. 실패 시 빈 문자열.
    """
    import json
    import time
    try:
        info = client.collect_audit_log_diagnostics()
    except Exception as e:
        info = {"error": str(e)}
    try:
        os.makedirs(dest_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(str(dest_dir), f"{ts}_dsm_audit_diag.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2, default=str)
        return path
    except Exception:
        return ""
