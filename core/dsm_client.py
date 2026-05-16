"""Synology DSM 7.2 API 클라이언트 — 인증 + 연결 테스트.

이 모듈은 그룹 B-1 단계: 인증과 기본 연결 테스트만 담당.
사용자 CRUD·그룹 관리는 그룹 B-2 에서 추가 예정 (`SYNO.Core.User`,
`SYNO.Core.Group` 메서드 호출).

DSM 7.2 인증 흐름:
    POST <base>/webapi/auth.cgi
        api=SYNO.API.Auth
        version=7
        method=login
        account=<admin>
        passwd=<admin_pw>
        session=ChorokGreenAdmin
        format=sid
        enable_syno_token=yes
        otp_code=<6자리>            (2단계 인증 시)

    응답 (성공):
        {"success": true, "data": {"sid": "...", "synotoken": "..."}}
    응답 (실패):
        {"success": false, "error": {"code": <int>}}

후속 API 호출 시 _sid 쿼리 파라미터 + X-SYNO-TOKEN 헤더를 함께 보낸다.

주요 에러 코드 (DSM 공식):
    400  계정 없음 또는 비밀번호 틀림
    401  계정 비활성화
    402  권한 거부
    403  2단계 인증 필요 (otp_code 누락)
    404  OTP 검증 실패
    406  계정 잠김
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


# 로그인 시 식별 이름 — DSM 동시 세션 추적용. 너무 길거나 특수문자 X.
_SESSION_NAME = "ChorokGreenAdmin"

# DSM 공식 에러 코드 → 한글 메시지
_AUTH_ERROR_MESSAGES: dict[int, str] = {
    100: "알 수 없는 오류",
    101: "잘못된 파라미터",
    102: "API 미지원",
    103: "메서드 미지원",
    104: "API 버전 미지원",
    105: "현재 사용자가 권한 부족",
    106: "세션 시간 초과",
    107: "다른 위치에서 로그인되어 세션 종료",
    400: "계정 또는 비밀번호가 잘못되었습니다",
    401: "계정이 비활성화되어 있습니다",
    402: "권한이 거부되었습니다",
    403: "2단계 인증이 필요합니다 — OTP 코드를 입력해 주세요",
    404: "OTP 코드가 잘못되었거나 만료되었습니다",
    405: "다른 곳에서 로그인 중 (앱 세션 충돌)",
    406: "계정이 잠겼습니다 — DSM 관리자에게 잠금 해제 문의 필요",
}


class DsmAuthError(Exception):
    """DSM 인증·연결 관련 모든 오류의 베이스."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


def explain_auth_error_code(code: int | None) -> str:
    if code is None:
        return "알 수 없는 인증 오류"
    return _AUTH_ERROR_MESSAGES.get(code, f"DSM 오류 코드 {code}")


# SYNO.Core.User create/set 시 비밀번호 강도 정책 위반으로 보이는 코드.
# 자동 생성 비밀번호로 만들다 이 코드가 나오면 더 강한 비밀번호로 1회 재시도한다.
PASSWORD_POLICY_ERROR_CODES = frozenset({3119, 3120, 3121})

# SYNO.Core.User create/set 오류 코드 → 운영자용 한글 힌트.
# 확신 있는 것만 매핑하고, 모르는 코드는 generic 메시지로 폴백.
_USER_API_ERROR_HINTS: dict[int, str] = {
    3102: "사용자명이 유효하지 않습니다 - 허용되지 않는 문자, 길이(1~64자), 예약어 여부를 확인하세요",
    3117: "사용자/그룹 처리 오류 - DSM 사용자 관리 화면에서 상태를 확인하세요",
    3119: "비밀번호가 너무 짧습니다 - DSM 비밀번호 정책의 최소 길이를 확인하세요",
    3120: "비밀번호가 너무 단순합니다 - 대소문자, 숫자, 특수문자를 섞어야 할 수 있습니다",
    3121: (
        "사용자 생성 거부 - 비밀번호가 DSM 정책(길이, 대소문자, 숫자, 특수문자, "
        "사용자명 포함 금지)을 만족하지 않거나 사용자명이 유효하지 않을 수 있습니다. "
        "DSM '사용자 - 고급 설정 - 비밀번호 정책' 을 확인하거나, "
        "'DSM 관리 - 신규 사용자 만들기' 에서 비밀번호를 직접 지정해 만들어 보세요"
    ),
    3132: "이미 존재하는 사용자명입니다",
}


def explain_user_api_error_code(code: int | None) -> str | None:
    """SYNO.Core.User create/set 오류 코드의 한글 힌트. 모르면 None."""
    if code is None:
        return None
    return _USER_API_ERROR_HINTS.get(code)


