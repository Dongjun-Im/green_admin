"""DSM 활성화 + 환영메일 자동 체인, 신규 가입자 검출.

두 가지 cross-concern 워크플로:

    activate_subscriber_with_welcome_mail
        DSM 활성화(생성·활성·그룹추가) 후 rtgreen 메일로 환영 발송까지 한 번에.
        메일 발송이 비활성(다른 계정 로그인 등)이면 활성화는 그대로 진행하고
        결과에 사유를 명시.

    detect_new_subscribers
        결제는 활성이지만 DSM 자료실 그룹엔 아직 없는 회원 후보.
        토스 거래내역 가져온 직후 자동 알림 다이얼로그를 띄우는 데 사용.

UI 는 PaymentDialog 가 호출. DSM 로그인·로그아웃은 호출자가 컨텍스트 매니저로
감싸서 책임진다. 즉 한 번 로그인으로 여러 후보를 일괄 처리하는 것을 권장.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from core.dsm_client import DsmAuthError, DsmClient
from core.dsm_service import (
    ActivationResult,
    activate_subscriber,
    is_user_in_dsm,
)
from core.mail_sender import MailResult, MailSender
from core.models import Member
from core.payment_mail import template_subscription_welcome
from core.payment_store import PaymentStore


# ---------- 활성화 + 환영메일 체인 ----------

@dataclass(frozen=True)
class WorkflowResult:
    activation: ActivationResult
    mail_attempted: bool          # 메일 발송을 시도했는가 (세션·계정 조건 충족)
    mail_sent: bool               # 실제 발송 성공 여부
    mail_skip_reason: str = ""    # 시도하지 않은 이유 ("" / "no_session" / "not_rtgreen")
    mail_message: str = ""        # 발송 시도 결과 사람이 읽는 한 줄 (성공·실패 모두)


def match_sorisem_member(
    user_id: str, sorisem_members: list[Member]
) -> Optional[Member]:
    """user_id 와 일치하는 소리샘 회원을 대소문자 무시 매칭.

    DSM 사용자명이 소리샘 user_id 와 같다는 전제 (운영 규약). 소리샘 회원
    목록에 없는 user_id 는 매칭 실패 → 호출자가 메일 발송을 생략하도록 한다.
    """
    if not user_id:
        return None
    target = user_id.strip().lower()
    for m in sorisem_members:
        if (m.user_id or "").strip().lower() == target:
            return m
    return None


def activate_subscriber_with_welcome_mail(
    *,
    dsm_client: DsmClient,
    member: Member,
    group_name: str,
    sorisem_session=None,
    sorisem_current_user_id: str = "",
    sorisem_members: Optional[list[Member]] = None,
    period_to: Optional[date] = None,
    months: int = 0,
    is_renewal: bool = False,
    initial_password: Optional[str] = None,
) -> WorkflowResult:
    """DSM 활성화 → 그룹 추가 → 소리샘 환영 메일 발송 체인.

    DSM 활성화는 항상 시도 (실패 시 예외). 메일은 세 조건이 모두 충족돼야 발송:
        1) sorisem_session 이 있음
        2) sorisem_current_user_id == 'rtgreen'
        3) member.user_id 가 sorisem_members 안에서 매칭됨

    DSM 에 저장된 이메일 주소는 사용하지 않는다 — 메일은 소리샘 내부 메일
    시스템(/message/write.php)으로 user_id 를 받는 사람으로 보내기 때문에,
    소리샘 회원 목록에 없는 사용자에게 발송하면 사이트 거부로 실패한다.
    sorisem_members=None 이면 매칭 검사를 생략하고 인자로 받은 member 를
    그대로 신뢰하는 레거시 모드 — 신규 호출자는 항상 sorisem_members 를 넘길 것.
    """
    activation = activate_subscriber(
        dsm_client,
        member.user_id,
        group_name,
        initial_password=initial_password,
        email="",
        description=member.name or member.nickname or "",
    )

    if sorisem_session is None:
        return WorkflowResult(
            activation=activation,
            mail_attempted=False,
            mail_sent=False,
            mail_skip_reason="no_session",
            mail_message="소리샘 세션이 없어 메일 발송 생략",
        )

    sender = MailSender(sorisem_session, sorisem_current_user_id)
    if not sender.enabled:
        return WorkflowResult(
            activation=activation,
            mail_attempted=False,
            mail_sent=False,
            mail_skip_reason="not_rtgreen",
            mail_message=(
                f"'{MailSender.SENDER_USER_ID}' 로 로그인하지 않아 메일 발송 생략"
            ),
        )

    # 소리샘 회원 매칭 — DSM 사용자명이 소리샘 user_id 와 일치해야만 발송.
    target_member: Member = member
    if sorisem_members is not None:
        canonical = match_sorisem_member(member.user_id, sorisem_members)
        if canonical is None:
            return WorkflowResult(
                activation=activation,
                mail_attempted=False,
                mail_sent=False,
                mail_skip_reason="no_sorisem_match",
                mail_message=(
                    f"'{member.user_id}' 가 소리샘 회원 목록에 없어 메일 발송 생략"
                ),
            )
        target_member = canonical

    # period_to/months 가 비어 있어도 템플릿이 강건하지 않으니 호출자 책임으로 둔다.
    period_to = period_to or date.today()
    months = months or 0
    subject, body = template_subscription_welcome(
        target_member, period_to, months, is_renewal=is_renewal,
    )
    results: list[MailResult] = sender.send(
        [target_member.user_id], subject, body,
    )
    mr = results[0] if results else MailResult(skipped=True, message="결과 없음")
    return WorkflowResult(
        activation=activation,
        mail_attempted=True,
        mail_sent=bool(mr.success),
        mail_skip_reason="",
        mail_message=mr.message or ("발송 완료" if mr.success else "발송 실패"),
    )


# ---------- 사용자 영구 삭제 ----------

@dataclass(frozen=True)
class DeletionResult:
    """delete_subscriber 결과."""
    user_name: str
    found_in_dsm: bool          # DSM 에 사용자가 존재했었는가
    deleted_in_dsm: bool        # DSM 삭제 호출이 정상 수행됐는가
    aliases_removed: int        # 함께 정리된 alias 매핑 행 수
    subscriptions_removed: int = 0   # purge_local 시 함께 삭제한 구독 row 수
    form_record_removed: int = 0     # purge_local 시 삭제한 폼 신청자 행 수 (0/1)


def delete_subscriber(
    *,
    dsm_client: DsmClient,
    member_user_id: str,
    store: Optional[PaymentStore] = None,
    purge_local: bool = False,
) -> DeletionResult:
    """DSM 사용자 영구 삭제 + PaymentStore 정리.

    기본(purge_local=False): DSM 계정 삭제 + alias 매핑만 정리. 구독·거래 이력은
    회계 자료로 보존 — 매트릭스에는 '이전 회원' 등으로 계속 보임.

    purge_local=True: 위에 더해 그 회원의 구독 row + 폼 신청자 캐시 행도 삭제 —
    매트릭스에서 그 회원 행이 완전히 사라진다 (거래 transactions 는 보존).

    DSM 에 없던 사용자면 found_in_dsm=False 로 반환하지만, store 정리는 그대로 수행
    (이미 DSM 에서 빠졌어도 로컬 기록이 남아 매트릭스에 보일 수 있으므로).
    """
    name = (member_user_id or "").strip()
    if not name:
        raise DsmAuthError("user_id 가 비어 있습니다.")

    found = is_user_in_dsm(dsm_client, name)
    deleted = False
    if found:
        dsm_client.delete_user(name)
        deleted = True

    aliases_removed = 0
    subs_removed = 0
    form_removed = 0
    if store is not None:
        aliases_removed = store.delete_aliases_for_user(name)
        if purge_local:
            subs_removed = store.delete_subscriptions_for_user(name)
            form_removed = store.delete_form_applicant(name)

    return DeletionResult(
        user_name=name,
        found_in_dsm=found,
        deleted_in_dsm=deleted,
        aliases_removed=aliases_removed,
        subscriptions_removed=subs_removed,
        form_record_removed=form_removed,
    )


# ---------- 신규 가입자 검출 ----------

@dataclass(frozen=True)
class NewSubscriberCandidate:
    """결제는 활성인데 DSM 자료실 그룹엔 없는 회원."""
    user_id: str
    member: Member          # all_members 에서 못 찾으면 user_id 만 채운 빈 Member
    period_to: date         # 가장 늦은 활성 구독의 만료일
    months: int             # 그 활성 구독의 개월 수
    is_renewal: bool        # 과거에 이미 구독한 적이 있으면 True


def detect_new_subscribers(
    *,
    store: PaymentStore,
    all_members: list[Member],
    dsm_group_member_names: list[str],
    today: Optional[date] = None,
) -> list[NewSubscriberCandidate]:
    """`결제 활성 구독자` 중 DSM 자료실 그룹 멤버가 아닌 사람 목록.

    user_id 매칭은 lower-case 정규화 (DSM 케이스 차이 대비).

    추가로 — 구글 폼('설문지 응답 시트1') 에 이미 같은 이름/희망아이디/입금자명이
    있는 사람은 승인 목록에서 제외한다. 폼 신청으로 이미 파이프라인에 들어와 있다고
    보고, 토스 기반 신규 승인 알림을 중복으로 띄우지 않는다.
    """
    today = today or date.today()
    dsm_set = {(n or "").strip().lower() for n in dsm_group_member_names if n}
    members_by_uid = {m.user_id: m for m in all_members}

    # 회원별 모든 구독 묶기
    by_uid: dict[str, list] = {}
    for sub in store.all_subscriptions():
        by_uid.setdefault(sub.member_user_id, []).append(sub)

    # 구글 폼 신청자 — 이름/희망아이디 집합. + 입금자명→user_id 별칭(역방향).
    try:
        applicants = store.all_form_applicants()
    except Exception:
        applicants = []
    sheet_uids = {(a.member_user_id or "").strip().lower() for a in applicants if a.member_user_id}
    sheet_names = {(a.name or "").strip() for a in applicants if (a.name or "").strip()}
    try:
        aliases = store.all_aliases()  # {payer_name: member_user_id}
    except Exception:
        aliases = {}
    # user_id(lower) → 그 회원으로 매핑된 입금자명 집합
    payers_by_uid: dict[str, set[str]] = {}
    for payer, uid in aliases.items():
        payers_by_uid.setdefault((uid or "").strip().lower(), set()).add((payer or "").strip())

    def _already_in_sheet(uid: str, member: Member) -> bool:
        uid_l = uid.strip().lower()
        if uid_l in sheet_uids:
            return True
        nm = (member.name or "").strip()
        if nm and nm in sheet_names:
            return True
        # 이 회원으로 매핑된 입금자명 중 시트 이름과 같은 게 있으면 제외
        for payer in payers_by_uid.get(uid_l, ()):
            if payer and payer in sheet_names:
                return True
        return False

    candidates: list[NewSubscriberCandidate] = []
    for uid, subs in by_uid.items():
        if not uid:
            continue
        if uid.strip().lower() in dsm_set:
            continue  # 이미 DSM 그룹 멤버
        active = [s for s in subs if s.period_to >= today]
        if not active:
            continue  # 만료된 회원 — 신규 활성화 대상이 아님
        member = members_by_uid.get(uid) or Member(user_id=uid, name="", nickname="")
        if _already_in_sheet(uid, member):
            continue  # 폼 시트에 이미 있는 사람 — 승인 목록에서 제외
        latest = max(active, key=lambda s: s.period_to)
        candidates.append(NewSubscriberCandidate(
            user_id=uid,
            member=member,
            period_to=latest.period_to,
            months=latest.months,
            is_renewal=len(subs) > 1,
        ))

    # 만료일이 가까운(오늘에 가까운) 사람부터 위로 — 일정상 오늘 활성화하면 더
    # 짧게 누리는 사람 먼저 처리하도록.
    candidates.sort(key=lambda c: c.period_to)
    return candidates
