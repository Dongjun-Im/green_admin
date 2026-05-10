"""DSM 사용자 활성화·비활성화 워크플로우.

DsmClient 의 raw API 메서드를 묶어 다음 같은 의도 단위 동작을 제공:

    activate_subscriber   — 신규/연장 회원을 DSM 자료실 그룹에 활성화
    deactivate_subscriber — 만료 회원을 DSM 에서 비활성 + 그룹 제거
    is_user_in_dsm        — 사용자 존재 여부
    compute_sync_diff     — DSM 그룹 멤버 vs 결제 활성 구독자 정합성 비교

높은 수준 흐름은 UI 가 호출. 클라이언트 로그인·로그아웃은 호출자가 책임
(컨텍스트 매니저로 감싸서 사용).
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from core.dsm_client import (
    PASSWORD_POLICY_ERROR_CODES,
    DsmAuthError,
    DsmClient,
)


@dataclass(frozen=True)
class ActivationResult:
    user_name: str
    created: bool      # 신규 계정 생성된 경우 True
    enabled: bool      # 활성화 완료 (이미 활성 → True)
    in_group: bool     # 그룹 추가 성공 (이미 멤버 → True). False 면 group_error 참고
    initial_password: str = ""  # 신규 생성 시 자동 임시 비밀번호 (운영자가 회원에게 안내)
    group_error: str = ""       # 그룹 추가 실패 사유 (빈 문자열이면 성공) — 사용자 계정 자체는 생성/활성됨


@dataclass(frozen=True)
class DeactivationResult:
    user_name: str
    found: bool         # DSM 에 존재했는지
    disabled: bool      # 비활성 처리 성공
    removed_from_group: bool


def is_user_in_dsm(client: DsmClient, name: str) -> bool:
    """사용자 존재 여부 — 전체 목록을 조회해 이름 일치 검색."""
    users = client.list_users()
    target = name.strip().lower()
    return any((u.get("name", "").strip().lower() == target) for u in users)


def is_user_in_group(client: DsmClient, user_name: str, group_name: str) -> bool:
    members = client.list_group_members(group_name)
    target = user_name.strip().lower()
    return any((u.get("name", "").strip().lower() == target) for u in members)


# 임시 비밀번호 문자 풀 — DSM 비밀번호 정책(대소문자/숫자/특수문자)을 항상 만족하도록
# 4종을 각 1자 이상 포함시킨다. 특수문자는 폼 POST·DSM 파서에서 안전한 것만 사용
# (& = + / \ " ' < > ; 공백 backtick 등 제외).
_PW_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"   # I, O 제외 (헷갈림 방지)
_PW_LOWER = "abcdefghijkmnpqrstuvwxyz"   # l, o 제외
_PW_DIGIT = "23456789"                   # 0, 1 제외
_PW_SPECIAL = "!@#$%^*"
_PW_ALL = _PW_UPPER + _PW_LOWER + _PW_DIGIT + _PW_SPECIAL
_PW_RNG = secrets.SystemRandom()


def generate_temp_password(length: int = 12) -> str:
    """초대용 임시 비밀번호 — 항상 대문자·소문자·숫자·특수문자 각 1자 이상 포함.

    DSM 의 비밀번호 강도 정책(대소문자 혼용/숫자 포함/특수문자 포함/최소 길이)을
    어떤 합리적 설정에서도 만족하도록 4종 문자류를 보장한다. 운영자가 회원에게
    메일로 안내 후 첫 로그인에서 변경하도록 유도.

    length < 4 이면 4 로 올림.
    """
    n = max(4, int(length))
    chars = [
        _PW_RNG.choice(_PW_UPPER),
        _PW_RNG.choice(_PW_LOWER),
        _PW_RNG.choice(_PW_DIGIT),
        _PW_RNG.choice(_PW_SPECIAL),
    ]
    chars += [_PW_RNG.choice(_PW_ALL) for _ in range(n - 4)]
    _PW_RNG.shuffle(chars)
    return "".join(chars)


def activate_subscriber(
    client: DsmClient,
    member_user_id: str,
    group_name: str,
    *,
    initial_password: str | None = None,
    email: str = "",
    description: str = "",
) -> ActivationResult:
    """신규 또는 비활성 회원을 활성화.

    이미 존재하면: enable + 그룹 추가
    없으면: create (자동 비밀번호 또는 초대 비밀번호) + 그룹 추가

    initial_password=None 이면 임시 비밀번호 자동 생성.
    """
    name = member_user_id.strip()
    if not name:
        raise DsmAuthError("user_id 가 비어 있습니다.")

    exists = is_user_in_dsm(client, name)
    created = False
    issued_password = ""

    if not exists:
        pw = initial_password or generate_temp_password()
        issued_password = pw
        try:
            client.create_user(
                name=name, password=pw,
                email=email, description=description,
                expired="normal",
            )
        except DsmAuthError as e:
            # 자동 생성 비밀번호가 정책 위반으로 거부됐으면 더 강한(긴) 비밀번호로 1회 재시도.
            # 운영자가 비밀번호를 직접 지정한 경우(initial_password)는 재시도 안 하고 그대로 전파.
            if initial_password is None and getattr(e, "code", None) in PASSWORD_POLICY_ERROR_CODES:
                pw = generate_temp_password(20)
                issued_password = pw
                client.create_user(
                    name=name, password=pw,
                    email=email, description=description,
                    expired="normal",
                )
            else:
                raise
        created = True
    else:
        client.enable_user(name)
        # 운영자가 비밀번호 재설정을 명시적으로 요청한 경우만 변경
        if initial_password:
            client.set_user(name, password=initial_password)
            issued_password = initial_password

    # 그룹 추가 — 이미 멤버이면 스킵. add 가 실패해도(일부 DSM 빌드는 그룹 멤버
    # API 자체가 안 됨) 사용자 계정 생성·활성은 그대로 유지하고, group_error 에
    # 사유를 담아 운영자가 DSM 웹에서 직접 그룹에 추가하도록 안내한다.
    group_error = ""
    in_group = True
    try:
        already_member = is_user_in_group(client, name, group_name)
    except DsmAuthError:
        already_member = False  # 그룹 멤버 조회가 안 되는 빌드 — 일단 추가 시도
    if not already_member:
        try:
            client.add_user_to_group(name, group_name)
        except DsmAuthError as e:
            in_group = False
            group_error = str(e)

    return ActivationResult(
        user_name=name,
        created=created,
        enabled=True,
        in_group=in_group,
        initial_password=issued_password,
        group_error=group_error,
    )


def deactivate_subscriber(
    client: DsmClient,
    member_user_id: str,
    group_name: str,
) -> DeactivationResult:
    """회원 비활성화 + 자료실 그룹 제거.

    DSM 에 없는 사용자면 found=False 로 조용히 반환 (이미 정리된 상태).
    """
    name = member_user_id.strip()
    if not name:
        raise DsmAuthError("user_id 가 비어 있습니다.")
    if not is_user_in_dsm(client, name):
        return DeactivationResult(
            user_name=name, found=False, disabled=False, removed_from_group=False,
        )

    client.disable_user(name)
    removed = False
    if is_user_in_group(client, name, group_name):
        client.remove_user_from_group(name, group_name)
        removed = True

    return DeactivationResult(
        user_name=name, found=True, disabled=True, removed_from_group=removed,
    )


# ---------- 정합성 진단 ----------

@dataclass(frozen=True)
class SyncDiff:
    """DSM 자료실 그룹 멤버 vs 결제 활성 구독자 비교 결과.

    의도: 운영자가 한눈에 어긋남을 보고 직접 결정 (자동 처리 X — 안전 우선).
    """
    dsm_only: list[str] = field(default_factory=list)
    """DSM 그룹엔 있지만 결제 활성 구독자가 아닌 사용자 — 정리 대상 후보."""
    payment_only: list[str] = field(default_factory=list)
    """결제는 활성인데 DSM 그룹엔 없는 회원 — 활성화 누락 후보."""
    consistent: list[str] = field(default_factory=list)
    """양쪽 모두 활성 — 정상."""
    dsm_total_users: int = 0
    """DSM 전체 사용자 수 (그룹 무관). 0 이면 동기화 중단."""

    @property
    def is_safe_to_compute(self) -> bool:
        """DSM 사용자 0명이면 비정상 응답일 가능성 — 동기화 중단 권장."""
        return self.dsm_total_users > 0


def compute_sync_diff(
    dsm_group_members: list[str],
    active_payment_user_ids: set[str],
    dsm_total_users: int,
) -> SyncDiff:
    """DSM 그룹 vs 결제 활성 구독자 비교.

    매칭은 user_id 의 lower-case 정규화로 한다 (DSM 케이스 차이 대비).
    """
    norm = lambda s: s.strip().lower()
    dsm_set = {norm(n) for n in dsm_group_members if n}
    pay_set = {norm(n) for n in active_payment_user_ids if n}

    only_dsm = sorted(dsm_set - pay_set)
    only_pay = sorted(pay_set - dsm_set)
    common = sorted(dsm_set & pay_set)

    return SyncDiff(
        dsm_only=only_dsm,
        payment_only=only_pay,
        consistent=common,
        dsm_total_users=dsm_total_users,
    )
