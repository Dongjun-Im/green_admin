"""DSM 워크플로우 서비스 테스트 — 클라이언트는 mock."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.dsm_client import DsmAuthError
from core.dsm_service import (
    activate_subscriber,
    deactivate_subscriber,
    generate_temp_password,
    is_user_in_dsm,
    is_user_in_group,
)


# ---------- 임시 비밀번호 ----------

def test_generate_temp_password_default_length():
    pw = generate_temp_password()
    assert len(pw) == 12


def test_generate_temp_password_uniqueness():
    """충돌 가능성은 낮지만 sanity — 두 번 호출이 항상 같지 않다."""
    a = generate_temp_password()
    b = generate_temp_password()
    assert a != b


def test_generate_temp_password_has_all_char_classes():
    """대문자·소문자·숫자·특수문자가 각 1자 이상 — DSM 정책 만족용."""
    for _ in range(50):  # 랜덤이라 여러 번 확인
        pw = generate_temp_password()
        assert any(c.isupper() for c in pw), pw
        assert any(c.islower() for c in pw), pw
        assert any(c.isdigit() for c in pw), pw
        assert any(c in "!@#$%^*" for c in pw), pw


def test_generate_temp_password_respects_length():
    assert len(generate_temp_password(8)) == 8
    assert len(generate_temp_password(20)) == 20
    # 4 미만이면 4로 올림
    assert len(generate_temp_password(1)) == 4
    assert len(generate_temp_password(0)) == 4


# ---------- 존재 검사 ----------

def test_is_user_in_dsm_case_insensitive():
    client = MagicMock()
    client.list_users.return_value = [{"name": "Hong"}, {"name": "Kim"}]
    assert is_user_in_dsm(client, "hong")
    assert is_user_in_dsm(client, "HONG")
    assert not is_user_in_dsm(client, "lee")


def test_is_user_in_group():
    client = MagicMock()
    client.list_group_members.return_value = [{"name": "hong"}]
    assert is_user_in_group(client, "hong", "자료실 회원")
    assert not is_user_in_group(client, "kim", "자료실 회원")


# ---------- activate_subscriber ----------

def test_activate_creates_new_user_when_absent():
    client = MagicMock()
    client.list_users.return_value = []           # DSM 에 없음
    client.list_group_members.return_value = []   # 그룹 멤버도 없음

    result = activate_subscriber(client, "hong", "자료실 회원")

    client.create_user.assert_called_once()
    call_kwargs = client.create_user.call_args.kwargs
    assert call_kwargs.get("name") == "hong" or client.create_user.call_args.args[0] == "hong"
    client.add_user_to_group.assert_called_once_with("hong", "자료실 회원")

    assert result.created is True
    assert result.in_group is True
    assert result.initial_password  # 자동 임시 비밀번호 발급됨


def test_activate_uses_provided_password_when_creating():
    client = MagicMock()
    client.list_users.return_value = []
    client.list_group_members.return_value = []

    result = activate_subscriber(
        client, "hong", "자료실 회원", initial_password="my_chosen_pw",
    )
    assert result.initial_password == "my_chosen_pw"


def test_activate_enables_existing_user_without_recreating():
    client = MagicMock()
    client.list_users.return_value = [{"name": "hong"}]   # 이미 존재
    client.list_group_members.return_value = []           # 그룹엔 없음

    result = activate_subscriber(client, "hong", "자료실 회원")

    client.create_user.assert_not_called()
    client.enable_user.assert_called_once_with("hong")
    client.add_user_to_group.assert_called_once_with("hong", "자료실 회원")
    assert result.created is False
    assert result.enabled is True
    assert result.initial_password == ""  # 임시 비밀번호 발급 안 됨


def test_activate_skips_group_add_if_already_member():
    client = MagicMock()
    client.list_users.return_value = [{"name": "hong"}]
    client.list_group_members.return_value = [{"name": "hong"}]   # 이미 그룹 멤버

    result = activate_subscriber(client, "hong", "자료실 회원")

    client.enable_user.assert_called_once()
    client.add_user_to_group.assert_not_called()
    assert result.in_group is True


def test_activate_rejects_empty_user_id():
    client = MagicMock()
    with pytest.raises(DsmAuthError):
        activate_subscriber(client, "", "자료실 회원")


def test_activate_retries_with_stronger_password_on_policy_error():
    """자동 생성 비밀번호가 정책 위반(code=3121)으로 거부되면 더 강한 비밀번호로 1회 재시도."""
    client = MagicMock()
    client.list_users.return_value = []
    client.list_group_members.return_value = []
    # 1회차 실패, 2회차 성공
    client.create_user.side_effect = [DsmAuthError("정책 위반", code=3121), None]

    result = activate_subscriber(client, "hong", "자료실 회원")

    assert client.create_user.call_count == 2
    assert result.created is True
    # 2회차 호출에 쓰인 비밀번호가 result.initial_password 와 일치
    second_pw = client.create_user.call_args_list[1].kwargs["password"]
    assert result.initial_password == second_pw
    # 재시도 비밀번호는 길게(20자) — 정책 만족 강화
    assert len(second_pw) == 20


def test_activate_propagates_when_retry_also_fails():
    """재시도도 정책 위반이면 그대로 예외 전파."""
    client = MagicMock()
    client.list_users.return_value = []
    client.list_group_members.return_value = []
    client.create_user.side_effect = DsmAuthError("정책 위반", code=3121)

    with pytest.raises(DsmAuthError):
        activate_subscriber(client, "hong", "자료실 회원")
    assert client.create_user.call_count == 2  # 최초 + 재시도 1회


def test_activate_no_retry_when_password_explicitly_given():
    """운영자가 비밀번호를 직접 지정했으면 정책 위반 시 재시도 안 함 — 바로 전파."""
    client = MagicMock()
    client.list_users.return_value = []
    client.list_group_members.return_value = []
    client.create_user.side_effect = DsmAuthError("정책 위반", code=3121)

    with pytest.raises(DsmAuthError):
        activate_subscriber(client, "hong", "자료실 회원", initial_password="weak")
    client.create_user.assert_called_once()


def test_activate_no_retry_on_non_policy_error():
    """정책 코드가 아닌 다른 오류(예: 권한)는 재시도 없이 그대로 전파."""
    client = MagicMock()
    client.list_users.return_value = []
    client.list_group_members.return_value = []
    client.create_user.side_effect = DsmAuthError("권한 없음", code=105)

    with pytest.raises(DsmAuthError):
        activate_subscriber(client, "hong", "자료실 회원")
    client.create_user.assert_called_once()


def test_activate_survives_group_add_failure():
    """그룹 추가가 실패해도 사용자 생성·활성은 유지하고 group_error 에 사유를 담는다."""
    client = MagicMock()
    client.list_users.return_value = []           # DSM 에 없음 → 생성
    client.list_group_members.return_value = []   # 그룹 멤버 아님
    client.add_user_to_group.side_effect = DsmAuthError(
        "이 DSM 빌드는 API 로 그룹 멤버 추가가 지원되지 않습니다 (마지막 코드=3201)…",
        code=3201,
    )

    result = activate_subscriber(client, "kmk8030", "자료실 회원")

    client.create_user.assert_called_once()
    assert result.created is True
    assert result.in_group is False
    assert "3201" in result.group_error or "지원" in result.group_error
    assert result.user_name == "kmk8030"


def test_activate_group_add_ok_no_error():
    """그룹 추가가 정상이면 group_error 는 빈 문자열, in_group=True."""
    client = MagicMock()
    client.list_users.return_value = []
    client.list_group_members.return_value = []
    # add_user_to_group 는 MagicMock 기본 동작 — 예외 없음

    result = activate_subscriber(client, "hong", "자료실 회원")
    assert result.in_group is True
    assert result.group_error == ""


# ---------- deactivate_subscriber ----------

def test_deactivate_disables_and_removes_from_group():
    client = MagicMock()
    client.list_users.return_value = [{"name": "hong"}]
    client.list_group_members.return_value = [{"name": "hong"}]

    result = deactivate_subscriber(client, "hong", "자료실 회원")

    client.disable_user.assert_called_once_with("hong")
    client.remove_user_from_group.assert_called_once_with("hong", "자료실 회원")
    assert result.found is True
    assert result.disabled is True
    assert result.removed_from_group is True


def test_deactivate_user_not_in_dsm():
    client = MagicMock()
    client.list_users.return_value = []  # DSM 에 없음

    result = deactivate_subscriber(client, "hong", "자료실 회원")

    client.disable_user.assert_not_called()
    client.remove_user_from_group.assert_not_called()
    assert result.found is False
    assert result.disabled is False


def test_deactivate_user_in_dsm_but_not_in_group():
    client = MagicMock()
    client.list_users.return_value = [{"name": "hong"}]
    client.list_group_members.return_value = []  # 그룹엔 이미 없음

    result = deactivate_subscriber(client, "hong", "자료실 회원")

    client.disable_user.assert_called_once()
    client.remove_user_from_group.assert_not_called()
    assert result.found is True
    assert result.disabled is True
    assert result.removed_from_group is False


# ---------- 정합성 진단 ----------

def test_compute_sync_diff_basic():
    from core.dsm_service import compute_sync_diff
    diff = compute_sync_diff(
        dsm_group_members=["hong", "kim", "lee"],
        active_payment_user_ids={"hong", "park"},
        dsm_total_users=20,
    )
    assert diff.dsm_only == ["kim", "lee"]
    assert diff.payment_only == ["park"]
    assert diff.consistent == ["hong"]
    assert diff.is_safe_to_compute is True


def test_compute_sync_diff_case_insensitive():
    from core.dsm_service import compute_sync_diff
    diff = compute_sync_diff(
        dsm_group_members=["HONG", "Kim"],
        active_payment_user_ids={"hong", "kim"},
        dsm_total_users=10,
    )
    assert diff.dsm_only == []
    assert diff.payment_only == []
    assert set(diff.consistent) == {"hong", "kim"}


def test_compute_sync_diff_zero_dsm_users_marked_unsafe():
    """DSM 사용자 0명 — 비정상 응답일 수 있어 동기화 중단해야 함."""
    from core.dsm_service import compute_sync_diff
    diff = compute_sync_diff([], {"hong"}, dsm_total_users=0)
    assert diff.is_safe_to_compute is False


def test_compute_sync_diff_strips_and_ignores_empty():
    from core.dsm_service import compute_sync_diff
    diff = compute_sync_diff(
        dsm_group_members=["  hong  ", "", "kim"],
        active_payment_user_ids={"  hong  ", ""},
        dsm_total_users=5,
    )
    assert "hong" in diff.consistent
    assert "kim" in diff.dsm_only