def _with_user_api_hint(e: "DsmAuthError") -> "DsmAuthError":
    """SYNO.Core.User 오류에 코드별 한글 힌트가 있으면 메시지에 덧붙여 새 예외 반환.

    힌트가 없으면 원래 예외를 그대로 반환 (generic 메시지 유지).
    이미 힌트가 붙어 있으면 (' — ' 포함) 중복 부착 안 함.
    """
    hint = explain_user_api_error_code(getattr(e, "code", None))
    if not hint:
        return e
    msg = str(e)
    if " — " in msg:
        return e
    return DsmAuthError(f"{msg} — {hint}", code=e.code)


@dataclass
class DsmInfo:
    """connection_test 의 반환 — DSM 가 응답한 기본 정보."""
    auth_min_version: int
    auth_max_version: int
    raw: dict[str, Any]


class DsmClient:
    """1회 로그인 → API 호출 → 로그아웃 단위로 사용. 컨텍스트 매니저 지원."""

    DEFAULT_TIMEOUT = 20

    def __init__(self, url: str, *, verify_ssl: bool = True) -> None:
        if not url:
            raise ValueError("DSM URL 이 비어 있습니다.")
        self.base = url.rstrip("/")
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        self.sid: str | None = None
        self.synotoken: str | None = None
        # 진단용 — None 이 아니면 _call 이 (요청, 응답) 한 묶음을 append.
        self._diag_buffer: list[dict[str, Any]] | None = None

    def __enter__(self) -> DsmClient:
        return self

    def __exit__(self, *_exc) -> None:
        self.logout()

    @property
    def is_authenticated(self) -> bool:
        return bool(self.sid)

    # ---------- 연결 테스트 (로그인 전 가능) ----------

    def test_connection(self) -> DsmInfo:
        """SYNO.API.Info 조회로 DSM 응답 가능 여부 + Auth API 버전 확인.

        로그인 시도 전에 호출 — URL 오타/네트워크/SSL 문제를 빠르게 분리.
        """
        url = f"{self.base}/webapi/query.cgi"
        params = {
            "api": "SYNO.API.Info",
            "version": "1",
            "method": "query",
            "query": "SYNO.API.Auth",
        }
        try:
            r = self.session.get(
                url, params=params,
                verify=self.verify_ssl, timeout=self.DEFAULT_TIMEOUT,
            )
        except requests.exceptions.SSLError as e:
            raise DsmAuthError(
                f"SSL 인증서 오류: {e}\n"
                f"자가 서명 인증서를 사용 중이라면 설정에서 'SSL 검증 끄기' 를 켜주세요."
            ) from e
        except requests.exceptions.ConnectionError as e:
            raise DsmAuthError(f"DSM 에 접속할 수 없습니다: {e}") from e
        except requests.exceptions.Timeout as e:
            raise DsmAuthError("DSM 응답 시간 초과") from e

        if not r.ok:
            raise DsmAuthError(f"HTTP {r.status_code} — DSM URL 을 확인해 주세요")

        try:
            body = r.json()
        except ValueError as e:
            raise DsmAuthError(
                f"응답이 JSON 이 아닙니다 — DSM URL 이 맞는지, "
                f"다른 페이지가 응답하지 않는지 확인해 주세요. ({e})"
            ) from e

        if not body.get("success"):
            raise DsmAuthError(
                f"DSM 정보 조회 실패: {body}",
                code=(body.get("error") or {}).get("code"),
            )
        data = body.get("data", {}).get("SYNO.API.Auth", {})
        return DsmInfo(
            auth_min_version=int(data.get("minVersion", 1)),
            auth_max_version=int(data.get("maxVersion", 7)),
            raw=body,
        )

    # ---------- 로그인 ----------

    def login(self, account: str, password: str, *, otp_code: str = "") -> None:
        """로그인 성공 시 self.sid, self.synotoken 설정. 실패 시 DsmAuthError."""
        if not account or not password:
            raise DsmAuthError("관리자 ID 또는 비밀번호가 비어 있습니다.")

        url = f"{self.base}/webapi/auth.cgi"
        data = {
            "api": "SYNO.API.Auth",
            "version": "7",
            "method": "login",
            "account": account,
            "passwd": password,
            "session": _SESSION_NAME,
            "format": "sid",
            "enable_syno_token": "yes",
        }
        if otp_code:
            data["otp_code"] = otp_code

        try:
            r = self.session.post(
                url, data=data,
                verify=self.verify_ssl, timeout=self.DEFAULT_TIMEOUT,
            )
        except requests.exceptions.SSLError as e:
            raise DsmAuthError(f"SSL 인증서 오류: {e}") from e
        except requests.exceptions.ConnectionError as e:
            raise DsmAuthError(f"DSM 에 접속할 수 없습니다: {e}") from e
        except requests.exceptions.Timeout as e:
            raise DsmAuthError("DSM 응답 시간 초과") from e

        if not r.ok:
            raise DsmAuthError(f"HTTP {r.status_code}")

        try:
            body = r.json()
        except ValueError as e:
            raise DsmAuthError(f"응답이 JSON 이 아닙니다: {e}") from e

        if not body.get("success"):
            err = body.get("error") or {}
            code = err.get("code")
            raise DsmAuthError(explain_auth_error_code(code), code=code)

        payload = body.get("data") or {}
        sid = payload.get("sid")
        token = payload.get("synotoken")
        if not sid:
            raise DsmAuthError(
                "로그인 응답에 sid 가 없습니다 — DSM 버전 호환성 문제일 수 있습니다."
            )
        self.sid = sid
        self.synotoken = token

    # ---------- 로그아웃 ----------

    def logout(self) -> None:
        """베스트 에포트 — 실패해도 로컬 상태는 클리어."""
        if not self.sid:
            return
        url = f"{self.base}/webapi/auth.cgi"
        params = {
            "api": "SYNO.API.Auth",
            "version": "7",
            "method": "logout",
            "session": _SESSION_NAME,
        }
        try:
            self.session.get(
                url, params=params,
                verify=self.verify_ssl, timeout=10,
            )
        except requests.exceptions.RequestException:
            pass
        self.sid = None
        self.synotoken = None

    # ---------- 기본 호출 헬퍼 ----------

    def _call(
        self,
        api: str,
        method: str,
        version: str = "1",
        params: dict[str, Any] | None = None,
        http_method: str = "GET",
    ) -> dict[str, Any]:
        """SYNO.Core.* 같은 인증된 API 호출 헬퍼."""
        if not self.is_authenticated:
            raise DsmAuthError("로그인되어 있지 않습니다.")
        url = f"{self.base}/webapi/entry.cgi"
        request_params: dict[str, Any] = {
            "api": api,
            "method": method,
            "version": version,
            "_sid": self.sid,
        }
        if params:
            request_params.update(params)
        headers = {"X-SYNO-TOKEN": self.synotoken or ""}
        try:
            if http_method.upper() == "POST":
                r = self.session.post(
                    url, data=request_params, headers=headers,
                    verify=self.verify_ssl, timeout=self.DEFAULT_TIMEOUT,
                )
            else:
                r = self.session.get(
                    url, params=request_params, headers=headers,
                    verify=self.verify_ssl, timeout=self.DEFAULT_TIMEOUT,
                )
        except requests.exceptions.RequestException as e:
            raise DsmAuthError(f"네트워크 오류: {e}") from e
        if not r.ok:
            raise DsmAuthError(f"HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError as e:
            raise DsmAuthError(f"응답이 JSON 이 아닙니다: {e}") from e
        # 진단 캡처 — 호출 결과 그대로 (성공·실패 모두) 기록.
        if self._diag_buffer is not None:
            try:
                self._diag_buffer.append({
                    "api": api,
                    "method": method,
                    "version": version,
                    "http_method": http_method.upper(),
                    "params": {
                        k: v for k, v in (params or {}).items()
                        if k != "_sid"  # 세션 토큰 노출 방지
                    },
                    "response": body,
                })
            except Exception:
                pass
        if not body.get("success"):
            err = body.get("error") or {}
            code = err.get("code")
            raise DsmAuthError(
                f"{api}.{method} 실패 (code={code})", code=code,
            )
        return body.get("data") or {}

    # ---------- 사용자 조회 ----------

    def list_users(
        self,
        *,
        offset: int = 0,
        limit: int = -1,
        additional: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """DSM 전체 사용자 목록.

        반환: [{"name": str, "uid": int, "expired": str, "email": str?, ...}, ...]
        expired 값:
            "normal"  활성
            "now"     즉시 만료/비활성
            "yyyy-MM-dd" 특정일 만료
        """
        if additional is None:
            additional = ["email", "description", "expired", "cannot_chg_passwd"]
        # additional 은 JSON 배열 문자열로 보냄.
        import json as _json
        data = self._call(
            "SYNO.Core.User", "list", version="1",
            params={
                "offset": offset, "limit": limit,
                "additional": _json.dumps(additional),
            },
        )
        return list(data.get("users") or [])

    def get_password_policy(self) -> dict[str, Any]:
        """DSM 비밀번호 정책 조회.

        반환 예: {"min_length": 6, "mixed_case": True, "included_special": False, ...}
        """
        return self._call(
            "SYNO.Core.PasswordPolicy.Default", "get", version="1",
        )

    # ---------- 그룹 조회 ----------

    def list_groups(self) -> list[dict[str, Any]]:
        """DSM 그룹 목록."""
        data = self._call(
            "SYNO.Core.Group", "list", version="1",
            params={"offset": 0, "limit": -1},
        )
        return list(data.get("groups") or [])

    # ---------- 사용자 CRUD (B-2-b) ----------

    def create_user(
        self,
        name: str,
        password: str,
        *,
        email: str = "",
        description: str = "",
        expired: str = "normal",
        cannot_chg_passwd: bool = False,
    ) -> dict[str, Any]:
        """DSM 사용자 생성. expired='normal' 활성, 'now' 비활성으로 시작."""
        if not name or not password:
            raise DsmAuthError("사용자 이름·비밀번호가 비어 있습니다.")
        params: dict[str, Any] = {
            "name": name,
            "password": password,
            "expired": expired,
            "email": email,
            "description": description,
            "cannot_chg_passwd": "true" if cannot_chg_passwd else "false",
        }
        try:
            return self._call(
                "SYNO.Core.User", "create", version="1",
                params=params, http_method="POST",
            )
        except DsmAuthError as e:
            raise _with_user_api_hint(e) from e

    def set_user(
        self,
        name: str,
        *,
        password: str | None = None,
        email: str | None = None,
        description: str | None = None,
        expired: str | None = None,
    ) -> dict[str, Any]:
        """기존 사용자 속성 수정. None 으로 둔 항목은 그대로."""
        if not name:
            raise DsmAuthError("사용자 이름이 비어 있습니다.")
        params: dict[str, Any] = {"name": name}
        if password is not None:
            params["password"] = password
        if email is not None:
            params["email"] = email
        if description is not None:
            params["description"] = description
        if expired is not None:
            params["expired"] = expired
        try:
            return self._call(
                "SYNO.Core.User", "set", version="1",
                params=params, http_method="POST",
            )
        except DsmAuthError as e:
            raise _with_user_api_hint(e) from e

    def disable_user(self, name: str) -> dict[str, Any]:
        """expired='now' 으로 즉시 비활성."""
        return self.set_user(name, expired="now")

    def enable_user(self, name: str) -> dict[str, Any]:
        """expired='normal' 로 활성 복구."""
        return self.set_user(name, expired="normal")

    def delete_user(self, name: str) -> dict[str, Any]:
        """사용자 영구 삭제 — 비활성과 다름. 일반적으론 disable 권장."""
        if not name:
            raise DsmAuthError("사용자 이름이 비어 있습니다.")
        import json as _json
        return self._call(
            "SYNO.Core.User", "delete", version="1",
            params={"name": _json.dumps([name])},
            http_method="POST",
        )

    # ---------- 그룹 멤버십 ----------

    # SYNO.Core.Group.Member 직접 호출이 빌드별로 다르게 거부될 때 "다음 변종 시도"로
    # 간주할 코드 (조회·추가·제거 공통).
    #   102  API 미지원   103  메서드 미지원   104  API 버전 미지원
    #   3201 파라미터/버전 호환 오류 (DSM 별 SYNO.Core.Group.Member 차이)
    # 105 (권한 부족) 같은 의미 있는 오류는 폴백 안 함 — 그대로 전파.
    _GROUP_MEMBER_FALLBACK_CODES = {102, 103, 104, 3201}

    # Log Center API 의 빌드 차이를 흡수할 때 다음 변종으로 넘어갈 코드들.
    # 400(잘못된 파라미터) 도 추가 — 일부 빌드가 logtype 값을 다르게 받음.
    _LOG_API_FALLBACK_CODES = frozenset({102, 103, 104, 400, 3201})

    def add_user_to_group(self, user_name: str, group_name: str) -> dict[str, Any]:
        """사용자를 그룹에 추가 — DSM 빌드 차이를 흡수한 다중 변종 시도.

        SYNO.Core.Group.Member API 가 빌드마다 요구 파라미터·버전·메서드가 달라
        (code=3201/103/104 등으로 거부) 여러 조합을 순차 시도한다. 비어 있지 않은
        성공 응답이 나오면 그걸 반환. 모든 변종이 폴백 코드로 실패하면 명확한
        한글 안내와 함께 예외 — 운영자가 DSM 웹에서 직접 추가하도록.
        """
        return self._modify_group_membership("add", user_name, group_name)

    def remove_user_from_group(self, user_name: str, group_name: str) -> dict[str, Any]:
        """사용자를 그룹에서 제거 — add_user_to_group 과 동일한 다중 변종 전략."""
        return self._modify_group_membership("remove", user_name, group_name)

    def _modify_group_membership(
        self, method: str, user_name: str, group_name: str,
    ) -> dict[str, Any]:
        import json as _json
        user_json = _json.dumps([user_name])
        gid = self._find_group_gid(group_name.strip().lower())
        # (params, version, http_method) — 가장 흔한 조합부터.
        attempts: list[tuple[dict[str, Any], str, str]] = [
            ({"name": group_name, "user": user_json}, "1", "POST"),
            ({"name": group_name, "user": user_json}, "2", "POST"),
            ({"name": group_name, "user": user_json}, "1", "GET"),
            ({"name": group_name, "member": user_json}, "1", "POST"),
            ({"name": group_name, "user": user_name}, "1", "POST"),  # plain string
        ]
        if gid is not None:
            attempts += [
                ({"gid": gid, "user": user_json}, "1", "POST"),
                ({"gid": gid, "user": user_json}, "2", "POST"),
            ]
        last_err: DsmAuthError | None = None
        for params, ver, http in attempts:
            try:
                return self._call(
                    "SYNO.Core.Group.Member", method, version=ver,
                    params=dict(params), http_method=http,
                )
            except DsmAuthError as e:
                if e.code not in self._GROUP_MEMBER_FALLBACK_CODES:
                    raise  # 권한·기타 의미 있는 오류 — 그대로 전파
                last_err = e
                continue
        verb = "추가" if method == "add" else "제거"
        code = getattr(last_err, "code", None)
        raise DsmAuthError(
            f"이 DSM 빌드는 API 로 그룹 멤버 {verb}가 지원되지 않습니다 "
            f"(마지막 코드={code}). DSM 웹 관리자에 접속해 "
            f"사용자 '{user_name}' 를 '{group_name}' 그룹에 직접 {verb}해 주세요.",
            code=code,
        )

    def list_group_members(self, group_name: str) -> list[dict[str, Any]]:
        """그룹의 사용자 목록 — DSM 빌드 차이를 흡수한 다중 폴백.

        DSM 빌드마다 `SYNO.Core.Group.Member/list` 가 안 되거나 `additional`
        파라미터를 무시하는 등 동작이 제각각이라 여러 경로를 순차 시도한다.
        비어 있지 않은 첫 결과를 반환:

            A) Group.Member/list?name=  v1/v2/v3 GET, v1/v2 POST
            B) gid 확보 — Group/list, 안 되면 Group/get 응답에서 gid 추출
            C) Group.Member/list?gid=   v1/v2/v3 GET, v1 POST
            D) Group/get?name=  +additional=["member"]  v1/v2
            E) Group/get?gid=   +additional=["member"]  v1/v2
            F) Group/list +additional=["member"]  v1/v2
            G) User/list +additional=["groups"]  v1/v2/v3 → 그룹명으로 필터
            H) 사용자별 User/get +additional=["groups"] (최후 — 느림)
        """
        target = group_name.strip()
        target_lower = target.lower()

        # A) name 으로 시도
        for kw in (
            {"name": target, "version": "1"},
            {"name": target, "version": "2"},
            {"name": target, "version": "3"},
            {"name": target, "version": "1", "http_method": "POST"},
            {"name": target, "version": "2", "http_method": "POST"},
        ):
            members = self._try_group_member_list(**kw)
            if members:
                return members

        # B) gid 확보 — Group/list 가 gid 를 안 주는 빌드가 많아 Group/get 도 시도.
        gid = self._find_group_gid(target_lower)

        # C) gid 로 Group.Member/list 재시도
        if gid is not None:
            for kw in (
                {"gid": gid, "version": "1"},
                {"gid": gid, "version": "2"},
                {"gid": gid, "version": "3"},
                {"gid": gid, "version": "1", "http_method": "POST"},
            ):
                members = self._try_group_member_list(**kw)
                if members:
                    return members

        # D) Group/get + additional 변종 — 이 빌드의 get 은 gid 를 주므로
        #    member 도 줄 가능성. additional 값 이름·형식은 빌드마다 달라 여럿 시도.
        _add_variants = (
            '["member"]', '["members"]', '["users"]', '["user"]',
            "member", "members", "users",
        )
        _selectors: list[dict[str, Any]] = [{"name": target}]
        if gid is not None:
            _selectors.append({"gid": gid})
        for sel in _selectors:
            for ver in ("1", "2", "3"):
                # additional 없이 한 번 (멤버 기본 포함 빌드)
                out = self._try_group_get_members(dict(sel), target_lower, ver)
                if out:
                    return out
                for av in _add_variants:
                    out = self._try_group_get_members(
                        {**sel, "additional": av}, target_lower, ver,
                    )
                    if out:
                        return out

        # F) Group/list + additional=member  (v1 무시되더라도 v2 가 honor 하는 빌드)
        for ver in ("1", "2"):
            try:
                data = self._call(
                    "SYNO.Core.Group", "list", version=ver,
                    params={"offset": 0, "limit": -1, "additional": '["member"]'},
                )
                out = _extract_members_from_group_object(data, target_lower)
                if out:
                    return out
            except DsmAuthError:
                pass

        # G) User/list + additional=groups  (v1 무시되면 v2/v3 시도)
        for ver in ("1", "2", "3"):
            out = self._members_via_user_filter(target_lower, version=ver)
            if out:
                return out

        # H) 최후 — 사용자별 User/get 으로 그룹 멤버십 하나씩 조회.
        return self._members_via_per_user_get(target_lower)

    def _try_group_member_list(
        self,
        *,
        name: str | None = None,
        gid: int | None = None,
        version: str = "1",
        http_method: str = "GET",
    ) -> list[dict[str, Any]]:
        """SYNO.Core.Group.Member/list 한 번 시도. 에러는 fallback codes 만 흡수."""
        params: dict[str, Any] = {"offset": 0, "limit": -1}
        if name is not None:
            params["name"] = name
        if gid is not None:
            params["gid"] = gid
        try:
            data = self._call(
                "SYNO.Core.Group.Member", "list", version=version,
                params=params, http_method=http_method,
            )
            return _extract_group_member_list(data)
        except DsmAuthError as e:
            if e.code not in self._GROUP_MEMBER_FALLBACK_CODES:
                raise
            return []

    def _try_group_get_members(
        self, params: dict[str, Any], target_lower: str, version: str,
    ) -> list[dict[str, Any]]:
        """SYNO.Core.Group/get 한 번 시도 → 응답에서 해당 그룹 멤버 추출."""
        try:
            data = self._call(
                "SYNO.Core.Group", "get", version=version, params=params,
            )
        except DsmAuthError:
            return []
        return _extract_members_from_group_object(data, target_lower)

    def _members_via_per_user_get(self, target_lower: str) -> list[dict[str, Any]]:
        """모든 사용자명을 받은 뒤 SYNO.Core.User/get 으로 한 명씩 그룹 조회.

        가장 느리지만(사용자 수만큼 API 호출) `additional` 무시 빌드에서도
        동작할 가능성이 있는 마지막 폴백. get 응답에 그룹 정보가 없으면 빈
        리스트 반환.
        """
        try:
            users = self.list_users()
        except DsmAuthError:
            return []
        names = [u.get("name") for u in users if u.get("name")]
        if not names:
            return []
        import json as _json
        members: list[dict[str, Any]] = []
        for nm in names:
            user_obj: dict[str, Any] | None = None
            for ver in ("1", "2", "3"):
                try:
                    data = self._call(
                        "SYNO.Core.User", "get", version=ver,
                        params={"name": nm, "additional": _json.dumps(["groups"])},
                    )
                except DsmAuthError:
                    continue
                # 응답 형태 변종 — {"users":[{...}]} 또는 {"user":{...}} 또는 평면 dict
                if isinstance(data, dict):
                    if isinstance(data.get("users"), list) and data["users"]:
                        user_obj = data["users"][0]
                    elif isinstance(data.get("user"), dict):
                        user_obj = data["user"]
                    elif "name" in data:
                        user_obj = data
                if user_obj is not None:
                    break
            if not isinstance(user_obj, dict):
                continue
            ug: list = []
            for key in ("groups", "group", "member_of", "groupList", "Groups"):
                v = user_obj.get(key)
                if v:
                    ug = list(v)
                    break
            for g in ug:
                gn = (g.get("name") if isinstance(g, dict) else str(g)) or ""
                if gn.strip().lower() == target_lower:
                    members.append({"name": nm})
                    break
        return members

    # ---------- 진단 덤프 ----------

    def collect_group_member_diagnostics(
        self, group_name: str,
    ) -> dict[str, Any]:
        """list_group_members 를 진단 캡처와 함께 한 번 실행.

        Returns:
            {
                "group_name": ...,
                "final_members_count": int,
                "final_members": [...],
                "attempts": [{"api","method","version","params","response"}, ...],
            }
        """
        self._diag_buffer = []
        members: list[dict[str, Any]] = []
        try:
            members = self.list_group_members(group_name)
        except Exception as e:
            self._diag_buffer.append({
                "step": "exception", "error": str(e),
            })
        attempts = list(self._diag_buffer)
        self._diag_buffer = None
        return {
            "group_name": group_name,
            "final_members_count": len(members),
            "final_members": members,
            "attempts": attempts,
        }

    # ---------- 자료실(NAS) 접속 로그 ----------

    def list_audit_logs(
        self,
        logtype: str,
        *,
        start_epoch: int | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """DSM Log Center 의 로그를 가져온다 — 빌드별 API 차이를 다중 변종으로 흡수.

        Args:
            logtype: 'file_transfer' 또는 'connection' (그 외 값은 그대로 전달).
            start_epoch: 이 epoch 이후 로그만(증분 수집). None 이면 전체.
            limit: 한 번에 가져올 최대 항목 수.

        Returns:
            DSM 가 돌려 준 항목 dict 의 리스트. 정규화는 nas_log_service 가 담당.
        """
        base_params: dict[str, Any] = {
            "logtype": str(logtype or "").strip(),
            "start": 0,
            "limit": int(limit),
        }
        if start_epoch:
            # 빌드마다 키 이름이 다른 것에 대비해 두 가지를 함께 보낸다 — 모르는 키는 무시됨.
            base_params["from"] = int(start_epoch)
            base_params["start_date"] = int(start_epoch)

        # (api, method, version, http_method, extra_params).
        # SYNO.LogCenter.Log 는 '로그 센터' 패키지가 들어 있을 때만 응답한다 — 패키지가
        # 저장하는 DB(WebDAV/SMB/File Station 등)는 SyslogClient.Log 에는 보통 안 잡혀
        # 빈 응답이 오므로, 빈 응답이어도 다음 변종을 계속 시도해야 한다.
        attempts: list[tuple[str, str, str, str, dict[str, Any]]] = [
            ("SYNO.LogCenter.Log",         "list", "1", "GET", {}),
            ("SYNO.LogCenter.Log",         "list", "2", "GET", {}),
            ("SYNO.Core.SyslogClient.Log", "list", "1", "GET", {}),
            ("SYNO.Core.SyslogClient.Log", "list", "2", "GET", {}),
            ("SYNO.Core.SyslogClient.Status.Log", "list", "1", "GET", {}),
        ]
        last_err: DsmAuthError | None = None
        empty_seen = False
        for api, method, ver, http, extra in attempts:
            params = dict(base_params)
            params.update(extra)
            try:
                data = self._call(api, method, version=ver, params=params, http_method=http)
            except DsmAuthError as e:
                if e.code is not None and e.code not in self._LOG_API_FALLBACK_CODES:
                    raise  # 권한·인증 등 의미 있는 오류는 그대로 전파
                last_err = e
                continue
            items = _extract_audit_log_items(data)
            if items:
                return items  # 첫 non-empty 응답 — 다른 변종 시도 안 함
            empty_seen = True
            # 빈 응답이면 다음 변종 시도 (다른 API/버전엔 데이터가 있을 수도 있음)

        if empty_seen:
            # 모든 변종이 '성공 + 빈 응답' — 해당 logtype 에 실제로 데이터가 없는 것.
            return []

        # 한 번도 성공한 변종이 없음 — 마지막 코드와 함께 통일된 한글 안내.
        code = getattr(last_err, "code", None)
        raise DsmAuthError(
            "이 DSM 빌드에서 Log Center API 로 로그를 가져올 수 없습니다 "
            f"(마지막 코드={code}). DSM '패키지 센터 → 로그 센터 → 설정' 에서 "
            "기록 대상(WebDAV / SMB / File Station 등) 이 켜져 있는지 확인하고, "
            "그래도 안 되면 '진단: 응답 원본 저장' 으로 파일을 만들어 주세요.",
            code=code,
        )

    def collect_audit_log_diagnostics(self) -> dict[str, Any]:
        """list_audit_logs 의 모든 변종 시도와 응답을 캡처해 dict 로 반환.

        UI 의 '응답 원본 저장' 버튼이 이 결과를 JSON 으로 떠서
        data/dumps/<ts>_dsm_audit_diag.json 에 저장 — fallback 추가 작업에 활용.

        로그 센터 패키지 빌드는 logtype 별칭(`webdav`, `filestation`, `smb` 등)에 데이터
        가 있을 수 있어 한 번에 여러 logtype 을 시도해 어느 조합에서 데이터가 잡히는지
        한눈에 보여 준다.
        """
        self._diag_buffer = []
        probe_logtypes = [
            "file_transfer", "FileTransfer", "transfer", "file",
            "webdav", "WebDAV",
            "filestation", "FileStation", "file_station",
            "smb", "SMB",
            "audit", "audit_log",
            "connection",  # 기준선 — 보통 잘 잡힘
        ]
        results: dict[str, dict[str, Any]] = {}
        errors: list[dict[str, Any]] = []
        for logtype in probe_logtypes:
            try:
                got = self.list_audit_logs(logtype=logtype, limit=20)
                results[logtype] = {"count": len(got), "sample": got[:3]}
            except Exception as e:
                errors.append({"logtype": logtype, "error": str(e)})
        attempts = list(self._diag_buffer or [])
        self._diag_buffer = None
        # 'logtype 중 어디에 데이터가 있었는지' 요약을 맨 앞에 박아 눈에 잘 띄게.
        with_data = sorted(
            (k for k, v in results.items() if v["count"] > 0),
            key=lambda k: -results[k]["count"],
        )
        return {
            "logtypes_with_data": with_data,
            "summary": {k: v["count"] for k, v in results.items()},
            "probes": results,
            "errors": errors,
            "attempts": attempts,
        }

    def _find_group_gid(self, target_lower: str) -> int | None:
        """그룹 gid 를 찾아 반환.

        Group/list 가 gid 를 안 주는 빌드가 있어 Group/get 도 시도한다 —
        이 환경에서 Group/get?name=… 응답이 gid 를 포함한다.
        """
        # 1) Group/list
        try:
            for g in self.list_groups():
                if (g.get("name") or "").strip().lower() != target_lower:
                    continue
                gid = _coerce_gid(g)
                if gid is not None:
                    return gid
        except DsmAuthError:
            pass
        # 2) Group/get?name=… (이 빌드는 여기서 gid 를 준다)
        for ver in ("1", "2"):
            try:
                data = self._call(
                    "SYNO.Core.Group", "get", version=ver,
                    params={"name": target_lower},  # 대소문자 무시 서버 측 검색 가정
                )
            except DsmAuthError:
                continue
            candidates: list[dict[str, Any]] = []
            if isinstance(data, dict):
                v = data.get("groups")
                if isinstance(v, dict):
                    candidates.append(v)
                elif isinstance(v, list):
                    candidates.extend(x for x in v if isinstance(x, dict))
                if isinstance(data.get("group"), dict):
                    candidates.append(data["group"])
                if "gid" in data:
                    candidates.append(data)
            for g in candidates:
                if (g.get("name") or "").strip().lower() != target_lower:
                    # name 이 비어있거나 다르면 — 그래도 단일 결과면 받아들임
                    if len(candidates) != 1:
                        continue
                gid = _coerce_gid(g)
                if gid is not None:
                    return gid
        return None

    def _members_via_user_filter(
        self, target_lower: str, *, version: str = "1",
    ) -> list[dict[str, Any]]:
        """모든 사용자를 받아 사용자별 그룹 멤버십 필드를 검사 (지정 버전으로)."""
        import json as _json
        try:
            data = self._call(
                "SYNO.Core.User", "list", version=version,
                params={
                    "offset": 0, "limit": -1,
                    "additional": _json.dumps(["groups"]),
                },
            )
            users = list(data.get("users") or [])
        except DsmAuthError:
            users = []
        members: list[dict[str, Any]] = []
        for u in users:
            user_groups: list = []
            for key in ("groups", "group", "member_of", "groupList", "Groups"):
                v = u.get(key)
                if v:
                    user_groups = list(v)
                    break
            if not user_groups:
                continue
            for g in user_groups:
                if isinstance(g, dict):
                    gn = (g.get("name") or "").strip().lower()
                else:
                    gn = str(g).strip().lower()
                if gn == target_lower:
                    members.append(u)
                    break
        return members


def _coerce_gid(group_obj: dict[str, Any]) -> int | None:
    """그룹 dict 에서 gid/id/group_id 중 정수형을 추출."""
    for key in ("gid", "id", "group_id"):
        v = group_obj.get(key)
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.isdigit():
            return int(v)
    return None


def _extract_members_from_group_object(
    data: dict[str, Any], target_lower: str,
) -> list[dict[str, Any]]:
    """Group/get 또는 Group/list?additional=member 응답에서 특정 그룹의 멤버 추출.

    응답 구조 변종:
        {"groups": [{"name":"…","member":[...]}]}    list/additional=member
        {"groups": {"name":"…","member":[...]}}      list 인데 dict 단일
        {"group":  {"name":"…","member":[...]}}      get
        {                                             get 인데 group key 없는 빌드
          "name":"…", "member":[...]
        }
    member 키도 빌드별 변종 — member / members / users / user.
    """
    if not isinstance(data, dict):
        return []
    candidates: list[dict[str, Any]] = []
    for key in ("groups", "group"):
        v = data.get(key)
        if isinstance(v, dict):
            candidates.append(v)
        elif isinstance(v, list):
            candidates.extend(g for g in v if isinstance(g, dict))
    # data 자체가 그룹 객체일 가능성
    if not candidates and ("name" in data and (
        "member" in data or "members" in data or "users" in data
    )):
        candidates.append(data)
    for g in candidates:
        if (g.get("name") or "").strip().lower() != target_lower:
            continue
        raw = g.get("member") or g.get("members") or g.get("users") or g.get("user")
        if raw:
            out = _extract_group_member_list({"users": raw})
            if out:
                return out
    return []


def _extract_audit_log_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    """SYNO.Core.SyslogClient.Log / SYNO.LogCenter.Log 응답에서 항목 배열을 안전하게 추출.

    빌드별 응답 구조가 달라 흔한 변종을 모두 시도:
        data.items   — 7.x 다수 빌드
        data.data    — 일부 빌드
        data.logs    — Log Center 패키지
        data 자체가 list — 드물게
    """
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for k in ("items", "data", "logs", "log", "entries"):
        v = data.get(k)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
    return []


def _extract_group_member_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    """SYNO.Core.Group.Member/list 응답에서 사용자 배열을 안전하게 추출.

    DSM 빌드 차이로 응답 필드명이 제각각이라 흔한 변종을 모두 시도:
        data.users   — 7.x 표준
        data.members — 일부 6.x/7.0 빌드
        data 자체가 list — 드물게 발생
    또한 각 항목이 dict 가 아닌 string user_name 만 있는 경우도 변환.
    """
    if not isinstance(data, dict):
        return []
    candidates = (
        data.get("users"),
        data.get("members"),
        data.get("user"),
    )
    raw = next((c for c in candidates if c), None)
    if raw is None and isinstance(data.get("data"), list):
        raw = data["data"]
    if not raw:
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            if item.get("name"):
                out.append(item)
        elif isinstance(item, str) and item:
            out.append({"name": item})
    return out
