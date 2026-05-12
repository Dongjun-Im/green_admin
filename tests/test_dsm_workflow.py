"""DSM 활성화+환영메일 체인 + 신규 가입자 검출 테스트."""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from core.dsm_workflow import (
    NewSubscriberCandidate,
    activate_subscriber_with_welcome_mail,
    delete_subscriber,
    detect_new_subscribers,
    match_sorisem_member,
)
from core.models import Member


# ---------- 활성화 + 환영메일 체인 ----------

def _mock_dsm_client(*, exists: bool = False):
    client = MagicMock()
    client.list_users.return_value = (
        [{"name": "hong"}] if exists else []
    )
    client.list_group_members.return_value = []
    return client


def test_workflow_activates_and_skips_mail_when_no_session():
    """세션이 None 이면 메일은 건너뛰지만 활성화는 정상 진행."""
    client = _mock_dsm_client(exists=False)
    member = Member(user_id="hong", name="홍길동", nickname="길동")

    res = activate_subscriber_with_welcome_mail(
        dsm_client=client,
        member=member,
        group_name="자료실 회원",
        sorisem_session=None,
        sorisem_current_user_id="",
        period_to=date.today() + timedelta(days=30),
        months=1,
        is_renewal=False,
    )

    client.create_user.assert_called_once()
    client.add_user_to_group.assert_called_once_with("hong", "자료실 회원")
    assert res.activation.created is True
    assert res.mail_attempted is False
    assert res.mail_sent is False
    assert res.mail_skip_reason == "no_session"


def test_workflow_skips_mail_when_not_rtgreen(monkeypatch):
    """rtgreen 이 아닌 계정으로 로그인했으면 메일 발송이 비활성."""
    client = _mock_dsm_client(exists=True)
    member = Member(user_id="hong", name="홍길동")

    fake_session = MagicMock()
    res = activate_subscriber_with_welcome_mail(
        dsm_client=client,
        member=member,
        group_name="자료실 회원",
        sorisem_session=fake_session,
        sorisem_current_user_id="someone_else",
        period_to=date.today() + timedelta(days=30),
        months=1,
    )

    # 활성화는 정상 (이미 존재 → enable + 그룹추가)
    client.enable_user.assert_called_once_with("hong")
    client.add_user_to_group.assert_called_once()
    assert res.activation.created is False
    assert res.mail_attempted is False
    assert res.mail_skip_reason == "not_rtgreen"


def test_workflow_sends_mail_when_rtgreen(monkeypatch):
    """rtgreen 으로 로그인했으면 환영 메일까지 전송."""
    client = _mock_dsm_client(exists=False)
    member = Member(user_id="hong", name="홍길동")

    sent_calls: list = []

    class FakeMailSender:
        SENDER_USER_ID = "rtgreen"

        def __init__(self, session, current_user_id):
            self.enabled = current_user_id.lower() == "rtgreen"

        def send(self, recipients, subject, body, **kwargs):
            from core.mail_sender import MailResult
            sent_calls.append((list(recipients), subject))
            return [MailResult(success=True, message="발송 완료", recipients=list(recipients))]

    monkeypatch.setattr("core.dsm_workflow.MailSender", FakeMailSender)

    fake_session = MagicMock()
    res = activate_subscriber_with_welcome_mail(
        dsm_client=client,
        member=member,
        group_name="자료실 회원",
        sorisem_session=fake_session,
        sorisem_current_user_id="rtgreen",
        period_to=date(2026, 6, 1),
        months=1,
        is_renewal=False,
    )

    assert len(sent_calls) == 1
    recipients, subject = sent_calls[0]
    assert recipients == ["hong"]
    assert "활성화" in subject  # 신규 톤 — "구독이 활성화되었습니다"
    assert res.mail_attempted is True
    assert res.mail_sent is True
    assert res.mail_skip_reason == ""


