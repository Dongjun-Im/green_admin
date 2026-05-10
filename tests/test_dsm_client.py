"""DSM 클라이언트 — 인증·연결 테스트 단위 테스트.

실제 NAS 호출은 하지 않고 requests Session 에 mock adapter 를 붙여
응답을 시뮬레이션. SID·SynoToken 추출, 에러 코드 매핑, 2FA 흐름 등을
회귀 보호.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import requests

from core.dsm_client import (
    DsmAuthError,
    DsmClient,
    explain_auth_error_code,
)


class _FakeResponse:
    def __init__(self, status: int = 200, body: dict | None = None) -> None:
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = body or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")


# ---------- explain_auth_error_code ----------

def test_explain_auth_error_code_known():
    assert "비밀번호" in explain_auth_error_code(400)
    assert "2단계" in explain_auth_error_code(403)
    assert "잠겼습니다" in explain_auth_error_code(406)


def test_explain_auth_error_code_unknown():
    assert "999" in explain_auth_error_code(999)


def test_explain_auth_error_code_none():
    assert explain_auth_error_code(None)


# ---------- test_connection ----------

def test_connection_success(monkeypatch):
    body = {
        "success": True,
        "data": {
            "SYNO.API.Auth": {"path": "auth.cgi", "minVersion": 1, "maxVersion": 7},
        },
    }
    with patch.object(
        requests.Session, "get", return_value=_FakeResponse(200, body),
    ):
        c = DsmClient("https://dsm.example.com")
        info = c.test_connection()
        assert info.auth_max_version == 7
        assert info.auth_min_version == 1


def test_connection_ssl_error_raises_auth_error():
    with patch.object(
        requests.Session, "get",
        side_effect=requests.exceptions.SSLError("self-signed"),
    ):
        c = DsmClient("https://dsm.example.com")
        with pytest.raises(DsmAuthError) as exc:
            c.test_connection()
        assert "SSL" in str(exc.value)


def test_connection_connection_error_raises_auth_error():
    with patch.object(
        requests.Session, "get",
        side_effect=requests.exceptions.ConnectionError("DNS"),
    ):
        c = DsmClient("https://dsm.example.com")
        with pytest.raises(DsmAuthError):
            c.test_connection()


def test_connection_non_json_response():
    """DSM 가 아니라 다른 페이지가 응답 시 JSON 파싱 실패."""
    class HtmlResp(_FakeResponse):
        def json(self):
            raise ValueError("not json")

    with patch.object(
        requests.Session, "get", return_value=HtmlResp(200, {}),
    ):
        c = DsmClient("https://dsm.example.com")
        with pytest.raises(DsmAuthError) as exc:
            c.test_connection()
        assert "JSON" in str(exc.value)


# ---------- login ----------

def test_login_success_extracts_sid_and_token():
    body = {
        "success": True,
        "data": {"sid": "fake_sid_xyz", "synotoken": "fake_token_abc"},
    }
    with patch.object(
        requests.Session, "post", return_value=_FakeResponse(200, body),
    ):
        c = DsmClient("https://dsm.example.com")
        c.login("admin", "pw")
        assert c.is_authenticated
        assert c.sid == "fake_sid_xyz"
        assert c.synotoken == "fake_token_abc"


def test_login_wrong_password_maps_to_korean_message():
    body = {"success": False, "error": {"code": 400}}
    with patch.object(
        requests.Session, "post", return_value=_FakeResponse(200, body),
    ):
        c = DsmClient("https://dsm.example.com")
        with pytest.raises(DsmAuthError) as exc:
            c.login("admin", "wrong")
        assert exc.value.code == 400
        assert "비밀번호" in str(exc.value)


def test_login_2fa_required_signaled():
    body = {"success": False, "error": {"code": 403}}
    with patch.object(
        requests.Session, "post", return_value=_FakeResponse(200, body),
    ):
        c = DsmClient("https://dsm.example.com")
        with pytest.raises(DsmAuthError) as exc:
            c.login("admin", "pw")
        assert exc.value.code == 403
        assert "2단계" in str(exc.value)


def test_login_with_otp_code_passes_param():
    """OTP 코드가 폼 데이터에 포함되는지."""
    captured: dict = {}

    def fake_post(self, url, data=None, **kwargs):
        captured["data"] = data
        return _FakeResponse(200, {
            "success": True, "data": {"sid": "x", "synotoken": "y"},
        })

    with patch.object(requests.Session, "post", fake_post):
        c = DsmClient("https://dsm.example.com")
        c.login("admin", "pw", otp_code="123456")
    assert captured["data"]["otp_code"] == "123456"
    assert captured["data"]["enable_syno_token"] == "yes"


def test_login_empty_credentials_rejected_locally():
    c = DsmClient("https://dsm.example.com")
    with pytest.raises(DsmAuthError):
        c.login("", "pw")
    with pytest.raises(DsmAuthError):
        c.login("admin", "")


def test_login_response_missing_sid_treated_as_failure():
    body = {"success": True, "data": {"synotoken": "only_token"}}
    with patch.object(
        requests.Session, "post", return_value=_FakeResponse(200, body),
    ):
        c = DsmClient("https://dsm.example.com")
        with pytest.raises(DsmAuthError) as exc:
            c.login("admin", "pw")
        assert "sid" in str(exc.value).lower()


# ---------- 컨텍스트 매니저 ----------

def test_context_manager_logs_out_on_exit():
    calls = {"logout": False}

    def fake_post(self, url, data=None, **kwargs):
        return _FakeResponse(200, {
            "success": True, "data": {"sid": "x", "synotoken": "y"},
        })

    def fake_get(self, url, params=None, **kwargs):
        if (params or {}).get("method") == "logout":
            calls["logout"] = True
        return _FakeResponse(200, {"success": True})

    with patch.object(requests.Session, "post", fake_post), \
         patch.object(requests.Session, "get", fake_get):
        with DsmClient("https://dsm.example.com") as c:
            c.login("admin", "pw")
            assert c.is_authenticated
        # __exit__ 후 로그아웃 호출됨 + 로컬 상태 클리어
        assert calls["logout"] is True
        assert not c.is_authenticated


def test_url_trailing_slash_normalized():
    c = DsmClient("https://dsm.example.com/")
    assert c.base == "https://dsm.example.com"


# ---------- 사용자 / 그룹 / 정책 조회 (B-2-a) ----------

def _logged_in_client():
    """로그인 상태 더미 클라이언트 — _call 만 mock."""
    c = DsmClient("https://dsm.example.com")
    c.sid = "fake_sid"
    c.synotoken = "fake_token"
    return c


def test_list_users_returns_array(monkeypatch):
    captured: dict = {}

    def fake_get(self, url, params=None, headers=None, **kwargs):
        captured["params"] = params or {}
        return _FakeResponse(200, {
            "success": True,
            "data": {
                "users": [
                    {"name": "admin", "uid": 1024, "expired": "normal"},
                    {"name": "guest", "uid": 1025, "expired": "now"},
                ],
                "total": 2,
            },
        })

    with patch.object(requests.Session, "get", fake_get):
        c = _logged_in_client()
        users = c.list_users()
    assert len(users) == 2
    assert users[0]["name"] == "admin"
    assert users[1]["expired"] == "now"
    assert captured["params"]["api"] == "SYNO.Core.User"
    assert captured["params"]["method"] == "list"
    # additional 은 JSON 문자열로 직렬화되어 넘어감
    assert "expired" in captured["params"]["additional"]


def test_list_users_requires_login():
    c = DsmClient("https://dsm.example.com")
    with pytest.raises(DsmAuthError) as exc:
        c.list_users()
    assert "로그인" in str(exc.value)


def test_get_password_policy(monkeypatch):
    body = {
        "success": True,
        "data": {
            "min_length": 8,
            "mixed_case": True,
            "included_special": False,
            "included_numeric": True,
        },
    }
    with patch.object(
        requests.Session, "get", return_value=_FakeResponse(200, body),
    ):
        c = _logged_in_client()
        policy = c.get_password_policy()
    assert policy["min_length"] == 8
    assert policy["mixed_case"] is True


def test_list_groups(monkeypatch):
    body = {
        "success": True,
        "data": {
            "groups": [
                {"name": "users", "gid": 100},
                {"name": "자료실 회원", "gid": 1024},
            ],
            "total": 2,
        },
    }
    with patch.object(
        requests.Session, "get", return_value=_FakeResponse(200, body),
    ):
        c = _logged_in_client()
        groups = c.list_groups()
    assert {g["name"] for g in groups} == {"users", "자료실 회원"}


def test_call_propagates_dsm_error_code(monkeypatch):
    """API 에러 응답이 DsmAuthError(code=...) 로 변환된다."""
    body = {"success": False, "error": {"code": 402}}
    with patch.object(
        requests.Session, "get", return_value=_FakeResponse(200, body),
    ):
        c = _logged_in_client()
        with pytest.raises(DsmAuthError) as exc:
            c.list_users()
    assert exc.value.code == 402


# ---------- 사용자 CRUD (B-2-b) ----------

def test_create_user_sends_required_params():
    captured: dict = {}

    def fake_post(self, url, data=None, headers=None, **kwargs):
        captured["data"] = data or {}
        return _FakeResponse(200, {"success": True, "data": {}})

    with patch.object(requests.Session, "post", fake_post):
        c = _logged_in_client()
        c.create_user("hong", "pw1234", email="hong@x.com", description="자료실")
    d = captured["data"]
    assert d["api"] == "SYNO.Core.User"
    assert d["method"] == "create"
    assert d["name"] == "hong"
    assert d["password"] == "pw1234"
    assert d["email"] == "hong@x.com"
    assert d["expired"] == "normal"


def test_create_user_rejects_empty():
    c = _logged_in_client()
    with pytest.raises(DsmAuthError):
        c.create_user("", "pw")
    with pytest.raises(DsmAuthError):
        c.create_user("hong", "")


def test_create_user_3121_gets_korean_hint():
    """code=3121 → 비밀번호 정책·사용자명 관련 한글 안내가 메시지에 붙는다."""
    body = {"success": False, "error": {"code": 3121}}
    with patch.object(requests.Session, "post", return_value=_FakeResponse(200, body)):
        c = _logged_in_client()
        with pytest.raises(DsmAuthError) as exc:
            c.create_user("hong", "weakpw")
    assert exc.value.code == 3121
    msg = str(exc.value)
    assert "비밀번호" in msg or "사용자명" in msg
    assert "code=3121" in msg  # 원본 코드도 유지


def test_create_user_unknown_code_no_hint():
    """모르는 코드는 기존 generic 메시지 그대로 (힌트 없음)."""
    body = {"success": False, "error": {"code": 9999}}
    with patch.object(requests.Session, "post", return_value=_FakeResponse(200, body)):
        c = _logged_in_client()
        with pytest.raises(DsmAuthError) as exc:
            c.create_user("hong", "pw")
    assert exc.value.code == 9999
    assert str(exc.value) == "SYNO.Core.User.create 실패 (code=9999)"


def test_set_user_password_error_gets_hint():
    """set_user 로 비밀번호 변경 시 정책 코드도 한글 안내 부착."""
    body = {"success": False, "error": {"code": 3120}}
    with patch.object(requests.Session, "post", return_value=_FakeResponse(200, body)):
        c = _logged_in_client()
        with pytest.raises(DsmAuthError) as exc:
            c.set_user("hong", password="123")
    assert exc.value.code == 3120
    assert "단순" in str(exc.value) or "비밀번호" in str(exc.value)


def test_disable_user_sets_expired_now():
    captured: dict = {}

    def fake_post(self, url, data=None, headers=None, **kwargs):
        captured["data"] = data or {}
        return _FakeResponse(200, {"success": True, "data": {}})

    with patch.object(requests.Session, "post", fake_post):
        c = _logged_in_client()
        c.disable_user("hong")
    assert captured["data"]["method"] == "set"
    assert captured["data"]["name"] == "hong"
    assert captured["data"]["expired"] == "now"


def test_enable_user_sets_expired_normal():
    captured: dict = {}

    def fake_post(self, url, data=None, headers=None, **kwargs):
        captured["data"] = data or {}
        return _FakeResponse(200, {"success": True, "data": {}})

    with patch.object(requests.Session, "post", fake_post):
        c = _logged_in_client()
        c.enable_user("hong")
    assert captured["data"]["expired"] == "normal"


def test_delete_user_sends_json_array():
    captured: dict = {}

    def fake_post(self, url, data=None, headers=None, **kwargs):
        captured["data"] = data or {}
        return _FakeResponse(200, {"success": True, "data": {}})

    with patch.object(requests.Session, "post", fake_post):
        c = _logged_in_client()
        c.delete_user("hong")
    # 이름은 JSON 배열 문자열로 보냄
    assert "hong" in captured["data"]["name"]
    assert "[" in captured["data"]["name"]


def _membership_fake(member_response_or_list, *, capture: list | None = None):
    """add/remove 용 가짜 get/post — SYNO.Core.Group.Member 응답만 지정, 나머지는 빈 성공.

    member_response_or_list: 단일 _FakeResponse 면 매번 그것, list 면 호출 순서대로 pop.
    capture: 주어지면 SYNO.Core.Group.Member 호출의 (merged params dict) 를 append.
    """
    def fake(self, url, params=None, headers=None, data=None, **kwargs):
        merged = dict(params or {})
        if data:
            merged.update(data)
        if merged.get("api") == "SYNO.Core.Group.Member":
            if capture is not None:
                capture.append(merged)
            if isinstance(member_response_or_list, list):
                return member_response_or_list.pop(0)
            return member_response_or_list
        # gid 조회용(SYNO.Core.Group) 등 — 빈 성공 → gid 못 찾음(None)
        return _FakeResponse(200, {"success": True, "data": {}})
    return fake


def test_add_user_to_group():
    cap: list = []
    fake = _membership_fake(_FakeResponse(200, {"success": True, "data": {}}), capture=cap)
    with patch.object(requests.Session, "post", fake), patch.object(requests.Session, "get", fake):
        c = _logged_in_client()
        c.add_user_to_group("hong", "자료실 회원")
    # 첫 변종 = SYNO.Core.Group.Member/add v1 POST, name+user(JSON)
    first = cap[0]
    assert first["api"] == "SYNO.Core.Group.Member"
    assert first["method"] == "add"
    assert first["name"] == "자료실 회원"
    assert "hong" in first["user"]


def test_remove_user_from_group():
    cap: list = []
    fake = _membership_fake(_FakeResponse(200, {"success": True, "data": {}}), capture=cap)
    with patch.object(requests.Session, "post", fake), patch.object(requests.Session, "get", fake):
        c = _logged_in_client()
        c.remove_user_from_group("hong", "자료실 회원")
    assert cap[0]["method"] == "remove"


def test_add_user_to_group_falls_back_through_variants():
    """첫 변종이 code=3201 로 거부되면 다음 변종 시도 — 두 번째(v2)에서 성공."""
    cap: list = []
    fake = _membership_fake([
        _FakeResponse(200, {"success": False, "error": {"code": 3201}}),  # v1 POST
        _FakeResponse(200, {"success": True, "data": {}}),                # v2 POST → 성공
    ], capture=cap)
    with patch.object(requests.Session, "post", fake), patch.object(requests.Session, "get", fake):
        c = _logged_in_client()
        c.add_user_to_group("hong", "자료실 회원")
    assert [m["version"] for m in cap] == ["1", "2"]


def test_add_user_to_group_propagates_non_fallback_error():
    """폴백 코드가 아닌 오류(예: 권한 105)는 즉시 전파 — 다음 변종 시도 안 함."""
    fake = _membership_fake(_FakeResponse(200, {"success": False, "error": {"code": 105}}))
    with patch.object(requests.Session, "post", fake), patch.object(requests.Session, "get", fake):
        c = _logged_in_client()
        with pytest.raises(DsmAuthError) as exc:
            c.add_user_to_group("hong", "자료실 회원")
    assert exc.value.code == 105


def test_add_user_to_group_all_variants_fail_gives_clear_error():
    """모든 변종이 폴백 코드(3201)로 실패하면 'DSM 웹에서 직접 추가' 안내 예외."""
    fake = _membership_fake(_FakeResponse(200, {"success": False, "error": {"code": 3201}}))
    with patch.object(requests.Session, "post", fake), patch.object(requests.Session, "get", fake):
        c = _logged_in_client()
        with pytest.raises(DsmAuthError) as exc:
            c.add_user_to_group("hong", "자료실 회원")
    msg = str(exc.value)
    assert "직접" in msg
    assert "hong" in msg and "자료실 회원" in msg
    assert exc.value.code == 3201


def test_list_group_members():
    body = {
        "success": True,
        "data": {
            "users": [{"name": "hong"}, {"name": "kim"}],
            "total": 2,
        },
    }
    with patch.object(
        requests.Session, "get", return_value=_FakeResponse(200, body),
    ):
        c = _logged_in_client()
        members = c.list_group_members("자료실 회원")
    assert {m["name"] for m in members} == {"hong", "kim"}


def _make_fake_get(responses_by_api: dict[str, object]):
    """API 이름으로 응답을 매칭하는 fake get/post.

    list_group_members 가 거치는 호출 시퀀스 (Group.Member/list, Group/get,
    Group/list, User/list) 를 각자 분리해서 mock — 호출 순서를 일일이
    세는 것보다 안정적. POST/GET 둘 다 같은 매핑 사용.
    """
    counters: dict[str, int] = {k: 0 for k in responses_by_api}

    def fake_request(self, url, params=None, headers=None, data=None, **kwargs):
        # POST 는 params= 가 비어 있고 data= 에 들어있을 수 있음.
        merged = dict(params or {})
        if data:
            merged.update(data)
        api = merged.get("api", "")
        if api in responses_by_api:
            entry = responses_by_api[api]
            if isinstance(entry, list):
                idx = counters[api]
                counters[api] = idx + 1
                if idx < len(entry):
                    return entry[idx]
                return _FakeResponse(200, {"success": True, "data": {}})
            return entry
        return _FakeResponse(200, {"success": True, "data": {}})

    return fake_request


@pytest.mark.parametrize("error_code", [102, 103, 104, 3201])
def test_list_group_members_falls_back_for_known_codes(error_code):
    """SYNO.Core.Group.Member/list 가 미지원(103) 또는 파라미터(3201) 실패 시 user-filter 폴백."""
    user_body = {
        "success": True,
        "data": {
            "users": [
                {"name": "hong", "groups": [{"name": "자료실 회원"}, {"name": "users"}]},
                {"name": "kim",  "groups": [{"name": "users"}]},
                {"name": "lee",  "groups": [{"name": "자료실 회원"}]},
            ],
            "total": 3,
        },
    }
    fake_get = _make_fake_get({
        "SYNO.Core.Group.Member": _FakeResponse(
            200, {"success": False, "error": {"code": error_code}},
        ),
        "SYNO.Core.Group": _FakeResponse(200, {"success": True, "data": {"groups": []}}),
        "SYNO.Core.User": _FakeResponse(200, user_body),
    })
    with patch.object(requests.Session, "get", fake_get), patch.object(requests.Session, "post", fake_get):
        c = _logged_in_client()
        members = c.list_group_members("자료실 회원")
    assert {m["name"] for m in members} == {"hong", "lee"}


def test_list_group_members_falls_back_with_string_groups():
    """일부 DSM 응답에서 groups 가 문자열 리스트로 올 수 있다."""
    user_body = {
        "success": True,
        "data": {
            "users": [
                {"name": "hong", "groups": ["자료실 회원", "users"]},
                {"name": "kim",  "groups": ["users"]},
            ],
        },
    }
    fake_get = _make_fake_get({
        "SYNO.Core.Group.Member": _FakeResponse(
            200, {"success": False, "error": {"code": 103}},
        ),
        "SYNO.Core.Group": _FakeResponse(200, {"success": True, "data": {"groups": []}}),
        "SYNO.Core.User": _FakeResponse(200, user_body),
    })
    with patch.object(requests.Session, "get", fake_get), patch.object(requests.Session, "post", fake_get):
        c = _logged_in_client()
        members = c.list_group_members("자료실 회원")
    assert {m["name"] for m in members} == {"hong"}


def test_list_group_members_falls_back_when_primary_returns_empty():
    """primary 가 success=true 면서 빈 list 반환 시에도 user-filter 폴백 사용."""
    user_body = {
        "success": True,
        "data": {
            "users": [
                {"name": "hong", "groups": [{"name": "자료실 회원"}]},
                {"name": "kim",  "groups": [{"name": "users"}]},
            ],
        },
    }
    fake_get = _make_fake_get({
        "SYNO.Core.Group.Member": _FakeResponse(
            200, {"success": True, "data": {"users": [], "total": 0}},
        ),
        "SYNO.Core.Group": _FakeResponse(200, {"success": True, "data": {"groups": []}}),
        "SYNO.Core.User": _FakeResponse(200, user_body),
    })
    with patch.object(requests.Session, "get", fake_get), patch.object(requests.Session, "post", fake_get):
        c = _logged_in_client()
        members = c.list_group_members("자료실 회원")
    assert {m["name"] for m in members} == {"hong"}


def test_list_group_members_handles_alternate_field_names():
    """일부 DSM 빌드는 'members' 필드를 쓰거나 data 가 직접 list."""
    body = {
        "success": True,
        "data": {"members": [{"name": "hong"}, {"name": "kim"}]},
    }
    with patch.object(
        requests.Session, "get", return_value=_FakeResponse(200, body),
    ):
        c = _logged_in_client()
        members = c.list_group_members("자료실 회원")
    assert {m["name"] for m in members} == {"hong", "kim"}


def test_list_group_members_falls_back_to_gid_when_name_returns_empty():
    """name 으로 조회 시 빈 결과면 list_groups 로 gid 를 찾아 다시 시도."""
    name_empty = {"success": True, "data": {"users": []}}
    list_groups_resp = {
        "success": True,
        "data": {
            "groups": [
                {"name": "users", "gid": 100},
                {"name": "자료실 회원", "gid": 102},
            ],
        },
    }
    gid_resp = {
        "success": True,
        "data": {"users": [{"name": "hong"}, {"name": "kim"}]},
    }
    fake_get = _make_fake_get({
        "SYNO.Core.Group.Member": [
            _FakeResponse(200, name_empty),  # name v1 GET
            _FakeResponse(200, name_empty),  # name v2 GET
            _FakeResponse(200, name_empty),  # name v1 POST
            _FakeResponse(200, gid_resp),    # gid v1 GET — match!
        ],
        "SYNO.Core.Group": _FakeResponse(200, list_groups_resp),
        "SYNO.Core.User": _FakeResponse(200, {"success": True, "data": {}}),
    })
    with patch.object(requests.Session, "get", fake_get), patch.object(requests.Session, "post", fake_get):
        c = _logged_in_client()
        members = c.list_group_members("자료실 회원")
    assert {m["name"] for m in members} == {"hong", "kim"}


def test_list_group_members_uses_group_list_with_additional_member():
    """name·gid 모두 빈 응답이면 SYNO.Core.Group/list?additional=member 시도."""
    empty = {"success": True, "data": {"users": []}}
    list_groups_resp = {
        "success": True,
        "data": {
            "groups": [{"name": "자료실 회원", "gid": 102}],
        },
    }
    group_list_with_member = {
        "success": True,
        "data": {
            "groups": [
                {"name": "자료실 회원", "gid": 102,
                 "member": [{"name": "hong"}, {"name": "kim"}]},
                {"name": "users", "gid": 100, "member": []},
            ],
        },
    }
    fake_get = _make_fake_get({
        "SYNO.Core.Group.Member": _FakeResponse(200, empty),
        "SYNO.Core.Group": [
            _FakeResponse(200, list_groups_resp),         # list (find gid)
            _FakeResponse(200, {"success": True, "data": {}}),  # get name=
            _FakeResponse(200, group_list_with_member),   # list additional=member
        ],
        "SYNO.Core.User": _FakeResponse(200, empty),
    })
    with patch.object(requests.Session, "get", fake_get), patch.object(requests.Session, "post", fake_get):
        c = _logged_in_client()
        members = c.list_group_members("자료실 회원")
    assert {m["name"] for m in members} == {"hong", "kim"}


def test_list_group_members_handles_string_only_items():
    """드물게 항목이 dict 가 아닌 user_name 문자열인 응답도 정상 변환."""
    body = {
        "success": True,
        "data": {"users": ["hong", "kim"]},
    }
    with patch.object(
        requests.Session, "get", return_value=_FakeResponse(200, body),
    ):
        c = _logged_in_client()
        members = c.list_group_members("자료실 회원")
    assert {m["name"] for m in members} == {"hong", "kim"}


def test_list_group_members_propagates_other_errors():
    """code=103 이 아닌 오류는 폴백 없이 그대로 예외."""
    body = {"success": False, "error": {"code": 105}}
    with patch.object(
        requests.Session, "get", return_value=_FakeResponse(200, body),
    ):
        c = _logged_in_client()
        with pytest.raises(DsmAuthError) as exc:
            c.list_group_members("자료실 회원")
        assert exc.value.code == 105