def test_workflow_skips_mail_when_no_sorisem_match(monkeypatch):
    """sorisem_members 가 주어졌고 user_id 가 그 안에 없으면 메일 발송 생략."""
    client = _mock_dsm_client(exists=False)
    member = Member(user_id="ghost", name="유령")

    sent_calls: list = []

    class FakeMailSender:
        SENDER_USER_ID = "rtgreen"

        def __init__(self, session, current_user_id):
            self.enabled = True

        def send(self, recipients, subject, body, **kwargs):
            from core.mail_sender import MailResult
            sent_calls.append(list(recipients))
            return [MailResult(success=True, recipients=list(recipients))]

    monkeypatch.setattr("core.dsm_workflow.MailSender", FakeMailSender)

    res = activate_subscriber_with_welcome_mail(
        dsm_client=client,
        member=member,
        group_name="자료실 회원",
        sorisem_session=MagicMock(),
        sorisem_current_user_id="rtgreen",
        sorisem_members=[Member(user_id="hong"), Member(user_id="kim")],  # ghost 없음
        period_to=date.today() + timedelta(days=30),
        months=1,
    )

    # 활성화는 됐어야 한다 (DSM 작업 자체는 정상)
    client.create_user.assert_called_once()
    # 메일은 보내지 않아야 한다
    assert sent_calls == []
    assert res.mail_attempted is False
    assert res.mail_sent is False
    assert res.mail_skip_reason == "no_sorisem_match"


def test_workflow_uses_canonical_member_for_subject_and_recipient(monkeypatch):
    """sorisem_members 매칭 결과로 닉네임·user_id 가 바뀌어도 정확히 사용된다."""
    client = _mock_dsm_client(exists=True)
    # DSM 사용자명은 'HONG' (대문자), 매트릭스에서 합성된 member 는 nickname 비어 있음
    raw_member = Member(user_id="HONG", name="", nickname="")
    canonical = Member(user_id="hong", name="홍길동", nickname="길동")

    captured: list = []

    class FakeMailSender:
        SENDER_USER_ID = "rtgreen"

        def __init__(self, session, current_user_id):
            self.enabled = True

        def send(self, recipients, subject, body, **kwargs):
            from core.mail_sender import MailResult
            captured.append((list(recipients), body))
            return [MailResult(success=True, recipients=list(recipients))]

    monkeypatch.setattr("core.dsm_workflow.MailSender", FakeMailSender)

    activate_subscriber_with_welcome_mail(
        dsm_client=client,
        member=raw_member,
        group_name="자료실 회원",
        sorisem_session=MagicMock(),
        sorisem_current_user_id="rtgreen",
        sorisem_members=[canonical],
        period_to=date(2026, 6, 30),
        months=1,
    )
    recipients, body = captured[0]
    # 수신자는 canonical 의 user_id 로 (소리샘 표기)
    assert recipients == ["hong"]
    # 본문 인사말에 닉네임이 들어갔는지
    assert "길동" in body


def test_match_sorisem_member_case_insensitive():
    members = [Member(user_id="Hong"), Member(user_id="kim")]
    assert match_sorisem_member("HONG", members).user_id == "Hong"
    assert match_sorisem_member("kim", members).user_id == "kim"
    assert match_sorisem_member("ghost", members) is None
    assert match_sorisem_member("", members) is None


def test_match_sorisem_member_by_name_unique():
    from core.dsm_workflow import match_sorisem_member_by_name
    members = [
        Member(user_id="rgw107", name="김혜정"),
        Member(user_id="kim2", name="김철수"),
    ]
    assert match_sorisem_member_by_name("김혜정", members).user_id == "rgw107"
    assert match_sorisem_member_by_name(" 김혜정 ", members).user_id == "rgw107"  # 공백 무시
    assert match_sorisem_member_by_name("없는사람", members) is None
    assert match_sorisem_member_by_name("", members) is None


def test_match_sorisem_member_by_name_ambiguous_returns_none():
    from core.dsm_workflow import match_sorisem_member_by_name
    members = [
        Member(user_id="kim1", name="김혜정"),
        Member(user_id="kim2", name="김혜정"),   # 동명이인
    ]
    assert match_sorisem_member_by_name("김혜정", members) is None


def test_resolve_dsm_username_to_sorisem():
    from core.dsm_workflow import resolve_dsm_username_to_sorisem
    members = [
        Member(user_id="rgw107", name="김혜정", nickname="혜정닉"),
        Member(user_id="hong", name="홍길동"),
    ]
    # ① user_id 일치 우선 — 실명은 무시
    m = resolve_dsm_username_to_sorisem("hong", sorisem_members=members, dsm_realname="아무개")
    assert m.user_id == "hong"
    # ② user_id 안 맞으면 실명(유일)으로
    m = resolve_dsm_username_to_sorisem("hj06", sorisem_members=members, dsm_realname="김혜정")
    assert m.user_id == "rgw107" and m.nickname == "혜정닉"
    # 둘 다 안 되면 None
    assert resolve_dsm_username_to_sorisem("hj06", sorisem_members=members, dsm_realname="") is None
    assert resolve_dsm_username_to_sorisem("zzz", sorisem_members=members, dsm_realname="모르는사람") is None


def test_workflow_renewal_subject(monkeypatch):
    """is_renewal=True 면 연장 톤 제목."""
    client = _mock_dsm_client(exists=True)
    member = Member(user_id="hong", name="홍길동")

    captured: list = []

    class FakeMailSender:
        SENDER_USER_ID = "rtgreen"

        def __init__(self, session, current_user_id):
            self.enabled = True

        def send(self, recipients, subject, body, **kwargs):
            from core.mail_sender import MailResult
            captured.append(subject)
            return [MailResult(success=True, message="발송 완료", recipients=recipients)]

    monkeypatch.setattr("core.dsm_workflow.MailSender", FakeMailSender)

    activate_subscriber_with_welcome_mail(
        dsm_client=client,
        member=member,
        group_name="자료실 회원",
        sorisem_session=MagicMock(),
        sorisem_current_user_id="rtgreen",
        period_to=date(2026, 12, 1),
        months=6,
        is_renewal=True,
    )
    assert "연장" in captured[0]


# ---------- 사용자 삭제 ----------

def test_delete_subscriber_removes_dsm_and_aliases():
    client = MagicMock()
    client.list_users.return_value = [{"name": "hong"}]   # DSM 에 존재

    store = MagicMock()
    store.delete_aliases_for_user.return_value = 2

    res = delete_subscriber(
        dsm_client=client, member_user_id="hong", store=store,
    )

    client.delete_user.assert_called_once_with("hong")
    store.delete_aliases_for_user.assert_called_once_with("hong")
    assert res.found_in_dsm is True
    assert res.deleted_in_dsm is True
    assert res.aliases_removed == 2


def test_delete_subscriber_dsm_already_gone():
    """DSM 에 없으면 found_in_dsm=False 로 조용히 반환, alias 정리는 그대로 진행."""
    client = MagicMock()
    client.list_users.return_value = []

    store = MagicMock()
    store.delete_aliases_for_user.return_value = 1

    res = delete_subscriber(
        dsm_client=client, member_user_id="ghost", store=store,
    )
    client.delete_user.assert_not_called()
    store.delete_aliases_for_user.assert_called_once_with("ghost")
    assert res.found_in_dsm is False
    assert res.deleted_in_dsm is False
    assert res.aliases_removed == 1


def test_delete_subscriber_without_store_skips_alias_cleanup():
    client = MagicMock()
    client.list_users.return_value = [{"name": "hong"}]

    res = delete_subscriber(dsm_client=client, member_user_id="hong", store=None)
    client.delete_user.assert_called_once()
    assert res.aliases_removed == 0


def test_delete_subscriber_rejects_empty_user_id():
    from core.dsm_client import DsmAuthError
    client = MagicMock()
    with pytest.raises(DsmAuthError):
        delete_subscriber(dsm_client=client, member_user_id="", store=None)


def test_delete_subscriber_purge_local_wipes_subs_and_form():
    """purge_local=True 면 alias + 구독 + 폼 신청 기록까지 모두 삭제."""
    client = MagicMock()
    client.list_users.return_value = [{"name": "hong"}]
    store = MagicMock()
    store.delete_aliases_for_user.return_value = 1
    store.delete_subscriptions_for_user.return_value = 3
    store.delete_form_applicant.return_value = 1

    res = delete_subscriber(
        dsm_client=client, member_user_id="hong", store=store, purge_local=True,
    )
    client.delete_user.assert_called_once_with("hong")
    store.delete_aliases_for_user.assert_called_once_with("hong")
    store.delete_subscriptions_for_user.assert_called_once_with("hong")
    store.delete_form_applicant.assert_called_once_with("hong")
    assert res.aliases_removed == 1
    assert res.subscriptions_removed == 3
    assert res.form_record_removed == 1


def test_delete_subscriber_no_purge_keeps_subs_and_form():
    """기본(purge_local=False) 이면 alias 만 정리, 구독·폼 기록은 건드리지 않음."""
    client = MagicMock()
    client.list_users.return_value = [{"name": "hong"}]
    store = MagicMock()
    store.delete_aliases_for_user.return_value = 1

    res = delete_subscriber(dsm_client=client, member_user_id="hong", store=store)
    store.delete_subscriptions_for_user.assert_not_called()
    store.delete_form_applicant.assert_not_called()
    assert res.subscriptions_removed == 0
    assert res.form_record_removed == 0


def test_delete_subscriber_purge_local_even_when_dsm_already_gone():
    """DSM 에 이미 없어도 purge_local 이면 로컬 기록은 정리 (매트릭스에서 사라지게)."""
    client = MagicMock()
    client.list_users.return_value = []  # DSM 에 없음
    store = MagicMock()
    store.delete_aliases_for_user.return_value = 0
    store.delete_subscriptions_for_user.return_value = 2
    store.delete_form_applicant.return_value = 1

    res = delete_subscriber(
        dsm_client=client, member_user_id="ghost", store=store, purge_local=True,
    )
    client.delete_user.assert_not_called()
    assert res.found_in_dsm is False
    assert res.subscriptions_removed == 2
    assert res.form_record_removed == 1


# ---------- 신규 가입자 검출 ----------

class _FakeStore:
    """PaymentStore 의 일부만 흉내내는 테스트용 더미."""

    def __init__(self, subs, *, form_applicants=None, aliases=None):
        self._subs = list(subs)
        self._applicants = list(form_applicants or [])
        self._aliases = dict(aliases or {})

    def all_subscriptions(self):
        return list(self._subs)

    def all_form_applicants(self):
        return list(self._applicants)

    def all_aliases(self):
        return dict(self._aliases)


def _make_sub(uid: str, period_to: date, months: int = 1, sub_id: int = 0):
    from core.payment_store import Subscription
    period_from = period_to - timedelta(days=months * 30)
    return Subscription(
        id=sub_id,
        member_user_id=uid,
        transaction_id=sub_id,
        months=months,
        period_from=period_from,
        period_to=period_to,
    )


def test_detect_new_subscribers_basic():
    """결제 활성인데 DSM 그룹엔 없는 사람만 후보로."""
    today = date(2026, 5, 9)
    store = _FakeStore([
        _make_sub("hong", date(2026, 6, 30), months=1, sub_id=1),
        _make_sub("kim", date(2026, 6, 30), months=1, sub_id=2),
        _make_sub("lee", date(2026, 4, 1), months=1, sub_id=3),  # 이미 만료
    ])
    members = [
        Member(user_id="hong", name="홍길동"),
        Member(user_id="kim", name="김철수"),
        Member(user_id="lee", name="이영희"),
    ]
    candidates = detect_new_subscribers(
        store=store,
        all_members=members,
        dsm_group_member_names=["hong"],   # hong 만 그룹에 있음
        today=today,
    )
    uids = [c.user_id for c in candidates]
    assert uids == ["kim"]
    assert candidates[0].months == 1
    assert candidates[0].period_to == date(2026, 6, 30)
    # period_from 도 활성 구독에서 채워진다 (시트 '시작일' 칸 기록용)
    assert candidates[0].period_from == date(2026, 6, 30) - timedelta(days=30)
    assert candidates[0].is_renewal is False  # 단 하나의 구독


def test_detect_new_subscribers_case_insensitive():
    today = date(2026, 5, 9)
    store = _FakeStore([
        _make_sub("Hong", date(2026, 6, 30), sub_id=1),
    ])
    members = [Member(user_id="Hong", name="홍길동")]
    # DSM 그룹 이름은 대문자 — 정규화로 매칭되어 후보에서 제외돼야 함
    candidates = detect_new_subscribers(
        store=store,
        all_members=members,
        dsm_group_member_names=["HONG"],
        today=today,
    )
    assert candidates == []


def test_detect_new_subscribers_marks_renewal():
    """과거에 이미 구독한 적이 있으면 is_renewal=True."""
    today = date(2026, 5, 9)
    store = _FakeStore([
        _make_sub("kim", date(2025, 12, 31), months=6, sub_id=1),  # 과거 구독
        _make_sub("kim", date(2026, 6, 30), months=1, sub_id=2),   # 최근 구독
    ])
    members = [Member(user_id="kim", name="김철수")]
    candidates = detect_new_subscribers(
        store=store,
        all_members=members,
        dsm_group_member_names=[],
        today=today,
    )
    assert len(candidates) == 1
    assert candidates[0].is_renewal is True


def test_detect_new_subscribers_sorted_by_period_to():
    """만료일이 더 가까운 사람이 먼저."""
    today = date(2026, 5, 9)
    store = _FakeStore([
        _make_sub("late", date(2026, 12, 31), sub_id=1),
        _make_sub("early", date(2026, 5, 31), sub_id=2),
    ])
    members = [
        Member(user_id="late"),
        Member(user_id="early"),
    ]
    candidates = detect_new_subscribers(
        store=store,
        all_members=members,
        dsm_group_member_names=[],
        today=today,
    )
    assert [c.user_id for c in candidates] == ["early", "late"]


def test_detect_new_subscribers_handles_member_not_in_list():
    """all_members 에 없는 user_id 도 빈 Member 로 후보 처리."""
    today = date(2026, 5, 9)
    store = _FakeStore([
        _make_sub("ghost", date(2026, 6, 30), sub_id=1),
    ])
    candidates = detect_new_subscribers(
        store=store,
        all_members=[],
        dsm_group_member_names=[],
        today=today,
    )
    assert len(candidates) == 1
    assert candidates[0].user_id == "ghost"
    assert candidates[0].member.user_id == "ghost"
    assert candidates[0].member.name == ""


def test_detect_new_subscribers_picks_latest_period():
    """동일 회원에 여러 활성 구독이 있으면 가장 늦은 만료일을 사용."""
    today = date(2026, 5, 9)
    store = _FakeStore([
        _make_sub("park", date(2026, 6, 30), months=1, sub_id=1),
        _make_sub("park", date(2026, 12, 31), months=6, sub_id=2),  # 더 늦음
    ])
    candidates = detect_new_subscribers(
        store=store,
        all_members=[Member(user_id="park")],
        dsm_group_member_names=[],
        today=today,
    )
    assert len(candidates) == 1
    assert candidates[0].period_to == date(2026, 12, 31)
    assert candidates[0].months == 6


def _applicant(uid: str = "", name: str = ""):
    from core.models import FormApplicant
    return FormApplicant(member_user_id=uid, name=name)


def test_detect_new_subscribers_excludes_form_sheet_name_match():
    """구글 폼 시트에 같은 이름이 있으면 승인 목록에서 제외."""
    today = date(2026, 5, 9)
    store = _FakeStore(
        [
            _make_sub("hong", date(2026, 6, 30), sub_id=1),
            _make_sub("kim", date(2026, 6, 30), sub_id=2),
        ],
        form_applicants=[_applicant(uid="someone_else", name="홍길동")],  # 이름 일치
    )
    members = [Member(user_id="hong", name="홍길동"), Member(user_id="kim", name="김철수")]
    candidates = detect_new_subscribers(
        store=store, all_members=members, dsm_group_member_names=[], today=today,
    )
    # hong 은 시트의 '홍길동' 과 이름이 같아 제외, kim 만 남음
    assert [c.user_id for c in candidates] == ["kim"]


def test_detect_new_subscribers_excludes_form_sheet_userid_match():
    """폼 시트의 희망아이디가 후보 user_id 와 같으면 제외 (대소문자 무시)."""
    today = date(2026, 5, 9)
    store = _FakeStore(
        [_make_sub("Hong", date(2026, 6, 30), sub_id=1), _make_sub("kim", date(2026, 6, 30), sub_id=2)],
        form_applicants=[_applicant(uid="hong", name="다른이름")],
    )
    members = [Member(user_id="Hong", name="홍길동"), Member(user_id="kim", name="김철수")]
    candidates = detect_new_subscribers(
        store=store, all_members=members, dsm_group_member_names=[], today=today,
    )
    assert [c.user_id for c in candidates] == ["kim"]


def test_detect_new_subscribers_excludes_form_sheet_payer_name_match():
    """입금자명(alias)→user_id 인 후보의 입금자명이 시트 이름과 같으면 제외."""
    today = date(2026, 5, 9)
    store = _FakeStore(
        [_make_sub("hong", date(2026, 6, 30), sub_id=1), _make_sub("kim", date(2026, 6, 30), sub_id=2)],
        form_applicants=[_applicant(uid="x", name="홍 길 동")],   # 시트 이름
        aliases={"홍 길 동": "hong"},                              # 입금자명 → hong
    )
    members = [Member(user_id="hong", name=""), Member(user_id="kim", name="김철수")]
    candidates = detect_new_subscribers(
        store=store, all_members=members, dsm_group_member_names=[], today=today,
    )
    assert [c.user_id for c in candidates] == ["kim"]


def test_detect_new_subscribers_keeps_when_not_in_sheet():
    """폼 시트에 없는 사람은 그대로 후보로 — 빈 시트면 기존 동작 유지."""
    today = date(2026, 5, 9)
    store = _FakeStore(
        [_make_sub("hong", date(2026, 6, 30), sub_id=1)],
        form_applicants=[_applicant(uid="park", name="박영희")],  # 무관한 사람만 시트에
    )
    members = [Member(user_id="hong", name="홍길동")]
    candidates = detect_new_subscribers(
        store=store, all_members=members, dsm_group_member_names=[], today=today,
    )
    assert [c.user_id for c in candidates] == ["hong"]
